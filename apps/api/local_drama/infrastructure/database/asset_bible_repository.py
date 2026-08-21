"""SQLite adapter for the Asset Bible bounded context.

Implements application/ports AssetBibleRepository against the V2 schema
(0042).  All writes run inside the caller's transaction; the adapter itself
never opens its own write transaction.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("state_json", "metadata_json", "extra_json"):
        if key in item and item[key] is not None:
            item[key.replace("_json", "")] = json.loads(str(item.pop(key)))
    return item


class SqliteAssetBibleRepository:
    """SQLite implementation; satisfies AssetBibleRepository structurally."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # ---- lookups ---------------------------------------------------------

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
        return _row_to_dict(row)

    def list_assets(self, project_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        clause = " AND kind=?" if kind else ""
        params: list[Any] = [project_id]
        if kind:
            params.append(kind)
        rows = self.connection.execute(
            f"SELECT * FROM story_assets WHERE project_id=?{clause} ORDER BY kind, code, id",
            params,
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def asset_project_id(self, asset_id: str) -> str | None:
        row = self.connection.execute("SELECT project_id FROM story_assets WHERE id=?", (asset_id,)).fetchone()
        return str(row["project_id"]) if row else None

    def multiview_generations(self, asset_id: str) -> list[dict[str, Any]]:
        return self._generation_batches(asset_id, "ASSET_MULTI_VIEW")

    def expression_generations(self, asset_id: str) -> list[dict[str, Any]]:
        return self._generation_batches(asset_id, "ASSET_EXPRESSION_GRID")

    def detail_generations(self, asset_id: str) -> list[dict[str, Any]]:
        return self._generation_batches(asset_id, "ASSET_CLOSEUP_DETAIL")

    def _generation_batches(self, asset_id: str, purpose: str) -> list[dict[str, Any]]:
        """Project the three independent jobs/results without duplicating facts.

        A promoted MediaVersion is discovered through immutable
        job-attempt/artifact lineage.  Missing output is expected for queued,
        running, or failed slots, so one failed view does not hide its siblings.
        """
        intents = self.connection.execute(
            """SELECT * FROM generation_intents
            WHERE owner_type='STORY_ASSET' AND owner_id=? AND purpose=?
            ORDER BY created_at DESC, id DESC""",
            (asset_id, purpose),
        ).fetchall()
        batches: list[dict[str, Any]] = []
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"}
        for intent in intents:
            variants = self.connection.execute(
                """SELECT gv.*, j.id AS job_id, j.state AS job_state, j.last_error_code,
                j.last_error_detail_redacted, j.progress_json
                FROM generation_variants gv
                LEFT JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id
                WHERE gv.intent_id=? ORDER BY gv.variant_no, gv.id""",
                (intent["id"],),
            ).fetchall()
            items: list[dict[str, Any]] = []
            states: list[str] = []
            for variant in variants:
                try:
                    parameters = json.loads(str(variant["parameter_set_json"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    parameters = {}
                reference_kind = str(parameters.get("OUTPUT_REFERENCE_KIND") or parameters.get("VIEW_KIND") or "OTHER")
                slot_kind = str(parameters.get("SLOT_KIND") or parameters.get("VIEW_KIND") or reference_kind)
                outputs = self.connection.execute(
                    """SELECT mv.id AS media_version_id, mv.source_artifact_id, mv.rel_path,
                    mv.mime_type, mv.sha256, mv.integrity_status
                    FROM media_versions mv
                    JOIN artifacts a ON a.id=mv.source_artifact_id
                    JOIN job_attempts ja ON ja.id=a.job_attempt_id
                    JOIN jobs j ON j.id=ja.job_id
                    WHERE j.subject_type='GENERATION_VARIANT' AND j.subject_id=?
                    ORDER BY mv.created_at, mv.id""",
                    (variant["id"],),
                ).fetchall()
                state = str(variant["job_state"] or variant["status"])
                states.append(state)
                progress = {}
                try:
                    progress = json.loads(str(variant["progress_json"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    pass
                items.append({
                    "reference_kind": reference_kind,
                    "slot_kind": slot_kind,
                    "yaw_deg": parameters.get("YAW_DEG"),
                    "variant_id": str(variant["id"]),
                    "variant_no": int(variant["variant_no"]),
                    "variant_status": str(variant["status"]),
                    "job_id": str(variant["job_id"]) if variant["job_id"] else None,
                    "job_state": str(variant["job_state"]) if variant["job_state"] else None,
                    "progress": progress,
                    "error": ({"code": variant["last_error_code"], "detail": variant["last_error_detail_redacted"]} if variant["last_error_code"] else None),
                    "outputs": [dict(row) for row in outputs],
                })
            succeeded = sum(1 for item in items if item["job_state"] == "SUCCEEDED")
            failed = sum(1 for item in items if item["job_state"] in {"FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"})
            if not items:
                aggregate = str(intent["status"])
            elif succeeded == len(items):
                aggregate = "SUCCEEDED"
            elif all(state in terminal for state in states):
                aggregate = "PARTIAL_FAILED" if succeeded else "FAILED"
            elif succeeded or failed:
                aggregate = "PARTIAL_RUNNING"
            else:
                aggregate = "RUNNING"
            batches.append({
                "intent_id": str(intent["id"]),
                "status": aggregate,
                "created_at": intent["created_at"],
                "completed_count": succeeded,
                "failed_count": failed,
                "total_count": len(items),
                "items": items,
            })
        return batches

    def media_project_id(self, media_version_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT ma.project_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?",
            (media_version_id,),
        ).fetchone()
        return str(row["project_id"]) if row else None

    def shot_project_id(self, shot_id: str) -> str | None:
        row = self.connection.execute(
            """SELECT se.project_id FROM shots s
            JOIN episodes e ON e.id=s.episode_id JOIN seasons se ON se.id=e.season_id
            WHERE s.id=?""",
            (shot_id,),
        ).fetchone()
        return str(row["project_id"]) if row else None

    def episode_project_id(self, episode_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?",
            (episode_id,),
        ).fetchone()
        return str(row["project_id"]) if row else None

    def update_asset_canonical(self, story_asset_id: str, media_version_id: str) -> None:
        row = self.connection.execute("SELECT revision, project_id FROM story_assets WHERE id=?", (story_asset_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": story_asset_id})
        self.connection.execute(
            "UPDATE story_assets SET canonical_media_version_id=?, updated_at=?, revision=revision+1 WHERE id=?",
            (media_version_id, _now(), story_asset_id),
        )
        self.audit(actor="local-user", role_context="writer", action="STORY_ASSET_HERO_PROJECTION", subject_type="story_asset", subject_id=story_asset_id, summary="HERO 参考同步 canonical 投影", metadata={"project_id": str(row["project_id"]), "canonical_media_version_id": media_version_id})

    # ---- states ----------------------------------------------------------

    def create_state(
        self,
        *,
        project_id: str,
        story_asset_id: str,
        code: str,
        label: str,
        state_kind: str,
        description: str,
        state_json: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        state_id, now = str(uuid.uuid4()), _now()
        try:
            self.connection.execute(
                """INSERT INTO story_asset_states
                (id, project_id, story_asset_id, code, label, state_kind, description, state_json,
                 status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,1,'v1')""",
                (state_id, project_id, story_asset_id, code, label, state_kind, description, _json(state_json), now, now, actor),
            )
        except sqlite3.IntegrityError as error:
            raise DomainRuleError("STORY_ASSET_STATE_CODE_CONFLICT", "同一资产的状态 code 已存在", {"code": code}) from error
        self.audit(actor=actor, role_context="writer", action="STORY_ASSET_STATE_CREATED", subject_type="story_asset_state", subject_id=state_id, summary="创建资产状态", metadata={"project_id": project_id, "story_asset_id": story_asset_id, "code": code, "state_kind": state_kind})
        return self._state(state_id)

    def update_state(self, state_id: str, expected_revision: int, *, label: str | None, description: str | None, state_json: dict[str, Any] | None, actor: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"state_id": state_id})
        if int(row["revision"]) != expected_revision:
            raise DomainRuleError("STORY_ASSET_STATE_REVISION_CONFLICT", "资产状态已被修改，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": int(row["revision"])})
        updates: list[str] = []
        params: list[Any] = []
        if label is not None:
            updates.append("label=?")
            params.append(label)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if state_json is not None:
            updates.append("state_json=?")
            params.append(_json(state_json))
        if updates:
            updates.append("updated_at=?")
            params.append(_now())
            params.append(state_id)
            self.connection.execute(f"UPDATE story_asset_states SET {', '.join(updates)},revision=revision+1 WHERE id=?", params)
        return self._state(state_id)

    def list_states(self, story_asset_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM story_asset_states WHERE story_asset_id=? AND status='ACTIVE' ORDER BY code, id",
            (story_asset_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_state(self, state_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM story_asset_states WHERE id=?", (state_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def archive_state(self, state_id: str, expected_revision: int, actor: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"state_id": state_id})
        if int(row["revision"]) != expected_revision:
            raise DomainRuleError("STORY_ASSET_STATE_REVISION_CONFLICT", "资产状态已被修改，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": int(row["revision"])})
        if str(row["status"]) == "ARCHIVED":
            raise DomainRuleError("STORY_ASSET_STATE_ALREADY_ARCHIVED", "该资产状态已归档")
        self.connection.execute("UPDATE story_asset_states SET status='ARCHIVED',updated_at=?,revision=revision+1 WHERE id=?", (_now(), state_id))
        self.audit(actor=actor, role_context="writer", action="STORY_ASSET_STATE_ARCHIVED", subject_type="story_asset_state", subject_id=state_id, summary="归档资产状态", metadata={"project_id": str(row["project_id"])})
        return self._state(state_id)

    def _state(self, state_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"state_id": state_id})
        return _row_to_dict(row)

    # ---- references ------------------------------------------------------

    def add_reference(
        self,
        *,
        project_id: str,
        story_asset_id: str,
        asset_state_id: str | None,
        media_version_id: str,
        reference_kind: str,
        label: str,
        priority: int,
        is_locked: bool,
        yaw_deg: float | None,
        pitch_deg: float | None,
        actor: str,
    ) -> dict[str, Any]:
        reference_id, now = str(uuid.uuid4()), _now()
        try:
            self.connection.execute(
                """INSERT INTO story_asset_references
                (id, project_id, story_asset_id, asset_state_id, media_version_id, reference_kind,
                 label, priority, is_locked, yaw_deg, pitch_deg, metadata_json, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'{}','ACTIVE',?,?,?,1,'v1')""",
                (reference_id, project_id, story_asset_id, asset_state_id, media_version_id, reference_kind, label, priority, 1 if is_locked else 0, yaw_deg, pitch_deg, now, now, actor),
            )
        except sqlite3.IntegrityError as error:
            raise DomainRuleError("STORY_ASSET_REFERENCE_INVALID", "参考图绑定失败：媒体版本不存在或状态不存在", {"media_version_id": media_version_id, "asset_state_id": asset_state_id}) from error
        self.audit(actor=actor, role_context="writer", action="STORY_ASSET_REFERENCE_CREATED", subject_type="story_asset_reference", subject_id=reference_id, summary="添加资产参考图", metadata={"project_id": project_id, "story_asset_id": story_asset_id, "reference_kind": reference_kind, "media_version_id": media_version_id})
        return self._reference(reference_id)

    def update_reference(self, reference_id: str, expected_revision: int, *, label: str | None, priority: int | None, is_locked: bool | None, yaw_deg: float | None, pitch_deg: float | None, actor: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_references WHERE id=?", (reference_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_REFERENCE_NOT_FOUND", "资产参考图不存在", {"reference_id": reference_id})
        if int(row["revision"]) != expected_revision:
            raise DomainRuleError("STORY_ASSET_REFERENCE_REVISION_CONFLICT", "参考图已被修改，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": int(row["revision"])})
        updates: list[str] = []
        params: list[Any] = []
        if label is not None:
            updates.append("label=?")
            params.append(label)
        if priority is not None:
            updates.append("priority=?")
            params.append(priority)
        if is_locked is not None:
            updates.append("is_locked=?")
            params.append(1 if is_locked else 0)
        if yaw_deg is not None:
            updates.append("yaw_deg=?")
            params.append(yaw_deg)
        if pitch_deg is not None:
            updates.append("pitch_deg=?")
            params.append(pitch_deg)
        if updates:
            updates.append("updated_at=?")
            params.append(_now())
            params.append(reference_id)
            self.connection.execute(f"UPDATE story_asset_references SET {', '.join(updates)},revision=revision+1 WHERE id=?", params)
        return self._reference(reference_id)

    def list_references(self, story_asset_id: str, asset_state_id: str | None = None) -> list[dict[str, Any]]:
        clause = " AND asset_state_id=?" if asset_state_id else ""
        params: list[Any] = [story_asset_id]
        if asset_state_id:
            params.append(asset_state_id)
        rows = self.connection.execute(
            f"SELECT * FROM story_asset_references WHERE story_asset_id=? AND status='ACTIVE'{clause} ORDER BY priority, created_at, id",
            params,
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_reference(self, reference_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM story_asset_references WHERE id=?", (reference_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def archive_reference(self, reference_id: str, expected_revision: int, actor: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_references WHERE id=?", (reference_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_REFERENCE_NOT_FOUND", "资产参考图不存在", {"reference_id": reference_id})
        if int(row["revision"]) != expected_revision:
            raise DomainRuleError("STORY_ASSET_REFERENCE_REVISION_CONFLICT", "参考图已被修改，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": int(row["revision"])})
        if str(row["status"]) == "ARCHIVED":
            raise DomainRuleError("STORY_ASSET_REFERENCE_ALREADY_ARCHIVED", "该参考图已归档")
        self.connection.execute("UPDATE story_asset_references SET status='ARCHIVED',updated_at=?,revision=revision+1 WHERE id=?", (_now(), reference_id))
        self.audit(actor=actor, role_context="writer", action="STORY_ASSET_REFERENCE_ARCHIVED", subject_type="story_asset_reference", subject_id=reference_id, summary="归档资产参考图", metadata={"project_id": str(row["project_id"])})
        return self._reference(reference_id)

    def _reference(self, reference_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM story_asset_references WHERE id=?", (reference_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_REFERENCE_NOT_FOUND", "资产参考图不存在", {"reference_id": reference_id})
        return _row_to_dict(row)

    # ---- episode / shot state bindings ------------------------------------

    def set_episode_asset_state(self, *, episode_id: str, story_asset_id: str, asset_state_id: str, actor: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT id FROM episode_asset_state_bindings WHERE episode_id=? AND story_asset_id=?",
            (episode_id, story_asset_id),
        ).fetchone()
        now = _now()
        if row is None:
            binding_id = str(uuid.uuid4())
            self.connection.execute(
                "INSERT INTO episode_asset_state_bindings (id, episode_id, story_asset_id, asset_state_id, created_at, created_by, revision, schema_version) VALUES (?,?,?,?,?,?,1,'v1')",
                (binding_id, episode_id, story_asset_id, asset_state_id, now, actor),
            )
            binding_id_out = binding_id
        else:
            self.connection.execute(
                "UPDATE episode_asset_state_bindings SET asset_state_id=?, created_by=? WHERE id=?",
                (asset_state_id, actor, row["id"]),
            )
            binding_id_out = str(row["id"])
        self.audit(actor=actor, role_context="director", action="EPISODE_ASSET_STATE_SET", subject_type="episode_asset_state_binding", subject_id=binding_id_out, summary="设置本集默认资产状态", metadata={"episode_id": episode_id, "story_asset_id": story_asset_id, "asset_state_id": asset_state_id})
        return {"id": binding_id_out, "episode_id": episode_id, "story_asset_id": story_asset_id, "asset_state_id": asset_state_id}

    def episode_asset_state(self, episode_id: str, story_asset_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT b.*, s.label AS state_label, s.code AS state_code, s.state_kind
            FROM episode_asset_state_bindings b
            JOIN story_asset_states s ON s.id=b.asset_state_id
            WHERE b.episode_id=? AND b.story_asset_id=? AND s.status='ACTIVE'""",
            (episode_id, story_asset_id),
        ).fetchone()
        return dict(row) if row else None

    def set_shot_asset_state(self, *, shot_id: str, asset_id: str, asset_state_id: str, actor: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT id FROM shot_asset_bindings WHERE shot_id=? AND asset_id=? AND role_in_shot='main'",
            (shot_id, asset_id),
        ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_ASSET_BINDING_NOT_FOUND", "镜头尚未绑定该资产，请先绑定", {"shot_id": shot_id, "asset_id": asset_id})
        self.connection.execute("UPDATE shot_asset_bindings SET asset_state_id=? WHERE id=?", (asset_state_id, row["id"]))
        self.audit(actor=actor, role_context="director", action="SHOT_ASSET_STATE_SET", subject_type="shot_asset_binding", subject_id=str(row["id"]), summary="设置镜头资产状态", metadata={"shot_id": shot_id, "asset_id": asset_id, "asset_state_id": asset_state_id})
        return {"id": str(row["id"]), "shot_id": shot_id, "asset_id": asset_id, "asset_state_id": asset_state_id}

    def shot_asset_states(self, shot_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT b.asset_id, b.asset_state_id, s.label AS state_label, s.code AS state_code, s.state_kind
            FROM shot_asset_bindings b
            LEFT JOIN story_asset_states s ON s.id=b.asset_state_id
            WHERE b.shot_id=? ORDER BY b.created_at, b.id""",
            (shot_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def voice_binding(self, asset_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT b.voice_profile_version_id, v.code AS voice_code, v.title AS voice_title, v.status
            FROM character_voice_bindings b
            JOIN voice_profile_versions v ON v.id=b.voice_profile_version_id
            WHERE b.character_asset_id=?""",
            (asset_id,),
        ).fetchone()
        return dict(row) if row else None

    def asset_shots(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT b.id AS binding_id, b.role_in_shot, b.asset_state_id,
            s.id AS shot_id, s.code AS shot_code, s.status AS shot_status,
            s.scene_id, sc.code AS scene_code, sc.title AS scene_title,
            e.id AS episode_id, e.code AS episode_code
            FROM shot_asset_bindings b
            JOIN shots s ON s.id=b.shot_id
            LEFT JOIN scenes sc ON sc.id=s.scene_id
            JOIN episodes e ON e.id=s.episode_id
            WHERE b.asset_id=? ORDER BY b.created_at, b.id""",
            (asset_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---- audit ------------------------------------------------------------

    def audit(self, *, actor: str, role_context: str, action: str, subject_type: str, subject_id: str, summary: str, metadata: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?,?,?,?,?,?,?)",
            (actor, role_context, action, subject_type, subject_id, summary, _json(metadata)),
        )
