"""Canonical Post / Audio workspace and transactional mix commands."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class SqliteAudioWorkspaceRepository:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,se.project_id,p.root_rel FROM episodes e
            JOIN seasons se ON se.id=e.season_id JOIN projects p ON p.id=se.project_id WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return dict(row)

    @staticmethod
    def _mix_revision(connection: sqlite3.Connection, episode_id: str) -> tuple[int, str]:
        row = connection.execute("SELECT revision,status FROM audio_mix_drafts WHERE episode_id=?", (episode_id,)).fetchone()
        return (int(row["revision"]), str(row["status"])) if row else (0, "DRAFT")

    def workspace(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            mix_revision, mix_status = self._mix_revision(connection, episode_id)
            dialogue = self._dialogue_references(connection, episode_id)
            tracks = self._tracks(connection, episode_id)
        gaps: list[dict[str, Any]] = []
        for line in dialogue:
            if not line["selected_media_version_id"]:
                gaps.append({"code": "DIALOGUE_TTS_MISSING", "message": f"{line['line_code']} 尚未采用工作语音", "owner_route": "SHOT_STUDIO", "repair_action": "OPEN_DIALOGUE", "subject_id": line["line_id"]})
            elif line["selection_stale"]:
                gaps.append({"code": "DIALOGUE_TTS_STALE", "message": f"{line['line_code']} 的工作语音已过期", "owner_route": "SHOT_STUDIO", "repair_action": "REFRESH_DIALOGUE", "subject_id": line["line_id"]})
        for track in tracks:
            if track["track_kind"] not in {"BGM", "SFX"}:
                gaps.append({"code": "LEGACY_AUDIO_TRACK_KIND", "message": f"{track['source_name']} 使用旧轨道类型 {track['track_kind']}", "owner_route": "POST_AUDIO", "repair_action": "REPLACE_TRACK", "subject_id": track["id"]})
            if track["authorization_status"] != "VERIFIED_EVIDENCE":
                gaps.append({"code": "AUDIO_LICENSE_EVIDENCE_MISSING", "message": f"{track['source_name']} 缺少可验证授权证据", "owner_route": "POST_AUDIO", "repair_action": "REPLACE_TRACK", "subject_id": track["id"]})
            if track["machine_status"] not in {None, "PASS"}:
                gaps.append({"code": "AUDIO_MACHINE_QC_FAILED", "message": f"{track['source_name']} 未通过机器检查", "owner_route": "REVIEW", "repair_action": "OPEN_REVIEW", "subject_id": track["media_version_id"]})
        summary = {
            "dialogue_count": len(dialogue),
            "adopted_dialogue_count": sum(1 for item in dialogue if item["selected_media_version_id"] and not item["selection_stale"]),
            "bgm_count": sum(1 for item in tracks if item["track_kind"] == "BGM"),
            "sfx_count": sum(1 for item in tracks if item["track_kind"] == "SFX"),
            "gap_count": len(gaps),
        }
        return {
            "episode_id": episode_id,
            "project_id": str(episode["project_id"]),
            "episode_code": str(episode["code"]),
            "episode_title": episode["title"],
            "mix_revision": mix_revision,
            "mix_status": mix_status,
            "dialogue_references": dialogue,
            "tracks": tracks,
            "gaps": gaps,
            "summary": summary,
            "allowed_actions": ["ADD_BGM", "ADD_SFX", "EDIT_MIX_DRAFT"],
        }

    @staticmethod
    def _dialogue_references(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT dl.id AS line_id,dl.code AS line_code,dl.shot_id,s.code AS shot_code,dl.speaker,
            dtr.text,dtr.id AS text_revision_id,dtr.revision_no,
            dcs.tts_candidate_id AS selected_tts_candidate_id,tc.media_version_id AS selected_media_version_id,
            mv.duration_ms AS selected_media_duration_ms,tc.candidate_kind AS selected_candidate_kind,
            CASE WHEN dcs.source_text_revision_id IS NOT NULL AND dcs.source_text_revision_id<>dtr.id THEN 1 ELSE 0 END AS selection_stale
            FROM dialogue_lines dl
            JOIN dialogue_text_revisions dtr ON dtr.id=(SELECT latest.id FROM dialogue_text_revisions latest
              WHERE latest.dialogue_line_id=dl.id ORDER BY latest.revision_no DESC,latest.id DESC LIMIT 1)
            LEFT JOIN shots s ON s.id=dl.shot_id
            LEFT JOIN dialogue_candidate_selections dcs ON dcs.id=(SELECT latest_selection.id FROM dialogue_candidate_selections latest_selection
              WHERE latest_selection.dialogue_line_id=dl.id ORDER BY latest_selection.created_at DESC,latest_selection.id DESC LIMIT 1)
            LEFT JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
            LEFT JOIN media_versions mv ON mv.id=tc.media_version_id
            WHERE dl.episode_id=? ORDER BY dl.code,dl.id""",
            (episode_id,),
        ).fetchall()
        return [{**dict(row), "selection_stale": bool(row["selection_stale"])} for row in rows]

    @staticmethod
    def _tracks(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT ab.id,ab.episode_id,ab.media_version_id,mv.source_name,ab.track_type AS track_kind,
            ab.start_us,ab.end_us,ab.gain_db,ab.loop_enabled,ab.fade_in_us,ab.fade_out_us,mv.duration_ms AS source_duration_ms,
            ab.source_license_status AS license_status,ab.license_evidence_json,ab.revision,
            mc.status AS machine_status,rd.decision AS latest_review_decision
            FROM audio_bindings ab JOIN media_versions mv ON mv.id=ab.media_version_id
            LEFT JOIN machine_check_runs mc ON mc.id=(SELECT latest.id FROM machine_check_runs latest
              WHERE latest.subject_type='MEDIA_VERSION' AND latest.subject_id=ab.media_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
            LEFT JOIN review_decisions rd ON rd.id=(SELECT latest_review.id FROM review_decisions latest_review
              WHERE latest_review.subject_type='MEDIA_VERSION' AND latest_review.subject_id=ab.media_version_id ORDER BY latest_review.created_at DESC,latest_review.id DESC LIMIT 1)
            WHERE ab.episode_id=? ORDER BY ab.start_us,ab.id""",
            (episode_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            evidence = json.loads(str(item.pop("license_evidence_json") or "{}"))
            item["authorization_status"] = "VERIFIED_EVIDENCE" if evidence.get("schema_version") == "localdrama.audio-license-evidence.v1" else "LEGACY_INCOMPLETE"
            item["loop_enabled"] = bool(item["loop_enabled"])
            item["allowed_actions"] = ["UPDATE_MIX_TRACK", "REMOVE_MIX_TRACK"] if item["track_kind"] in {"BGM", "SFX"} else []
            items.append(item)
        return items

    @staticmethod
    def _idempotent_replay(connection: sqlite3.Connection, scope: str, key: str, payload_hash: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (scope, key)
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise DomainRuleError("AUDIO_IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 idempotency_key 的声音请求内容不一致")
        return {**json.loads(str(row["response_json"])), "idempotent_replay": True}

    @staticmethod
    def _store_idempotency(connection: sqlite3.Connection, scope: str, key: str, payload_hash: str, result: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
            (scope, key, payload_hash, _json(result)),
        )

    @staticmethod
    def _validate_range(command: dict[str, Any], source_duration_ms: int | None = None) -> None:
        start_us, end_us = int(command["start_us"]), int(command["end_us"])
        if end_us <= start_us:
            raise DomainRuleError("AUDIO_BINDING_RANGE_INVALID", "音轨结束时间必须晚于开始时间")
        if int(command["fade_in_us"]) + int(command["fade_out_us"]) > end_us - start_us:
            raise DomainRuleError("AUDIO_FADE_RANGE_INVALID", "淡入与淡出总时长不能超过音轨范围")
        duration_us = int(source_duration_ms or 0) * 1000
        if not bool(command["loop_enabled"]) and duration_us > 0 and end_us - start_us > duration_us:
            raise DomainRuleError("AUDIO_BINDING_EXCEEDS_SOURCE", "未循环时音轨范围不能超过源音频时长")

    @staticmethod
    def _ensure_mix(connection: sqlite3.Connection, episode_id: str, actor: str, now: str) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO audio_mix_drafts
            (episode_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'DRAFT',?,?,?,0,'v2')""",
            (episode_id, now, now, actor),
        )

    @staticmethod
    def _advance_mix(connection: sqlite3.Connection, episode_id: str, expected: int, now: str) -> int:
        changed = connection.execute(
            "UPDATE audio_mix_drafts SET revision=revision+1,status='DRAFT',updated_at=? WHERE episode_id=? AND revision=?",
            (now, episode_id, expected),
        ).rowcount
        if changed != 1:
            actual = connection.execute("SELECT revision FROM audio_mix_drafts WHERE episode_id=?", (episode_id,)).fetchone()
            raise DomainRuleError("AUDIO_MIX_REVISION_CONFLICT", "声音混音草稿已变化，请刷新后重试", {"expected_revision": expected, "actual_revision": int(actual[0]) if actual else 0})
        return expected + 1

    def create_track(self, episode_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        if str(command.get("track_kind") or "") not in {"BGM", "SFX"}:
            raise DomainRuleError("AUDIO_TRACK_KIND_UNSUPPORTED", "后期混音只接受 BGM 或 SFX；对白由 Shot Studio 的工作语音提供")
        media = self.media.verify_content_integrity(str(command["media_version_id"]))
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
        if str(media["project_id"]) != str(episode["project_id"]) or str(media["media_kind"]) != "AUDIO":
            raise DomainRuleError("AUDIO_BINDING_MEDIA_INVALID", "音轨必须引用同项目已验证的音频")
        self._validate_range(command, int(media.get("duration_ms") or 0))
        root = self.settings.resolve_project_root(str(episode["root_rel"]))
        evidence_path = controlled_path(
            root,
            str(command["license_evidence_path_rel"]),
            must_exist=True,
            require_file=True,
            code="AUDIO_LICENSE_EVIDENCE_INVALID",
        )
        evidence_hash, evidence_size = _hash_file(evidence_path)
        evidence = {"schema_version": "localdrama.audio-license-evidence.v1", "path_rel": evidence_path.relative_to(root).as_posix(), "sha256": evidence_hash, "byte_size": evidence_size}
        key = str(command["idempotency_key"]).strip()
        scope = f"audio-track:create:{episode_id}"
        payload_hash = _digest({name: value for name, value in command.items() if name != "idempotency_key"})
        binding_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            self._ensure_mix(connection, episode_id, actor, now)
            mix_revision = self._advance_mix(connection, episode_id, int(command["expected_mix_revision"]), now)
            connection.execute(
                """INSERT INTO audio_bindings
                (id,episode_id,media_version_id,track_type,start_us,end_us,gain_db,source_license_status,status,snapshot_json,
                 loop_enabled,fade_in_us,fade_out_us,license_evidence_json,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,?,?,?,?,?,1,'v2')""",
                (binding_id, episode_id, command["media_version_id"], command["track_kind"], command["start_us"], command["end_us"], command["gain_db"], command["license_status"],
                 _json({"schema_version": "localdrama.audio-binding.v2", "media_version_id": command["media_version_id"], "media_sha256": media["sha256"]}), int(bool(command["loop_enabled"])), command["fade_in_us"], command["fade_out_us"], _json(evidence), now, now, actor),
            )
            result = {"id": binding_id, "episode_id": episode_id, "media_version_id": command["media_version_id"], "track_kind": command["track_kind"], "revision": 1, "mix_revision": mix_revision, "outcome": "CREATED", "idempotent_replay": False}
            self._audit_and_event(connection, actor, episode["project_id"], binding_id, episode_id, "AUDIO_TRACK_CREATED", "AudioSourceChanged", result)
            self._store_idempotency(connection, scope, key, payload_hash, result)
            return result

    def update_track(self, binding_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key, scope = str(command["idempotency_key"]).strip(), f"audio-track:update:{binding_id}"
        payload_hash = _digest({name: value for name, value in command.items() if name != "idempotency_key"})
        now = _now()
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            row = connection.execute(
                """SELECT ab.*,mv.duration_ms,se.project_id FROM audio_bindings ab JOIN media_versions mv ON mv.id=ab.media_version_id
                JOIN episodes e ON e.id=ab.episode_id JOIN seasons se ON se.id=e.season_id WHERE ab.id=?""", (binding_id,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("AUDIO_TRACK_NOT_FOUND", "声音轨道不存在")
            if str(row["track_type"]) not in {"BGM", "SFX"}:
                raise DomainRuleError("AUDIO_TRACK_LEGACY_READ_ONLY", "旧轨道类型只能替换，不能直接编辑")
            self._validate_range(command, int(row["duration_ms"] or 0))
            if int(row["revision"]) != int(command["expected_revision"]):
                raise DomainRuleError("AUDIO_TRACK_REVISION_CONFLICT", "声音轨道已变化，请刷新后重试")
            self._ensure_mix(connection, str(row["episode_id"]), actor, now)
            mix_revision = self._advance_mix(connection, str(row["episode_id"]), int(command["expected_mix_revision"]), now)
            connection.execute(
                """UPDATE audio_bindings SET start_us=?,end_us=?,gain_db=?,loop_enabled=?,fade_in_us=?,fade_out_us=?,
                updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                (command["start_us"], command["end_us"], command["gain_db"], int(bool(command["loop_enabled"])), command["fade_in_us"], command["fade_out_us"], now, binding_id, command["expected_revision"]),
            )
            result = {"id": binding_id, "episode_id": str(row["episode_id"]), "media_version_id": str(row["media_version_id"]), "track_kind": str(row["track_type"]), "revision": int(row["revision"]) + 1, "mix_revision": mix_revision, "outcome": "UPDATED", "idempotent_replay": False}
            self._audit_and_event(connection, actor, row["project_id"], binding_id, row["episode_id"], "AUDIO_TRACK_UPDATED", "MixDraftChanged", result)
            self._store_idempotency(connection, scope, key, payload_hash, result)
            return result

    def remove_track(self, binding_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key, scope = str(command["idempotency_key"]).strip(), f"audio-track:remove:{binding_id}"
        payload_hash = _digest({name: value for name, value in command.items() if name != "idempotency_key"})
        now = _now()
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            row = connection.execute(
                """SELECT ab.*,se.project_id FROM audio_bindings ab JOIN episodes e ON e.id=ab.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE ab.id=?""", (binding_id,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("AUDIO_TRACK_NOT_FOUND", "声音轨道不存在")
            if int(row["revision"]) != int(command["expected_revision"]):
                raise DomainRuleError("AUDIO_TRACK_REVISION_CONFLICT", "声音轨道已变化，请刷新后重试")
            self._ensure_mix(connection, str(row["episode_id"]), actor, now)
            mix_revision = self._advance_mix(connection, str(row["episode_id"]), int(command["expected_mix_revision"]), now)
            connection.execute("DELETE FROM audio_bindings WHERE id=? AND revision=?", (binding_id, command["expected_revision"]))
            result = {"id": binding_id, "episode_id": str(row["episode_id"]), "media_version_id": str(row["media_version_id"]), "track_kind": str(row["track_type"]), "revision": int(row["revision"]), "mix_revision": mix_revision, "outcome": "REMOVED", "idempotent_replay": False}
            self._audit_and_event(connection, actor, row["project_id"], binding_id, row["episode_id"], "AUDIO_TRACK_REMOVED", "AudioSourceChanged", {**result, "reason": command["reason"]})
            self._store_idempotency(connection, scope, key, payload_hash, result)
            return result

    @staticmethod
    def _audit_and_event(connection: sqlite3.Connection, actor: str, project_id: str, binding_id: str, episode_id: str, action: str, event_type: str, payload: dict[str, Any]) -> None:
        connection.execute(
            """UPDATE timeline_revisions SET status='STALE',updated_at=?
            WHERE id=(SELECT id FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1)
              AND status!='STALE'""",
            (_now(), episode_id),
        )
        connection.execute(
            """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'editor',?,'audio_binding',?,?,?)""",
            (actor, action, binding_id, action, _json(payload)),
        )
        connection.execute(
            "INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json) VALUES (? ,?,'EPISODE',?,?)",
            (event_type, project_id, episode_id, _json(payload)),
        )
