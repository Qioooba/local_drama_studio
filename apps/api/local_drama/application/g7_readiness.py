"""Read-only G7 configuration and LOCAL_ONLY readiness."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _is_loopback_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname.lower() == "localhost"


class G7ReadinessService:
    """Report persisted G7 blockers without probing runtimes or changing state."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})

            runtimes = connection.execute(
                "SELECT id, code, transport, base_url, status FROM local_runtimes ORDER BY code, id"
            ).fetchall()
            remote_runtimes = [
                row
                for row in runtimes
                if row["transport"] not in {"LOOPBACK_HTTP", "LOCAL_PROCESS", "LOCAL_CLI"}
                or (row["transport"] == "LOOPBACK_HTTP" and not _is_loopback_url(row["base_url"]))
            ]
            published_profiles = connection.execute(
                """SELECT id, capability, output_contract_json, resource_policy_json, capability_json
                FROM execution_profile_versions WHERE status='PUBLISHED' ORDER BY capability, id"""
            ).fetchall()
            editor_published = [
                row
                for row in published_profiles
                if bool(__import__("json").loads(str(row["output_contract_json"] or "{}")))
                and bool(__import__("json").loads(str(row["resource_policy_json"] or "{}")))
                and bool(__import__("json").loads(str(row["capability_json"] or "{}")).get("contract_validation_attestation_id"))
            ]
            bindings = connection.execute(
                """SELECT ppb.capability, ppb.status, ppb.execution_profile_version_id, epv.status AS profile_status
                FROM project_profile_bindings ppb
                JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
                WHERE ppb.project_id=? ORDER BY ppb.capability""",
                (project_id,),
            ).fetchall()
            plan = connection.execute(
                """SELECT ppb.production_plan_version_id, ppv.status
                FROM project_plan_bindings ppb
                JOIN production_plan_versions ppv ON ppv.id=ppb.production_plan_version_id
                WHERE ppb.project_id=?""",
                (project_id,),
            ).fetchone()
            targets = connection.execute(
                """SELECT dtv.id, dt.transport, dt.status, dtv.status AS version_status
                FROM delivery_targets dt
                JOIN delivery_target_versions dtv ON dtv.delivery_target_id=dt.id
                WHERE dt.project_id=? ORDER BY dt.code, dtv.version_no DESC""",
                (project_id,),
            ).fetchall()
            active_local_targets = [
                row
                for row in targets
                if row["transport"] == "LOCAL_FILESYSTEM" and row["status"] == "ACTIVE" and row["version_status"] == "ACTIVE"
            ]
            remote_targets = [row for row in targets if row["transport"] != "LOCAL_FILESYSTEM"]

        active_published_bindings = [
            row for row in bindings if row["status"] == "ACTIVE" and row["profile_status"] == "PUBLISHED"
        ]
        checks = [
            {"code": "LOCAL_RUNTIME_REGISTRY", "passed": bool(runtimes), "count": len(runtimes)},
            {"code": "NO_REMOTE_RUNTIME_TRANSPORT", "passed": not remote_runtimes, "count": len(remote_runtimes)},
            {"code": "PUBLISHED_CAPABILITY_PROFILE", "passed": bool(published_profiles), "count": len(published_profiles)},
            {"code": "PROJECT_PRODUCTION_PLAN", "passed": bool(plan and plan["status"] == "ACTIVE")},
            {"code": "PROJECT_PROFILE_BINDING", "passed": bool(active_published_bindings), "count": len(active_published_bindings)},
            {"code": "LOCAL_DELIVERY_TARGET_VERSION", "passed": bool(active_local_targets), "count": len(active_local_targets)},
            {"code": "NO_REMOTE_DELIVERY_TRANSPORT", "passed": not remote_targets, "count": len(remote_targets)},
            # These G7 exit items need dedicated persisted evidence. They remain hard
            # blockers instead of being inferred from the older G3 configuration tables.
            {"code": "PROFILE_EDITOR_TEST_PUBLISH", "passed": bool(editor_published), "count": len(editor_published)},
            {"code": "PROFILE_CAPABILITY_COMPATIBILITY", "passed": False},
            {"code": "ZERO_PUBLIC_NETWORK_E2E", "passed": False},
            {"code": "WORKSPACE_ASSET_AUTHORIZATION", "passed": False},
            {"code": "MODEL_LICENSE_HASH_QUANTIZATION_REPORT", "passed": False},
        ]
        first_blocker = next((item["code"] for item in checks if not item["passed"]), None)
        return {
            "gate": "G7",
            "status": "PASS" if first_blocker is None else "IN_PROGRESS",
            "project_id": project_id,
            "checks": checks,
            "next_required_action": first_blocker,
            "evidence": {
                "runtime_ids": [str(row["id"]) for row in runtimes],
                "published_profile_version_ids": [str(row["id"]) for row in published_profiles],
                "production_plan_version_id": str(plan["production_plan_version_id"]) if plan else None,
                "profile_binding_version_ids": [str(row["execution_profile_version_id"]) for row in active_published_bindings],
                "delivery_target_version_ids": [str(row["id"]) for row in active_local_targets],
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
