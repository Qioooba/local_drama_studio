"""Local-only timeline, subtitle, audio, enhancement and delivery services.

This module deliberately uses persisted immutable revisions and real FFmpeg
files.  It does not know about ComfyUI; a generation provider only needs to
produce a registered MediaVersion before the timeline can consume it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from queue import Empty, Queue
from time import monotonic
from typing import Any, Callable, cast

from local_drama.application.media import _hash_file
from local_drama.application.ports.timeline import TimelineMediaPort, TimelineUnitOfWork
from local_drama.application.subtitle_styles import DEFAULT_SUBTITLE_STYLE, validate_style
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.timeline_formatting import (
    canonical_json as _json,
)
from local_drama.domain.timeline_formatting import (
    normalized_text_with_offsets as _normalized_text_with_offsets,
)
from local_drama.domain.timeline_formatting import (
    snapshot_hash as _hash,
)
from local_drama.domain.timeline_formatting import (
    subtitle_time,
)
from local_drama.infrastructure.filesystem.atomic import replace_path


def _build_media_service(database: TimelineUnitOfWork, settings: Settings) -> TimelineMediaPort:
    media = __import__("local_drama.application.media", fromlist=["MediaService"])
    return cast(TimelineMediaPort, media.MediaService(database, settings))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp_us(value: int) -> str:
    return subtitle_time(value)


def _srt_time(value: int) -> str:
    return subtitle_time(value)


class TimelineService:
    def __init__(self, database: TimelineUnitOfWork, settings: Settings, media: TimelineMediaPort | None = None) -> None:
        self.database = database
        self.settings = settings
        self.media = media or _build_media_service(database, settings)
        self.cancel_check: Callable[[], bool] | None = None
        self.progress_callback: Callable[[dict[str, Any]], None] | None = None

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

    def timeline_selections(self, project_id: str, episode_id: str, limit: int = 500) -> dict[str, Any]:
        """Return bounded timeline inputs without loading the Shot Studio aggregate."""
        bounded_limit = max(1, min(int(limit), 500))
        with self.database.connect() as connection:
            context = connection.execute(
                """SELECT 1 FROM projects p JOIN seasons se ON se.project_id=p.id
                JOIN episodes e ON e.season_id=se.id WHERE p.id=? AND e.id=?""",
                (project_id, episode_id),
            ).fetchone()
            if context is None:
                raise DomainRuleError(
                    "EPISODE_NOT_FOUND", "分集不存在或不属于当前项目",
                    {"project_id": project_id, "episode_id": episode_id},
                )
            total = int(connection.execute(
                "SELECT COUNT(*) FROM shots WHERE episode_id=? AND archived_at IS NULL", (episode_id,),
            ).fetchone()[0])
            rows = connection.execute(
                """WITH continuity AS (
                  SELECT shot_id,
                  CASE WHEN MAX(is_stale)=1 THEN 'STALE'
                       WHEN MAX(is_conflict)=1 THEN 'CONFLICT'
                       WHEN MAX(is_attention)=1 THEN 'ATTENTION' ELSE 'OK' END continuity_status
                  FROM (
                    SELECT to_shot_id shot_id,is_stale,
                    CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END is_conflict,
                    CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END is_attention
                    FROM shot_transition_constraints
                    UNION ALL
                    SELECT from_shot_id,is_stale,
                    CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END,
                    CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END
                    FROM shot_transition_constraints
                  ) GROUP BY shot_id
                )
                SELECT s.id,s.code,s.order_key,s.status,s.target_duration_ms,
                (SELECT se.media_version_id
                 FROM selections se
                 JOIN media_versions mv ON mv.id=se.media_version_id
                 JOIN media_assets ma ON ma.id=mv.media_asset_id
                 LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                 LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                 WHERE ma.media_kind='VIDEO' AND mv.mime_type LIKE 'video/%'
                   AND se.selection_type IN ('FORMAL_SELECTION','PROXY_WINNER')
                   AND ((ma.owner_type='SHOT' AND ma.owner_id=s.id)
                     OR (gi.owner_type='SHOT' AND gi.owner_id=s.id))
                 ORDER BY CASE se.selection_type WHEN 'FORMAL_SELECTION' THEN 2 ELSE 1 END DESC,
                          se.created_at DESC,se.id DESC LIMIT 1) current_video_media_version_id,
                COALESCE(c.continuity_status,'MISSING') continuity_status
                FROM shots s LEFT JOIN continuity c ON c.shot_id=s.id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id LIMIT ?""",
                (episode_id, bounded_limit),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows], "total": total,
            "has_more": total > len(rows), "limit": bounded_limit,
            "read_only": True, "request_shape": "bounded_timeline_selection_read_model",
        }

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

    def plan_tts_subtitle_draft(
        self,
        episode_id: str,
        *,
        source_document_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a read-only subtitle draft from current TTS selections.

        Dialogue revisions remain the text authority.  Selected TTS media only
        contributes timing evidence; this method never creates a subtitle
        revision and never treats synthesized/ASR text as authoritative.
        """
        episode = self._episode(episode_id)
        with self.database.connect() as connection:
            shots = connection.execute(
                "SELECT id,code,order_key,target_duration_ms FROM shots WHERE episode_id=? ORDER BY CAST(order_key AS REAL),code,id",
                (episode_id,),
            ).fetchall()
            lines = connection.execute(
                """SELECT dl.id,dl.code,dl.speaker,dl.shot_id,
                    dtr.id AS text_revision_id,dtr.revision_no AS text_revision_no,dtr.text,dtr.text_hash,
                    dcs.id AS selection_id,dcs.source_text_revision_id,
                    tc.id AS tts_candidate_id,tc.media_version_id,tc.candidate_kind,
                    mv.duration_ms,mv.sha256 AS media_sha256,mv.integrity_status
                FROM dialogue_lines dl
                JOIN dialogue_text_revisions dtr ON dtr.id=(
                    SELECT latest.id FROM dialogue_text_revisions latest
                    WHERE latest.dialogue_line_id=dl.id ORDER BY latest.revision_no DESC LIMIT 1
                )
                LEFT JOIN dialogue_candidate_selections dcs ON dcs.id=(
                    SELECT latest_selection.id FROM dialogue_candidate_selections latest_selection
                    WHERE latest_selection.dialogue_line_id=dl.id
                    ORDER BY latest_selection.created_at DESC,latest_selection.id DESC LIMIT 1
                )
                LEFT JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
                LEFT JOIN media_versions mv ON mv.id=tc.media_version_id
                LEFT JOIN shots shot ON shot.id=dl.shot_id
                WHERE dl.episode_id=?
                ORDER BY CASE WHEN shot.id IS NULL THEN 1 ELSE 0 END,
                    CAST(shot.order_key AS REAL),shot.code,dl.code,dl.id""",
                (episode_id,),
            ).fetchall()
            applied_sources = connection.execute(
                """SELECT DISTINCT draft.source_document_version_id
                FROM script_breakdown_scene_applications application
                JOIN script_breakdown_drafts draft ON draft.id=application.breakdown_draft_id
                WHERE application.episode_id=?
                ORDER BY draft.source_document_version_id""",
                (episode_id,),
            ).fetchall()

        source_candidates = [str(row["source_document_version_id"]) for row in applied_sources]
        requested_source = str(source_document_version_id or "").strip()
        resolved_source = requested_source or (source_candidates[0] if len(source_candidates) == 1 else "")
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if not resolved_source:
            blockers.append(
                {
                    "code": "SUBTITLE_SOURCE_REQUIRED" if not source_candidates else "SUBTITLE_SOURCE_AMBIGUOUS",
                    "message": "请选择本集采用的已解析剧本文档版本。" if not source_candidates else "本集应用了多个剧本文档版本，请明确选择字幕权威。",
                }
            )

        source_text = ""
        authority_snapshot: dict[str, Any] | None = None
        if resolved_source:
            try:
                source_text, authority_snapshot = self._subtitle_authority(
                    episode,
                    {"text_authority": "SCRIPT", "source_document_version_id": resolved_source},
                )
            except DomainRuleError as error:
                blockers.append({"code": error.code, "message": error.message})

        shot_start_us: dict[str, int] = {}
        shot_duration_us: dict[str, int] = {}
        elapsed_us = 0
        for shot in shots:
            shot_id = str(shot["id"])
            duration_us = max(100_000, int(shot["target_duration_ms"]) * 1000)
            shot_start_us[shot_id] = elapsed_us
            shot_duration_us[shot_id] = duration_us
            elapsed_us += duration_us

        cues: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        per_shot_cursor: dict[str, int] = {}
        previous_end = 0
        normalized_source, _ = _normalized_text_with_offsets(source_text) if source_text else ("", [])
        source_cursor = 0
        for row in lines:
            line_id = str(row["id"])
            if not row["selection_id"]:
                missing.append({"line_id": line_id, "code": str(row["code"]), "reason": "TTS_SELECTION_MISSING", "message": "尚未采用 TTS 候选"})
                continue
            if str(row["source_text_revision_id"] or "") != str(row["text_revision_id"]):
                missing.append({"line_id": line_id, "code": str(row["code"]), "reason": "TTS_SELECTION_STALE", "message": "采用候选对应旧文本 revision"})
                continue
            if not row["media_version_id"] or str(row["integrity_status"] or "") != "VERIFIED":
                missing.append({"line_id": line_id, "code": str(row["code"]), "reason": "TTS_MEDIA_UNVERIFIED", "message": "采用候选缺少已验证音频"})
                continue

            text = str(row["text"]).strip()
            normalized_text, _ = _normalized_text_with_offsets(text)
            if normalized_source:
                match_start = normalized_source.find(normalized_text, source_cursor)
                if match_start < 0:
                    blockers.append(
                        {
                            "code": "SUBTITLE_SCRIPT_AUTHORITY_MISMATCH",
                            "message": f"{row['code']} 的当前对白文本无法按顺序在所选剧本中定位。",
                            "line_id": line_id,
                        }
                    )
                else:
                    source_cursor = match_start + len(normalized_text)

            shot_id = str(row["shot_id"] or "")
            local_cursor = per_shot_cursor.get(shot_id, 0)
            intended_start = shot_start_us.get(shot_id, previous_end) + local_cursor
            start_us = max(previous_end, intended_start)
            media_duration_us = max(0, int(row["duration_ms"] or 0) * 1000)
            cps_duration_us = max(500_000, (len(text) * 1_000_000 + 24) // 25)
            duration_us = max(media_duration_us, cps_duration_us)
            end_us = start_us + duration_us
            per_shot_cursor[shot_id] = max(0, end_us - shot_start_us.get(shot_id, start_us))
            previous_end = end_us
            cues.append({"start_us": start_us, "end_us": end_us, "text": text})
            evidence.append(
                {
                    "line_id": line_id,
                    "line_code": str(row["code"]),
                    "speaker": str(row["speaker"]),
                    "shot_id": shot_id or None,
                    "text_revision_id": str(row["text_revision_id"]),
                    "text_revision_no": int(row["text_revision_no"]),
                    "text_hash": str(row["text_hash"]),
                    "selection_id": str(row["selection_id"]),
                    "tts_candidate_id": str(row["tts_candidate_id"]),
                    "media_version_id": str(row["media_version_id"]),
                    "media_sha256": str(row["media_sha256"]),
                    "media_duration_ms": int(row["duration_ms"] or 0) or None,
                    "timing_basis": "SELECTED_TTS_MEDIA_EXTENDED_FOR_CPS" if duration_us > media_duration_us else "SELECTED_TTS_MEDIA",
                }
            )

        if not lines:
            blockers.append({"code": "DIALOGUE_LINES_REQUIRED", "message": "本集还没有对白文本。"})
        elif not cues:
            blockers.append({"code": "CURRENT_TTS_SELECTIONS_REQUIRED", "message": "至少需要一条对应当前对白文本、媒体已验证的 TTS 采用结果。"})
        if missing:
            warnings.append({"code": "TTS_SELECTIONS_INCOMPLETE", "message": f"{len(missing)} 条对白未进入草稿；可先补齐采用结果，或审阅现有条目。"})

        ready = bool(cues and authority_snapshot and not blockers)
        status = "BLOCKED" if not ready else "PARTIAL" if missing else "READY"
        return {
            "episode_id": episode_id,
            "status": status,
            "ready_to_load": ready,
            "source_document_version_id": resolved_source or None,
            "source_document_candidates": source_candidates,
            "authority": authority_snapshot,
            "cues": cues,
            "evidence": evidence,
            "missing": missing,
            "blockers": blockers,
            "warnings": warnings,
            "summary": {"dialogue_count": len(lines), "cue_count": len(cues), "missing_count": len(missing), "duration_us": previous_end},
            "text_authority": "SCRIPT",
            "timing_authority": "SELECTED_TTS_MEDIA",
            "requires_human_review": True,
            "would_create_revision": False,
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def plan_stale_timeline_refresh(self, episode_id: str) -> dict[str, Any]:
        """Recheck current upstream facts before replacing a stale timeline.

        The plan hashes exact current selections, durations, active audio and
        latest subtitle facts.  It reads and hashes local media but performs no
        database writes and never freezes a revision by itself.
        """
        episode = self._episode(episode_id)
        with self.database.connect() as connection:
            latest = connection.execute(
                "SELECT id,revision_no,revision_hash,status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            subtitle = connection.execute(
                "SELECT id,revision_no,content_hash,status FROM subtitle_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        selections = self.timeline_selections(
            str(episode["project_id"]), episode_id, limit=500,
        )
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if latest is None:
            blockers.append({"code": "TIMELINE_REVISION_REQUIRED", "message": "本集还没有可恢复的时间线 revision。"})
        elif str(latest["status"]).upper() != "STALE":
            blockers.append({"code": "TIMELINE_NOT_STALE", "message": "最新时间线并未失效；请在时间线工作台完成普通编辑或冻结。"})
        if selections["has_more"]:
            blockers.append({"code": "TIMELINE_SHOT_LIMIT_EXCEEDED", "message": "本集镜头超过 500 个安全上限，请先拆分分集。"})
        if not selections["items"]:
            blockers.append({"code": "TIMELINE_SHOTS_REQUIRED", "message": "本集没有可编排镜头。"})

        video_items: list[dict[str, Any]] = []
        cursor_us = 0
        selected_videos: list[dict[str, Any]] = []
        for _index, item in enumerate(selections["items"]):
            media_version_id = str(item.get("current_video_media_version_id") or "")
            shot_id = str(item["id"])
            if not media_version_id:
                blockers.append({"code": "TIMELINE_VIDEO_SELECTION_MISSING", "message": f"{item['code']} 尚未采用视频。", "shot_id": shot_id})
                continue
            continuity = str(item.get("continuity_status") or "MISSING")
            if continuity == "CONFLICT":
                blockers.append({"code": "TIMELINE_CONTINUITY_CONFLICT", "message": f"{item['code']} 存在连续性冲突。", "shot_id": shot_id})
            elif continuity in {"STALE", "ATTENTION"}:
                warnings.append({"code": "TIMELINE_CONTINUITY_REVIEW", "message": f"{item['code']} 的连续性状态为 {continuity}，冻结后仍建议人工复核。", "shot_id": shot_id})
            try:
                media, path = self.media.content_path(media_version_id)
                actual_sha, actual_size = _hash_file(path)
                if (
                    str(media["project_id"]) != str(episode["project_id"])
                    or str(media["media_kind"]) != "VIDEO"
                    or not hmac.compare_digest(actual_sha, str(media["sha256"]))
                    or actual_size != int(media["byte_size"])
                ):
                    raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "采用视频的项目、类型或完整性不符合冻结要求")
            except DomainRuleError as error:
                blockers.append({"code": error.code, "message": f"{item['code']}：{error.message}", "shot_id": shot_id})
                continue
            duration_us = max(100_000, int(item["target_duration_ms"]) * 1000)
            video_items.append(
                {
                    "track_type": "VIDEO",
                    "media_version_id": media_version_id,
                    "start_us": cursor_us,
                    "end_us": cursor_us + duration_us,
                    "parameters": {"shot_id": shot_id, "shot_code": str(item["code"]), "transition_in": "CUT"},
                }
            )
            selected_videos.append({"shot_id": shot_id, "media_version_id": media_version_id, "sha256": str(media["sha256"])})
            cursor_us += duration_us

        audio_bindings = self._audio_bindings_for_render(episode_id)
        audio_items: list[dict[str, Any]] = []
        for binding in audio_bindings:
            try:
                _, path = self.media.content_path(str(binding["media_version_id"]))
                actual_sha, actual_size = _hash_file(path)
                if not hmac.compare_digest(actual_sha, str(binding["media_sha256"])) or actual_size != int(binding["media_byte_size"]):
                    raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "活动音轨完整性校验失败")
            except DomainRuleError as error:
                blockers.append({"code": error.code, "message": f"音轨 {str(binding['id'])[:12]}：{error.message}", "audio_binding_id": str(binding["id"])})
                continue
            audio_items.append(
                {
                    "track_type": str(binding["track_type"]),
                    "media_version_id": str(binding["media_version_id"]),
                    "start_us": int(binding["start_us"]),
                    "end_us": int(binding["end_us"]),
                    "parameters": {
                        "audio_binding_id": str(binding["id"]),
                        "gain_db": float(binding["gain_db"]),
                        "loop_enabled": bool(binding["loop_enabled"]),
                        "fade_in_us": int(binding["fade_in_us"]),
                        "fade_out_us": int(binding["fade_out_us"]),
                    },
                }
            )

        source_timeline = dict(latest) if latest is not None else None
        subtitle_snapshot = (
            {"id": str(subtitle["id"]), "revision_no": int(subtitle["revision_no"]), "content_hash": str(subtitle["content_hash"]), "status": str(subtitle["status"])}
            if subtitle is not None else None
        )
        snapshot = {
            "schema_version": "localdrama.timeline-stale-refresh-plan.v1",
            "episode_id": episode_id,
            "source_timeline": source_timeline,
            "selected_videos": selected_videos,
            "audio_bindings": self._binding_snapshot(audio_bindings),
            "subtitle_revision": subtitle_snapshot,
            "items": [*video_items, *audio_items],
        }
        plan_hash = _hash(snapshot)
        return {
            "episode_id": episode_id,
            "status": "READY" if not blockers else "BLOCKED",
            "plan_hash": plan_hash,
            "source_timeline": source_timeline,
            "summary": {"shot_count": len(selections["items"]), "video_count": len(video_items), "audio_count": len(audio_items), "subtitle_count": 1 if subtitle else 0, "duration_us": cursor_us},
            "items": [*video_items, *audio_items],
            "selected_videos": selected_videos,
            "audio_binding_ids": [str(binding["id"]) for binding in audio_bindings],
            "subtitle_revision_id": str(subtitle["id"]) if subtitle else None,
            "blockers": blockers,
            "warnings": warnings,
            "would_create_status": "FROZEN",
            "requires_confirmation": True,
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def commit_stale_timeline_refresh(self, episode_id: str, expected_plan_hash: str, actor: str = "local-user") -> dict[str, Any]:
        plan = self.plan_stale_timeline_refresh(episode_id)
        if not hmac.compare_digest(str(plan["plan_hash"]), str(expected_plan_hash)):
            raise DomainRuleError("TIMELINE_REFRESH_PLAN_STALE", "当前采用、音轨或字幕已变化，请重新预检后确认")
        if plan["status"] != "READY":
            raise DomainRuleError("TIMELINE_REFRESH_BLOCKED", "当前事实仍不足以创建新的冻结时间线", {"blockers": plan["blockers"]})
        source = plan["source_timeline"] or {}
        revision = self.create_timeline_revision(
            episode_id,
            plan["items"],
            {
                "schema_version": "localdrama.timeline-editor.v2",
                "source": "DELIVERY_STALE_REFRESH",
                "refresh_plan_hash": plan["plan_hash"],
                "refreshed_from_timeline_revision_id": source.get("id"),
                "refreshed_from_timeline_revision_hash": source.get("revision_hash"),
                "selected_videos": plan["selected_videos"],
                "audio_binding_ids": plan["audio_binding_ids"],
                "subtitle_revision_id": plan["subtitle_revision_id"],
                "requires_human_confirmation": True,
            },
            status="FROZEN",
            actor=actor,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'producer','TIMELINE_STALE_REFRESH_COMMITTED','timeline_revision',?,'基于当前采用事实复检并创建新的冻结时间线',?)",
                (actor, str(revision["id"]), _json({"episode_id": episode_id, "source_timeline_revision_id": source.get("id"), "plan_hash": plan["plan_hash"]})),
            )
        return {"timeline": revision, "plan_hash": plan["plan_hash"], "source_timeline_revision_id": source.get("id"), "mutated": True}

    def create_subtitle_revision(
        self,
        episode_id: str,
        cues: list[dict[str, Any]],
        *,
        format: str = "SRT",
        authority: dict[str, Any],
        style: dict[str, Any] | None = None,
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
        revision_style = validate_style(style) if style is not None else dict(DEFAULT_SUBTITLE_STYLE)
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
            cue_style = {**revision_style, **raw.get("style", {})}
            normalized.append({"cue_no": index, "start_us": start_us, "end_us": end_us, "text": text, "style": cue_style})
            previous_end = end_us
        authority_snapshot["source_passages"] = source_passages
        authority_snapshot["style"] = revision_style
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
            connection.execute(
                """UPDATE timeline_revisions SET status='STALE',updated_at=?
                WHERE id=(SELECT id FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1)
                  AND status!='STALE'""",
                (now, episode_id),
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
            # P1-12: emit a [V4+ Styles] block derived from the effective cue
            # style (see subtitle_styles.validate_style).  Dialogue events keep
            # the legacy "Layer, Start, End, Text" layout for backward
            # compatibility with existing render output; the authoritative
            # style is also persisted per-cue in subtitle_cues.style_json.
            style = cues[0].get("style") or {}
            position_to_alignment = {"TOP": 8, "CENTER": 5, "BOTTOM": 2}
            hex_color = str(style.get("color", "#FFFFFF")).lstrip("#").upper()
            # ASS stores colours as &HAABBGGRR&; swap the RR/BB pair order.
            ass_rgb = f"{hex_color[4:6]}{hex_color[2:4]}{hex_color[0:2]}"
            primary = f"&H00{ass_rgb}&"
            fontname = str(style.get("font", "Microsoft YaHei"))
            fontsize = int(style.get("size", 48))
            outline = int(style.get("outline", 2))
            alignment = position_to_alignment.get(str(style.get("position", "BOTTOM")), 2)
            lines = [
                "[Script Info]",
                "ScriptType: v4.00+",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                f"Style: Default,{fontname},{fontsize},{primary},&H000000FF&,&H00000000&,&H80000000&,0,0,0,0,100,100,0,0,1,{outline},0,{alignment},10,10,10,1",
                "",
                "[Events]",
                "Format: Layer, Start, End, Text",
            ]
            for cue in cues:
                def ass_time(value: int) -> str:
                    centiseconds = value // 10_000
                    hours, rest = divmod(centiseconds, 360_000)
                    minutes, rest = divmod(rest, 6000)
                    seconds, cs = divmod(rest, 100)
                    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
                dialogue_text = str(cue.get("text") or "")
                dialogue_text = dialogue_text.replace(chr(10), "\\N")
                lines.append(
                    "Dialogue: 0,"
                    + ass_time(cue["start_us"])
                    + ","
                    + ass_time(cue["end_us"])
                    + ","
                    + dialogue_text
                )
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

    # Legacy audio binding create/get/update/remove operations are implemented in
    # audio_v2. This service now reads bindings only from timeline/audio facts
    # to avoid duplicate mutable write paths.

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
            imported = self.media.import_file(source["project_id"], str(frame_path), purpose="FRAME_ANCHOR", owner_type="MEDIA_VERSION", owner_id=source_media_version_id, media_kind="IMAGE", stage="FRAME_ANCHOR", actor=actor)
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
        valid_types = {
            "MATCH_CUT",
            "CONTINUOUS_MOTION",
            "START_FROM_PREVIOUS_LAST",
            "END_AT_NEXT_FIRST",
            "SHARED_BOUNDARY_FRAME",
            "STYLE_ONLY",
            "DIRECTIONAL_CONTINUITY",
            # Legacy clients used this concise alias before the blueprint
            # settled on START_FROM_PREVIOUS_LAST.
            "LAST_TO_FIRST",
        }
        if constraint_type not in valid_types:
            raise DomainRuleError("TRANSITION_TYPE_INVALID", "连续性约束类型不受支持", {"constraint_type": constraint_type, "supported": sorted(valid_types)})
        if enforcement not in {"ADVISORY", "REQUIRED", "HARD", "SOFT"}:
            raise DomainRuleError("TRANSITION_ENFORCEMENT_INVALID", "连续性约束 enforcement 必须是 ADVISORY、REQUIRED 或兼容别名", {"enforcement": enforcement})
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
            # Boundary policies are explicit.  Missing anchors are allowed
            # while drafting a transition, but validation must stop a shared
            # boundary from being treated as a pixel-level guarantee without
            # two immutable FrameAnchor references.
            if constraint["constraint_type"] == "SHARED_BOUNDARY_FRAME" and (not constraint["from_anchor_id"] or not constraint["to_anchor_id"]):
                blockers.append({"code": "SHARED_BOUNDARY_ANCHORS_REQUIRED"})
            if constraint["constraint_type"] in {"START_FROM_PREVIOUS_LAST", "LAST_TO_FIRST"} and not constraint["from_anchor_id"]:
                warnings.append({"code": "PREVIOUS_LAST_ANCHOR_NOT_BOUND"})
            if constraint["constraint_type"] == "END_AT_NEXT_FIRST" and not constraint["to_anchor_id"]:
                warnings.append({"code": "NEXT_FIRST_ANCHOR_NOT_BOUND"})
            anchor_hashes: dict[str, str] = {}
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
                anchor_hashes[side] = str(anchor["extracted_sha256"])
                if str(anchor["sha256"]) != str(anchor["extracted_sha256"]):
                    blockers.append({"code": "FRAME_ANCHOR_HASH_MISMATCH", "side": side})
                if anchor["owner_type"] == "SHOT" and str(anchor["owner_id"]) != str(shot_id):
                    blockers.append({"code": "FRAME_ANCHOR_SHOT_MISMATCH", "side": side})
                elif anchor["owner_type"] != "SHOT":
                    warnings.append({"code": "FRAME_ANCHOR_PROJECT_BRIDGE", "side": side})
            if constraint["constraint_type"] == "SHARED_BOUNDARY_FRAME" and len(anchor_hashes) == 2 and anchor_hashes["from"] != anchor_hashes["to"]:
                blockers.append({"code": "SHARED_BOUNDARY_HASH_MISMATCH", "from_sha256": anchor_hashes["from"], "to_sha256": anchor_hashes["to"]})
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
                step["mode"] = str(step.get("mode", "EXPLICIT"))
                if step["mode"] == "KEEP_SOURCE":
                    step["fit"] = "CONTAIN"
                    normalized.append(step)
                    continue
                if step["mode"] != "EXPLICIT":
                    raise DomainRuleError("POST_PROCESS_SCALE_INVALID", "SCALE mode 仅支持 EXPLICIT/KEEP_SOURCE")
                width, height = step.get("width"), step.get("height")
                if not isinstance(width, int) or not isinstance(height, int) or not 64 <= width <= 8192 or not 64 <= height <= 8192 or width % 2 or height % 2:
                    raise DomainRuleError("POST_PROCESS_SCALE_INVALID", "SCALE 必须显式给出 64—8192 的偶数 width/height")
                step["fit"] = str(step.get("fit", "CONTAIN"))
                if step["fit"] not in {"CONTAIN", "COVER", "STRETCH"}:
                    raise DomainRuleError("POST_PROCESS_SCALE_INVALID", "SCALE fit 仅支持 CONTAIN/COVER/STRETCH")
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
        keep_source = str(scale.get("mode", "EXPLICIT")) == "KEEP_SOURCE"
        width, height = (0, 0) if keep_source else (int(scale["width"]), int(scale["height"]))
        processing_steps = [step for step in steps if step["kind"] not in {"TECHNICAL_QC", "ENCODE"}]
        filter_specs: list[tuple[dict[str, Any], str]] = []
        for step in processing_steps:
            kind = str(step["kind"])
            if kind == "SCALE":
                if keep_source:
                    continue
                if scale["fit"] == "STRETCH":
                    filter_spec = f"scale={width}:{height}"
                elif scale["fit"] == "COVER":
                    filter_spec = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
                else:
                    filter_spec = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
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
            imported = self.media.import_file(source_item["project_id"], str(output), purpose="ENHANCEMENT", owner_type="MEDIA_VERSION", owner_id=input_media_version_id, media_kind=source_item["media_kind"], stage="ENHANCED", actor=actor)
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

    def render_episode(self, timeline_revision_id: str, *, force_rerender: bool = False, actor: str = "local-user") -> dict[str, Any]:
        """Render the whole episode from its VIDEO timeline items.

        Without audio bindings this keeps the historical single-command concat
        (identical command and artifact).  When the episode has DIALOGUE/BGM/SFX
        audio bindings the video is concatenated first, the bound tracks are
        mixed (volume/loop/fades/adelay), then the final mp4 is muxed.
        """
        timeline = self.get_timeline(timeline_revision_id)
        self._assert_timeline_renderable(timeline)
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
        bindings = self._audio_bindings_for_timeline(timeline, str(episode["id"]))
        input_snapshot = {
            "schema_version": "localdrama.episode-render-input.v1",
            "renderer_contract": "TIMELINE_DURATION_AND_SUBTITLE_V2",
            "timeline_revision_id": timeline_revision_id,
            "timeline_revision_hash": timeline["revision_hash"],
            "timeline_input_snapshot": timeline["input_snapshot"],
            "items": input_snapshot_items,
        }
        subtitle = self._subtitle_for_render(timeline, str(episode["id"]))
        if subtitle is not None:
            input_snapshot["subtitle_revision"] = {
                "id": str(subtitle["id"]),
                "revision_no": int(subtitle["revision_no"]),
                "format": str(subtitle["format"]),
                "content_hash": str(subtitle["content_hash"]),
                "status": str(subtitle["status"]),
            }
        if bindings:
            input_snapshot["render_mode"] = "MIXED_AUDIO"
            input_snapshot["audio_bindings"] = self._binding_snapshot(bindings)
        project_root = (self.settings.projects_root / episode["root_rel"]).resolve()
        if not force_rerender:
            existing = self._existing_render(timeline_revision_id, input_snapshot, project_root)
            if existing is not None:
                return existing
        render_dir = project_root / "05_timelines" / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        render_path = render_dir / f"episode-{episode['code']}-{uuid.uuid4().hex}.mp4"
        execution = self._concat_and_mix(
            paths,
            bindings,
            render_dir,
            render_path,
            video_items=video_items,
            subtitle=subtitle,
        )
        return self._register_render(episode=episode, timeline_revision_id=timeline_revision_id, timeline=timeline, render_path=render_path, project_root=project_root, input_snapshot=input_snapshot, ffmpeg_execution=execution, actor=actor)

    def preflight_episode_render(self, timeline_revision_id: str) -> dict[str, Any]:
        """Freeze the exact Compose inputs without executing FFmpeg or writing."""
        timeline = self.get_timeline(timeline_revision_id)
        self._assert_timeline_renderable(timeline)
        episode = self._episode(str(timeline["episode_id"]))
        items: list[dict[str, Any]] = []
        for item in timeline["items"]:
            if str(item["track_type"]).upper() != "VIDEO" or not item["media_version_id"]:
                continue
            media = self._media_for_episode(str(timeline["episode_id"]), str(item["media_version_id"]))
            if media["media_kind"] != "VIDEO":
                raise DomainRuleError("TIMELINE_MEDIA_KIND_INVALID", "VIDEO track 只能绑定视频媒体")
            # Resolve and integrity-check every source during preflight, but do
            # not expose workstation paths in the durable job snapshot.
            _, source_path = self.media.content_path(str(item["media_version_id"]))
            actual_sha, actual_size = _hash_file(source_path)
            if not hmac.compare_digest(actual_sha, str(media["sha256"])) or actual_size != int(media["byte_size"]):
                raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "Compose 输入媒体 hash/size 与不可变 MediaVersion 不一致", {"media_version_id": str(media["id"])})
            items.append({"media_version_id": str(media["id"]), "sha256": str(media["sha256"]), "byte_size": int(media["byte_size"]), "start_us": int(item["start_us"]), "end_us": int(item["end_us"]), "track_type": str(item["track_type"]), "parameters": item["parameters"]})
        if not items:
            raise DomainRuleError("TIMELINE_VIDEO_REQUIRED", "整集渲染至少需要一个 VIDEO item")
        bindings = self._audio_bindings_for_timeline(timeline, str(episode["id"]))
        for binding in bindings:
            _, audio_path = self.media.content_path(str(binding["media_version_id"]))
            actual_sha, actual_size = _hash_file(audio_path)
            if not hmac.compare_digest(actual_sha, str(binding["media_sha256"])) or actual_size != int(binding["media_byte_size"]):
                raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "Compose 音频输入 hash/size 与不可变 MediaVersion 不一致", {"media_version_id": str(binding["media_version_id"])})
        snapshot: dict[str, Any] = {
            "schema_version": "localdrama.episode-render-input.v1",
            "renderer_contract": "TIMELINE_DURATION_AND_SUBTITLE_V2",
            "timeline_revision_id": timeline_revision_id,
            "timeline_revision_hash": timeline["revision_hash"],
            "timeline_input_snapshot": timeline["input_snapshot"],
            "items": items,
        }
        subtitle = self._subtitle_for_render(timeline, str(episode["id"]))
        if subtitle is not None:
            snapshot["subtitle_revision"] = {
                "id": str(subtitle["id"]),
                "revision_no": int(subtitle["revision_no"]),
                "format": str(subtitle["format"]),
                "content_hash": str(subtitle["content_hash"]),
                "status": str(subtitle["status"]),
            }
        if bindings:
            snapshot["render_mode"] = "MIXED_AUDIO"
            snapshot["audio_bindings"] = self._binding_snapshot(bindings)
        project_root = (self.settings.projects_root / episode["root_rel"]).resolve()
        existing = self._existing_render(timeline_revision_id, snapshot, project_root)
        return {
            "project_id": str(episode["project_id"]), "episode_id": str(episode["id"]),
            "timeline_revision_id": timeline_revision_id, "compose_fingerprint": _hash(snapshot),
            "input_snapshot": snapshot, "existing_render": existing,
            "would_execute_ffmpeg": existing is None, "read_only": True, "writes_performed": 0,
        }

    def preflight_segmented_episode_render(self, timeline_revision_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
        timeline = self.get_timeline(timeline_revision_id)
        self._assert_timeline_renderable(timeline)
        episode = self._episode(str(timeline["episode_id"]))
        if not segments:
            raise DomainRuleError("SEGMENT_VIDEOS_REQUIRED", "分段渲染至少需要一个分段视频")
        ordered = sorted(segments, key=lambda segment: int(segment.get("segment_no", 0)))
        snapshot_items: list[dict[str, Any]] = []
        for index, segment in enumerate(ordered, start=1):
            media_version_id = str(segment["media_version_id"])
            media = self._media_for_episode(str(episode["id"]), media_version_id)
            if media["media_kind"] != "VIDEO":
                raise DomainRuleError("SEGMENT_MEDIA_KIND_INVALID", "分段渲染的媒体必须是视频", {"segment_no": int(segment.get("segment_no", index))})
            snapshot_items.append({
                "segment_no": int(segment.get("segment_no", index)),
                "media_version_id": str(media["id"]),
                "sha256": str(media["sha256"]),
                "byte_size": int(media["byte_size"]),
                "start_seconds": segment.get("start_seconds"),
                "end_seconds": segment.get("end_seconds"),
                "frames": segment.get("frames"),
                "continuation": segment.get("continuation"),
            })
        bindings = self._audio_bindings_for_timeline(timeline, str(episode["id"]))
        input_snapshot = {
            "schema_version": "localdrama.episode-render-input.v1",
            "render_mode": "SEGMENTED_CONCAT",
            "segments": snapshot_items,
            "timeline_revision_id": timeline_revision_id,
            "timeline_revision_hash": timeline["revision_hash"],
            "timeline_input_snapshot": timeline["input_snapshot"],
            "items": snapshot_items,
        }
        if bindings:
            input_snapshot["audio_bindings"] = self._binding_snapshot(bindings)
        project_root = (self.settings.projects_root / episode["root_rel"]).resolve()
        existing = self._existing_render(timeline_revision_id, input_snapshot, project_root)
        return {
            "project_id": str(episode["project_id"]),
            "episode_id": str(episode["id"]),
            "timeline_revision_id": timeline_revision_id,
            "compose_fingerprint": _hash(input_snapshot),
            "input_snapshot": input_snapshot,
            "existing_render": existing,
            "read_only": True,
            "writes_performed": 0,
        }

    def render_segmented_episode(
        self,
        timeline_revision_id: str,
        segments: list[dict[str, Any]],
        *,
        force_rerender: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Render a long take (P1-9) from pre-generated segment videos.

        Segments are concatenated in ``segment_no`` order with the same concat
        command as ``render_episode`` (same encoding parameters, seamless
        concat demuxer).  The segment plan guarantees segment N's tail frame is
        segment N+1's head frame, so the segments simply play back-to-back.
        Episode audio bindings are mixed in exactly like ``render_episode``.
        """
        timeline = self.get_timeline(timeline_revision_id)
        self._assert_timeline_renderable(timeline)
        episode = self._episode(str(timeline["episode_id"]))
        if not segments:
            raise DomainRuleError("SEGMENT_VIDEOS_REQUIRED", "分段渲染至少需要一个分段视频")
        ordered = sorted(segments, key=lambda segment: int(segment.get("segment_no", 0)))
        paths: list[Path] = []
        input_snapshot_items: list[dict[str, Any]] = []
        for index, segment in enumerate(ordered, start=1):
            media_version_id = str(segment["media_version_id"])
            media = self._media_for_episode(str(episode["id"]), media_version_id)
            if media["media_kind"] != "VIDEO":
                raise DomainRuleError("SEGMENT_MEDIA_KIND_INVALID", "分段渲染的媒体必须是视频", {"segment_no": int(segment.get("segment_no", index))})
            _, path = self.media.content_path(media_version_id)
            paths.append(path)
            input_snapshot_items.append(
                {
                    "segment_no": int(segment.get("segment_no", index)),
                    "media_version_id": str(media["id"]),
                    "sha256": str(media["sha256"]),
                    "byte_size": int(media["byte_size"]),
                    "start_seconds": segment.get("start_seconds"),
                    "end_seconds": segment.get("end_seconds"),
                    "frames": segment.get("frames"),
                    "continuation": segment.get("continuation"),
                }
            )
        bindings = self._audio_bindings_for_timeline(timeline, str(episode["id"]))
        input_snapshot = {
            "schema_version": "localdrama.episode-render-input.v1",
            "render_mode": "SEGMENTED_CONCAT",
            "segments": input_snapshot_items,
            "timeline_revision_id": timeline_revision_id,
            "timeline_revision_hash": timeline["revision_hash"],
            "timeline_input_snapshot": timeline["input_snapshot"],
            "items": input_snapshot_items,
        }
        if bindings:
            input_snapshot["audio_bindings"] = self._binding_snapshot(bindings)
        project_root = (self.settings.projects_root / episode["root_rel"]).resolve()
        if not force_rerender:
            existing = self._existing_render(timeline_revision_id, input_snapshot, project_root)
            if existing is not None:
                return existing
        render_dir = project_root / "05_timelines" / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        render_path = render_dir / f"episode-{episode['code']}-{uuid.uuid4().hex}.mp4"
        execution = self._concat_and_mix(paths, bindings, render_dir, render_path)
        return self._register_render(episode=episode, timeline_revision_id=timeline_revision_id, timeline=timeline, render_path=render_path, project_root=project_root, input_snapshot=input_snapshot, ffmpeg_execution=execution, actor=actor)

    @staticmethod
    def _assert_timeline_renderable(timeline: dict[str, Any]) -> None:
        if str(timeline.get("status") or "").upper() == "STALE":
            raise DomainRuleError(
                "TIMELINE_STALE",
                "时间线 revision 已失效；请从当前 selection 创建新的 timeline revision 后再合成",
                {"timeline_revision_id": str(timeline["id"]), "remediation": "CREATE_NEW_TIMELINE_REVISION"},
            )

    def _existing_render(
        self, timeline_revision_id: str, input_snapshot: dict[str, Any], project_root: Path,
    ) -> dict[str, Any] | None:
        """Return an intact completed render for the exact current inputs.

        The immutable input snapshot is the compose idempotency fingerprint.
        Missing/tampered files are never replayed; the caller creates a new
        version while preserving the damaged historical row for diagnosis.
        """
        expected = _hash(input_snapshot)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM episode_render_versions
                WHERE timeline_revision_id=? AND integrity_status='VERIFIED'
                ORDER BY created_at DESC,id DESC LIMIT 20""", (timeline_revision_id,),
            ).fetchall()
        for row in rows:
            try:
                snapshot = json.loads(str(row["input_snapshot_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            if _hash(snapshot) != expected:
                continue
            path = (project_root / str(row["rel_path"])).resolve()
            if not path.is_relative_to(project_root) or path.is_symlink() or not path.is_file():
                continue
            digest, size = _hash_file(path)
            if not hmac.compare_digest(digest, str(row["sha256"])):
                continue
            return {
                "id": str(row["id"]), "episode_id": str(row["episode_id"]),
                "timeline_revision_id": str(row["timeline_revision_id"]), "rel_path": str(row["rel_path"]),
                "sha256": digest, "byte_size": size, "probe": json.loads(str(row["probe_json"] or "{}")),
                "input_snapshot": snapshot, "ffmpeg_command": json.loads(str(row["ffmpeg_command_json"] or "{}")),
                "execution_log": str(row["execution_log_text"] or ""), "revision": int(row["revision"]),
                "status": "VERIFIED", "compose_fingerprint": expected, "idempotent_replay": True,
            }
        return None

    def _audio_bindings_for_render(self, episode_id: str) -> list[dict[str, Any]]:
        """Active audio bindings of an episode with media fingerprints for the render snapshot."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT ab.id, ab.media_version_id, ab.track_type, ab.start_us, ab.end_us,
                ab.gain_db, ab.loop_enabled, ab.fade_in_us, ab.fade_out_us, ab.status,
                mv.sha256 AS media_sha256, mv.byte_size AS media_byte_size
                FROM audio_bindings ab JOIN media_versions mv ON mv.id=ab.media_version_id
                WHERE ab.episode_id=? AND ab.status='ACTIVE' ORDER BY ab.start_us, ab.id""",
                (episode_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _audio_bindings_for_timeline(self, timeline: dict[str, Any], episode_id: str) -> list[dict[str, Any]]:
        """Resolve audio from the immutable revision for v3 timelines.

        Historical revisions predate embedded dialogue/mix items, so they keep
        the legacy lookup until migrated. A v3 frozen revision never reads the
        mutable current mix.
        """
        snapshot = timeline.get("input_snapshot") or {}
        if str(snapshot.get("schema_version") or "") != "localdrama.timeline-editor.v3":
            return self._audio_bindings_for_render(episode_id)
        bindings: list[dict[str, Any]] = []
        for item in timeline.get("items") or []:
            track_type = str(item.get("track_type") or "").upper()
            if track_type == "VIDEO" or not item.get("media_version_id"):
                continue
            media = self._media_for_episode(episode_id, str(item["media_version_id"]))
            if str(media["media_kind"]).upper() != "AUDIO":
                raise DomainRuleError(
                    "TIMELINE_MEDIA_KIND_INVALID",
                    "时间线音频轨只能引用音频媒体",
                    {"media_version_id": str(item["media_version_id"])},
                )
            parameters = item.get("parameters") or {}
            bindings.append(
                {
                    "id": str(parameters.get("audio_binding_id") or parameters.get("dialogue_line_id") or item["id"]),
                    "media_version_id": str(item["media_version_id"]),
                    "track_type": track_type,
                    "start_us": int(item["start_us"]),
                    "end_us": int(item["end_us"]),
                    "gain_db": float(parameters.get("gain_db") or 0.0),
                    "loop_enabled": bool(parameters.get("loop_enabled", False)),
                    "fade_in_us": int(parameters.get("fade_in_us") or 0),
                    "fade_out_us": int(parameters.get("fade_out_us") or 0),
                    "media_sha256": str(media["sha256"]),
                    "media_byte_size": int(media["byte_size"]),
                }
            )
        return bindings

    @staticmethod
    def _binding_snapshot(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": str(binding["id"]),
                "media_version_id": str(binding["media_version_id"]),
                "track_type": str(binding["track_type"]),
                "start_us": int(binding["start_us"]),
                "end_us": int(binding["end_us"]),
                "gain_db": float(binding["gain_db"]),
                "loop_enabled": bool(binding["loop_enabled"]),
                "fade_in_us": int(binding["fade_in_us"]),
                "fade_out_us": int(binding["fade_out_us"]),
                "media_sha256": str(binding["media_sha256"]),
                "media_byte_size": int(binding["media_byte_size"]),
            }
            for binding in bindings
        ]

    def _subtitle_for_render(self, timeline: dict[str, Any], episode_id: str) -> dict[str, Any] | None:
        subtitle_revision_id = str(timeline.get("input_snapshot", {}).get("subtitle_revision_id") or "").strip()
        if not subtitle_revision_id:
            return None
        subtitle = self.get_subtitles(subtitle_revision_id)
        if str(subtitle["episode_id"]) != episode_id:
            raise DomainRuleError(
                "TIMELINE_SUBTITLE_EPISODE_MISMATCH",
                "时间线引用的字幕 revision 不属于当前集",
                {"subtitle_revision_id": subtitle_revision_id},
            )
        return subtitle

    def _concat_videos(self, paths: list[Path], output_path: Path) -> dict[str, Any]:
        concat_list = output_path.parent / f".partial-{uuid.uuid4().hex}.concat.txt"
        escaped_paths = [path.as_posix().replace("'", "'\\''") for path in paths]
        concat_list.write_text("\n".join(f"file '{path}'" for path in escaped_paths) + "\n", encoding="utf-8")
        try:
            return self._run_ffmpeg(
                ["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", "-y", str(output_path)],
                timeout=900,
            )
        finally:
            concat_list.unlink(missing_ok=True)

    @staticmethod
    def _timeline_transition_kind(item: dict[str, Any] | None) -> str:
        return str((item or {}).get("parameters", {}).get("transition_in") or "CUT").upper()

    @staticmethod
    def _xfade_name(kind: str) -> str:
        if kind == "DISSOLVE":
            return "dissolve"
        if kind == "FADE":
            return "fade"
        return "fade"

    @classmethod
    def _timeline_transition_seconds(cls, previous_duration_seconds: float, current_duration_seconds: float) -> float:
        if previous_duration_seconds <= 0.01 or current_duration_seconds <= 0.01:
            return 0.0
        requested = 0.5
        return round(min(requested, previous_duration_seconds / 2, current_duration_seconds / 2), 3)

    def _concat_timeline_videos(
        self,
        paths: list[Path],
        video_items: list[dict[str, Any]],
        render_dir: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        """Honor every timeline item's explicit duration before concatenation.

        Source generations often have provider-defined durations that differ
        from the editor's immutable ``end_us - start_us``.  Each source is
        therefore trimmed or extended by holding its last frame.  A silent
        audio stream is added when the source has none so concat always sees a
        stable stream layout.
        """
        normalized: list[Path] = []
        item_durations_seconds: list[float] = []
        steps: list[dict[str, Any]] = []
        try:
            first_probe = self._probe(paths[0])
            first_video = next(
                (stream for stream in first_probe.get("streams", []) if stream.get("codec_type") == "video"),
                None,
            )
            if first_video is None:
                raise DomainRuleError("TIMELINE_VIDEO_STREAM_REQUIRED", "时间线源文件缺少视频流")
            width = int(first_video.get("width") or 0)
            height = int(first_video.get("height") or 0)
            if width <= 0 or height <= 0:
                raise DomainRuleError("TIMELINE_VIDEO_DIMENSIONS_INVALID", "时间线源视频尺寸无效")

            for index, (path, item) in enumerate(zip(paths, video_items, strict=True)):
                duration_seconds = (int(item["end_us"]) - int(item["start_us"])) / 1_000_000
                if duration_seconds <= 0:
                    raise DomainRuleError("TIMELINE_ITEM_DURATION_INVALID", "时间线视频项时长必须大于 0")
                clip_path = render_dir / f".partial-{uuid.uuid4().hex}.timeline-clip.mp4"
                probe = self._probe(path)
                has_audio = any(stream.get("codec_type") == "audio" for stream in probe.get("streams", []))
                video_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                    f"tpad=stop_mode=clone:stop_duration={duration_seconds:.6f},"
                    f"trim=duration={duration_seconds:.6f},setpts=PTS-STARTPTS"
                )
                source_start_seconds = int((item.get("parameters") or {}).get("source_start_us") or 0) / 1_000_000
                args = (["-ss", f"{source_start_seconds:.6f}"] if source_start_seconds > 0 else []) + ["-i", str(path)]
                if has_audio:
                    args += [
                        "-map", "0:v:0", "-map", "0:a:0", "-vf", video_filter,
                        "-af", f"apad,atrim=duration={duration_seconds:.6f},asetpts=PTS-STARTPTS",
                    ]
                else:
                    args += [
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter,
                    ]
                args += [
                    "-t", f"{duration_seconds:.6f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    "-y", str(clip_path),
                ]
                execution = self._run_ffmpeg(args, timeout=900)
                normalized.append(clip_path)
                item_durations_seconds.append(duration_seconds)
                steps.append(
                    {
                        "stage": "timeline-duration",
                        "item_index": index,
                        "duration_us": int(item["end_us"]) - int(item["start_us"]),
                        "stdout_tail": execution["stdout_tail"],
                        "stderr_tail": execution["stderr_tail"],
                    }
                )

            if len(normalized) == 1:
                concat_execution = self._concat_videos(normalized, output_path)
                return {
                    **concat_execution,
                    "steps": [
                        *steps,
                        {
                            "stage": "concat",
                            "stdout_tail": concat_execution["stdout_tail"],
                            "stderr_tail": concat_execution["stderr_tail"],
                        },
                    ],
                }

            transition_present = False
            for item in video_items[1:]:
                if self._timeline_transition_kind(item) != "CUT":
                    transition_present = True
                    break

            if not transition_present:
                concat_execution = self._concat_videos(normalized, output_path)
                return {
                    **concat_execution,
                    "steps": [
                        *steps,
                        {
                            "stage": "concat",
                            "stdout_tail": concat_execution["stdout_tail"],
                            "stderr_tail": concat_execution["stderr_tail"],
                        },
                    ],
                }

            filter_pieces: list[str] = []
            transition_pieces: list[dict[str, Any]] = []
            for index in range(len(normalized)):
                filter_pieces.append(f"[{index}:v:0]setpts=PTS-STARTPTS[v{index}]")
                filter_pieces.append(f"[{index}:a:0]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{index}]")

            current_video_label = "v0"
            current_audio_label = "a0"
            current_duration = item_durations_seconds[0]
            for index in range(1, len(normalized)):
                item = video_items[index]
                transition_in = self._timeline_transition_kind(item)
                if transition_in == "CUT":
                    next_video = f"v{index}_concat"
                    next_audio = f"a{index}_concat"
                    filter_pieces.append(f"[{current_video_label}][v{index}]concat=n=2:v=1:a=0[{next_video}]")
                    filter_pieces.append(f"[{current_audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
                    current_video_label = next_video
                    current_audio_label = next_audio
                else:
                    transition_seconds = self._timeline_transition_seconds(current_duration, item_durations_seconds[index])
                    if transition_seconds <= 0.0:
                        next_video = f"v{index}_concat"
                        next_audio = f"a{index}_concat"
                        filter_pieces.append(f"[{current_video_label}][v{index}]concat=n=2:v=1:a=0[{next_video}]")
                        filter_pieces.append(f"[{current_audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
                        current_video_label = next_video
                        current_audio_label = next_audio
                    else:
                        transition = self._xfade_name(transition_in)
                        v_ext = f"v{index}_ext"
                        v_out = f"v{index}_x"
                        filter_pieces.append(f"[{current_video_label}]tpad=stop_mode=clone:stop_duration={transition_seconds:.3f}[{v_ext}]")
                        filter_pieces.append(
                            f"[{v_ext}][v{index}]xfade=transition={transition}:duration={transition_seconds:.3f}:offset={current_duration:.3f}[{v_out}]"
                        )
                        next_audio = f"a{index}_concat"
                        filter_pieces.append(f"[{current_audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
                        current_video_label = v_out
                        current_audio_label = next_audio
                        transition_pieces.append(
                            {
                                "stage": "timeline-transition",
                                "from_item_index": index - 1,
                                "to_item_index": index,
                                "kind": transition_in,
                                "duration_seconds": transition_seconds,
                            }
                        )

                current_duration += item_durations_seconds[index]

            args = []
            for clip_path in normalized:
                args += ["-i", str(clip_path)]

            concat_execution = self._run_ffmpeg(
                [
                    *args,
                    "-filter_complex",
                    ";".join(filter_pieces),
                    "-map",
                    f"[{current_video_label}]",
                    "-map",
                    f"[{current_audio_label}]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(output_path),
                ],
                timeout=900,
            )
            return {
                **concat_execution,
                "steps": [
                    *steps,
                    *transition_pieces,
                    {
                        "stage": "concat",
                        "stdout_tail": concat_execution["stdout_tail"],
                        "stderr_tail": concat_execution["stderr_tail"],
                    },
                ],
            }
        finally:
            for clip_path in normalized:
                clip_path.unlink(missing_ok=True)

    @staticmethod
    def _subtitle_filter_path(path: Path) -> str:
        value = path.resolve().as_posix()
        return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]")

    def _burn_subtitle(self, video_path: Path, subtitle: dict[str, Any], output_path: Path) -> dict[str, Any]:
        suffix = "." + str(subtitle["format"]).lower()
        subtitle_path = output_path.parent / f".partial-{uuid.uuid4().hex}{suffix}"
        subtitle_path.write_text(str(subtitle["content_text"]), encoding="utf-8", newline="")
        try:
            filter_spec = f"subtitles=filename='{self._subtitle_filter_path(subtitle_path)}'"
            return self._run_ffmpeg(
                [
                    "-i", str(video_path), "-map", "0:v:0", "-map", "0:a?", "-vf", filter_spec,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy",
                    "-movflags", "+faststart", "-y", str(output_path),
                ],
                timeout=900,
            )
        finally:
            subtitle_path.unlink(missing_ok=True)

    def _concat_and_mix(
        self,
        paths: list[Path],
        bindings: list[dict[str, Any]],
        render_dir: Path,
        render_path: Path,
        *,
        video_items: list[dict[str, Any]] | None = None,
        subtitle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Concat videos and, when bindings exist, mix + mux the audio tracks.

        Without bindings the concat output IS the final render (identical
        command and artifact to the historical single-pass render).  With
        bindings the concat goes to a partial file, the audio is mixed and the
        final mp4 is muxed with the mixed track.
        """
        if not bindings and not subtitle and video_items is None:
            return self._concat_videos(paths, render_path)
        concat_out = render_dir / f".partial-{uuid.uuid4().hex}.mp4"
        concat_execution = (
            self._concat_timeline_videos(paths, video_items, render_dir, concat_out)
            if video_items is not None
            else self._concat_videos(paths, concat_out)
        )
        current_video = concat_out
        subtitle_out: Path | None = None
        subtitle_execution: dict[str, Any] | None = None
        if subtitle is not None:
            subtitle_out = render_dir / f".partial-{uuid.uuid4().hex}.subtitled.mp4"
            subtitle_execution = self._burn_subtitle(current_video, subtitle, subtitle_out)
            current_video = subtitle_out
        mixed_wav = render_dir / f".partial-{uuid.uuid4().hex}.mix.wav"
        try:
            video_duration = self._probe(current_video)["duration_ms"] / 1000
            if video_duration <= 0:
                raise DomainRuleError("RENDER_DURATION_INVALID", "整集渲染时长无效，无法混音")
            if bindings:
                mix_execution = self._mix_audio(current_video, bindings, mixed_wav, video_duration)
                mux_args = ["-i", str(current_video), "-i", str(mixed_wav), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(render_path)]
                mux_execution = self._run_ffmpeg(mux_args, timeout=900)
            else:
                mix_execution = None
                mux_args = subtitle_execution["args"] if subtitle_execution is not None else concat_execution["args"]
                shutil.move(str(current_video), str(render_path))
                mux_execution = subtitle_execution or concat_execution
        finally:
            concat_out.unlink(missing_ok=True)
            if subtitle_out is not None:
                subtitle_out.unlink(missing_ok=True)
            mixed_wav.unlink(missing_ok=True)
        detailed_steps = list(concat_execution.get("steps", []))
        if not detailed_steps:
            detailed_steps.append({"stage": "concat", "stdout_tail": concat_execution["stdout_tail"], "stderr_tail": concat_execution["stderr_tail"]})
        if subtitle_execution is not None:
            detailed_steps.append({"stage": "subtitle", "stdout_tail": subtitle_execution["stdout_tail"], "stderr_tail": subtitle_execution["stderr_tail"]})
        if mix_execution is not None:
            detailed_steps.extend(
                [
                    {"stage": "mix", "stdout_tail": mix_execution["stdout_tail"], "stderr_tail": mix_execution["stderr_tail"]},
                    {"stage": "mux", "stdout_tail": mux_execution["stdout_tail"], "stderr_tail": mux_execution["stderr_tail"]},
                ]
            )
        return {
            "executable": mux_execution["executable"],
            "args": mux_args,
            "returncode": mux_execution["returncode"],
            "stdout_tail": mux_execution["stdout_tail"],
            "stderr_tail": mux_execution["stderr_tail"],
            "steps": detailed_steps,
        }

    def _mix_audio(self, video_path: Path, bindings: list[dict[str, Any]], output_path: Path, video_duration_seconds: float) -> dict[str, Any]:
        """Build one mixed audio stream: the video's own audio plus every binding.

        Each binding is placed at its ``start_us`` with ``adelay``, ``gain_db``
        applied as linear volume, optional loop (``atrim`` to the binding
        range) and optional fade in/out.  Every input is normalized
        (fltp/48 kHz/stereo) before ``amix`` so mixed sample formats can never
        fail; ``apad`` + ``-t`` pin the mix to the exact video duration.
        """
        probe = self._probe(video_path)
        video_has_audio = any(str(stream.get("codec_type")) == "audio" for stream in probe.get("streams", []))
        args: list[str] = []
        chains: list[str] = []
        mix_inputs: list[str] = []
        input_index = 0
        if video_has_audio:
            args += ["-i", str(video_path)]
            chains.append("[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[vid_a]")
            mix_inputs.append("[vid_a]")
            input_index = 1
        for index, binding in enumerate(bindings):
            _, source_path = self.media.content_path(str(binding["media_version_id"]))
            if binding.get("loop_enabled"):
                args += ["-stream_loop", "-1", "-i", str(source_path)]
            else:
                args += ["-i", str(source_path)]
            start_us = int(binding["start_us"])
            end_us = int(binding["end_us"])
            duration_s = (end_us - start_us) / 1_000_000
            start_ms = int(start_us / 1000)
            gain_db = float(binding.get("gain_db") or 0.0)
            fade_in_s = int(binding.get("fade_in_us") or 0) / 1_000_000
            fade_out_s = int(binding.get("fade_out_us") or 0) / 1_000_000
            chain = f"[{input_index}:a]volume={10 ** (gain_db / 20):.6f}"
            if fade_in_s > 0:
                chain += f",afade=t=in:st=0:d={fade_in_s:.3f}"
            if fade_out_s > 0:
                chain += f",afade=t=out:st={max(0.0, duration_s - fade_out_s):.3f}:d={fade_out_s:.3f}"
            if binding.get("loop_enabled"):
                chain += f",atrim=duration={duration_s:.3f}"
            chain += f",adelay={start_ms}:all=1,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{index}]"
            chains.append(chain)
            mix_inputs.append(f"[a{index}]")
            input_index += 1
        filter_complex = (
            ";".join(chains)
            + f";{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=longest:normalize=0[mix]"
            + ";[mix]apad,loudnorm=I=-16:TP=-2:LRA=11,aresample=48000[mixout]"
        )
        return self._run_ffmpeg(
            [*args, "-filter_complex", filter_complex, "-map", "[mixout]", "-t", f"{video_duration_seconds:.3f}", "-c:a", "pcm_s16le", "-y", str(output_path)],
            timeout=900,
        )

    def _register_render(
        self,
        *,
        episode: dict[str, Any],
        timeline_revision_id: str,
        timeline: dict[str, Any],
        render_path: Path,
        project_root: Path,
        input_snapshot: dict[str, Any],
        ffmpeg_execution: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Register a finished render file as an immutable episode_render_versions row.

        Shared by the plain, mixed-audio and segmented render paths so every
        render version is registered with identical columns and provenance.
        """
        digest, size = _hash_file(render_path)
        probe = self._probe(render_path)
        ffmpeg_command = {"executor": "builtin:ffmpeg", "executable": ffmpeg_execution["executable"], "args": ffmpeg_execution["args"], "returncode": ffmpeg_execution["returncode"]}
        if "steps" in ffmpeg_execution:
            execution_log = json.dumps(
                {"steps": ffmpeg_execution["steps"], "stdout_tail": ffmpeg_execution["stdout_tail"], "stderr_tail": ffmpeg_execution["stderr_tail"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            execution_log = json.dumps({"stdout_tail": ffmpeg_execution["stdout_tail"], "stderr_tail": ffmpeg_execution["stderr_tail"]}, ensure_ascii=False, sort_keys=True)
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
            render_revision = int(connection.execute("SELECT revision FROM episode_render_versions WHERE id=?", (render_id,)).fetchone()[0])
        # Expose the immutable render revision to the review client.  The
        # delivery gate compares the review's expected_subject_revision with
        # this value; omitting it forced the UI to guess ``1`` and made a
        # future render-revision migration impossible to use safely.
        return {"id": render_id, "episode_id": episode["id"], "timeline_revision_id": timeline_revision_id, "rel_path": render_path.relative_to(project_root).as_posix(), "sha256": digest, "byte_size": size, "probe": probe, "input_snapshot": input_snapshot, "compose_fingerprint": _hash(input_snapshot), "ffmpeg_command": ffmpeg_command, "execution_log": execution_log, "revision": render_revision, "status": "VERIFIED"}

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

    def render_thumbnail(self, episode_render_version_id: str, size: str = "medium", frame: str = "poster") -> tuple[Path, str]:
        """Return only a derived poster after the render's path/hash checks pass."""
        render, source = self.render_content_path(episode_render_version_id)
        mime_type = str(render.get("mime_type") or "").lower()
        if not mime_type.startswith("video/"):
            raise DomainRuleError("THUMBNAIL_UNSUPPORTED", "整集渲染不是可生成海报的视频")
        destination, mime, _, _ = self.media.cached_video_thumbnail(
            source,
            cache_namespace=f"episode-render-{episode_render_version_id}",
            source_sha256=str(render["sha256"]),
            duration_ms=int(render.get("duration_ms") or 0),
            size=size,
            frame=frame,
        )
        return destination, mime

    def build_delivery(self, episode_render_version_id: str, target_version_id: str, brand_kit_id: str | None = None, watermark_profile_id: str | None = None, compliance_policy_id: str | None = None, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            render = connection.execute("SELECT erv.*, e.code AS episode_code, e.id AS episode_id, s.project_id, p.root_rel FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id WHERE erv.id=?", (episode_render_version_id,)).fetchone()
            target = connection.execute("SELECT dtv.*, dt.project_id, dt.transport, dt.code AS target_code, dt.title AS target_title FROM delivery_target_versions dtv JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id WHERE dtv.id=?", (target_version_id,)).fetchone()
        if render is None:
            raise DomainRuleError("EPISODE_RENDER_NOT_FOUND", "整集渲染版本不存在")
        if target is None:
            raise DomainRuleError("DELIVERY_TARGET_VERSION_NOT_FOUND", "交付目标版本不存在")
        if render["project_id"] != target["project_id"]:
            raise DomainRuleError("DELIVERY_PROJECT_MISMATCH", "交付目标必须属于同一项目")
        if target["transport"] != "LOCAL_FILESYSTEM":
            raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许本地文件交付")
        project_id = str(render["project_id"])
        explicit_no_brand = brand_kit_id == "NONE"
        explicit_no_watermark = watermark_profile_id == "NONE"
        explicit_no_compliance = compliance_policy_id == "NONE"
        # A machine-verified render is not an episode approval.  Delivery is a
        # separate irreversible hand-off and therefore requires the latest
        # non-stale human approval before any output path is touched.
        with self.database.connect() as connection:
            approval = connection.execute(
                """SELECT decision, is_stale, subject_revision FROM review_decisions
                WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1""",
                (episode_render_version_id,),
            ).fetchone()
        with self.database.connect() as connection:
            brand = None if explicit_no_brand else connection.execute("SELECT * FROM brand_kits WHERE id=? AND project_id=? AND status='ACTIVE'", (brand_kit_id, project_id)).fetchone() if brand_kit_id else connection.execute("SELECT * FROM brand_kits WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
            watermark = None if explicit_no_watermark else connection.execute("SELECT * FROM watermark_profiles WHERE id=? AND project_id=? AND status='ACTIVE'", (watermark_profile_id, project_id)).fetchone() if watermark_profile_id else connection.execute("SELECT * FROM watermark_profiles WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
            compliance = None if explicit_no_compliance else connection.execute("SELECT * FROM compliance_policies WHERE id=? AND project_id=? AND status='ACTIVE'", (compliance_policy_id, project_id)).fetchone() if compliance_policy_id else connection.execute("SELECT * FROM compliance_policies WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
        if brand_kit_id and not explicit_no_brand and brand is None:
            raise DomainRuleError("BRAND_KIT_NOT_ACTIVE", "BrandKit 不存在、项目不匹配或已 RETIRED")
        if watermark_profile_id and not explicit_no_watermark and watermark is None:
            raise DomainRuleError("WATERMARK_PROFILE_NOT_ACTIVE", "水印版本不存在、项目不匹配或已 RETIRED")
        if compliance_policy_id and not explicit_no_compliance and compliance is None:
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
            if compliance_rules.get("require_watermark") and (not watermark or not bool((watermark_config or {}).get("enabled", True))):
                findings.append({"code": "WATERMARK_REQUIRED", "severity": "ERROR", "message": "当前合规策略要求水印，但没有 ACTIVE 水印版本"})
            max_duration_ms = compliance_rules.get("max_duration_ms")
            if max_duration_ms is not None and duration_ms > int(max_duration_ms):
                findings.append({"code": "DURATION_EXCEEDED", "severity": "ERROR", "message": "整集时长超过当前本地合规策略上限", "observed": duration_ms, "limit": int(max_duration_ms)})
        machine_preflight: dict[str, object] = {"status": "FAIL" if findings else "PASS", "findings": findings, "checked_render_sha256": str(render["sha256"]), "responsibility": {"machine": "本地规则预检与文件完整性", "human": "内容/版权/平台最终审核，不由机器结果替代"}}
        if findings:
            raise DomainRuleError("COMPLIANCE_PREFLIGHT_FAILED", "本地合规机器预检未通过", machine_preflight)
        if approval is None or str(approval["decision"]) != "APPROVED" or int(approval["is_stale"] or 0) != 0:
            raise DomainRuleError("EPISODE_RENDER_APPROVAL_REQUIRED", "只有最新、未过期的整集批准版本才能创建交付候选")
        if int(approval["subject_revision"]) != int(render["revision"]):
            raise DomainRuleError("EPISODE_RENDER_APPROVAL_STALE", "整集批准基于旧 revision，不能创建交付候选")
        spec = json.loads(target["target_spec_json"])
        path_value = spec.get("path_rel")
        if not isinstance(path_value, str) or not path_value.strip():
            raise DomainRuleError("DELIVERY_TARGET_SPEC_INCOMPLETE", "交付目标必须显式指定项目内 path_rel")
        path_rel = path_value.strip()
        if str(target["status"]) != "ACTIVE":
            raise DomainRuleError("DELIVERY_TARGET_VERSION_INACTIVE", "只能使用当前 ACTIVE 的交付目标版本创建新候选")
        if Path(path_rel).is_absolute() or ".." in Path(path_rel).parts:
            raise DomainRuleError("INVALID_DELIVERY_TARGET", "交付目标路径越界")
        project_root = (self.settings.projects_root / render["root_rel"]).resolve()
        source = (project_root / render["rel_path"]).resolve()
        if not source.is_file() or not source.is_relative_to(project_root) or source.is_symlink():
            raise DomainRuleError("EPISODE_RENDER_FILE_MISSING", "整集渲染文件缺失、为 symlink 或路径越界")
        source_hash, _ = _hash_file(source)
        if source_hash != str(render["sha256"]):
            raise DomainRuleError("EPISODE_RENDER_INTEGRITY_FAILED", "整集渲染文件 hash 与登记值不一致")
        package_id = str(uuid.uuid4())
        delivery_parent = (project_root / path_rel).resolve()
        destination_base = (delivery_parent / str(render["episode_code"])).resolve()
        if not delivery_parent.is_relative_to(project_root) or not destination_base.is_relative_to(project_root):
            raise DomainRuleError("PATH_ESCAPE", "交付目标目录越界")
        base_rel = destination_base.relative_to(project_root).as_posix()
        with self.database.connect() as connection:
            base_package = connection.execute("SELECT COUNT(*) AS count FROM delivery_packages WHERE rel_path=?", (base_rel,)).fetchone()
        # Preserve the original first-package layout for existing projects, but
        # every subsequent build gets a unique immutable directory.  A stale
        # directory with no package row is never overwritten.
        if int(base_package["count"] or 0) == 0:
            destination_dir = destination_base
        else:
            destination_dir = (delivery_parent / str(render["episode_code"]) / f"delivery-{package_id}").resolve()
        if destination_dir.exists():
            raise DomainRuleError("DELIVERY_DESTINATION_OCCUPIED", "交付目标目录已存在，系统不会覆盖既有文件")
        partial_dir = destination_dir.with_name(f".{destination_dir.name}.partial-{package_id}")
        destination_dir.parent.mkdir(parents=True, exist_ok=True)
        partial_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / f"{render['episode_code']}.mp4"
        partial_output = partial_dir / f"{render['episode_code']}.mp4"
        watermark_text_path = partial_dir / f"{render['episode_code']}.watermark.txt"
        try:
            if watermark_config and bool(watermark_config.get("enabled", True)):
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
                self._run_ffmpeg(["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", drawtext, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", "-y", str(partial_output)], timeout=900)
            else:
                shutil.copyfile(source, partial_output)
            # The directory itself is published atomically only after the
            # render has been copied/transcoded successfully.
            replace_path(partial_dir, destination_dir)
        finally:
            partial_output.unlink(missing_ok=True)
            watermark_text_path.unlink(missing_ok=True)
            if partial_dir.exists():
                shutil.rmtree(partial_dir, ignore_errors=True)
        file_hash, byte_size = _hash_file(destination)
        delivery_manifest_files = [
            {"rel_path": destination.relative_to(project_root).as_posix(), "sha256": file_hash, "byte_size": byte_size}
        ]
        delivered_watermark = destination_dir / f"{render['episode_code']}.watermark.txt"
        if delivered_watermark.is_file():
            watermark_hash, watermark_size = _hash_file(delivered_watermark)
            delivery_manifest_files.append(
                {
                    "rel_path": delivered_watermark.relative_to(project_root).as_posix(),
                    "sha256": watermark_hash,
                    "byte_size": watermark_size,
                }
            )
        timeline_input = json.loads(str(render["input_snapshot_json"] or "{}"))
        with self.database.connect() as connection:
            subtitle = connection.execute(
                "SELECT id, revision_no, format, content_hash, status FROM subtitle_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
                (render["episode_id"],),
            ).fetchone()
            audio_rows = connection.execute(
                """SELECT ab.id, ab.media_version_id, ab.track_type, ab.start_us, ab.end_us,
                ab.source_license_status, ab.license_evidence_json, mv.sha256, mv.byte_size
                FROM audio_bindings ab JOIN media_versions mv ON mv.id=ab.media_version_id
                WHERE ab.episode_id=? ORDER BY ab.start_us, ab.id""",
                (render["episode_id"],),
            ).fetchall()
        subtitle_snapshot = ({"id": str(subtitle["id"]), "revision_no": int(subtitle["revision_no"]), "format": str(subtitle["format"]), "content_hash": str(subtitle["content_hash"]), "status": str(subtitle["status"])} if subtitle else None)
        audio_snapshot = [{"id": str(row["id"]), "media_version_id": str(row["media_version_id"]), "track_type": str(row["track_type"]), "start_us": int(row["start_us"]), "end_us": int(row["end_us"]), "source_license_status": str(row["source_license_status"]), "license_evidence": json.loads(str(row["license_evidence_json"] or "{}")), "sha256": str(row["sha256"]), "byte_size": int(row["byte_size"])} for row in audio_rows]
        target_spec = json.loads(str(target["target_spec_json"] or "{}"))
        manifest = {
            "schema_version": "delivery-manifest.v3",
            "package_id": package_id,
            "episode_id": str(render["episode_id"]),
            "source": {"episode_render_version_id": episode_render_version_id, "sha256": str(render["sha256"]), "byte_size": int(source.stat().st_size), "timeline_revision_id": str(render["timeline_revision_id"]), "timeline_revision_hash": str(timeline_input.get("timeline_revision_hash") or ""), "input_snapshot": timeline_input},
            "target": {"target_version_id": target_version_id, "target_code": str(target["target_code"]), "target_title": str(target["target_title"]), "version_no": int(target["version_no"]), "transport": str(target["transport"]), "spec": target_spec},
            "encoding": {"render_mime_type": str(render["mime_type"] or "video/mp4"), "probe": probe, "target": {key: target_spec.get(key) for key in ("width", "height", "fps", "bitrate", "video_codec", "audio_codec") if key in target_spec}},
            "subtitles": subtitle_snapshot,
            "cover": {"status": "NOT_SELECTED", "media_version_id": None},
            "licenses": {"audio": [{"media_version_id": item["media_version_id"], "status": item["source_license_status"], "evidence": item["license_evidence"]} for item in audio_snapshot], "model": "USER_SUPPLIED_LOCAL_REFERENCE_ONLY"},
            "controls": {"brand_kit": brand_snapshot, "watermark_profile": watermark_snapshot, "compliance_policy": compliance_snapshot, "machine_preflight": machine_preflight},
            "review_responsibility": {"machine_preflight": "PASS", "human_review": "PENDING", "platform_review": "PENDING"},
            "files": delivery_manifest_files,
        }
        manifest_hash = _hash(manifest)
        manifest_path = destination_dir / "manifest.json"
        manifest_path.write_text(json.dumps({**manifest, "manifest_sha256": manifest_hash}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO delivery_packages (id, episode_render_version_id, target_version_id, rel_path, status, manifest_sha256, brand_kit_id, watermark_profile_id, compliance_policy_id, machine_preflight_status, machine_preflight_json, human_review_status, platform_review_status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, 'VERIFIED', ?, ?, ?, ?, 'PASS', ?, 'PENDING', 'PENDING', ?, ?, ?, 1, 'v3')", (package_id, episode_render_version_id, target_version_id, destination_dir.relative_to(project_root).as_posix(), manifest_hash, brand["id"] if brand else None, watermark["id"] if watermark else None, compliance["id"] if compliance else None, _json(machine_preflight), now, now, actor))
            for delivery_file in delivery_manifest_files:
                connection.execute(
                    "INSERT INTO delivery_files (id, delivery_package_id, rel_path, sha256, byte_size) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), package_id, delivery_file["rel_path"], delivery_file["sha256"], delivery_file["byte_size"]),
                )
            connection.execute("INSERT INTO delivery_files (id, delivery_package_id, rel_path, sha256, byte_size) VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), package_id, manifest_path.relative_to(project_root).as_posix(), hashlib.sha256(manifest_path.read_bytes()).hexdigest(), manifest_path.stat().st_size))
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'BUILT', ?, ?, ?, ?, ?, 1, 'v3')", (str(uuid.uuid4()), package_id, manifest_hash, "local filesystem delivery built; machine preflight PASS; human/platform review remains separate", now, now, actor))
        return {"id": package_id, "status": "VERIFIED", "rel_path": destination_dir.relative_to(project_root).as_posix(), "manifest_sha256": manifest_hash, "files": manifest["files"], "controls": manifest["controls"], "machine_preflight": machine_preflight, "human_review_status": "PENDING", "platform_review_status": "PENDING"}

    def _delivery_package_row(self, package_id: str) -> Any:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT dp.*, e.id AS episode_id, e.code AS episode_code,
                erv.timeline_revision_id, erv.sha256 AS render_sha256,
                p.id AS project_id, p.root_rel,
                dt.code AS target_code, dt.title AS target_title, dt.transport,
                dtv.version_no, dtv.target_spec_json
                FROM delivery_packages dp
                JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons s ON s.id=e.season_id
                JOIN projects p ON p.id=s.project_id
                JOIN delivery_target_versions dtv ON dtv.id=dp.target_version_id
                JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id
                WHERE dp.id=?""",
                (package_id,),
            ).fetchone()

    @staticmethod
    def _delivery_file_dict(row: Any) -> dict[str, Any]:
        return {"id": str(row["id"]), "delivery_package_id": str(row["delivery_package_id"]), "rel_path": str(row["rel_path"]), "sha256": str(row["sha256"]), "byte_size": int(row["byte_size"])}

    def get_delivery_package(self, package_id: str) -> dict[str, Any]:
        package = self._delivery_package_row(package_id)
        if package is None:
            raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
        with self.database.connect() as connection:
            files = connection.execute("SELECT * FROM delivery_files WHERE delivery_package_id=? ORDER BY rel_path", (package_id,)).fetchall()
            events = connection.execute("SELECT id, action, manifest_sha256, note, created_at, created_by FROM delivery_events WHERE delivery_package_id=? ORDER BY created_at, id", (package_id,)).fetchall()
        return {
            "id": str(package["id"]),
            "episode_id": str(package["episode_id"]),
            "episode_render_version_id": str(package["episode_render_version_id"]),
            "timeline_revision_id": str(package["timeline_revision_id"]),
            "target_version_id": str(package["target_version_id"]),
            "target": {"code": str(package["target_code"]), "title": str(package["target_title"]), "transport": str(package["transport"]), "version_no": int(package["version_no"]), "spec": json.loads(str(package["target_spec_json"] or "{}"))},
            "status": str(package["status"]),
            "rel_path": str(package["rel_path"]),
            "manifest_sha256": str(package["manifest_sha256"] or ""),
            "withdrawn_reason": package["withdrawn_reason"],
            "machine_preflight_status": str(package["machine_preflight_status"]),
            "machine_preflight": json.loads(str(package["machine_preflight_json"] or "{}")),
            "human_review_status": str(package["human_review_status"]),
            "platform_review_status": str(package["platform_review_status"]),
            "created_at": str(package["created_at"]),
            "updated_at": str(package["updated_at"]),
            "revision": int(package["revision"]),
            "files": [self._delivery_file_dict(row) for row in files],
            "events": [{"id": str(row["id"]), "action": str(row["action"]), "manifest_sha256": row["manifest_sha256"], "note": row["note"], "created_at": str(row["created_at"]), "created_by": str(row["created_by"])} for row in events],
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def list_delivery_files(self, package_id: str) -> list[dict[str, Any]]:
        if self._delivery_package_row(package_id) is None:
            raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM delivery_files WHERE delivery_package_id=? ORDER BY rel_path", (package_id,)).fetchall()
        return [self._delivery_file_dict(row) for row in rows]

    def list_episode_deliveries(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone() is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在")
            rows = connection.execute(
                """SELECT dp.id FROM delivery_packages dp
                JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                WHERE erv.episode_id=? ORDER BY dp.created_at DESC, dp.id DESC""",
                (episode_id,),
            ).fetchall()
        return [self.get_delivery_package(str(row["id"])) for row in rows]

    def delivery_download_path(self, package_id: str) -> tuple[Path, str]:
        package = self._delivery_package_row(package_id)
        if package is None:
            raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
        if str(package["status"]) not in {"VERIFIED", "WITHDRAWN"}:
            raise DomainRuleError("DELIVERY_NOT_VERIFIED", "只有 manifest verify 通过的交付包可以下载")
        root = (self.settings.projects_root / str(package["root_rel"])).resolve()
        if not root.is_dir() or root.is_symlink() or not root.is_relative_to(self.settings.projects_root.resolve()):
            raise DomainRuleError("DELIVERY_PATH_INVALID", "交付项目目录不存在或越界")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT rel_path, sha256, byte_size FROM delivery_files WHERE delivery_package_id=? AND lower(rel_path) LIKE '%.mp4' ORDER BY rel_path LIMIT 1",
                (package_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("DELIVERY_VIDEO_NOT_FOUND", "交付包不包含可下载的视频文件")
        rel = Path(str(row["rel_path"]))
        path = (root / rel).resolve()
        if rel.is_absolute() or ".." in rel.parts or path.is_symlink() or not path.is_file() or not path.is_relative_to(root):
            raise DomainRuleError("DELIVERY_FILE_INVALID", "交付文件缺失或路径越界")
        # Downloads are a material hand-off even though the file itself is
        # immutable.  Persist a bounded audit event with only the package
        # manifest/file fingerprints (never a path outside the project or
        # request data), so delivery history can answer who/when a package
        # was downloaded without treating this as a new build or review.
        now = _now()
        note = _json(
            {
                "file_rel_path": str(row["rel_path"]),
                "file_sha256": str(row["sha256"]),
                "file_byte_size": int(row["byte_size"]),
                "transport": "LOCAL_FILESYSTEM",
            }
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'DOWNLOAD', ?, ?, ?, ?, ?, 1, 'v3')",
                (str(uuid.uuid4()), package_id, str(package["manifest_sha256"] or ""), note, now, now, "local-user"),
            )
        return path, f"{package['episode_code']}-{package_id[:8]}.mp4"

    def verify_delivery(self, package_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            package = connection.execute("SELECT dp.*, e.code AS episode_code, p.root_rel FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id JOIN episodes e ON e.id=erv.episode_id JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id WHERE dp.id=?", (package_id,)).fetchone()
            files = connection.execute("SELECT * FROM delivery_files WHERE delivery_package_id=? ORDER BY rel_path", (package_id,)).fetchall()
        if package is None:
            raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
        root = (self.settings.projects_root / package["root_rel"]).resolve()
        checks: list[dict[str, Any]] = []
        root_valid = root.is_dir() and not root.is_symlink() and root.is_relative_to(self.settings.projects_root.resolve())
        manifest_payload: dict[str, Any] | None = None
        manifest_path: Path | None = None
        verification_source: Path | None = None
        for file in files:
            rel_path = Path(str(file["rel_path"]))
            safe_rel = not rel_path.is_absolute() and ".." not in rel_path.parts
            path = (root / rel_path).resolve() if root_valid and safe_rel else root / "__invalid_delivery_path__"
            safe_file = safe_rel and root_valid and path.is_relative_to(root) and not path.is_symlink() and path.is_file()
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if safe_file else None
            actual_size = path.stat().st_size if safe_file else None
            if safe_file and verification_source is None and not str(file["rel_path"]).lower().endswith("manifest.json"):
                verification_source = path
            item = {"rel_path": str(file["rel_path"]), "expected_sha256": str(file["sha256"]), "actual_sha256": actual, "expected_byte_size": int(file["byte_size"]), "actual_byte_size": actual_size, "ok": bool(safe_file and actual == str(file["sha256"]) and actual_size == int(file["byte_size"]))}
            checks.append(item)
            if str(file["rel_path"]).lower().endswith("manifest.json") and safe_file:
                manifest_path = path
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        manifest_payload = parsed
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    manifest_payload = None
        manifest_ok = False
        manifest_actual_hash: str | None = None
        if manifest_payload is not None and manifest_path is not None:
            embedded = manifest_payload.get("manifest_sha256")
            canonical = dict(manifest_payload)
            canonical.pop("manifest_sha256", None)
            manifest_actual_hash = _hash(canonical)
            manifest_ok = hmac.compare_digest(manifest_actual_hash, str(package["manifest_sha256"] or "")) and hmac.compare_digest(manifest_actual_hash, str(embedded or ""))
        ok = bool(checks) and all(item["ok"] for item in checks) and manifest_ok
        prior_status = str(package["status"])
        next_status = ("WITHDRAWN" if prior_status == "WITHDRAWN" else "VERIFIED") if ok else "CORRUPT"
        self_test_passed = False
        if ok and verification_source is not None:
            original = verification_source.read_bytes()
            with tempfile.TemporaryDirectory(prefix="localdrama-delivery-integrity-") as temp_dir:
                isolated_copy = Path(temp_dir) / verification_source.name
                isolated_copy.write_bytes(original + b"\0")
                self_test_passed = not hmac.compare_digest(
                    hashlib.sha256(isolated_copy.read_bytes()).hexdigest(),
                    hashlib.sha256(original).hexdigest(),
                )
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE delivery_packages SET status=?, updated_at=?, revision=revision+1 WHERE id=?", (next_status, now, package_id))
            if self_test_passed:
                connection.execute(
                    "INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'VERIFY', ?, ?, ?, ?, ?, 1, 'v3')",
                    (str(uuid.uuid4()), package_id, str(package["manifest_sha256"] or ""), _json({"ok": False, "self_test": "ISOLATED_TEMP_COPY", "official_files_mutated": False, "temporary_copy_removed": True}), now, now, "local-system"),
                )
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'VERIFY', ?, ?, ?, ?, ?, 1, 'v3')", (str(uuid.uuid4()), package_id, str(package["manifest_sha256"] or ""), _json({"ok": ok, "previous_status": prior_status, "manifest_hash": manifest_actual_hash}), now, now, "local-user"))
        return {"id": package_id, "status": next_status, "checks": checks, "manifest_check": {"ok": manifest_ok, "expected_sha256": package["manifest_sha256"], "actual_sha256": manifest_actual_hash}, "machine_preflight_status": package["machine_preflight_status"], "human_review_status": package["human_review_status"], "platform_review_status": package["platform_review_status"], "withdrawn_reason": package["withdrawn_reason"]}

    def withdraw_delivery(self, package_id: str, reason: str, actor: str = "local-user") -> dict[str, Any]:
        if not reason.strip():
            raise DomainRuleError("DELIVERY_WITHDRAW_REASON_REQUIRED", "撤回交付必须记录原因")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT id, status, manifest_sha256 FROM delivery_packages WHERE id=?", (package_id,)).fetchone()
            if row is None:
                raise DomainRuleError("DELIVERY_PACKAGE_NOT_FOUND", "交付包不存在")
            if str(row["status"]) == "WITHDRAWN":
                # Idempotent withdrawal keeps the original file set and audit
                # history intact while returning the already withdrawn state.
                return {"id": package_id, "status": "WITHDRAWN", "reason": reason.strip(), "idempotent": True}
            connection.execute("UPDATE delivery_packages SET status='WITHDRAWN', withdrawn_reason=?, updated_at=?, revision=revision+1 WHERE id=?", (reason, now, package_id))
            # Keep the immutable package fingerprint on the withdrawal event;
            # otherwise history loses the link between a withdrawal reason and
            # the exact manifest that was handed off.
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, manifest_sha256, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'WITHDRAWN', ?, ?, ?, ?, ?, 1, 'v2')", (str(uuid.uuid4()), package_id, row["manifest_sha256"], reason, now, now, actor))
        return {"id": package_id, "status": "WITHDRAWN", "reason": reason.strip(), "idempotent": False}

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
            # A machine preflight PASS is not enough to authorize review.  A
            # package whose manifest/files have subsequently failed integrity
            # verification must be rebuilt, rather than allowing a human or
            # platform decision to be attached to a corrupt artifact.
            if str(row["status"]) != "VERIFIED":
                raise DomainRuleError("DELIVERY_NOT_VERIFIED", "只有完整性校验通过的交付包才能进入人工/平台审核")
            connection.execute(f"UPDATE delivery_packages SET {column}=?, updated_at=?, revision=revision+1 WHERE id=?", (decision, now, package_id))
            connection.execute("INSERT INTO delivery_events (id, delivery_package_id, action, note, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'v3')", (str(uuid.uuid4()), package_id, f"{reviewer_type}_REVIEW_{decision}", note.strip(), now, now, actor))
            updated = connection.execute("SELECT * FROM delivery_packages WHERE id=?", (package_id,)).fetchone()
        return {"id": package_id, "status": str(updated["status"]), "machine_preflight_status": str(updated["machine_preflight_status"]), "human_review_status": str(updated["human_review_status"]), "platform_review_status": str(updated["platform_review_status"]), "reviewer_type": reviewer_type, "decision": decision, "note": note.strip()}

    def _ffmpeg_expected_duration_ms(self, args: list[str]) -> int | None:
        """Infer a defensible current-step duration from frozen FFmpeg input.

        Explicit ``-t`` is authoritative. Otherwise normal media inputs use
        FFprobe duration, while a concat manifest sums its immutable files.
        Unknown generators/filters deliberately remain indeterminate.
        """
        explicit: list[int] = []
        for index, value in enumerate(args[:-1]):
            if value != "-t":
                continue
            try:
                explicit.append(max(0, round(float(args[index + 1]) * 1000)))
            except (TypeError, ValueError):
                continue
        if explicit:
            return explicit[-1] or None

        durations: list[int] = []
        for index, value in enumerate(args[:-1]):
            if value != "-i":
                continue
            candidate = Path(args[index + 1])
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() == ".txt" and "concat" in args:
                total = 0
                try:
                    for line in candidate.read_text(encoding="utf-8").splitlines():
                        match = re.fullmatch(r"file '(.+)'", line.strip())
                        if not match:
                            continue
                        source = Path(match.group(1).replace("'\\''", "'"))
                        if source.is_file():
                            total += int(self._probe(source).get("duration_ms") or 0)
                except (OSError, DomainRuleError):
                    total = 0
                if total > 0:
                    durations.append(total)
                continue
            try:
                duration = int(self._probe(candidate).get("duration_ms") or 0)
            except DomainRuleError:
                duration = 0
            if duration > 0:
                durations.append(duration)
        return max(durations) if durations else None

    @staticmethod
    def _ffmpeg_progress_value(line: str) -> tuple[str, int | str] | None:
        key, separator, raw_value = line.strip().partition("=")
        if not separator:
            return None
        if key in {"out_time_us", "out_time_ms"}:
            try:
                return "processed_ms", max(0, int(raw_value) // 1000)
            except ValueError:
                return None
        if key == "progress":
            return "progress", raw_value
        return None

    def _run_ffmpeg(self, args: list[str], *, timeout: int) -> dict[str, Any]:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            process = subprocess.Popen(
                [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            deadline = monotonic() + timeout
            process_stdout = getattr(process, "stdout", None)
            process_stderr = getattr(process, "stderr", None)
            if not hasattr(process_stdout, "readline") or not hasattr(process_stderr, "readline"):
                while True:
                    try:
                        stdout_tail, stderr_tail = process.communicate(timeout=min(1.0, max(0.1, deadline - monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancel_check is not None and self.cancel_check():
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=3)
                            raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次 FFmpeg 输出不会登记") from None
                        if monotonic() >= deadline:
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=3)
                            raise DomainRuleError("FFMPEG_EXECUTION_TIMEOUT", "本地 FFmpeg 执行超时") from None
                stdout_tail = stdout_tail or ""
                stderr_tail = stderr_tail or ""
                if process.returncode != 0:
                    raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "本地 FFmpeg 执行失败", {"stderr_redacted": stderr_tail[-500:]})
                return {
                    "executable": str(ffmpeg), "args": args, "returncode": process.returncode,
                    "stdout_tail": stdout_tail[-2000:], "stderr_tail": stderr_tail[-4000:],
                }

            messages: Queue[tuple[str, str]] = Queue()
            stdout_lines: deque[str] = deque(maxlen=160)
            stderr_lines: deque[str] = deque(maxlen=240)

            def pump(stream: Any, channel: str) -> None:
                for line in iter(stream.readline, ""):
                    messages.put((channel, line))
                messages.put((channel, ""))

            readers = [
                threading.Thread(target=pump, args=(process_stdout, "stdout"), daemon=True),
                threading.Thread(target=pump, args=(process_stderr, "stderr"), daemon=True),
            ]
            for reader in readers:
                reader.start()
            expected_ms = self._ffmpeg_expected_duration_ms(args)
            processed_ms = 0
            open_streams = 2
            while True:
                try:
                    channel, line = messages.get(timeout=0.2)
                    if not line:
                        open_streams -= 1
                    elif channel == "stdout":
                        stdout_lines.append(line)
                        parsed = self._ffmpeg_progress_value(line)
                        if parsed and parsed[0] == "processed_ms":
                            processed_ms = max(processed_ms, int(parsed[1]))
                        if parsed and parsed[0] == "progress" and self.progress_callback is not None:
                            payload: dict[str, Any] = {
                                "phase": "ENCODING",
                                "processed_ms": processed_ms,
                            }
                            if expected_ms and expected_ms > 0:
                                payload.update(
                                    {
                                        "total_ms": expected_ms,
                                        "step_percent": min(100, round(processed_ms * 100 / expected_ms)),
                                    }
                                )
                            self.progress_callback(payload)
                    else:
                        stderr_lines.append(line)
                except Empty:
                    pass
                if self.cancel_check is not None and self.cancel_check():
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次 FFmpeg 输出不会登记")
                if monotonic() >= deadline:
                    raise DomainRuleError("FFMPEG_EXECUTION_TIMEOUT", "本地 FFmpeg 执行超时")
                if process.poll() is not None and open_streams <= 0:
                    break
            process.wait(timeout=3)
            for reader in readers:
                reader.join(timeout=1)
            stdout_tail = "".join(stdout_lines)
            stderr_tail = "".join(stderr_lines)
        except DomainRuleError:
            process_state = getattr(process, "poll", lambda: getattr(process, "returncode", None))() if "process" in locals() else 0
            if "process" in locals() and process_state is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            raise
        except OSError as error:
            raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "本地 FFmpeg 执行失败", {"reason": type(error).__name__}) from error
        stdout_tail = stdout_tail or ""
        stderr_tail = stderr_tail or ""
        if process.returncode != 0:
            raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "本地 FFmpeg 执行失败", {"stderr_redacted": stderr_tail[-500:]})
        return {"executable": str(ffmpeg), "args": args, "returncode": process.returncode, "stdout_tail": stdout_tail[-2000:], "stderr_tail": stderr_tail[-4000:]}

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = self.settings.ffprobe_path
        if not ffprobe or not Path(ffprobe).is_file():
            raise DomainRuleError("FFPROBE_UNAVAILABLE", "本机 FFprobe 不可用")
        try:
            result = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
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
