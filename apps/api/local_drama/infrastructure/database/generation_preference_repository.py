"""SQLite adapter for generation preference history and resolution."""

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


def _preference(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["settings"] = json.loads(str(item.pop("settings_json")))
    item["is_frozen"] = bool(item["is_frozen"])
    return item


class SqliteGenerationPreferenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def project_exists(self, project_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def owner_project_id(self, owner_type: str, owner_id: str) -> str | None:
        if owner_type == "PROJECT":
            row = self.connection.execute("SELECT id AS project_id FROM projects WHERE id=?", (owner_id,)).fetchone()
        elif owner_type == "EPISODE":
            row = self.connection.execute(
                "SELECT s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?", (owner_id,),
            ).fetchone()
        elif owner_type == "SHOT":
            row = self.connection.execute(
                "SELECT se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons se ON se.id=e.season_id WHERE sh.id=?",
                (owner_id,),
            ).fetchone()
        else:
            return None
        return str(row["project_id"]) if row else None

    def shot_episode_id(self, shot_id: str) -> str | None:
        row = self.connection.execute("SELECT episode_id FROM shots WHERE id=?", (shot_id,)).fetchone()
        return str(row["episode_id"]) if row else None

    def profile(self, profile_version_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT v.id, v.version_no, v.capability, v.status, v.capability_json, p.code, p.title
            FROM execution_profile_versions v JOIN execution_profiles p ON p.id=v.execution_profile_id
            WHERE v.id=?""",
            (profile_version_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        capability_json = json.loads(str(item.pop("capability_json") or "{}"))
        item["resources"] = capability_json.get("resources", {})
        return item

    def auto_profile(self, capability: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT v.id FROM execution_profile_versions v
            JOIN execution_profiles p ON p.id=v.execution_profile_id
            WHERE v.status='PUBLISHED' AND UPPER(v.capability)=?
            ORDER BY v.updated_at DESC, p.code, v.version_no DESC LIMIT 1""",
            (capability.upper(),),
        ).fetchone()
        return self.profile(str(row["id"])) if row else None

    def recent_terminal_attempts(self, profile_version_id: str, *, limit: int) -> dict[str, Any]:
        """Return bounded terminal evidence without assuming an old database has scheduler tables."""
        try:
            job_columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(jobs)").fetchall()}
            attempt_columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(job_attempts)").fetchall()}
            required_jobs = {"id", "execution_profile_version_id", "input_snapshot_json", "channel"}
            required_attempts = {"id", "job_id", "state", "finished_at"}
            if not required_jobs <= job_columns or not required_attempts <= attempt_columns:
                return {"schema_available": False, "items": [], "query_count": 2}
            lease_columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(job_resource_leases)").fetchall()}
            lease_expr = "NULL"
            if {"attempt_id", "resource_key", "acquired_at", "id"} <= lease_columns:
                lease_expr = "(SELECT rl.resource_key FROM job_resource_leases rl WHERE rl.attempt_id=a.id ORDER BY rl.acquired_at DESC,rl.id DESC LIMIT 1)"
            rows = self.connection.execute(
                f"""SELECT a.id AS attempt_id,a.state,a.finished_at,j.input_snapshot_json,j.channel,
                {lease_expr} AS resource_key
                FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE a.state IN ('SUCCEEDED','FAILED') AND a.finished_at IS NOT NULL
                  AND j.execution_profile_version_id=?
                ORDER BY a.finished_at DESC,a.id DESC LIMIT ?""",
                (profile_version_id, max(1, min(limit, 200))),
            ).fetchall()
        except sqlite3.DatabaseError:
            return {"schema_available": False, "items": [], "query_count": 4}
        return {"schema_available": True, "items": [dict(row) for row in rows], "query_count": 4}

    def current_preference(self, project_id: str, owner_type: str, owner_id: str, capability: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT s.id AS preference_set_id, s.project_id, s.owner_type, s.owner_id, s.capability,
            s.status, s.revision, s.current_version_id, v.version_no, v.execution_profile_version_id,
            v.resolution_mode, v.settings_json, v.reason, v.is_frozen, v.created_at, v.created_by
            FROM generation_preference_sets s
            JOIN generation_preference_versions v ON v.id=s.current_version_id
            WHERE s.project_id=? AND s.owner_type=? AND s.owner_id=? AND s.capability=? AND s.status='ACTIVE'""",
            (project_id, owner_type, owner_id, capability),
        ).fetchone()
        return _preference(row) if row else None

    def put_preference(
        self, *, project_id: str, owner_type: str, owner_id: str, capability: str,
        execution_profile_version_id: str | None, resolution_mode: str,
        settings: dict[str, Any], reason: str, actor: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        current = self.connection.execute(
            """SELECT id, revision FROM generation_preference_sets
            WHERE project_id=? AND owner_type=? AND owner_id=? AND capability=?""",
            (project_id, owner_type, owner_id, capability),
        ).fetchone()
        now = _now()
        if current is None:
            if expected_revision is not None:
                raise DomainRuleError("GENERATION_PREFERENCE_REVISION_CONFLICT", "偏好尚不存在，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": None})
            set_id = str(uuid.uuid4())
            revision = 1
            self.connection.execute(
                """INSERT INTO generation_preference_sets
                (id,project_id,owner_type,owner_id,capability,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,NULL,'ACTIVE',?,?,?,?, 'v1')""",
                (set_id, project_id, owner_type, owner_id, capability, now, now, actor, revision),
            )
            version_no = 1
        else:
            set_id = str(current["id"])
            actual_revision = int(current["revision"])
            if expected_revision is None or expected_revision != actual_revision:
                raise DomainRuleError(
                    "GENERATION_PREFERENCE_REVISION_CONFLICT", "生成偏好已变化，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": actual_revision},
                )
            revision = actual_revision + 1
            version_no = int(self.connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM generation_preference_versions WHERE preference_set_id=?", (set_id,),
            ).fetchone()["n"])

        version_id = str(uuid.uuid4())
        self.connection.execute(
            """INSERT INTO generation_preference_versions
            (id,preference_set_id,version_no,execution_profile_version_id,resolution_mode,settings_json,reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,?,?,?,?,?,1,?,?,'v1')""",
            (version_id, set_id, version_no, execution_profile_version_id, resolution_mode, _json(settings), reason, now, actor),
        )
        self.connection.execute(
            "UPDATE generation_preference_sets SET current_version_id=?,status='ACTIVE',updated_at=?,revision=? WHERE id=?",
            (version_id, now, revision, set_id),
        )
        self.connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
            VALUES (?,'director','GENERATION_PREFERENCE_CHANGED','generation_preference_set',?,?,?,?,?)""",
            (actor, set_id, revision - 1 if revision > 1 else None, revision, "更新生成能力偏好", _json({
                "project_id": project_id, "owner_type": owner_type, "owner_id": owner_id,
                "capability": capability, "resolution_mode": resolution_mode,
                "profile_version_id": execution_profile_version_id,
            })),
        )
        result = self.current_preference(project_id, owner_type, owner_id, capability)
        assert result is not None
        return result

    def list_current(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT s.id AS preference_set_id, s.project_id, s.owner_type, s.owner_id, s.capability,
            s.status, s.revision, s.current_version_id, v.version_no, v.execution_profile_version_id,
            v.resolution_mode, v.settings_json, v.reason, v.is_frozen, v.created_at, v.created_by
            FROM generation_preference_sets s JOIN generation_preference_versions v ON v.id=s.current_version_id
            WHERE s.project_id=? AND s.status='ACTIVE'
            ORDER BY s.capability, CASE s.owner_type WHEN 'PROJECT' THEN 1 WHEN 'EPISODE' THEN 2 ELSE 3 END, s.owner_id""",
            (project_id,),
        ).fetchall()
        return [_preference(row) for row in rows]
