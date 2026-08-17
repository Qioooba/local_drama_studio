"""G11 story asset library: characters/scenes/props/costumes with canonical references."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

KINDS = {"CHARACTER", "SCENE", "PROP", "COSTUME"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_project_media(connection: Any, project_id: str, media_version_id: str) -> None:
    row = connection.execute(
        """SELECT 1 FROM media_versions v JOIN media_assets a ON a.id=v.media_asset_id
        WHERE v.id=? AND a.project_id=?""",
        (media_version_id, project_id),
    ).fetchone()
    if row is None:
        raise DomainRuleError(
            "STORY_ASSET_MEDIA_SCOPE_INVALID",
            "canonical 媒体版本必须存在且属于当前项目",
            {"media_version_id": media_version_id, "project_id": project_id},
        )


class StoryAssetService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def create_asset(
        self,
        project_id: str,
        kind: str,
        code: str,
        name: str,
        description: str = "",
        canonical_media_version_id: str | None = None,
        extra: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        code, name = code.strip(), name.strip()
        if kind not in KINDS:
            raise DomainRuleError("STORY_ASSET_KIND_INVALID", "故事资产 kind 必须是 CHARACTER/SCENE/PROP/COSTUME", {"allowed": sorted(KINDS)})
        if not code or not name:
            raise DomainRuleError("STORY_ASSET_FIELDS_REQUIRED", "资产 code 与名称必填")
        if len(code) > 120 or len(name) > 200:
            raise DomainRuleError("STORY_ASSET_FIELDS_REQUIRED", "资产 code 不得超过 120 字符，名称不得超过 200 字符")
        asset_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            if canonical_media_version_id:
                _require_project_media(connection, project_id, canonical_media_version_id)
            try:
                connection.execute(
                    """INSERT INTO story_assets
                    (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                     created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,1,'v2')""",
                    (asset_id, project_id, kind, code, name, description, canonical_media_version_id, _json(extra or {}), now, now, actor),
                )
            except sqlite3.IntegrityError as error:
                raise DomainRuleError("STORY_ASSET_CODE_CONFLICT", "同一项目内的资产 code 已存在") from error
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_ASSET_CREATED','story_asset',?,'创建故事资产卡',?)""",
                (
                    actor,
                    asset_id,
                    _json({"project_id": project_id, "kind": kind, "code": code, "canonical_media_version_id": canonical_media_version_id}),
                ),
            )
        return self.get_asset(asset_id)

    def update_asset(
        self,
        asset_id: str,
        expected_revision: int,
        *,
        name: str | None = None,
        description: str | None = None,
        canonical_media_version_id: str | None = None,
        extra: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError(
                    "STORY_ASSET_REVISION_CONFLICT",
                    "故事资产已被其他操作修改，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": int(row["revision"])},
                )
            project_id = str(row["project_id"])
            if canonical_media_version_id:
                _require_project_media(connection, project_id, canonical_media_version_id)
            updates: list[str] = []
            params: list[Any] = []
            if name is not None:
                name = name.strip()
                if not name:
                    raise DomainRuleError("STORY_ASSET_FIELDS_REQUIRED", "资产名称不能为空")
                updates.append("name=?")
                params.append(name)
            if description is not None:
                updates.append("description=?")
                params.append(description)
            if canonical_media_version_id is not None:
                updates.append("canonical_media_version_id=?")
                params.append(canonical_media_version_id)
            if extra is not None:
                updates.append("extra_json=?")
                params.append(_json(extra))
            if updates:
                updates.append("updated_at=?")
                params.append(now)
                params.append(asset_id)
                connection.execute(f"UPDATE story_assets SET {', '.join(updates)},revision=revision+1 WHERE id=?", params)
            after_revision = expected_revision + (1 if updates else 0)
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_ASSET_UPDATED','story_asset',?,?,?,'更新故事资产卡',?)""",
                (actor, asset_id, expected_revision, after_revision, _json({"project_id": project_id})),
            )
        return self.get_asset(asset_id)

    def list_assets(self, project_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [project_id]
        clause = " AND kind=?" if kind else ""
        if kind:
            params.append(kind)
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            rows = connection.execute(
                f"SELECT * FROM story_assets WHERE project_id=?{clause} ORDER BY kind,code,id",
                params,
            ).fetchall()
        return [self._asset(row) for row in rows]

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
        return self._asset(row)

    def archive_asset(self, asset_id: str, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError(
                    "STORY_ASSET_REVISION_CONFLICT",
                    "故事资产已被其他操作修改，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": int(row["revision"])},
                )
            if str(row["status"]) == "ARCHIVED":
                raise DomainRuleError("STORY_ASSET_ALREADY_ARCHIVED", "该资产已归档")
            connection.execute("UPDATE story_assets SET status='ARCHIVED',updated_at=?,revision=revision+1 WHERE id=?", (now, asset_id))
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_ASSET_ARCHIVED','story_asset',?,?,?,'归档故事资产卡（保留绑定关系）',?)""",
                (actor, asset_id, expected_revision, expected_revision + 1, _json({"project_id": str(row["project_id"]), "kind": str(row["kind"]), "code": str(row["code"])})),
            )
        return self.get_asset(asset_id)

    def bind_asset_to_shot(
        self,
        shot_id: str,
        asset_id: str,
        role_in_shot: str = "main",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        role_in_shot = role_in_shot.strip()
        if not role_in_shot:
            raise DomainRuleError("STORY_ASSET_FIELDS_REQUIRED", "镜头内角色（role_in_shot）不能为空")
        binding_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            shot = connection.execute(
                """SELECT s.id,se.project_id FROM shots s
                JOIN episodes e ON e.id=s.episode_id JOIN seasons se ON se.id=e.season_id WHERE s.id=?""",
                (shot_id,),
            ).fetchone()
            asset = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            if asset is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
            if str(asset["status"]) != "ACTIVE":
                raise DomainRuleError("STORY_ASSET_ARCHIVED", "只有 ACTIVE 资产可以绑定镜头，已归档资产不能新建绑定")
            if str(asset["project_id"]) != str(shot["project_id"]):
                raise DomainRuleError("STORY_ASSET_PROJECT_MISMATCH", "故事资产与镜头必须属于同一项目")
            try:
                connection.execute(
                    """INSERT INTO shot_asset_bindings
                    (id,shot_id,asset_id,role_in_shot,created_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,1,'v2')""",
                    (binding_id, shot_id, asset_id, role_in_shot, now, actor),
                )
            except sqlite3.IntegrityError as error:
                raise DomainRuleError("STORY_ASSET_ALREADY_BOUND", "该镜头已绑定同一资产与角色") from error
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'director','SHOT_ASSET_BOUND','shot_asset_binding',?,'绑定故事资产到镜头',?)""",
                (actor, binding_id, _json({"shot_id": shot_id, "asset_id": asset_id, "role_in_shot": role_in_shot})),
            )
        return next(item for item in self.list_shot_assets(shot_id) if item["binding_id"] == binding_id)

    def unbind_asset_from_shot(self, binding_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT b.*,a.name,a.code,a.kind,a.status,a.canonical_media_version_id
                FROM shot_asset_bindings b JOIN story_assets a ON a.id=b.asset_id WHERE b.id=?""",
                (binding_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("SHOT_ASSET_BINDING_NOT_FOUND", "镜头资产绑定不存在", {"binding_id": binding_id})
            connection.execute("DELETE FROM shot_asset_bindings WHERE id=?", (binding_id,))
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'director','SHOT_ASSET_UNBOUND','shot_asset_binding',?,'解除故事资产与镜头的绑定',?)""",
                (actor, binding_id, _json({"shot_id": str(row["shot_id"]), "asset_id": str(row["asset_id"]), "role_in_shot": str(row["role_in_shot"])})),
            )
        return {"binding_id": binding_id, "unbound": True}

    def list_shot_assets(self, shot_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM shots WHERE id=?", (shot_id,)).fetchone() is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            rows = connection.execute(
                """SELECT b.id,b.shot_id,b.asset_id,b.role_in_shot,b.created_at,b.created_by,
                a.name,a.code,a.kind,a.status,a.canonical_media_version_id
                FROM shot_asset_bindings b JOIN story_assets a ON a.id=b.asset_id
                WHERE b.shot_id=? ORDER BY b.created_at,b.id""",
                (shot_id,),
            ).fetchall()
        return [self._binding_summary(row) for row in rows]

    def list_asset_shots(self, asset_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM story_assets WHERE id=?", (asset_id,)).fetchone() is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
            rows = connection.execute(
                """SELECT b.id AS binding_id,b.role_in_shot,b.created_at,
                s.id AS shot_id,s.code AS shot_code,s.status AS shot_status,
                e.id AS episode_id,e.code AS episode_code
                FROM shot_asset_bindings b
                JOIN shots s ON s.id=b.shot_id JOIN episodes e ON e.id=s.episode_id
                WHERE b.asset_id=? ORDER BY b.created_at,b.id""",
                (asset_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _asset(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["extra"] = json.loads(str(item.pop("extra_json")))
        return item

    @staticmethod
    def _binding_summary(row: Any) -> dict[str, Any]:
        return {
            "binding_id": str(row["id"]),
            "shot_id": str(row["shot_id"]),
            "asset_id": str(row["asset_id"]),
            "name": str(row["name"]),
            "code": str(row["code"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "canonical_media_version_id": str(row["canonical_media_version_id"]) if row["canonical_media_version_id"] else None,
            "role_in_shot": str(row["role_in_shot"]),
            "created_at": str(row["created_at"]),
            "created_by": str(row["created_by"]),
        }
