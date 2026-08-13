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
