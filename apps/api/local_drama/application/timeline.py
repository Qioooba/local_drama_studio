"""Local-only timeline, subtitle, audio, enhancement and delivery services.

This module deliberately uses persisted immutable revisions and real FFmpeg
files.  It does not know about ComfyUI; a generation provider only needs to
produce a registered MediaVersion before the timeline can consume it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from local_drama.application.media import MediaService, _hash_file
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _normalized_text_with_offsets(value: str) -> tuple[str, list[int]]:
    """Collapse whitespace while retaining a map back to the authoritative text."""
    normalized: list[str] = []
    offsets: list[int] = []
    in_whitespace = False
    for offset, character in enumerate(value):
        if character.isspace():
            if normalized and not in_whitespace:
                normalized.append(" ")
                offsets.append(offset)
            in_whitespace = True
            continue
        normalized.append(character)
        offsets.append(offset)
        in_whitespace = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def _timestamp_us(value: int) -> str:
    if value < 0:
        raise DomainRuleError("TIMELINE_TIME_INVALID", "时间戳不能为负数")
    total_ms = value // 1000
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, milliseconds = divmod(remainder, 60_000)
    seconds, millis = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _srt_time(value: int) -> str:
    total_ms = value // 1000
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


class TimelineService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def _episode(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT e.*, s.project_id, p.root_rel FROM episodes e
                JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id
                WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return dict(row)

    def _project_root(self, episode_id: str) -> Path:
        episode = self._episode(episode_id)
        root = (self.settings.projects_root / str(episode["root_rel"])).resolve()
        if not root.is_relative_to(self.settings.projects_root.resolve()):
            raise DomainRuleError("PATH_ESCAPE", "项目根目录越界")
        return root

    def _project_root_for_project(self, project_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        root = (self.settings.projects_root / str(row["root_rel"])).resolve()
        if not root.is_relative_to(self.settings.projects_root.resolve()):
            raise DomainRuleError("PATH_ESCAPE", "项目根目录越界")
        return root

    def _resolve_lut_file(self, project_id: str, path_rel: str) -> Path:
        candidate_rel = Path(path_rel)
        if candidate_rel.is_absolute() or ".." in candidate_rel.parts or not path_rel.strip():
            raise DomainRuleError("POST_PROCESS_LUT_PATH_INVALID", "LUT 文件必须是项目内相对路径")
        root = self._project_root_for_project(project_id)
        candidate = root / candidate_rel
        resolved = candidate.resolve()
        if candidate.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(root):
            raise DomainRuleError("POST_PROCESS_LUT_FILE_INVALID", "LUT 文件必须是项目内普通文件")
        if resolved.suffix.lower() != ".cube":
            raise DomainRuleError("POST_PROCESS_LUT_FORMAT_INVALID", "当前只接受 .cube LUT 文件")
        return resolved

    def _media_for_episode(self, episode_id: str, media_version_id: str) -> dict[str, Any]:
        episode = self._episode(episode_id)
        item = self.media.get_version(media_version_id)
        if item["project_id"] != episode["project_id"]:
            raise DomainRuleError("MEDIA_PROJECT_MISMATCH", "时间线媒体必须属于同一项目")
        return item

    def create_timeline_revision(
        self,
        episode_id: str,
        items: list[dict[str, Any]],
        input_snapshot: dict[str, Any],
        *,
        status: str = "DRAFT",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        self._episode(episode_id)
        if not items:
            raise DomainRuleError("TIMELINE_ITEMS_REQUIRED", "时间线至少需要一个 item")
        normalized: list[dict[str, Any]] = []
        for raw in items:
            try:
                start_us = int(raw["start_us"])
                end_us = int(raw["end_us"])
            except (KeyError, TypeError, ValueError) as error:
                raise DomainRuleError("TIMELINE_TIME_INVALID", "timeline item 必须包含整数 start_us/end_us") from error
            if start_us < 0 or end_us <= start_us:
                raise DomainRuleError("TIMELINE_TIME_INVALID", "timeline item 必须满足 0 <= start_us < end_us")
            media_version_id = raw.get("media_version_id")
            if media_version_id:
                self._media_for_episode(episode_id, str(media_version_id))
            normalized.append(
                {
                    "track_type": str(raw.get("track_type", "VIDEO")),
                    "media_version_id": str(media_version_id) if media_version_id else None,
                    "start_us": start_us,
                    "end_us": end_us,
                    "parameters": raw.get("parameters", {}),
                }
            )
        normalized.sort(key=lambda item: (item["start_us"], item["track_type"], item["media_version_id"] or ""))
        snapshot = {"items": normalized, "input_snapshot": input_snapshot}
        revision_hash = _hash(snapshot)
        revision_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            next_no = connection.execute(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM timeline_revisions WHERE episode_id=?", (episode_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO timeline_revisions
                (id, episode_id, revision_no, content_json, input_snapshot_json, revision_hash, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (revision_id, episode_id, next_no, _json(normalized), _json(input_snapshot), revision_hash, status, now, now, actor),
            )
            for item in normalized:
                connection.execute(
                    """INSERT INTO timeline_items
                    (id, timeline_revision_id, track_type, media_version_id, start_us, end_us, parameters_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), revision_id, item["track_type"], item["media_version_id"], item["start_us"], item["end_us"], _json(item["parameters"])),
                )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'TIMELINE_REVISION_CREATED', 'timeline_revision', ?, ?, ?)""",
                (actor, revision_id, "创建不可变时间线 revision", _json({"episode_id": episode_id, "revision_hash": revision_hash})),
            )
        return self.get_timeline(revision_id)

    def get_timeline(self, timeline_revision_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM timeline_revisions WHERE id=?", (timeline_revision_id,)).fetchone()
            if row is None:
                raise DomainRuleError("TIMELINE_REVISION_NOT_FOUND", "时间线 revision 不存在")
            items = connection.execute("SELECT * FROM timeline_items WHERE timeline_revision_id=? ORDER BY start_us, id", (timeline_revision_id,)).fetchall()
        return {
            **dict(row),
            "content": json.loads(row["content_json"]),
            "input_snapshot": json.loads(row["input_snapshot_json"]),
            "items": [{**dict(item), "parameters": json.loads(item["parameters_json"])} for item in items],
        }

    def create_subtitle_revision(
        self,
        episode_id: str,
        cues: list[dict[str, Any]],
        *,
        format: str = "SRT",
        authority: dict[str, Any],
        actor: str = "local-user",
    ) -> dict[str, Any]:
        episode = self._episode(episode_id)
        source_text, authority_snapshot = self._subtitle_authority(episode, authority)
        normalized_source, source_offsets = _normalized_text_with_offsets(source_text)
        normalized_format = format.upper()
        if normalized_format not in {"SRT", "VTT", "ASS"}:
            raise DomainRuleError("SUBTITLE_FORMAT_UNSUPPORTED", "只支持 SRT、VTT、ASS")
        if not cues:
            raise DomainRuleError("SUBTITLE_CUES_REQUIRED", "字幕至少需要一个 cue")
        normalized: list[dict[str, Any]] = []
        previous_end = -1
        source_cursor = 0
        source_passages: list[dict[str, Any]] = []
        for index, raw in enumerate(cues, start=1):
            try:
                start_us = int(raw["start_us"])
                end_us = int(raw["end_us"])
                text = str(raw["text"]).strip()
            except (KeyError, TypeError, ValueError) as error:
                raise DomainRuleError("SUBTITLE_CUE_INVALID", "字幕 cue 字段无效") from error
            if start_us < 0 or end_us <= start_us or not text:
                raise DomainRuleError("SUBTITLE_CUE_INVALID", "字幕 cue 必须有正时长和非空文本")
            if start_us < previous_end:
                raise DomainRuleError("SUBTITLE_OVERLAP", "字幕 cue 不能重叠")
            duration_seconds = (end_us - start_us) / 1_000_000
            cps = len(text) / duration_seconds
            if cps > 25:
                raise DomainRuleError("SUBTITLE_CPS_EXCEEDED", "字幕字符速度超过 25 CPS", {"cue_no": index, "cps": round(cps, 2)})
            normalized_cue, _ = _normalized_text_with_offsets(text)
            match_start = normalized_source.find(normalized_cue, source_cursor)
            if match_start < 0:
                raise DomainRuleError(
                    "SUBTITLE_SCRIPT_AUTHORITY_MISMATCH",
                    "字幕文本必须按剧本原文顺序逐字派生；ASR 只能辅助时间对齐",
                    {"cue_no": index},
                )
            match_end = match_start + len(normalized_cue)
            original_start = source_offsets[match_start]
            original_end = source_offsets[match_end - 1] + 1
            source_passages.append(
                {
                    "cue_no": index,
                    "source_start": original_start,
                    "source_end": original_end,
                    "source_quote_sha256": hashlib.sha256(source_text[original_start:original_end].encode("utf-8")).hexdigest(),
                }
            )
            source_cursor = match_end
            normalized.append({"cue_no": index, "start_us": start_us, "end_us": end_us, "text": text, "style": raw.get("style", {})})
            previous_end = end_us
        authority_snapshot["source_passages"] = source_passages
        content = self._render_subtitles(normalized, normalized_format)
        revision_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            next_no = connection.execute("SELECT COALESCE(MAX(revision_no), 0) + 1 FROM subtitle_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0]
            connection.execute(
                """INSERT INTO subtitle_revisions
                (id, episode_id, revision_no, format, content_text, content_hash, input_snapshot_json, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, 1, 'v2')""",
                (revision_id, episode_id, next_no, normalized_format, content, hashlib.sha256(content.encode()).hexdigest(), _json(authority_snapshot), now, now, actor),
            )
            for cue in normalized:
                connection.execute(
                    "INSERT INTO subtitle_cues (id, subtitle_revision_id, cue_no, start_us, end_us, text, style_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), revision_id, cue["cue_no"], cue["start_us"], cue["end_us"], cue["text"], _json(cue["style"])),
                )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'SUBTITLE_REVISION_CREATED', 'subtitle_revision', ?, ?, ?)",
                (actor, revision_id, "创建字幕 revision", _json({"episode_id": episode_id, "format": normalized_format})),
            )
        return self.get_subtitles(revision_id)

    def _subtitle_authority(self, episode: dict[str, Any], authority: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if authority.get("text_authority") != "SCRIPT":
            raise DomainRuleError("SUBTITLE_SCRIPT_AUTHORITY_REQUIRED", "字幕文本权威必须明确设置为 SCRIPT")
        version_id = str(authority.get("source_document_version_id") or "")
        with self.database.connect() as connection:
            version = connection.execute(
                """SELECT sdv.*, sd.project_id, sd.source_kind
                FROM source_document_versions sdv JOIN source_documents sd ON sd.id=sdv.source_document_id
                WHERE sdv.id=?""",
                (version_id,),
            ).fetchone()
        if version is None:
            raise DomainRuleError("SOURCE_DOCUMENT_VERSION_NOT_FOUND", "剧本文档版本不存在")
        if str(version["project_id"]) != str(episode["project_id"]):
            raise DomainRuleError("SUBTITLE_SOURCE_PROJECT_MISMATCH", "字幕权威剧本必须属于同一项目")
        if version["source_kind"] != "SCRIPT" or version["parse_status"] != "PARSED" or not version["extracted_text_rel"]:
            raise DomainRuleError("SUBTITLE_SCRIPT_SOURCE_INVALID", "字幕权威来源必须是已解析的剧本文档版本")
        project_root = (self.settings.projects_root / str(episode["root_rel"])).resolve()
        source_candidate = project_root / str(version["extracted_text_rel"])
        source_path = source_candidate.resolve()
        if source_candidate.is_symlink() or not source_path.is_relative_to(project_root) or not source_path.is_file():
            raise DomainRuleError("SUBTITLE_SCRIPT_SOURCE_INVALID", "剧本提取文本不存在或路径不安全")
        source_text = source_path.read_text(encoding="utf-8")
        text_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if text_sha256 != version["text_sha256"]:
            raise DomainRuleError("SUBTITLE_SCRIPT_INTEGRITY_FAILED", "剧本提取文本哈希校验失败")

        asr_media_id = authority.get("asr_alignment_media_version_id")
        asr_profile_id = authority.get("asr_profile_version_id")
        if bool(asr_media_id) != bool(asr_profile_id):
            raise DomainRuleError("SUBTITLE_ASR_ALIGNMENT_INCOMPLETE", "ASR 对齐媒体与已发布 Profile 必须同时提供")
        asr_snapshot: dict[str, Any] | None = None
        if asr_media_id and asr_profile_id:
            media = self._media_for_episode(str(episode["id"]), str(asr_media_id))
            if media["media_kind"] not in {"AUDIO", "VIDEO"} or media["integrity_status"] != "VERIFIED":
                raise DomainRuleError("SUBTITLE_ASR_MEDIA_INVALID", "ASR 对齐媒体必须是已验证的本地音频或视频")
            with self.database.connect() as connection:
                profile = connection.execute(
                    "SELECT capability, status FROM execution_profile_versions WHERE id=?",
                    (str(asr_profile_id),),
                ).fetchone()
            if profile is None or profile["status"] != "PUBLISHED":
                raise DomainRuleError("SUBTITLE_ASR_PROFILE_INVALID", "ASR 对齐必须使用已发布 Profile")
            capability_text = str(profile["capability"]).upper()
            if not re.search(r"(^|[^A-Z])ASR([^A-Z]|$)|SPEECH[_ -]?TO[_ -]?TEXT", capability_text):
                raise DomainRuleError("SUBTITLE_ASR_CAPABILITY_REQUIRED", "所选 Profile 未声明 ASR 能力")
            asr_snapshot = {
                "media_version_id": str(asr_media_id),
                "media_sha256": media["sha256"],
                "profile_version_id": str(asr_profile_id),
                "text_authority": False,
                "purpose": "TIMING_ALIGNMENT_ONLY",
            }
        return source_text, {
            "schema_version": "localdrama.subtitle-authority.v1",
            "text_authority": "SCRIPT",
            "source_document_version_id": version_id,
            "source_text_sha256": text_sha256,
            "asr_alignment": asr_snapshot,
            "asr_text_authority": False,
        }

    def _render_subtitles(self, cues: list[dict[str, Any]], format: str) -> str:
        if format == "VTT":
            lines = ["WEBVTT", ""]
            for cue in cues:
                lines.extend([str(cue["cue_no"]), f"{_timestamp_us(cue['start_us']).replace(',', '.')} --> {_timestamp_us(cue['end_us']).replace(',', '.')}", cue["text"], ""])
            return "\n".join(lines)
        if format == "ASS":
            lines = ["[Script Info]", "ScriptType: v4.00+", "", "[Events]", "Format: Layer, Start, End, Text"]
            for cue in cues:
                def ass_time(value: int) -> str:
                    centiseconds = value // 10_000
                    hours, rest = divmod(centiseconds, 360_000)
                    minutes, rest = divmod(rest, 6000)
                    seconds, cs = divmod(rest, 100)
                    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
                lines.append(f"Dialogue: 0,{ass_time(cue['start_us'])},{ass_time(cue['end_us'])},{cue['text'].replace(chr(10), r'\\N')}")
            return "\n".join(lines) + "\n"
        lines = []
        for cue in cues:
            lines.extend([str(cue["cue_no"]), f"{_srt_time(cue['start_us'])} --> {_srt_time(cue['end_us'])}", cue["text"], ""])
        return "\n".join(lines)

    def get_subtitles(self, subtitle_revision_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM subtitle_revisions WHERE id=?", (subtitle_revision_id,)).fetchone()
            if row is None:
                raise DomainRuleError("SUBTITLE_REVISION_NOT_FOUND", "字幕 revision 不存在")
            cues = connection.execute("SELECT * FROM subtitle_cues WHERE subtitle_revision_id=? ORDER BY cue_no", (subtitle_revision_id,)).fetchall()
        result = dict(row)
        snapshot = json.loads(result.pop("input_snapshot_json"))
        result["input_snapshot"] = snapshot
        result["authority_status"] = (
            "VERIFIED_SCRIPT" if snapshot.get("schema_version") == "localdrama.subtitle-authority.v1" else "LEGACY_INCOMPLETE"
        )
        result["cues"] = [{**dict(cue), "style": json.loads(cue["style_json"])} for cue in cues]
        return result

    def bind_audio(
        self,
        episode_id: str,
        media_version_id: str,
        track_type: str,
        start_us: int,
        end_us: int,
        *,
        gain_db: float = 0.0,
        source_license_status: str = "VERIFIED_LOCAL",
        license_evidence_path_rel: str,
        loop_enabled: bool = False,
        fade_in_us: int = 0,
        fade_out_us: int = 0,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        media = self.media.verify_content_integrity(media_version_id)
        episode = self._episode(episode_id)
        if media["project_id"] != episode["project_id"] or media["media_kind"] != "AUDIO":
            raise DomainRuleError("AUDIO_BINDING_MEDIA_INVALID", "音频绑定必须引用同项目已验证 AUDIO MediaVersion")
        if track_type not in {"DIALOGUE", "ENVIRONMENT", "SFX", "MUSIC"}:
            raise DomainRuleError("AUDIO_TRACK_TYPE_INVALID", "音频轨道必须是 DIALOGUE、ENVIRONMENT、SFX 或 MUSIC")
        if end_us <= start_us or start_us < 0:
            raise DomainRuleError("AUDIO_BINDING_RANGE_INVALID", "音频绑定时间范围无效")
        if source_license_status not in {"VERIFIED_LOCAL", "PUBLIC_DOMAIN", "USER_OWNED"}:
            raise DomainRuleError("AUDIO_LICENSE_REQUIRED", "音频必须具有可证明的本地授权状态")
        duration_us = int(media.get("duration_ms") or 0) * 1000
        if not loop_enabled and duration_us > 0 and end_us - start_us > duration_us:
            raise DomainRuleError("AUDIO_BINDING_EXCEEDS_SOURCE", "未启用 loop 时绑定时长不能超过源音频")
        if fade_in_us + fade_out_us > end_us - start_us:
            raise DomainRuleError("AUDIO_FADE_RANGE_INVALID", "淡入与淡出总时长不能超过绑定范围")
        root = (self.settings.projects_root / str(episode["root_rel"])).resolve()
        evidence_candidate = root / license_evidence_path_rel
        evidence_path = evidence_candidate.resolve()
        if evidence_candidate.is_symlink() or not evidence_path.is_relative_to(root) or not evidence_path.is_file():
            raise DomainRuleError("AUDIO_LICENSE_EVIDENCE_INVALID", "音频授权证据必须是项目内普通文件")
        evidence_sha256, evidence_size = _hash_file(evidence_path)
        license_evidence = {
            "schema_version": "localdrama.audio-license-evidence.v1",
            "path_rel": evidence_path.relative_to(root).as_posix(),
            "sha256": evidence_sha256,
            "byte_size": evidence_size,
        }
        binding_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO audio_bindings
                (id, episode_id, media_version_id, track_type, start_us, end_us, gain_db, source_license_status,
                 status, snapshot_json, loop_enabled, fade_in_us, fade_out_us, license_evidence_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (
                    binding_id, episode_id, media_version_id, track_type, start_us, end_us, gain_db, source_license_status,
                    _json({"schema_version": "localdrama.audio-binding.v1", "media_version_id": media_version_id, "media_sha256": media["sha256"], "gain_db": gain_db, "loop_enabled": loop_enabled, "fade_in_us": fade_in_us, "fade_out_us": fade_out_us}),
                    int(loop_enabled), fade_in_us, fade_out_us, _json(license_evidence), now, now, actor,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'AUDIO_BINDING_CREATED', 'audio_binding', ?, ?, ?)",
                (actor, binding_id, "绑定本地音频轨道", _json({"episode_id": episode_id, "track_type": track_type})),
            )
        return self.get_audio_binding(binding_id)

    def get_audio_binding(self, binding_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM audio_bindings WHERE id=?", (binding_id,)).fetchone()
        if row is None:
            raise DomainRuleError("AUDIO_BINDING_NOT_FOUND", "音频绑定不存在")
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        evidence = json.loads(result.pop("license_evidence_json"))
        result["license_evidence"] = evidence
        result["authorization_status"] = "VERIFIED_EVIDENCE" if evidence.get("schema_version") == "localdrama.audio-license-evidence.v1" else "LEGACY_INCOMPLETE"
        result["loop_enabled"] = bool(result["loop_enabled"])
        return result

    def list_audio_bindings(self, episode_id: str) -> list[dict[str, Any]]:
        self._episode(episode_id)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM audio_bindings WHERE episode_id=? ORDER BY start_us, id", (episode_id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["snapshot"] = json.loads(item.pop("snapshot_json"))
            evidence = json.loads(item.pop("license_evidence_json"))
            item["license_evidence"] = evidence
            item["authorization_status"] = "VERIFIED_EVIDENCE" if evidence.get("schema_version") == "localdrama.audio-license-evidence.v1" else "LEGACY_INCOMPLETE"
            item["loop_enabled"] = bool(item["loop_enabled"])
            items.append(item)
        return items

    def create_frame_anchor(
        self,
        source_media_version_id: str,
        *,
        source_time_us: int | None = None,
        source_frame_index: int | None = None,
        position_mode: str | None = None,
        role_hint: str = "LAST_FRAME",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        source = self.media.verify_content_integrity(source_media_version_id)
        if source["media_kind"] != "VIDEO":
            raise DomainRuleError("FRAME_ANCHOR_SOURCE_INVALID", "首尾帧必须来自视频媒体")
        if sum(value is not None for value in (source_time_us, source_frame_index, position_mode)) != 1:
            raise DomainRuleError("FRAME_ANCHOR_POSITION_REQUIRED", "必须且只能提供 time_us、frame_index 或 FIRST/LAST position_mode 之一")
        if (source_time_us is not None and source_time_us < 0) or (source_frame_index is not None and source_frame_index < 0):
            raise DomainRuleError("FRAME_ANCHOR_POSITION_REQUIRED", "time_us/frame_index 必须为非负整数")
        project_root = (self.settings.projects_root / source["root_rel"]).resolve()
        source_path = (project_root / source["rel_path"]).resolve()
        timestamps = self._video_frame_timestamps(source_path)
        requested_time_us = source_time_us
        if position_mode is not None:
            resolved_frame_index = 0 if position_mode == "FIRST_FRAME" else len(timestamps) - 1
        elif source_frame_index is not None:
            if source_frame_index >= len(timestamps):
                raise DomainRuleError(
                    "FRAME_ANCHOR_POSITION_OUT_OF_RANGE",
                    "请求帧索引超过视频真实帧范围",
                    {"requested_frame_index": source_frame_index, "frame_count": len(timestamps)},
                )
            resolved_frame_index = source_frame_index
        else:
            assert source_time_us is not None
            probe = source.get("probe", {})
            format_data = probe.get("format", {}) if isinstance(probe, dict) else {}
            try:
                duration_us = int(Decimal(str(format_data.get("duration", "0"))) * 1_000_000)
            except InvalidOperation as error:
                raise DomainRuleError("FRAME_DURATION_INVALID", "视频 probe duration 无效") from error
            if duration_us <= 0:
                raise DomainRuleError("FRAME_DURATION_REQUIRED", "视频缺少可验证的正 duration")
            if source_time_us >= duration_us:
                raise DomainRuleError(
                    "FRAME_ANCHOR_POSITION_OUT_OF_RANGE",
                    "请求时间超出视频真实时长范围",
                    {"requested_time_us": source_time_us, "duration_us": duration_us, "last_frame_time_us": timestamps[-1]},
                )
            resolved_frame_index = max(index for index, timestamp in enumerate(timestamps) if timestamp <= source_time_us)
        resolved_time_us = timestamps[resolved_frame_index]
        frame_dir = self.settings.work_root / "frame_anchor_extract"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / f"anchor-{uuid.uuid4().hex}.png"
        args = ["-i", str(source_path), "-vf", f"select=eq(n\\,{resolved_frame_index})", "-frames:v", "1", "-fps_mode", "vfr", "-y", str(frame_path)]
        try:
            self._run_ffmpeg(args, timeout=120)
            if not frame_path.is_file() or frame_path.stat().st_size == 0:
                raise DomainRuleError("FRAME_ANCHOR_EXTRACTION_EMPTY", "FFmpeg 未生成可注册的真实视频帧")
            imported = self.media.import_file(source["project_id"], frame_path, purpose="FRAME_ANCHOR", owner_type="MEDIA_VERSION", owner_id=source_media_version_id, media_kind="IMAGE", stage="FRAME_ANCHOR", actor=actor)
        finally:
            frame_path.unlink(missing_ok=True)
        anchor_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO frame_anchors
                (id, source_media_version_id, source_time_us, source_frame_index, extracted_media_version_id,
                 role_hint, sha256, approval_id, created_at, updated_at, created_by, revision, schema_version,
                 requested_time_us, resolved_time_us, source_sha256, extraction_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 1, 'v2', ?, ?, ?, 'FFPROBE_PTS_FRAME_INDEX')""",
                (anchor_id, source_media_version_id, resolved_time_us, resolved_frame_index, imported["media_version_id"], role_hint, imported["sha256"], now, now, actor, requested_time_us, resolved_time_us, source["actual_sha256"]),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'FRAME_ANCHOR_CREATED', 'frame_anchor', ?, ?, ?)",
                (actor, anchor_id, "从视频提取连续性关键帧", _json({"source_media_version_id": source_media_version_id, "source_sha256": source["actual_sha256"], "position_mode": position_mode, "requested_time_us": requested_time_us, "resolved_time_us": resolved_time_us, "resolved_frame_index": resolved_frame_index, "role_hint": role_hint})),
            )
        return self.get_frame_anchor(anchor_id)

    def _video_frame_timestamps(self, path: Path) -> list[int]:
        ffprobe = self.settings.ffprobe_path
        if not ffprobe or not Path(ffprobe).is_file():
            raise DomainRuleError("FFPROBE_UNAVAILABLE", "本机 FFprobe 不可用")
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("FFPROBE_FAILED", "本地 FFprobe 帧时间码读取失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("FFPROBE_FAILED", "本地 FFprobe 帧时间码读取失败", {"stderr_redacted": result.stderr[-500:]})
        timestamps: list[int] = []
        for line in result.stdout.splitlines():
            raw = line.strip().split(",", 1)[0]
            if not raw or raw == "N/A":
                continue
            try:
                timestamps.append(int(Decimal(raw) * 1_000_000))
            except InvalidOperation as error:
                raise DomainRuleError("FRAME_TIMESTAMP_INVALID", "FFprobe 返回了无效帧时间码", {"value": raw}) from error
        if not timestamps or timestamps != sorted(timestamps):
            raise DomainRuleError("FRAME_TIMESTAMPS_UNAVAILABLE", "视频缺少可用于精确取帧的单调时间码")
        return timestamps

    def get_frame_anchor(self, anchor_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM frame_anchors WHERE id=?", (anchor_id,)).fetchone()
        if row is None:
            raise DomainRuleError("FRAME_ANCHOR_NOT_FOUND", "FrameAnchor 不存在")
        return dict(row)

    def create_transition_constraint(
        self,
        from_shot_id: str,
        to_shot_id: str,
        constraint_type: str,
        *,
        from_anchor_id: str | None = None,
        to_anchor_id: str | None = None,
        enforcement: str = "HARD",
        note: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if from_shot_id == to_shot_id:
            raise DomainRuleError("TRANSITION_SHOT_INVALID", "连续性约束不能连接同一镜头")
        with self.database.transaction() as connection:
            shots = connection.execute(
                """SELECT sh.id, se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE sh.id IN (?, ?)""",
                (from_shot_id, to_shot_id),
            ).fetchall()
            if len(shots) != 2:
                raise DomainRuleError("SHOT_NOT_FOUND", "连续性约束镜头不存在")
            if len({str(shot["project_id"]) for shot in shots}) != 1:
                raise DomainRuleError("TRANSITION_PROJECT_MISMATCH", "连续性约束的两个镜头必须属于同一项目")
            if from_anchor_id:
                anchor = connection.execute("SELECT id, is_stale, stale_reason FROM frame_anchors WHERE id=?", (from_anchor_id,)).fetchone()
                if anchor is None:
                    raise DomainRuleError("FRAME_ANCHOR_NOT_FOUND", "from anchor 不存在")
                if int(anchor["is_stale"]):
                    raise DomainRuleError(
                        "FRAME_ANCHOR_STALE", "不能用已失效 FrameAnchor 创建新连续性约束", {"side": "from", "stale_reason": anchor["stale_reason"]}
                    )
            if to_anchor_id:
                anchor = connection.execute("SELECT id, is_stale, stale_reason FROM frame_anchors WHERE id=?", (to_anchor_id,)).fetchone()
                if anchor is None:
                    raise DomainRuleError("FRAME_ANCHOR_NOT_FOUND", "to anchor 不存在")
                if int(anchor["is_stale"]):
                    raise DomainRuleError(
                        "FRAME_ANCHOR_STALE", "不能用已失效 FrameAnchor 创建新连续性约束", {"side": "to", "stale_reason": anchor["stale_reason"]}
                    )
            constraint_id = str(uuid.uuid4())
            now = _now()
            connection.execute(
                """INSERT INTO shot_transition_constraints
                (id, from_shot_id, to_shot_id, constraint_type, from_anchor_id, to_anchor_id, enforcement,
                 compatibility_status, note, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?, ?, ?, ?, 1, 'v2')""",
                (constraint_id, from_shot_id, to_shot_id, constraint_type, from_anchor_id, to_anchor_id, enforcement, note, now, now, actor),
            )
        return {"id": constraint_id, "from_shot_id": from_shot_id, "to_shot_id": to_shot_id, "constraint_type": constraint_type, "compatibility_status": "PENDING_REVIEW"}

    def validate_transition_constraint(self, constraint_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            constraint = connection.execute(
                """SELECT stc.*, sf.project_id AS from_project_id, st.project_id AS to_project_id
                FROM shot_transition_constraints stc
                JOIN shots f ON f.id=stc.from_shot_id JOIN episodes ef ON ef.id=f.episode_id JOIN seasons sf ON sf.id=ef.season_id
                JOIN shots t ON t.id=stc.to_shot_id JOIN episodes et ON et.id=t.episode_id JOIN seasons st ON st.id=et.season_id
                WHERE stc.id=?""",
                (constraint_id,),
            ).fetchone()
            if constraint is None:
                raise DomainRuleError("SHOT_TRANSITION_NOT_FOUND", "ShotTransitionConstraint 不存在")
            if int(constraint["is_stale"]):
                return {
                    "constraint_id": constraint_id,
                    "status": "STALE",
                    "blockers": [{"code": "TRANSITION_BOUNDARY_STALE", "reason": constraint["stale_reason"]}],
                    "warnings": [],
                }
            blockers: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            if constraint["from_project_id"] != constraint["to_project_id"]:
                blockers.append({"code": "TRANSITION_PROJECT_MISMATCH"})
            for side, anchor_id, shot_id in (
                ("from", constraint["from_anchor_id"], constraint["from_shot_id"]),
                ("to", constraint["to_anchor_id"], constraint["to_shot_id"]),
            ):
                if not anchor_id:
                    continue
                anchor = connection.execute(
                    """SELECT fa.*, source.integrity_status AS source_integrity, extracted.integrity_status AS extracted_integrity,
                    source.sha256 AS source_sha256, extracted.sha256 AS extracted_sha256,
                    ma.project_id, ma.owner_type, ma.owner_id
                    FROM frame_anchors fa
                    JOIN media_versions source ON source.id=fa.source_media_version_id
                    JOIN media_versions extracted ON extracted.id=fa.extracted_media_version_id
                    JOIN media_assets ma ON ma.id=source.media_asset_id WHERE fa.id=?""",
                    (anchor_id,),
                ).fetchone()
                if anchor is None:
                    blockers.append({"code": "FRAME_ANCHOR_NOT_FOUND", "side": side})
                    continue
                if int(anchor["is_stale"]):
                    blockers.append({"code": "FRAME_ANCHOR_STALE", "side": side, "reason": anchor["stale_reason"]})
                if str(anchor["project_id"]) != str(constraint["from_project_id"]):
                    blockers.append({"code": "FRAME_ANCHOR_PROJECT_MISMATCH", "side": side})
                for media_role, media_version_id in (
                    ("source", str(anchor["source_media_version_id"])),
                    ("extracted", str(anchor["extracted_media_version_id"])),
                ):
                    try:
                        self.media.verify_content_integrity(media_version_id, connection=connection)
                    except DomainRuleError as error:
                        if error.code not in {"SOURCE_INTEGRITY_FAILED", "MEDIA_FILE_MISSING"}:
                            raise
                        blockers.append(
                            {
                                "code": "FRAME_ANCHOR_INTEGRITY_FAILED",
                                "side": side,
                                "media_role": media_role,
                                "media_version_id": media_version_id,
                                "reason": error.code,
                            }
                        )
                if str(anchor["sha256"]) != str(anchor["extracted_sha256"]):
                    blockers.append({"code": "FRAME_ANCHOR_HASH_MISMATCH", "side": side})
                if anchor["owner_type"] == "SHOT" and str(anchor["owner_id"]) != str(shot_id):
                    blockers.append({"code": "FRAME_ANCHOR_SHOT_MISMATCH", "side": side})
                elif anchor["owner_type"] != "SHOT":
                    warnings.append({"code": "FRAME_ANCHOR_PROJECT_BRIDGE", "side": side})
            status = "BLOCKED" if blockers else "WARNING" if warnings else "COMPATIBLE"
            connection.execute(
                "UPDATE shot_transition_constraints SET compatibility_status=?, updated_at=?, revision=revision+1 WHERE id=?",
                (status, _now(), constraint_id),
            )
        return {"constraint_id": constraint_id, "status": status, "blockers": blockers, "warnings": warnings}

    @staticmethod
    def _validated_enhancement_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not steps:
            raise DomainRuleError("POST_PROCESS_RECIPE_INVALID", "增强 recipe 至少需要 SCALE、TECHNICAL_QC 和 ENCODE")
        normalized: list[dict[str, Any]] = []
        supported = {"SCALE", "TECHNICAL_QC", "ENCODE", "FRAME_INTERPOLATION", "DENOISE", "STABILIZE", "LUT_3D"}
        for ordinal, raw in enumerate(steps):
            if not isinstance(raw, dict) or str(raw.get("kind")) not in supported:
                raise DomainRuleError("CAPABILITY_UNSUPPORTED", "增强 recipe 包含未支持步骤", {"ordinal": ordinal, "kind": raw.get("kind") if isinstance(raw, dict) else None})
            step = dict(raw)
            step["kind"] = str(step["kind"])
            executor_ref = str(step.get("executor_ref", ""))
            expected_executor = "builtin:ffprobe" if step["kind"] == "TECHNICAL_QC" else "builtin:ffmpeg"
            if executor_ref != expected_executor:
                raise DomainRuleError("POST_PROCESS_EXECUTOR_REQUIRED", "每个增强步骤必须显式绑定受支持的本地 executor", {"ordinal": ordinal, "expected": expected_executor})
            if step["kind"] == "SCALE":
                width, height = step.get("width"), step.get("height")
                if not isinstance(width, int) or not isinstance(height, int) or not 64 <= width <= 8192 or not 64 <= height <= 8192 or width % 2 or height % 2:
                    raise DomainRuleError("POST_PROCESS_SCALE_INVALID", "SCALE 必须显式给出 64—8192 的偶数 width/height")
                step["fit"] = str(step.get("fit", "CONTAIN"))
                if step["fit"] not in {"CONTAIN", "STRETCH"}:
                    raise DomainRuleError("POST_PROCESS_SCALE_INVALID", "SCALE fit 仅支持 CONTAIN/STRETCH")
            elif step["kind"] == "ENCODE":
                step["codec"] = str(step.get("codec", "H264"))
                step["preset"] = str(step.get("preset", "veryfast"))
                step["crf"] = int(step.get("crf", 18))
                if step["codec"] != "H264" or step["preset"] not in {"ultrafast", "veryfast", "medium", "slow"} or not 0 <= step["crf"] <= 51:
                    raise DomainRuleError("POST_PROCESS_ENCODE_INVALID", "ENCODE 当前只支持本地 H264、受控 preset 和 0—51 CRF")
            elif step["kind"] == "FRAME_INTERPOLATION":
                try:
                    target_fps = int(step.get("target_fps", 0))
                except (TypeError, ValueError) as error:
                    raise DomainRuleError("POST_PROCESS_INTERPOLATION_INVALID", "补帧必须显式提供整数 target_fps") from error
                if not 1 <= target_fps <= 120:
                    raise DomainRuleError("POST_PROCESS_INTERPOLATION_INVALID", "补帧 target_fps 必须在 1—120 之间")
                step["target_fps"] = target_fps
                step["mode"] = str(step.get("mode", "MCI"))
                if step["mode"] not in {"MCI", "DUPLICATE"}:
                    raise DomainRuleError("POST_PROCESS_INTERPOLATION_INVALID", "补帧 mode 仅支持 MCI 或 DUPLICATE")
            elif step["kind"] == "DENOISE":
                try:
                    strength = float(step.get("strength", 1.0))
                except (TypeError, ValueError) as error:
                    raise DomainRuleError("POST_PROCESS_DENOISE_INVALID", "降噪 strength 必须是数字") from error
                if not 0.1 <= strength <= 10.0:
                    raise DomainRuleError("POST_PROCESS_DENOISE_INVALID", "降噪 strength 必须在 0.1—10.0 之间")
                step["strength"] = strength
            elif step["kind"] == "STABILIZE":
                step["mode"] = str(step.get("mode", "DESHAKE"))
                if step["mode"] != "DESHAKE":
                    raise DomainRuleError("POST_PROCESS_STABILIZE_INVALID", "防抖当前只支持本地 FFmpeg deshake")
            elif step["kind"] == "LUT_3D":
                path_rel = str(step.get("path_rel", "")).strip()
                if not path_rel or Path(path_rel).is_absolute() or ".." in Path(path_rel).parts:
                    raise DomainRuleError("POST_PROCESS_LUT_PATH_INVALID", "LUT_3D 必须提供项目内相对 path_rel")
                if Path(path_rel).suffix.lower() != ".cube":
                    raise DomainRuleError("POST_PROCESS_LUT_FORMAT_INVALID", "当前只接受 .cube LUT 文件")
            normalized.append(step)
        kinds = [str(step["kind"]) for step in normalized]
        if (
            kinds.count("SCALE") != 1
            or kinds.count("TECHNICAL_QC") != 1
            or kinds.count("ENCODE") != 1
            or not kinds
            or kinds[0] != "SCALE"
            or len(kinds) < 3
            or kinds[-2:] != ["TECHNICAL_QC", "ENCODE"]
        ):
            raise DomainRuleError("POST_PROCESS_REQUIRED_STEPS_MISSING", "正式增强链必须按 SCALE → 可选步骤 → TECHNICAL_QC → ENCODE 且核心步骤各一次", {"observed": kinds})
        return normalized

    def create_recipe(self, code: str, title: str, steps: list[dict[str, Any]], capability_contract: dict[str, Any], parent_recipe_id: str | None = None, actor: str = "local-user") -> dict[str, Any]:
        recipe_key = code.strip()
        if not recipe_key or not title.strip():
            raise DomainRuleError("POST_PROCESS_RECIPE_INVALID", "增强 recipe 需要 code 和 title")
        normalized_steps = self._validated_enhancement_steps(steps)
        if capability_contract.get("transport") != "LOCAL_PROCESS" or capability_contract.get("network_allowed") is not False:
            raise DomainRuleError("POST_PROCESS_LOCAL_CONTRACT_REQUIRED", "增强 recipe 必须显式声明 LOCAL_PROCESS 且 network_allowed=false")
        optional_kinds = {str(step["kind"]) for step in normalized_steps if step["kind"] not in {"SCALE", "TECHNICAL_QC", "ENCODE"}}
        declared_optional = capability_contract.get("optional_steps", [])
        if optional_kinds and (not isinstance(declared_optional, list) or not optional_kinds.issubset({str(kind) for kind in declared_optional})):
            raise DomainRuleError("CAPABILITY_UNSUPPORTED", "可选后处理步骤必须由 recipe capability_contract 显式声明", {"required": sorted(optional_kinds), "declared": declared_optional})
        recipe_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            parent = None
            if parent_recipe_id:
                parent = connection.execute("SELECT * FROM post_process_recipes WHERE id=?", (parent_recipe_id,)).fetchone()
                if parent is None:
                    raise DomainRuleError("POST_PROCESS_RECIPE_NOT_FOUND", "父增强 recipe 不存在")
                recipe_key = str(parent["recipe_key"] or parent["code"])
            next_version = int(connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM post_process_recipes WHERE recipe_key=?", (recipe_key,)).fetchone()[0])
            physical_code = recipe_key if next_version == 1 else f"{recipe_key}@v{next_version}"
            recipe_hash = _hash({"recipe_key": recipe_key, "version_no": next_version, "steps": normalized_steps, "capability_contract": capability_contract})
            connection.execute(
                """INSERT INTO post_process_recipes
                (id, code, title, steps_json, capability_contract_json, status, created_at, updated_at, created_by,
                 revision, schema_version, recipe_key, version_no, parent_recipe_id, recipe_hash)
                VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, 1, 'v2', ?, ?, ?, ?)""",
                (recipe_id, physical_code, title, _json(normalized_steps), _json(capability_contract), now, now, actor, recipe_key, next_version, parent_recipe_id, recipe_hash),
            )
        return self.get_recipe(recipe_id)

    def list_recipes(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM post_process_recipes ORDER BY recipe_key, version_no DESC, created_at DESC").fetchall()
        return [self.get_recipe(str(row["id"])) for row in rows]

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM post_process_recipes WHERE id=?", (recipe_id,)).fetchone()
        if row is None:
            raise DomainRuleError("POST_PROCESS_RECIPE_NOT_FOUND", "增强 recipe 不存在")
        item = {**dict(row), "steps": json.loads(row["steps_json"]), "capability_contract": json.loads(row["capability_contract_json"])}
        if not item.get("recipe_hash"):
            item["recipe_hash"] = _hash({"recipe_key": item.get("recipe_key") or item["code"], "version_no": item.get("version_no") or 1, "steps": item["steps"], "capability_contract": item["capability_contract"]})
        return item

    def publish_recipe(self, recipe_id: str, actor: str = "local-user") -> dict[str, Any]:
        recipe = self.get_recipe(recipe_id)
        if recipe["status"] != "DRAFT":
            raise DomainRuleError("POST_PROCESS_RECIPE_NOT_DRAFT", "只有 DRAFT recipe 可发布")
        self._validated_enhancement_steps(recipe["steps"])
        now = _now()
        recipe_key = str(recipe.get("recipe_key") or recipe["code"])
        with self.database.transaction() as connection:
            connection.execute("UPDATE post_process_recipes SET status='RETIRED', updated_at=?, revision=revision+1 WHERE recipe_key=? AND status='ACTIVE'", (now, recipe_key))
            connection.execute("UPDATE post_process_recipes SET status='ACTIVE', published_at=?, updated_at=?, revision=revision+1 WHERE id=?", (now, now, recipe_id))
            connection.execute("INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'POST_PROCESS_RECIPE_PUBLISHED', 'post_process_recipe', ?, ?, ?)", (actor, recipe_id, "发布本地增强 recipe", _json({"recipe_key": recipe_key, "version_no": recipe["version_no"], "recipe_hash": recipe["recipe_hash"]})))
        return self.get_recipe(recipe_id)

    def plan_enhancement(self, input_media_version_id: str, recipe_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        recipe = self.get_recipe(recipe_id)
        if recipe["status"] != "ACTIVE":
            raise DomainRuleError("POST_PROCESS_RECIPE_NOT_ACTIVE", "只有已发布 ACTIVE recipe 可执行")
        steps = self._validated_enhancement_steps(recipe["steps"])
        source = self.media.get_version(input_media_version_id)
        if source["media_kind"] != "VIDEO":
            raise DomainRuleError("ENHANCEMENT_VIDEO_REQUIRED", "FR-PST-001 增强链当前只接受 VERIFIED VIDEO MediaVersion")
        verified = self.media.verify_content_integrity(input_media_version_id)
        lut_inputs: list[dict[str, Any]] = []
        for step in steps:
            if step["kind"] != "LUT_3D":
                continue
            lut_path = self._resolve_lut_file(str(source["project_id"]), str(step["path_rel"]))
            lut_sha256, lut_size = _hash_file(lut_path)
            lut_inputs.append({"path_rel": str(step["path_rel"]), "sha256": lut_sha256, "byte_size": lut_size})
        snapshot = {
            "input_media_version_id": input_media_version_id,
            "input_sha256": verified["sha256"],
            "recipe_id": recipe_id,
            "recipe_hash": recipe["recipe_hash"],
            "steps": steps,
            "parameters": parameters or {},
            "lut_inputs": lut_inputs,
        }
        return {
            "status": "READY",
            "plan_hash": _hash(snapshot),
            "snapshot": snapshot,
            "command_preview": {"executor": "builtin:ffmpeg", "input": "REGISTERED_MEDIA_VERSION", "output": "NEW_IMMUTABLE_MEDIA_VERSION", "steps": [step["kind"] for step in steps], "optional_steps": [step["kind"] for step in steps if step["kind"] not in {"SCALE", "TECHNICAL_QC", "ENCODE"}]},
            "would_create_run": False,
            "would_overwrite_input": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def run_enhancement(self, input_media_version_id: str, recipe_id: str, plan_hash: str, parameters: dict[str, Any] | None = None, actor: str = "local-user") -> dict[str, Any]:
        plan = self.plan_enhancement(input_media_version_id, recipe_id, parameters)
        if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("ENHANCEMENT_PLAN_STALE", "增强计划已变化，请重新预检")
        recipe = self.get_recipe(recipe_id)
        source_item, source_path = self.media.content_path(input_media_version_id)
        out_dir = self.settings.work_root / "enhancement_runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        output = out_dir / f"enhanced-{run_id}.mp4"
        intermediate_paths: list[Path] = []
        now = _now()
        steps = recipe["steps"]
        scale = next(step for step in steps if step["kind"] == "SCALE")
        encode = next(step for step in steps if step["kind"] == "ENCODE")
        width, height = int(scale["width"]), int(scale["height"])
        processing_steps = [step for step in steps if step["kind"] not in {"TECHNICAL_QC", "ENCODE"}]
        filter_specs: list[tuple[dict[str, Any], str]] = []
        for step in processing_steps:
            kind = str(step["kind"])
            if kind == "SCALE":
                filter_spec = f"scale={width}:{height}" if scale["fit"] == "STRETCH" else f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            elif kind == "FRAME_INTERPOLATION":
                filter_spec = f"fps={int(step['target_fps'])}" if step["mode"] == "DUPLICATE" else f"minterpolate=fps={int(step['target_fps'])}:mi_mode=mci"
            elif kind == "DENOISE":
                strength = float(step["strength"])
                filter_spec = f"hqdn3d={strength:g}:{strength:g}:{strength:g}:{strength:g}"
            elif kind == "STABILIZE":
                filter_spec = "deshake"
            elif kind == "LUT_3D":
                lut_path = self._resolve_lut_file(str(source_item["project_id"]), str(step["path_rel"]))
                lut_filter_path = str(lut_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                filter_spec = f"lut3d=file='{lut_filter_path}'"
            else:
                raise DomainRuleError("CAPABILITY_UNSUPPORTED", "增强 recipe 包含未支持的本地步骤", {"kind": kind})
            filter_specs.append((step, filter_spec))
        execution_snapshot = {
            **plan["snapshot"],
            "ffmpeg": {
                "video_filters": [{"kind": step["kind"], "filter": "lut3d" if step["kind"] == "LUT_3D" else filter_spec.split("=", 1)[0]} for step, filter_spec in filter_specs],
                "codec": "libx264",
                "preset": encode["preset"],
                "crf": encode["crf"],
                "audio_codec": "aac",
            },
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO enhancement_runs
                (id, input_media_version_id, recipe_id, parameters_json, status, created_at, updated_at, created_by,
                 revision, schema_version, plan_hash, input_sha256, execution_snapshot_json)
                VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?, 1, 'v2', ?, ?, ?)""",
                (run_id, input_media_version_id, recipe_id, _json(parameters or {}), now, now, actor, plan_hash, plan["snapshot"]["input_sha256"], _json(execution_snapshot)),
            )
        try:
            before_qc = self._probe(source_path)
            current_path = source_path
            current_hash = str(plan["snapshot"]["input_sha256"])
            step_trace: list[dict[str, Any]] = []
            for ordinal, (step, filter_spec) in enumerate(filter_specs):
                stage_output = out_dir / f"step-{run_id}-{ordinal}-{str(step['kind']).lower()}.mkv"
                intermediate_paths.append(stage_output)
                self._run_ffmpeg(["-i", str(current_path), "-map", "0:v:0", "-map", "0:a?", "-vf", filter_spec, "-c:v", "ffv1", "-level", "3", "-c:a", "pcm_s16le", "-y", str(stage_output)], timeout=300)
                next_hash, _ = _hash_file(stage_output)
                step_trace.append({"ordinal": ordinal, "kind": step["kind"], "executor_ref": "builtin:ffmpeg", "profile": step, "input_sha256": current_hash, "output_sha256": next_hash, "status": "SUCCEEDED"})
                current_path, current_hash = stage_output, next_hash
            processed_qc = self._probe(current_path)
            qc_step = next(step for step in steps if step["kind"] == "TECHNICAL_QC")
            step_trace.append({"ordinal": len(step_trace), "kind": "TECHNICAL_QC", "executor_ref": "builtin:ffprobe", "profile": qc_step, "input_sha256": current_hash, "output_sha256": current_hash, "status": "PASSED", "result": {"dimensions_match": True}})
            self._run_ffmpeg(["-i", str(current_path), "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", str(encode["preset"]), "-crf", str(encode["crf"]), "-c:a", "aac", "-movflags", "+faststart", "-y", str(output)], timeout=300)
            output_hash, _ = _hash_file(output)
            after_qc = self._probe(output)
            preserved_source = self.media.verify_content_integrity(input_media_version_id)
            if preserved_source["actual_sha256"] != plan["snapshot"]["input_sha256"]:
                raise DomainRuleError("ENHANCEMENT_INPUT_CHANGED", "增强期间输入媒体发生变化，输出未注册")
            expected_fps = next((int(step["target_fps"]) for step in steps if step["kind"] == "FRAME_INTERPOLATION"), None)
            processed_video: dict[str, Any] = next((stream for stream in processed_qc.get("streams", []) if stream.get("codec_type") == "video"), {})
            after_video: dict[str, Any] = next((stream for stream in after_qc.get("streams", []) if stream.get("codec_type") == "video"), {})
            fps_passed = expected_fps is None or all(self._stream_fps_matches(stream, expected_fps) for stream in (processed_video, after_video))
            qc = {
                "before": self._technical_qc_summary(before_qc),
                "scaled": self._technical_qc_summary(processed_qc),
                "processed": self._technical_qc_summary(processed_qc),
                "after": self._technical_qc_summary(after_qc),
                "expected_dimensions": {"width": width, "height": height},
                "expected_fps": expected_fps,
                "fps_match": fps_passed,
                "passed": fps_passed and all(any(int(stream.get("width", 0)) == width and int(stream.get("height", 0)) == height for stream in probe.get("streams", [])) for probe in (processed_qc, after_qc)),
            }
            if not qc["passed"]:
                raise DomainRuleError("ENHANCEMENT_QC_FAILED", "增强输出尺寸或目标帧率未通过技术 QC")
            step_trace.append({"ordinal": len(step_trace), "kind": "ENCODE", "executor_ref": "builtin:ffmpeg", "profile": encode, "input_sha256": current_hash, "output_sha256": output_hash, "status": "SUCCEEDED"})
            execution_snapshot["step_trace"] = step_trace
            imported = self.media.import_file(source_item["project_id"], output, purpose="ENHANCEMENT", owner_type="MEDIA_VERSION", owner_id=input_media_version_id, media_kind=source_item["media_kind"], stage="ENHANCED", actor=actor)
            if imported.get("duplicate"):
                raise DomainRuleError("ENHANCEMENT_OUTPUT_DUPLICATE", "增强输出与现有媒体 hash 相同，未注册伪新版本")
            with self.database.transaction() as connection:
                connection.execute("UPDATE media_versions SET parent_version_id=? WHERE id=?", (input_media_version_id, imported["media_version_id"]))
                connection.execute("UPDATE enhancement_runs SET output_media_version_id=?, output_sha256=?, execution_snapshot_json=?, qc_json=?, status='SUCCEEDED', updated_at=?, revision=revision+1 WHERE id=?", (imported["media_version_id"], imported["sha256"], _json(execution_snapshot), _json(qc), _now(), run_id))
            return self.get_enhancement_run(run_id)
        except DomainRuleError as error:
            with self.database.transaction() as connection:
                connection.execute("UPDATE enhancement_runs SET status='FAILED', error_detail=?, updated_at=?, revision=revision+1 WHERE id=?", (error.code, _now(), run_id))
            raise
        except Exception as error:
            with self.database.transaction() as connection:
                connection.execute("UPDATE enhancement_runs SET status='FAILED', error_detail='UNEXPECTED_LOCAL_FAILURE', updated_at=?, revision=revision+1 WHERE id=?", (_now(), run_id))
            raise DomainRuleError("ENHANCEMENT_EXECUTION_FAILED", "本地增强执行失败", {"reason": type(error).__name__}) from error
        finally:
            for intermediate_path in intermediate_paths:
                intermediate_path.unlink(missing_ok=True)
            output.unlink(missing_ok=True)

    def get_enhancement_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM enhancement_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("ENHANCEMENT_RUN_NOT_FOUND", "增强运行不存在")
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
        item["execution_snapshot"] = json.loads(item.pop("execution_snapshot_json") or "{}")
        item["qc"] = json.loads(item.pop("qc_json") or "{}")
        item["bypass_comparison"] = {"input_media_version_id": item["input_media_version_id"], "output_media_version_id": item["output_media_version_id"], "input_preserved": True}
        return item

    @staticmethod
    def _technical_qc_summary(probe: dict[str, Any]) -> dict[str, Any]:
        """Keep durable QC evidence without persisting source paths or container metadata."""
        streams = probe.get("streams", [])
        return {
            "duration_ms": probe.get("duration_ms"),
            "video": [
                {key: stream.get(key) for key in ("codec_name", "width", "height", "pix_fmt", "avg_frame_rate")}
                for stream in streams
                if stream.get("codec_type") == "video"
            ],
            "audio": [
                {key: stream.get(key) for key in ("codec_name", "sample_rate", "channels", "channel_layout")}
                for stream in streams
                if stream.get("codec_type") == "audio"
            ],
        }

    @staticmethod
    def _stream_fps_matches(stream: dict[str, Any], expected_fps: int) -> bool:
        value = str(stream.get("avg_frame_rate", "0/1"))
        try:
            numerator, denominator = value.split("/", 1)
            observed = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            return False
        return abs(observed - expected_fps) < 0.05

    def render_episode(self, timeline_revision_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        timeline = self.get_timeline(timeline_revision_id)
        episode = self._episode(str(timeline["episode_id"]))
        video_items = [item for item in timeline["items"] if item["track_type"].upper() == "VIDEO" and item["media_version_id"]]
        if not video_items:
            raise DomainRuleError("TIMELINE_VIDEO_REQUIRED", "整集渲染至少需要一个 VIDEO item")
        paths: list[Path] = []
        input_snapshot_items: list[dict[str, Any]] = []
        for item in video_items:
            media = self._media_for_episode(str(timeline["episode_id"]), str(item["media_version_id"]))
            _, path = self.media.content_path(str(item["media_version_id"]))
            if media["media_kind"] != "VIDEO":
                raise DomainRuleError("TIMELINE_MEDIA_KIND_INVALID", "VIDEO track 只能绑定视频媒体")
            paths.append(path)
            input_snapshot_items.append({"media_version_id": str(media["id"]), "sha256": str(media["sha256"]), "byte_size": int(media["byte_size"]), "start_us": int(item["start_us"]), "end_us": int(item["end_us"]), "track_type": str(item["track_type"]), "parameters": item["parameters"]})
        project_root = (self.settings.projects_root / episode["root_rel"]).resolve()
        render_dir = project_root / "05_timelines" / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        render_path = render_dir / f"episode-{episode['code']}-{uuid.uuid4().hex}.mp4"
        concat_list = render_dir / f".partial-{uuid.uuid4().hex}.concat.txt"
        escaped_paths = [path.as_posix().replace("'", "'\\''") for path in paths]
        concat_list.write_text("\n".join(f"file '{path}'" for path in escaped_paths) + "\n", encoding="utf-8")
        try:
            ffmpeg_args = ["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", "-y", str(render_path)]
            execution = self._run_ffmpeg(ffmpeg_args, timeout=900)
        finally:
            concat_list.unlink(missing_ok=True)
        digest, size = _hash_file(render_path)
        probe = self._probe(render_path)
        input_snapshot = {"schema_version": "localdrama.episode-render-input.v1", "timeline_revision_id": timeline_revision_id, "timeline_revision_hash": timeline["revision_hash"], "timeline_input_snapshot": timeline["input_snapshot"], "items": input_snapshot_items}
        ffmpeg_command = {"executor": "builtin:ffmpeg", "executable": execution["executable"], "args": execution["args"], "returncode": execution["returncode"]}
        execution_log = json.dumps({"stdout_tail": execution["stdout_tail"], "stderr_tail": execution["stderr_tail"]}, ensure_ascii=False, sort_keys=True)
        render_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO episode_render_versions
                (id, episode_id, timeline_revision_id, rel_path, sha256, probe_json, integrity_status, duration_ms, mime_type,
                 input_snapshot_json, ffmpeg_command_json, execution_log_text,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', ?, 'video/mp4', ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (render_id, episode["id"], timeline_revision_id, render_path.relative_to(project_root).as_posix(), digest, _json(probe), probe.get("duration_ms"), _json(input_snapshot), _json(ffmpeg_command), execution_log, now, now, actor),
            )
        return {"id": render_id, "episode_id": episode["id"], "timeline_revision_id": timeline_revision_id, "rel_path": render_path.relative_to(project_root).as_posix(), "sha256": digest, "byte_size": size, "probe": probe, "input_snapshot": input_snapshot, "ffmpeg_command": ffmpeg_command, "execution_log": execution_log, "status": "VERIFIED"}

    def render_content_path(self, episode_render_version_id: str) -> tuple[dict[str, Any], Path]:
        """Resolve a registered episode render through the local project root.

        The browser is never allowed to turn a database path into an arbitrary
        filesystem read.  We resolve the project root and render path, reject
        symlinks/escapes, and verify the immutable render hash before serving.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT erv.*, e.code AS episode_code, s.project_id, p.root_rel
                FROM episode_render_versions erv
                JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons s ON s.id=e.season_id
                JOIN projects p ON p.id=s.project_id
                WHERE erv.id=?""",
                (episode_render_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_RENDER_NOT_FOUND", "整集渲染版本不存在")
        project_root = (self.settings.projects_root / str(row["root_rel"])).resolve()
        projects_root = self.settings.projects_root.resolve()
        if project_root.is_symlink() or not project_root.is_dir() or not project_root.is_relative_to(projects_root):
            raise DomainRuleError("EPISODE_RENDER_FILE_INVALID", "整集渲染项目目录不存在或路径越界")
        candidate = project_root / str(row["rel_path"])
        render_path = candidate.resolve()
        if candidate.is_symlink() or not render_path.is_file() or not render_path.is_relative_to(project_root):
            raise DomainRuleError("EPISODE_RENDER_FILE_MISSING", "整集渲染文件缺失或路径越界")
        digest, _ = _hash_file(render_path)
        if not hmac.compare_digest(digest, str(row["sha256"])):
            raise DomainRuleError("EPISODE_RENDER_INTEGRITY_FAILED", "整集渲染文件 hash 与登记值不一致")
        return dict(row), render_path

    def build_delivery(self, episode_render_version_id: str, target_version_id: str, brand_kit_id: str | None = None, watermark_profile_id: str | None = None, compliance_policy_id: str | None = None, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            render = connection.execute("SELECT erv.*, e.code AS episode_code, e.id AS episode_id, s.project_id, p.root_rel FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id WHERE erv.id=?", (episode_render_version_id,)).fetchone()
            target = connection.execute("SELECT dtv.*, dt.project_id, dt.transport, dt.code AS target_code FROM delivery_target_versions dtv JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id WHERE dtv.id=?", (target_version_id,)).fetchone()
        if render is None:
            raise DomainRuleError("EPISODE_RENDER_NOT_FOUND", "整集渲染版本不存在")
        if target is None:
            raise DomainRuleError("DELIVERY_TARGET_VERSION_NOT_FOUND", "交付目标版本不存在")
        if render["project_id"] != target["project_id"]:
            raise DomainRuleError("DELIVERY_PROJECT_MISMATCH", "交付目标必须属于同一项目")
        if target["transport"] != "LOCAL_FILESYSTEM":
            raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许本地文件交付")
        project_id = str(render["project_id"])
        with self.database.connect() as connection:
            brand = connection.execute("SELECT * FROM brand_kits WHERE id=? AND project_id=? AND status='ACTIVE'", (brand_kit_id, project_id)).fetchone() if brand_kit_id else connection.execute("SELECT * FROM brand_kits WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
            watermark = connection.execute("SELECT * FROM watermark_profiles WHERE id=? AND project_id=? AND status='ACTIVE'", (watermark_profile_id, project_id)).fetchone() if watermark_profile_id else connection.execute("SELECT * FROM watermark_profiles WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
            compliance = connection.execute("SELECT * FROM compliance_policies WHERE id=? AND project_id=? AND status='ACTIVE'", (compliance_policy_id, project_id)).fetchone() if compliance_policy_id else connection.execute("SELECT * FROM compliance_policies WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
        if brand_kit_id and brand is None:
            raise DomainRuleError("BRAND_KIT_NOT_ACTIVE", "BrandKit 不存在、项目不匹配或已 RETIRED")
        if watermark_profile_id and watermark is None:
            raise DomainRuleError("WATERMARK_PROFILE_NOT_ACTIVE", "水印版本不存在、项目不匹配或已 RETIRED")
        if compliance_policy_id and compliance is None:
            raise DomainRuleError("COMPLIANCE_POLICY_NOT_ACTIVE", "合规策略不存在、项目不匹配或已 RETIRED")
        brand_snapshot = {"id": str(brand["id"]), "code": str(brand["code"]), "version_no": int(brand["version_no"])} if brand else None
        watermark_config = json.loads(str(watermark["config_json"])) if watermark else None
        watermark_snapshot = {"id": str(watermark["id"]), "code": str(watermark["code"]), "version_no": int(watermark["version_no"]), "config": watermark_config} if watermark else None
        compliance_rules = json.loads(str(compliance["rules_json"])) if compliance else None
        compliance_snapshot = {"id": str(compliance["id"]), "code": str(compliance["code"]), "version_no": int(compliance["version_no"]), "rules": compliance_rules} if compliance else None
        probe = json.loads(str(render["probe_json"]))
        duration_ms = int(render["duration_ms"] or probe.get("duration_ms") or 0)
        findings: list[dict[str, Any]] = []
        if compliance_rules:
            if compliance_rules.get("require_watermark") and not watermark:
                findings.append({"code": "WATERMARK_REQUIRED", "severity": "ERROR", "message": "当前合规策略要求水印，但没有 ACTIVE 水印版本"})
            max_duration_ms = compliance_rules.get("max_duration_ms")
            if max_duration_ms is not None and duration_ms > int(max_duration_ms):
                findings.append({"code": "DURATION_EXCEEDED", "severity": "ERROR", "message": "整集时长超过当前本地合规策略上限", "observed": duration_ms, "limit": int(max_duration_ms)})
        machine_preflight: dict[str, object] = {"status": "FAIL" if findings else "PASS", "findings": findings, "checked_render_sha256": str(render["sha256"]), "responsibility": {"machine": "本地规则预检与文件完整性", "human": "内容/版权/平台最终审核，不由机器结果替代"}}
        if findings:
            raise DomainRuleError("COMPLIANCE_PREFLIGHT_FAILED", "本地合规机器预检未通过", machine_preflight)
        spec = json.loads(target["target_spec_json"])
        path_rel = str(spec.get("path_rel", "06_delivery"))
        if Path(path_rel).is_absolute() or ".." in Path(path_rel).parts:
            raise DomainRuleError("INVALID_DELIVERY_TARGET", "交付目标路径越界")
        project_root = (self.settings.projects_root / render["root_rel"]).resolve()
        source = (project_root / render["rel_path"]).resolve()
        if not source.is_file() or not source.is_relative_to(project_root) or source.is_symlink():
            raise DomainRuleError("EPISODE_RENDER_FILE_MISSING", "整集渲染文件缺失、为 symlink 或路径越界")
        source_hash, _ = _hash_file(source)
        if source_hash != str(render["sha256"]):
            raise DomainRuleError("EPISODE_RENDER_INTEGRITY_FAILED", "整集渲染文件 hash 与登记值不一致")
        destination_dir = (project_root / path_rel / str(render["episode_code"])).resolve()
        if not destination_dir.is_relative_to(project_root):
            raise DomainRuleError("PATH_ESCAPE", "交付目标目录越界")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{render['episode_code']}.mp4"
        partial = destination.with_name(f".partial-{destination.name}")
        watermark_text_path = destination_dir / f".partial-{destination.name}.watermark.txt"
        try:
            if watermark_config:
                watermark_text_path.write_text(str(watermark_config["text"]), encoding="utf-8")
                margin = int(watermark_config["margin"])
                position = str(watermark_config["position"])
                coordinates = {"TOP_LEFT": (str(margin), str(margin)), "TOP_RIGHT": (f"w-text_w-{margin}", str(margin)), "BOTTOM_LEFT": (str(margin), f"h-text_h-{margin}"), "BOTTOM_RIGHT": (f"w-text_w-{margin}", f"h-text_h-{margin}"), "CENTER": ("(w-text_w)/2", "(h-text_h)/2")}[position]
                textfile = watermark_text_path.as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                font_candidates = (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/segoeui.ttf"))
                font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
                if font_path is None:
                    raise DomainRuleError("WATERMARK_FONT_UNAVAILABLE", "本机缺少可用的 Windows 水印字体")
                fontfile = font_path.as_posix().replace(":", "\\:")
                drawtext = f"drawtext=fontfile='{fontfile}':textfile='{textfile}':x={coordinates[0]}:y={coordinates[1]}:fontcolor={watermark_config['color']}@{float(watermark_config['opacity']):.3f}:fontsize={int(watermark_config['font_size'])}"
                self._run_ffmpeg(["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", drawtext, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", "-y", str(partial)], timeout=900)
            else:
                shutil.copyfile(source, partial)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
            watermark_text_path.unlink(missing_ok=True)
        file_hash, byte_size = _hash_file(destination)
        manifest = {"schema_version": "delivery-manifest.v2", "episode_id": render["episode_id"], "timeline_revision_id": render["timeline_revision_id"], "target_version_id": target_version_id, "controls": {"brand_kit": brand_snapshot, "watermark_profile": watermark_snapshot, "compliance_policy": compliance_snapshot, "machine_preflight": machine_preflight}, "review_responsibility": {"machine_preflight": "PASS", "human_review": "PENDING", "platform_review": "PENDING"}, "files": [{"rel_path": destination.relative_to(project_root).as_posix(), "sha256": file_hash, "byte_size": byte_size}]}
        manifest_hash = _hash(manifest)
        manifest_path = destination_dir / "manifest.json"
        manifest_path.write_text(json.dumps({**manifest, "manifest_sha256": manifest_hash}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        package_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO delivery_packages (id, episode_render_version_id, target_version_id, rel_path, status, manifest_sha256, brand_kit_id, watermark_profile_id, compliance_policy_id, machine_preflight_status, machine_preflight_json, human_review_status, platform_review_status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, 'VERIFIED', ?, ?, ?, ?, 'PASS', ?, 'PENDING', 'PENDING', ?, ?, ?, 1, 'v3')", (package_id, episode_render_version_id, target_version_id, destination_dir.relative_to(project_root).as_posix(), manifest_hash, brand["id"] if brand else None, watermark["id"] if watermark else None, compliance["id"] if compliance else None, _json(machine_preflight), now, now, actor))
            connection.execute("INSERT INTO delivery_files (id, delivery_package_id, rel_path, sha256, byte_size) VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), package_id, destination.relative_to(project_root).as_posix(), file_hash, byte_size))
            connection.execute("INSERT INTO delivery_files (id, delivery_package_id, rel_path, sha256, byte_size) VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), package_id, manifest_path.relative_to(project_root).as_posix(), hashlib.sha256(manifest_path.read_bytes()).hexdigest(), manifest_path.stat().st_size))
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'BUILT', ?, ?, ?, ?, ?, 1, 'v3')", (str(uuid.uuid4()), package_id, manifest_hash, "local filesystem delivery built; machine preflight PASS; human/platform review remains separate", now, now, actor))
        return {"id": package_id, "status": "VERIFIED", "rel_path": destination_dir.relative_to(project_root).as_posix(), "manifest_sha256": manifest_hash, "files": manifest["files"], "controls": manifest["controls"], "machine_preflight": machine_preflight, "human_review_status": "PENDING", "platform_review_status": "PENDING"}

    def verify_delivery(self, package_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            package = connection.execute("SELECT dp.*, e.code AS episode_code, p.root_rel FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id JOIN episodes e ON e.id=erv.episode_id JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id WHERE dp.id=?", (package_id,)).fetchone()
            files = connection.execute("SELECT * FROM delivery_files WHERE delivery_package_id=? ORDER BY rel_path", (package_id,)).fetchall()
        if package is None:
            raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
        root = (self.settings.projects_root / package["root_rel"]).resolve()
        checks = []
        for file in files:
            path = (root / file["rel_path"]).resolve()
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            checks.append({"rel_path": file["rel_path"], "expected_sha256": file["sha256"], "actual_sha256": actual, "ok": actual == file["sha256"]})
        ok = all(item["ok"] for item in checks)
        with self.database.transaction() as connection:
            connection.execute("UPDATE delivery_packages SET status=?, updated_at=?, revision=revision+1 WHERE id=?", ("VERIFIED" if ok else "CORRUPT", _now(), package_id))
        return {"id": package_id, "status": "VERIFIED" if ok else "CORRUPT", "checks": checks, "machine_preflight_status": package["machine_preflight_status"], "human_review_status": package["human_review_status"], "platform_review_status": package["platform_review_status"]}

    def withdraw_delivery(self, package_id: str, reason: str, actor: str = "local-user") -> dict[str, Any]:
        if not reason.strip():
            raise DomainRuleError("DELIVERY_WITHDRAW_REASON_REQUIRED", "撤回交付必须记录原因")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT id FROM delivery_packages WHERE id=?", (package_id,)).fetchone()
            if row is None:
                raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
            connection.execute("UPDATE delivery_packages SET status='WITHDRAWN', withdrawn_reason=?, updated_at=?, revision=revision+1 WHERE id=?", (reason, now, package_id))
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'WITHDRAWN', ?, ?, ?, ?, 1, 'v2')", (str(uuid.uuid4()), package_id, reason, now, now, actor))
        return {"id": package_id, "status": "WITHDRAWN", "reason": reason}

    def review_delivery(self, package_id: str, reviewer_type: str, decision: str, note: str, actor: str = "local-user") -> dict[str, Any]:
        if reviewer_type not in {"HUMAN", "PLATFORM"}:
            raise DomainRuleError("DELIVERY_REVIEWER_TYPE_INVALID", "交付审核责任方必须是 HUMAN 或 PLATFORM")
        if decision not in {"APPROVED", "REJECTED"} or not note.strip():
            raise DomainRuleError("DELIVERY_REVIEW_INVALID", "交付审核必须包含 APPROVED/REJECTED 与说明")
        now = _now()
        column = "human_review_status" if reviewer_type == "HUMAN" else "platform_review_status"
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM delivery_packages WHERE id=?", (package_id,)).fetchone()
            if row is None:
                raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
            if str(row["status"]) == "WITHDRAWN":
                raise DomainRuleError("DELIVERY_WITHDRAWN", "已撤回交付不能继续审核")
            connection.execute(f"UPDATE delivery_packages SET {column}=?, updated_at=?, revision=revision+1 WHERE id=?", (decision, now, package_id))
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'v3')", (str(uuid.uuid4()), package_id, f"{reviewer_type}_REVIEW_{decision}", note.strip(), now, now, actor))
            updated = connection.execute("SELECT * FROM delivery_packages WHERE id=?", (package_id,)).fetchone()
        return {"id": package_id, "status": str(updated["status"]), "machine_preflight_status": str(updated["machine_preflight_status"]), "human_review_status": str(updated["human_review_status"]), "platform_review_status": str(updated["platform_review_status"]), "reviewer_type": reviewer_type, "decision": decision, "note": note.strip()}

    def _run_ffmpeg(self, args: list[str], *, timeout: int) -> dict[str, Any]:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            result = subprocess.run([ffmpeg, *args], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "本地 FFmpeg 执行失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "本地 FFmpeg 执行失败", {"stderr_redacted": result.stderr[-500:]})
        return {"executable": str(ffmpeg), "args": args, "returncode": result.returncode, "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-4000:]}

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = self.settings.ffprobe_path
        if not ffprobe or not Path(ffprobe).is_file():
            raise DomainRuleError("FFPROBE_UNAVAILABLE", "本机 FFprobe 不可用")
        try:
            result = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("FFPROBE_FAILED", "本地 FFprobe 检查失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("FFPROBE_FAILED", "本地 FFprobe 检查失败")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DomainRuleError("FFPROBE_INVALID_JSON", "FFprobe 返回无效 JSON") from error
        format_data = data.get("format", {})
        duration = float(format_data.get("duration", 0) or 0)
        return {"duration_ms": round(duration * 1000), "format": format_data, "streams": data.get("streams", [])}
