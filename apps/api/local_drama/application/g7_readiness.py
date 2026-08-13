"""Read-only G7 configuration and LOCAL_ONLY readiness."""

from __future__ import annotations

import hashlib
import ipaddress
import json
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
                if bool(json.loads(str(row["output_contract_json"] or "{}")))
                and bool(json.loads(str(row["resource_policy_json"] or "{}")))
                and bool(json.loads(str(row["capability_json"] or "{}")).get("contract_validation_attestation_id"))
            ]
            compatibility_candidates = connection.execute(
                """SELECT epv.id, epv.input_contract_json, epv.parameter_schema_json,
                epv.output_contract_json, epv.resource_policy_json, epv.capability_json,
                pca.contract_hash
                FROM execution_profile_versions epv
                JOIN profile_compatibility_attestations pca
                ON pca.profile_version_id=epv.id
                OR pca.id=json_extract(epv.capability_json, '$.contract_compatibility_attestation_id')
                WHERE epv.status='PUBLISHED' AND pca.status='PASS'
                AND pca.created_at=(SELECT MAX(p2.created_at) FROM profile_compatibility_attestations p2
                WHERE p2.profile_version_id=epv.id
                OR p2.id=json_extract(epv.capability_json, '$.contract_compatibility_attestation_id'))"""
            ).fetchall()
            compatibility_profiles = []
            for row in compatibility_candidates:
                payload = {
                    "input_contract": json.loads(str(row["input_contract_json"] or "{}")),
                    "parameter_schema": json.loads(str(row["parameter_schema_json"] or "{}")),
                    "output_contract": json.loads(str(row["output_contract_json"] or "{}")),
                    "resource_policy": json.loads(str(row["resource_policy_json"] or "{}")),
                }
                contract_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                if contract_hash == str(row["contract_hash"]):
                    compatibility_profiles.append(row)
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
            network_e2e = connection.execute(
                """SELECT id, status FROM g7_network_e2e_attestations
                WHERE project_id=? ORDER BY created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            workspace_asset = connection.execute(
                """SELECT waa.id, bk.id AS brand_kit_id FROM workspace_asset_authorizations waa
                JOIN brand_kits bk ON bk.project_id=waa.project_id AND bk.status='ACTIVE'
                WHERE waa.project_id=? AND waa.authorization_status='AUTHORIZED'
                AND waa.license_status='LOCAL_PROJECT_AUTHORIZED'
                ORDER BY waa.updated_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            model_report = connection.execute(
                """SELECT id FROM model_compatibility_reports WHERE report_status='PASS'
                AND license_status IN ('LOCAL_LICENSE_VERIFIED','USER_OWNED') ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()

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
            {"code": "PROFILE_CAPABILITY_COMPATIBILITY", "passed": bool(compatibility_profiles), "count": len(compatibility_profiles)},
            {"code": "ZERO_PUBLIC_NETWORK_E2E", "passed": bool(network_e2e and network_e2e["status"] == "PASS")},
            {"code": "WORKSPACE_ASSET_AUTHORIZATION", "passed": bool(workspace_asset)},
            {"code": "MODEL_LICENSE_HASH_QUANTIZATION_REPORT", "passed": bool(model_report)},
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
                "network_e2e_attestation_id": str(network_e2e["id"]) if network_e2e else None,
                "workspace_asset_authorization_id": str(workspace_asset["id"]) if workspace_asset else None,
                "brand_kit_id": str(workspace_asset["brand_kit_id"]) if workspace_asset else None,
                "model_compatibility_report_id": str(model_report["id"]) if model_report else None,
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
