"""Explicit project bindings for production plans, delivery targets and profiles."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_local_target(spec: dict[str, Any]) -> None:
    target_rel = str(spec.get("path_rel", ""))
    if not target_rel or PurePosixPath(target_rel).is_absolute() or ".." in PurePosixPath(target_rel).parts:
        raise DomainRuleError("INVALID_DELIVERY_TARGET", "交付目标必须是项目内相对路径")


class ConfigurationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_plan_binding(self, project_id: str, code: str, title: str, plan: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        plan_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        binding_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            if not code.strip() or not title.strip():
                raise DomainRuleError("INVALID_PRODUCTION_PLAN", "ProductionPlan code/title 不能为空")
            connection.execute(
                "INSERT INTO production_plans (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 1, 'v2')",
                (plan_id, code, title, now, now, actor),
            )
            connection.execute(
                "INSERT INTO production_plan_versions (id, production_plan_id, version_no, plan_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?, ?, 1, 'v2')",
                (version_id, plan_id, _json(plan), now, now, actor),
            )
            connection.execute(
                "INSERT INTO project_plan_bindings (id, project_id, production_plan_version_id, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 1, 'v2') ON CONFLICT(project_id) DO UPDATE SET production_plan_version_id=excluded.production_plan_version_id, updated_at=excluded.updated_at, revision=project_plan_bindings.revision+1",
                (binding_id, project_id, version_id, now, now, actor),
            )
            connection.execute(
                "UPDATE projects SET production_plan_version_id = ?, revision = revision + 1, updated_at = ? WHERE id = ?", (version_id, now, project_id)
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'PRODUCTION_PLAN_BOUND', 'project', ?, ?, ?)",
                (actor, project_id, "绑定 ProductionPlan", _json({"production_plan_version_id": version_id})),
            )
        return {"production_plan_id": plan_id, "production_plan_version_id": version_id, "project_id": project_id, "plan": plan}

    def create_delivery_target(self, project_id: str, code: str, title: str, transport: str, spec: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        if transport != "LOCAL_FILESYSTEM":
            raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许 LOCAL_FILESYSTEM 交付")
        _validate_local_target(spec)
        target_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            connection.execute(
                "INSERT INTO delivery_targets (id, project_id, code, title, transport, target_spec_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, 1, 'v2')",
                (target_id, project_id, code, title, transport, _json(spec), now, now, actor),
            )
            connection.execute(
                "INSERT INTO delivery_target_versions (id, delivery_target_id, version_no, target_spec_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?, ?, 1, 'v2')",
                (version_id, target_id, _json(spec), now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'DELIVERY_TARGET_CREATED', 'delivery_target', ?, ?, ?)",
                (actor, target_id, "创建本地交付目标", _json({"project_id": project_id, "transport": transport})),
            )
        return {
            "id": target_id,
            "version_id": version_id,
            "project_id": project_id,
            "code": code,
            "title": title,
            "transport": transport,
            "spec": spec,
            "status": "ACTIVE",
        }

    def bind_profile(
        self, project_id: str, capability: str, profile_version_id: str, confirm_candidate: bool = False, actor: str = "local-user"
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            profile = connection.execute("SELECT id, capability, status FROM execution_profile_versions WHERE id = ?", (profile_version_id,)).fetchone()
            if profile is None:
                raise DomainRuleError("PROFILE_NOT_FOUND", "Profile 版本不存在")
            if profile["status"] != "PUBLISHED" and not confirm_candidate:
                raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能绑定项目；候选 Profile 不会被静默激活")
            if connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            binding_id = str(uuid.uuid4())
            binding_status = "ACTIVE" if profile["status"] == "PUBLISHED" else "SELECTED_CANDIDATE"
            connection.execute(
                "INSERT INTO project_profile_bindings (id, project_id, capability, execution_profile_version_id, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2') ON CONFLICT(project_id, capability) DO UPDATE SET execution_profile_version_id=excluded.execution_profile_version_id, status=excluded.status, updated_at=excluded.updated_at, revision=project_profile_bindings.revision+1",
                (binding_id, project_id, capability, profile_version_id, binding_status, now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'PROFILE_BOUND', 'project', ?, ?, ?)",
                (actor, project_id, "绑定本地 Profile", _json({"capability": capability, "profile_version_id": profile_version_id})),
            )
        return {
            "id": binding_id,
            "project_id": project_id,
            "capability": capability,
            "profile_version_id": profile_version_id,
            "status": binding_status,
            "profile_status": profile["status"],
        }

    def blockers(self, project_id: str) -> list[str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM project_profile_bindings WHERE project_id=? AND status IN ('ACTIVE', 'SELECTED_CANDIDATE')) AS profile_bound,
                EXISTS(SELECT 1 FROM project_plan_bindings WHERE project_id=?) AS plan_bound,
                EXISTS(SELECT 1 FROM delivery_targets WHERE project_id=? AND status='ACTIVE') AS delivery_bound""",
                (project_id, project_id, project_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        blockers: list[str] = []
        if not row["profile_bound"]:
            blockers.append("PROFILE_NOT_BOUND")
        if not row["plan_bound"]:
            blockers.append("PRODUCTION_PLAN_NOT_BOUND")
        if not row["delivery_bound"]:
            blockers.append("DELIVERY_TARGET_NOT_BOUND")
        return blockers
