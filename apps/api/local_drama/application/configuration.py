"""Explicit project bindings for production plans, delivery targets and profiles."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from local_drama.application.delivery_presets import preset_items, require_preset
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contract_hash(value: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _validate_local_target(spec: dict[str, Any]) -> None:
    target_rel = str(spec.get("path_rel", ""))
    if not target_rel or PurePosixPath(target_rel).is_absolute() or ".." in PurePosixPath(target_rel).parts:
        raise DomainRuleError("INVALID_DELIVERY_TARGET", "交付目标必须是项目内相对路径")


def _activate_project_delivery_target(
    connection: Any,
    *,
    project_id: str,
    target_id: str,
    version_id: str,
    now: str,
) -> None:
    """Keep one project-wide current delivery target after every explicit create/select.

    Creating a target is already an explicit user choice. Requiring a second
    activation click left multiple ACTIVE versions and made the read model
    ambiguous, so activation is part of the same domain operation.
    """

    connection.execute(
        "UPDATE delivery_target_versions SET status='RETIRED', updated_at=?, revision=revision+1 "
        "WHERE status='ACTIVE' AND id<>? AND delivery_target_id IN (SELECT id FROM delivery_targets WHERE project_id=?)",
        (now, version_id, project_id),
    )
    connection.execute(
        "UPDATE delivery_target_versions SET status='ACTIVE', updated_at=?, revision=revision+1 WHERE id=? AND status<>'ACTIVE'",
        (now, version_id),
    )
    connection.execute(
        "UPDATE delivery_targets SET status='INACTIVE', updated_at=?, revision=revision+1 WHERE project_id=? AND id<>? AND status<>'INACTIVE'",
        (now, project_id, target_id),
    )
    connection.execute(
        "UPDATE delivery_targets SET status='ACTIVE', updated_at=?, revision=revision+1 WHERE id=? AND status<>'ACTIVE'",
        (now, target_id),
    )


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
            _activate_project_delivery_target(
                connection,
                project_id=project_id,
                target_id=target_id,
                version_id=version_id,
                now=now,
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'DELIVERY_TARGET_CREATED', 'delivery_target', ?, ?, ?)",
                (actor, target_id, "创建并启用本地交付目标", _json({"project_id": project_id, "transport": transport, "auto_selected": True})),
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

    def list_delivery_presets(self) -> dict[str, Any]:
        """List the immutable built-in platform delivery spec presets (G11 P1-6)."""
        return {"items": preset_items()}

    def create_delivery_target_from_preset(self, project_id: str, preset_code: str, title: str, actor: str = "local-user") -> dict[str, Any]:
        """Create a LOCAL_FILESYSTEM delivery target by applying one platform preset.

        Validation, persistence and the base DELIVERY_TARGET_CREATED audit are
        fully reused from ``create_delivery_target``; this method only resolves
        the preset spec and records the preset provenance audit event.
        """
        preset = require_preset(preset_code)
        target = self.create_delivery_target(project_id, preset.code, title, "LOCAL_FILESYSTEM", dict(preset.spec), actor=actor)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'DELIVERY_TARGET_CREATED_FROM_PRESET', 'delivery_target', ?, ?, ?)""",
                (
                    actor,
                    target["id"],
                    f"按平台预设创建交付目标：{preset.title}",
                    _json({"project_id": project_id, "preset_code": preset.code, "title": title}),
                ),
            )
        return {**target, "preset_code": preset.code}

    def create_delivery_target_version(
        self,
        project_id: str,
        target_id: str,
        spec: dict[str, Any],
        *,
        transport: str = "LOCAL_FILESYSTEM",
        title: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Create a new immutable DeliveryTargetVersion without overwriting history.

        A target's stable identity is useful in project configuration while each
        delivery package points at an immutable version.  The previous active
        version is retired, but existing delivery packages continue to resolve
        their original target snapshot.
        """

        if transport != "LOCAL_FILESYSTEM":
            raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许 LOCAL_FILESYSTEM 交付")
        _validate_local_target(spec)
        now = _utc_now()
        version_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            target = connection.execute(
                "SELECT * FROM delivery_targets WHERE id=? AND project_id=?",
                (target_id, project_id),
            ).fetchone()
            if target is None:
                raise DomainRuleError("DELIVERY_TARGET_NOT_FOUND", "交付目标不存在或不属于当前项目")
            if str(target["transport"]) != transport:
                raise DomainRuleError("DELIVERY_TARGET_TRANSPORT_IMMUTABLE", "交付目标 transport 不可跨版本改变")
            latest = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) AS version_no FROM delivery_target_versions WHERE delivery_target_id=?",
                (target_id,),
            ).fetchone()
            next_version = int(latest["version_no"] or 0) + 1
            connection.execute(
                """INSERT INTO delivery_target_versions
                (id, delivery_target_id, version_no, target_spec_json, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, 1, 'v2')""",
                (version_id, target_id, next_version, _json(spec), now, now, actor),
            )
            _activate_project_delivery_target(
                connection,
                project_id=project_id,
                target_id=target_id,
                version_id=version_id,
                now=now,
            )
            if title is not None:
                connection.execute(
                    "UPDATE delivery_targets SET title=?, target_spec_json=?, updated_at=?, revision=revision+1 WHERE id=?",
                    (title.strip(), _json(spec), now, target_id),
                )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'DELIVERY_TARGET_VERSION_CREATED', 'delivery_target_version', ?, ?, ?)""",
                (actor, version_id, "创建本地交付目标新版本", _json({"project_id": project_id, "target_id": target_id, "version_no": next_version, "transport": transport})),
            )
        return {
            "id": target_id,
            "version_id": version_id,
            "project_id": project_id,
            "code": str(target["code"]),
            "title": title.strip() if title is not None else str(target["title"]),
            "transport": transport,
            "spec": spec,
            "version_no": next_version,
            "status": "ACTIVE",
        }

    def select_delivery_target_version(self, project_id: str, version_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Explicitly activate one version of a project delivery target."""

        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT dt.id AS target_id, dt.project_id AS project_id, dt.code AS target_code,
                dt.title AS target_title, dt.transport AS target_transport,
                dtv.id AS version_id, dtv.version_no AS target_version_no,
                dtv.target_spec_json AS version_spec_json, dtv.status AS version_status
                FROM delivery_target_versions dtv JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id
                WHERE dtv.id=? AND dt.project_id=?""",
                (version_id, project_id),
            ).fetchone()
            if row is None:
                raise DomainRuleError("DELIVERY_TARGET_VERSION_NOT_FOUND", "交付目标版本不存在或不属于当前项目")
            # Every selected column has an explicit alias.  Use those aliases
            # rather than relying on positional offsets, because SQLite schema
            # extensions can change the physical table order without changing
            # this read contract.
            target_id = str(row["target_id"])
            target_project_id = str(row["project_id"])
            target_code = str(row["target_code"])
            target_title = str(row["target_title"])
            target_transport = str(row["target_transport"])
            target_version_no = int(row["target_version_no"])
            version_spec_json = str(row["version_spec_json"] or "{}")
            if target_transport != "LOCAL_FILESYSTEM":
                raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许 LOCAL_FILESYSTEM 交付")
            _activate_project_delivery_target(
                connection,
                project_id=target_project_id,
                target_id=target_id,
                version_id=version_id,
                now=now,
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'DELIVERY_TARGET_VERSION_SELECTED', 'delivery_target_version', ?, ?, ?)""",
                (actor, version_id, "显式选择本地交付目标版本", _json({"project_id": target_project_id, "target_id": target_id, "version_no": target_version_no})),
            )
        return {
            "id": target_id,
            "version_id": version_id,
            "project_id": project_id,
            "code": target_code,
            "title": target_title,
            "transport": target_transport,
            "spec": json.loads(version_spec_json),
            "version_no": target_version_no,
            "status": "ACTIVE",
        }

    def bind_profile(
        self, project_id: str, capability: str, profile_version_id: str, confirm_candidate: bool = False, actor: str = "local-user"
    ) -> dict[str, Any]:
        try:
            canonical_capability = normalize_capability(capability)
        except ValueError as error:
            raise DomainRuleError(
                "PROFILE_CAPABILITY_INVALID",
                "绑定 capability 未知或含义不唯一",
                {"capability": capability},
            ) from error
        now = _utc_now()
        with self.database.transaction() as connection:
            profile = connection.execute("SELECT id, capability, status FROM execution_profile_versions WHERE id = ?", (profile_version_id,)).fetchone()
            if profile is None:
                raise DomainRuleError("PROFILE_NOT_FOUND", "Profile 版本不存在")
            try:
                profile_capability = normalize_capability(str(profile["capability"]))
            except ValueError as error:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_INVALID",
                    "Profile 版本记录了未知或含义不唯一的 capability",
                    {"profile_version_id": profile_version_id},
                ) from error
            if profile_capability != canonical_capability:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_MISMATCH",
                    "Profile capability 与绑定键不一致",
                    {"requested": canonical_capability, "profile_capability": profile_capability},
                )
            if profile["status"] != "PUBLISHED" and not confirm_candidate:
                raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能绑定项目；候选 Profile 不会被静默激活")
            if connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            binding_id = str(uuid.uuid4())
            binding_status = "ACTIVE" if profile["status"] == "PUBLISHED" else "SELECTED_CANDIDATE"
            connection.execute(
                "INSERT INTO project_profile_bindings (id, project_id, capability, execution_profile_version_id, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2') ON CONFLICT(project_id, capability) DO UPDATE SET execution_profile_version_id=excluded.execution_profile_version_id, status=excluded.status, updated_at=excluded.updated_at, revision=project_profile_bindings.revision+1",
                (binding_id, project_id, canonical_capability, profile_version_id, binding_status, now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'PROFILE_BOUND', 'project', ?, ?, ?)",
                (actor, project_id, "绑定本地 Profile", _json({"capability": canonical_capability, "profile_version_id": profile_version_id})),
            )
        return {
            "id": binding_id,
            "project_id": project_id,
            "capability": canonical_capability,
            "profile_version_id": profile_version_id,
            "status": binding_status,
            "profile_status": profile["status"],
        }

    def blockers(self, project_id: str) -> list[str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM projects WHERE id=?) AS project_exists,
                EXISTS(SELECT 1 FROM project_profile_bindings WHERE project_id=? AND status IN ('ACTIVE', 'SELECTED_CANDIDATE')) AS profile_bound,
                EXISTS(SELECT 1 FROM project_plan_bindings WHERE project_id=?) AS plan_bound,
                EXISTS(SELECT 1 FROM delivery_targets WHERE project_id=? AND status='ACTIVE') AS delivery_bound""",
                (project_id, project_id, project_id, project_id),
            ).fetchone()
        if row is None or not row["project_exists"]:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        blockers: list[str] = []
        if not row["profile_bound"]:
            blockers.append("PROFILE_NOT_BOUND")
        if not row["plan_bound"]:
            blockers.append("PRODUCTION_PLAN_NOT_BOUND")
        if not row["delivery_bound"]:
            blockers.append("DELIVERY_TARGET_NOT_BOUND")
        return blockers

    def inspect_project_configuration(self, project_id: str) -> dict[str, Any]:
        """Return a read-only project configuration snapshot and switch impact.

        The snapshot is intentionally derived from persisted bindings and job
        snapshots. It never probes a runtime or mutates a binding; historical
        jobs remain tied to the profile version captured at submission time.
        """
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT id, code, title, production_plan_version_id FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            plan = connection.execute(
                """SELECT pp.id AS production_plan_id, pp.code, pp.title,
                ppv.id AS version_id, ppv.version_no, ppv.plan_json, ppv.status
                FROM production_plan_versions ppv
                JOIN production_plans pp ON pp.id=ppv.production_plan_id
                WHERE ppv.id=?""",
                (project["production_plan_version_id"],),
            ).fetchone()
            bindings = connection.execute(
                """SELECT ppb.capability, ppb.status AS binding_status,
                epv.id AS profile_version_id, epv.version_no, epv.status AS profile_status,
                epv.input_contract_json, epv.parameter_schema_json, epv.output_contract_json,
                epv.resource_policy_json, ep.code AS profile_code, ep.title AS profile_title
                FROM project_profile_bindings ppb
                JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
                JOIN execution_profiles ep ON ep.id=epv.execution_profile_id
                WHERE ppb.project_id=? ORDER BY ppb.capability""",
                (project_id,),
            ).fetchall()
            targets = connection.execute(
                """SELECT dt.id AS target_id, dt.code, dt.title, dt.transport, dt.status AS target_status,
                dtv.id AS version_id, dtv.version_no, dtv.target_spec_json, dtv.status AS version_status
                FROM delivery_targets dt JOIN delivery_target_versions dtv ON dtv.delivery_target_id=dt.id
                WHERE dt.project_id=? ORDER BY dt.code, dtv.version_no DESC""",
                (project_id,),
            ).fetchall()
            profile_jobs = connection.execute(
                """SELECT execution_profile_version_id AS version_id, COUNT(*) AS count
                FROM jobs WHERE project_id=? AND execution_profile_version_id IS NOT NULL
                GROUP BY execution_profile_version_id""",
                (project_id,),
            ).fetchall()
            delivery_packages = connection.execute(
                """SELECT dp.target_version_id AS version_id, COUNT(*) AS count
                FROM delivery_packages dp
                JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? GROUP BY dp.target_version_id""",
                (project_id,),
            ).fetchall()
        job_counts = {str(row["version_id"]): int(row["count"]) for row in profile_jobs}
        package_counts = {str(row["version_id"]): int(row["count"]) for row in delivery_packages}
        parsed_plan = json.loads(str(plan["plan_json"])) if plan else None
        profile_items = [
            {
                "capability": str(row["capability"]),
                "binding_status": str(row["binding_status"]),
                "profile_version_id": str(row["profile_version_id"]),
                "profile_code": str(row["profile_code"]),
                "profile_title": str(row["profile_title"]),
                "version_no": int(row["version_no"]),
                "profile_status": str(row["profile_status"]),
                "contract_hash": _contract_hash({
                    "input_contract": json.loads(str(row["input_contract_json"] or "{}")),
                    "parameter_schema": json.loads(str(row["parameter_schema_json"] or "{}")),
                    "output_contract": json.loads(str(row["output_contract_json"] or "{}")),
                    "resource_policy": json.loads(str(row["resource_policy_json"] or "{}")),
                }),
                "frozen_job_count": job_counts.get(str(row["profile_version_id"]), 0),
            }
            for row in bindings
        ]
        target_items = [
            {
                "target_id": str(row["target_id"]),
                "code": str(row["code"]),
                "title": str(row["title"]),
                "transport": str(row["transport"]),
                "target_status": str(row["target_status"]),
                "version_id": str(row["version_id"]),
                "version_no": int(row["version_no"]),
                "version_status": str(row["version_status"]),
                "spec": json.loads(str(row["target_spec_json"] or "{}")),
                "delivery_package_count": package_counts.get(str(row["version_id"]), 0),
            }
            for row in targets
        ]
        active_targets = [item for item in target_items if item["target_status"] == "ACTIVE" and item["version_status"] == "ACTIVE"]
        return {
            "project": {"id": str(project["id"]), "code": str(project["code"]), "title": str(project["title"])},
            "production_plan": ({
                "id": str(plan["production_plan_id"]), "code": str(plan["code"]), "title": str(plan["title"]),
                "version_id": str(plan["version_id"]), "version_no": int(plan["version_no"]),
                "status": str(plan["status"]), "plan": parsed_plan,
            } if plan else None),
            "profile_bindings": profile_items,
            "delivery_targets": target_items,
            "selected_delivery_target_version_id": active_targets[0]["version_id"] if len(active_targets) == 1 else None,
            "impact": {
                "profile_switches_preserve_frozen_jobs": True,
                "profile_frozen_job_counts": {item["profile_version_id"]: item["frozen_job_count"] for item in profile_items},
                "delivery_package_counts": {item["version_id"]: item["delivery_package_count"] for item in target_items},
                "remote_transport_allowed": False,
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
