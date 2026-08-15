"""Project-scoped asset authorization and immutable BrandKit versions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class WorkspaceAssetService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def authorize_media_version(self, project_id: str, media_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT mv.*, ma.project_id, ma.media_kind, ma.purpose, p.root_rel
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                JOIN projects p ON p.id=ma.project_id WHERE mv.id=?""",
                (media_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "媒体版本不存在")
        if str(row["project_id"]) != project_id:
            raise DomainRuleError("WORKSPACE_ASSET_PROJECT_MISMATCH", "工作区资产必须属于当前项目")
        if row["integrity_status"] != "VERIFIED":
            raise DomainRuleError("WORKSPACE_ASSET_NOT_VERIFIED", "只有 VERIFIED 媒体版本可以授权")
        project_root = (self.settings.projects_root / str(row["root_rel"])).resolve()
        path = (project_root / str(row["rel_path"])).resolve()
        if not path.is_relative_to(project_root) or path.is_symlink() or not path.is_file():
            raise DomainRuleError("WORKSPACE_ASSET_PATH_INVALID", "工作区资产路径越界、缺失或为 symlink")
        digest, size = _hash_file(path)
        if digest != str(row["sha256"]) or size != int(row["byte_size"]):
            raise DomainRuleError("WORKSPACE_ASSET_INTEGRITY_FAILED", "资产内容与登记 hash/size 不一致")
        now = _utc_now()
        authorization_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT * FROM workspace_asset_authorizations WHERE project_id=? AND media_version_id=?",
                (project_id, media_version_id),
            ).fetchone()
            if prior is not None:
                return {**dict(prior), "duplicate": True}
            connection.execute(
                """INSERT INTO workspace_asset_authorizations
                (id, project_id, media_version_id, asset_kind, path_rel, sha256, byte_size,
                authorization_status, license_status, details_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'AUTHORIZED', 'LOCAL_PROJECT_AUTHORIZED', ?, ?, ?, ?, 1, 'v2')""",
                (authorization_id, project_id, media_version_id, str(row["media_kind"]), str(row["rel_path"]), digest, size,
                 _json({"purpose": row["purpose"], "source": "project_local_media"}), now, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'WORKSPACE_ASSET_AUTHORIZED', 'media_version', ?, ?, ?)""",
                (actor, media_version_id, "授权项目工作区媒体资产", _json({"authorization_id": authorization_id, "sha256": digest, "byte_size": size})),
            )
        return {"id": authorization_id, "project_id": project_id, "media_version_id": media_version_id,
                "asset_kind": str(row["media_kind"]), "path_rel": str(row["rel_path"]), "sha256": digest,
                "byte_size": size, "authorization_status": "AUTHORIZED", "license_status": "LOCAL_PROJECT_AUTHORIZED", "duplicate": False}

    @staticmethod
    def _grant_impact(row: dict[str, Any]) -> list[str]:
        impact: list[str] = []
        if str(row.get("authorization_status")) != "AUTHORIZED":
            impact.append("SOURCE_AUTHORIZATION_REVOKED")
        if row.get("source_revision") is not None and int(row.get("source_revision") or 0) != int(row.get("authorization_revision") or 0):
            impact.append("SOURCE_AUTHORIZATION_REVISION_CHANGED")
        if row.get("source_sha256") is not None and (str(row.get("source_sha256")) != str(row.get("authorization_sha256")) or int(row.get("source_byte_size") or 0) != int(row.get("authorization_byte_size") or 0)):
            impact.append("SOURCE_CONTENT_CHANGED")
        if row.get("media_sha256") is not None and (str(row.get("media_sha256")) != str(row.get("authorization_sha256")) or int(row.get("media_byte_size") or 0) != int(row.get("authorization_byte_size") or 0)):
            impact.append("SOURCE_CONTENT_CHANGED")
        return impact

    def list_grant_candidates(self, target_project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (target_project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "目标项目不存在")
            rows = connection.execute(
                """SELECT waa.id AS authorization_id, waa.project_id AS source_project_id,
                source.code AS source_project_code, source.title AS source_project_title,
                waa.media_version_id, waa.asset_kind, waa.path_rel, waa.sha256 AS authorization_sha256,
                waa.byte_size AS authorization_byte_size, waa.authorization_status,
                waa.license_status, waa.revision AS authorization_revision,
                mv.version_no, mv.stage, mv.integrity_status, mv.sha256 AS media_sha256, mv.byte_size AS media_byte_size,
                EXISTS(SELECT 1 FROM project_asset_grants pag WHERE pag.target_project_id=? AND pag.media_version_id=mv.id AND pag.status='ACTIVE') AS already_granted
                FROM workspace_asset_authorizations waa
                JOIN projects source ON source.id=waa.project_id
                JOIN media_versions mv ON mv.id=waa.media_version_id
                WHERE waa.project_id<>? ORDER BY source.code, waa.path_rel, waa.created_at DESC""",
                (target_project_id, target_project_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["impact"] = self._grant_impact(item)
            item["grantable"] = not item["already_granted"] and not item["impact"] and item["integrity_status"] == "VERIFIED"
            result.append(item)
        return result

    def revoke_authorization(self, project_id: str, media_version_id: str, reason: str, actor: str = "local-user") -> dict[str, Any]:
        if not reason.strip():
            raise DomainRuleError("WORKSPACE_ASSET_WITHDRAWAL_REASON_REQUIRED", "撤回源资产授权必须填写原因")
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM workspace_asset_authorizations WHERE project_id=? AND media_version_id=?", (project_id, media_version_id)).fetchone()
            if row is None:
                raise DomainRuleError("WORKSPACE_ASSET_AUTHORIZATION_NOT_FOUND", "项目资产授权不存在")
            if str(row["authorization_status"]) == "REVOKED":
                return {**dict(row), "duplicate": True}
            connection.execute("UPDATE workspace_asset_authorizations SET authorization_status='REVOKED', details_json=?, updated_at=?, revision=revision+1 WHERE id=?", (_json({"withdrawal_reason": reason.strip(), "previous_details": json.loads(str(row["details_json"]))}), now, str(row["id"])))
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'WORKSPACE_ASSET_AUTHORIZATION_REVOKED', 'media_version', ?, ?, ?)""",
                (actor, media_version_id, "撤回项目工作区资产授权", _json({"project_id": project_id, "reason": reason.strip()})),
            )
            updated = connection.execute("SELECT * FROM workspace_asset_authorizations WHERE id=?", (str(row["id"]),)).fetchone()
        return {**dict(updated), "duplicate": False}

    def list_grants(self, target_project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (target_project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "目标项目不存在")
            rows = connection.execute(
                """SELECT pag.*, source.code AS source_project_code, source.title AS source_project_title,
                waa.authorization_status, waa.revision AS authorization_revision,
                waa.sha256 AS authorization_sha256, waa.byte_size AS authorization_byte_size,
                mv.integrity_status, mv.sha256 AS media_sha256, mv.byte_size AS media_byte_size
                FROM project_asset_grants pag
                JOIN projects source ON source.id=pag.source_project_id
                JOIN workspace_asset_authorizations waa ON waa.id=pag.source_authorization_id
                JOIN media_versions mv ON mv.id=pag.media_version_id
                WHERE pag.target_project_id=? ORDER BY pag.created_at DESC""",
                (target_project_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["impact"] = self._grant_impact(item)
            item["usable"] = item["status"] == "ACTIVE" and not item["impact"] and item["integrity_status"] == "VERIFIED"
            result.append(item)
        return result

    def create_grant(self, target_project_id: str, authorization_id: str, access_mode: str, actor: str = "local-user") -> dict[str, Any]:
        if access_mode not in {"READ_ONLY", "DERIVED"}:
            raise DomainRuleError("ASSET_GRANT_MODE_INVALID", "资产授权模式必须是 READ_ONLY 或 DERIVED")
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (target_project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "目标项目不存在")
            row = connection.execute(
                """SELECT waa.*, mv.integrity_status, mv.sha256 AS media_sha256, mv.byte_size AS media_byte_size
                FROM workspace_asset_authorizations waa JOIN media_versions mv ON mv.id=waa.media_version_id
                WHERE waa.id=?""",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("ASSET_AUTHORIZATION_NOT_FOUND", "源资产授权不存在")
        source_project_id = str(row["project_id"])
        if source_project_id == target_project_id:
            raise DomainRuleError("ASSET_GRANT_SAME_PROJECT", "同项目资产不需要跨项目授权")
        if str(row["authorization_status"]) != "AUTHORIZED":
            raise DomainRuleError("ASSET_GRANT_SOURCE_REVOKED", "源资产授权已撤回")
        if str(row["integrity_status"]) != "VERIFIED" or str(row["sha256"]) != str(row["media_sha256"]) or int(row["byte_size"]) != int(row["media_byte_size"]):
            raise DomainRuleError("ASSET_GRANT_SOURCE_INVALID", "源资产完整性或授权快照已失效")
        now = _utc_now()
        grant_id = str(uuid.uuid4())
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO project_asset_grants
                    (id, source_project_id, target_project_id, source_authorization_id, media_version_id,
                     source_revision, source_sha256, source_byte_size, access_mode, status,
                     created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, 1, 'v1')""",
                    (grant_id, source_project_id, target_project_id, authorization_id, str(row["media_version_id"]), int(row["revision"]), str(row["sha256"]), int(row["byte_size"]), access_mode, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES (?, 'producer', 'PROJECT_ASSET_GRANTED', 'project_asset_grant', ?, ?, ?)""",
                    (actor, grant_id, "创建跨项目工作区资产授权", _json({"source_project_id": source_project_id, "target_project_id": target_project_id, "media_version_id": row["media_version_id"], "access_mode": access_mode})),
                )
        except Exception as error:
            if "UNIQUE" in str(error).upper():
                with self.database.connect() as connection:
                    prior = connection.execute("SELECT * FROM project_asset_grants WHERE target_project_id=? AND media_version_id=?", (target_project_id, row["media_version_id"])).fetchone()
                if prior is not None:
                    return {**dict(prior), "duplicate": True, "impact": []}
            raise
        return {"id": grant_id, "source_project_id": source_project_id, "target_project_id": target_project_id, "source_authorization_id": authorization_id, "media_version_id": str(row["media_version_id"]), "source_revision": int(row["revision"]), "source_sha256": str(row["sha256"]), "source_byte_size": int(row["byte_size"]), "access_mode": access_mode, "status": "ACTIVE", "duplicate": False, "impact": [], "usable": True}

    def revoke_grant(self, grant_id: str, reason: str, actor: str = "local-user") -> dict[str, Any]:
        if not reason.strip():
            raise DomainRuleError("ASSET_GRANT_WITHDRAWAL_REASON_REQUIRED", "撤回资产授权必须填写原因")
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM project_asset_grants WHERE id=?", (grant_id,)).fetchone()
            if row is None:
                raise DomainRuleError("ASSET_GRANT_NOT_FOUND", "资产授权不存在")
            if row["status"] == "REVOKED":
                return {**dict(row), "duplicate": True}
            connection.execute("UPDATE project_asset_grants SET status='REVOKED', withdrawal_reason=?, updated_at=?, revision=revision+1 WHERE id=?", (reason.strip(), now, grant_id))
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'PROJECT_ASSET_GRANT_REVOKED', 'project_asset_grant', ?, ?, ?)""",
                (actor, grant_id, "撤回跨项目工作区资产授权", _json({"reason": reason.strip()})),
            )
            updated = connection.execute("SELECT * FROM project_asset_grants WHERE id=?", (grant_id,)).fetchone()
        return {**dict(updated), "duplicate": False}

    def create_brand_kit(self, project_id: str, code: str, title: str, tokens: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        if not code.strip() or not title.strip() or not isinstance(tokens, dict) or not tokens:
            raise DomainRuleError("INVALID_BRAND_KIT", "BrandKit 必须包含 code、title 和非空 tokens")
        allowed = {"colors", "typography", "spacing", "radii", "motion", "iconography"}
        if not set(tokens).issubset(allowed):
            raise DomainRuleError("INVALID_BRAND_KIT", "BrandKit tokens 含未支持的分区")
        now = _utc_now()
        kit_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            version = connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM brand_kits WHERE project_id=? AND code=?", (project_id, code)).fetchone()[0]
            connection.execute("UPDATE brand_kits SET status='RETIRED', updated_at=? WHERE project_id=? AND code=? AND status='ACTIVE'", (now, project_id, code))
            connection.execute(
                """INSERT INTO brand_kits (id, project_id, code, title, version_no, tokens_json, status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, 1, 'v2')""",
                (kit_id, project_id, code, title, int(version), _json(tokens), now, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'BRAND_KIT_PUBLISHED', 'brand_kit', ?, ?, ?)""",
                (actor, kit_id, "发布项目 BrandKit 版本", _json({"project_id": project_id, "version_no": int(version)})),
            )
        return {"id": kit_id, "project_id": project_id, "code": code, "title": title, "version_no": int(version), "tokens": tokens, "status": "ACTIVE"}
