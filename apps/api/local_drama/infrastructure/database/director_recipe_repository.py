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


def _version(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["recipe"] = json.loads(str(item.pop("recipe_json")))
    item["is_frozen"] = bool(item["is_frozen"])
    return item


class SqliteDirectorRecipeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def project_exists(self, project_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def _audit(self, actor: str, action: str, subject_type: str, subject_id: str, metadata: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'director',?,?,?,?,?)""",
            (actor, action, subject_type, subject_id, action.replace("_", " ").title(), _json(metadata)),
        )

    def _qc_policy_project(self, policy_version_id: str) -> str | None:
        row = self.connection.execute(
            """SELECT s.project_id FROM generation_qc_policy_versions v
            JOIN generation_qc_policy_sets s ON s.id=v.policy_set_id WHERE v.id=?""", (policy_version_id,),
        ).fetchone()
        return str(row["project_id"]) if row else None

    def _assert_qc_scope(self, project_id: str, recipe: dict[str, Any]) -> None:
        policy_version_id = str(recipe["qc_policy_ref"]["policy_version_id"])
        if self._qc_policy_project(policy_version_id) != project_id:
            raise DomainRuleError("DIRECTOR_RECIPE_QC_POLICY_NOT_FOUND", "QC policy version 不存在或不属于当前项目")

    def create_recipe(self, *, project_id: str, code: str, title: str, recipe: dict[str, Any], recipe_hash: str, reason: str, actor: str) -> dict[str, Any]:
        self._assert_qc_scope(project_id, recipe)
        if self.connection.execute("SELECT 1 FROM director_recipes WHERE project_id=? AND code=?", (project_id, code)).fetchone():
            raise DomainRuleError("DIRECTOR_RECIPE_CODE_EXISTS", "Director Recipe code 已存在")
        recipe_id, version_id, now = str(uuid.uuid4()), str(uuid.uuid4()), _now()
        self.connection.execute(
            """INSERT INTO director_recipes
            (id,project_id,code,title,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,'ACTIVE',?,?,?,1,'v1')""", (recipe_id, project_id, code, title, now, now, actor),
        )
        self.connection.execute(
            """INSERT INTO director_recipe_versions
            (id,recipe_id,version_no,recipe_json,recipe_hash,reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,1,?,?,?,1,?,?,'v1')""", (version_id, recipe_id, _json(recipe), recipe_hash, reason, now, actor),
        )
        self._audit(actor, "DIRECTOR_RECIPE_CREATED", "director_recipe", recipe_id, {"project_id": project_id, "version_id": version_id, "recipe_hash": recipe_hash})
        item = self.get_recipe(project_id, recipe_id)
        assert item is not None
        return item

    def create_version(self, *, project_id: str, recipe_id: str, recipe: dict[str, Any], recipe_hash: str, reason: str, actor: str) -> dict[str, Any]:
        owner = self.connection.execute("SELECT project_id FROM director_recipes WHERE id=? AND status='ACTIVE'", (recipe_id,)).fetchone()
        if owner is None or str(owner["project_id"]) != project_id:
            raise DomainRuleError("DIRECTOR_RECIPE_NOT_FOUND", "Director Recipe 不存在或不属于当前项目")
        self._assert_qc_scope(project_id, recipe)
        duplicate = self.connection.execute("SELECT id FROM director_recipe_versions WHERE recipe_id=? AND recipe_hash=?", (recipe_id, recipe_hash)).fetchone()
        if duplicate:
            raise DomainRuleError("DIRECTOR_RECIPE_VERSION_DUPLICATE", "相同内容已存在不可变版本", {"version_id": duplicate["id"]})
        version_no = int(self.connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM director_recipe_versions WHERE recipe_id=?", (recipe_id,)).fetchone()[0])
        version_id, now = str(uuid.uuid4()), _now()
        self.connection.execute(
            """INSERT INTO director_recipe_versions
            (id,recipe_id,version_no,recipe_json,recipe_hash,reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,?,?,?,?,1,?,?,'v1')""", (version_id, recipe_id, version_no, _json(recipe), recipe_hash, reason, now, actor),
        )
        self.connection.execute("UPDATE director_recipes SET updated_at=?,revision=revision+1 WHERE id=?", (now, recipe_id))
        self._audit(actor, "DIRECTOR_RECIPE_VERSION_CREATED", "director_recipe_version", version_id, {"recipe_id": recipe_id, "version_no": version_no, "recipe_hash": recipe_hash})
        return _version(self.connection.execute("SELECT * FROM director_recipe_versions WHERE id=?", (version_id,)).fetchone())

    def bind(self, *, project_id: str, recipe_version_id: str, reason: str, actor: str, expected_revision: int | None) -> dict[str, Any]:
        row = self.connection.execute(
            """SELECT v.id FROM director_recipe_versions v JOIN director_recipes r ON r.id=v.recipe_id
            WHERE v.id=? AND r.project_id=? AND r.status='ACTIVE'""", (recipe_version_id, project_id),
        ).fetchone()
        if row is None:
            raise DomainRuleError("DIRECTOR_RECIPE_VERSION_NOT_FOUND", "Recipe version 不存在或不属于当前项目")
        current = self.connection.execute("SELECT * FROM project_director_recipe_bindings WHERE project_id=?", (project_id,)).fetchone()
        now = _now()
        if current is None:
            if expected_revision is not None:
                raise DomainRuleError("DIRECTOR_RECIPE_BINDING_REVISION_CONFLICT", "项目尚未绑定 Recipe", {"actual_revision": None})
            revision = 1
            self.connection.execute(
                """INSERT INTO project_director_recipe_bindings
                (project_id,recipe_version_id,reason,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,1,'v1')""", (project_id, recipe_version_id, reason, now, now, actor),
            )
        else:
            actual = int(current["revision"])
            if expected_revision != actual:
                raise DomainRuleError("DIRECTOR_RECIPE_BINDING_REVISION_CONFLICT", "Recipe binding 已变化，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": actual})
            revision = actual + 1
            self.connection.execute(
                """UPDATE project_director_recipe_bindings SET recipe_version_id=?,reason=?,updated_at=?,created_by=?,revision=?
                WHERE project_id=?""", (recipe_version_id, reason, now, actor, revision, project_id),
            )
        self._audit(actor, "PROJECT_DIRECTOR_RECIPE_BOUND", "project", project_id, {"recipe_version_id": recipe_version_id, "revision": revision})
        result = self.current_binding(project_id)
        assert result is not None
        return result

    def list_recipes(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id FROM director_recipes WHERE project_id=? ORDER BY code", (project_id,)).fetchall()
        return [item for row in rows if (item := self.get_recipe(project_id, str(row["id"]))) is not None]

    def get_recipe(self, project_id: str, recipe_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM director_recipes WHERE id=? AND project_id=?", (recipe_id, project_id)).fetchone()
        if row is None:
            return None
        versions = self.connection.execute("SELECT * FROM director_recipe_versions WHERE recipe_id=? ORDER BY version_no", (recipe_id,)).fetchall()
        return {**dict(row), "versions": [_version(item) for item in versions]}

    def current_binding(self, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT b.*,v.recipe_id,v.version_no,v.recipe_json,v.recipe_hash,v.is_frozen,v.created_at AS version_created_at,
            r.code,r.title FROM project_director_recipe_bindings b JOIN director_recipe_versions v ON v.id=b.recipe_version_id
            JOIN director_recipes r ON r.id=v.recipe_id WHERE b.project_id=?""", (project_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["recipe"] = json.loads(str(item.pop("recipe_json")))
        item["is_frozen"] = bool(item["is_frozen"])
        return item
