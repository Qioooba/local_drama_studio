"""把已发布 Comfy Profile 的空参数合同按工作流绑定重建（可回滚的开发实例修复）。

为什么需要：`comfy_workflow_profiles._ensure_contracts()` 过去硬编码空合同，已经发布到实例里的
Profile 不会自动重算，于是“提示词提交必被 MP_PARAMETER_UNKNOWN 拒绝”依旧存在。本脚本用**同一段
派生逻辑**为每个已存在的 Comfy Profile 生成新合同并重新指向它，同时把可覆盖字段（seed/steps/…）
写回 payload。旧合同行保留，回滚只需把 profile 指回原 id。

    python scripts/repair_comfy_profile_contracts.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.config import Settings  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402
from local_drama.model_platform.application.comfy_workflow_profiles import (  # noqa: E402
    ComfyWorkflowProfileService,
)
from local_drama.application.workflows import WorkflowService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    database = Database(settings.database_path)
    workflows = WorkflowService(database, settings)
    service = ComfyWorkflowProfileService(database, settings, workflows=workflows)

    with database.connect() as connection:
        rows = connection.execute(
            """SELECT v.id, v.capability_definition_id, d.code AS capability, v.workflow_version_id,
                      v.parameter_contract_version_id, v.payload_json
                 FROM mp_execution_profile_versions v
                 JOIN mp_capability_definitions d ON d.id = v.capability_definition_id
                WHERE v.workflow_version_id IS NOT NULL"""
        ).fetchall()

    plan = []
    for row in rows:
        workflow = workflows.get_version(str(row["workflow_version_id"]))
        bindings = workflow.get("node_bindings") or {}
        contracts = service._ensure_contracts(str(row["capability_definition_id"]), bindings)  # noqa: SLF001 - the repair uses the product's own derivation
        payload = json.loads(row["payload_json"] or "{}")
        plan.append(
            {
                "profile_version_id": str(row["id"]),
                "capability": row["capability"],
                "old_contract": str(row["parameter_contract_version_id"]),
                "new_contract": contracts["parameter_contract_version_id"],
                "inputs": sorted(bindings),
                "allowed_override_fields": contracts["allowed_override_fields"],
                "previous_override_fields": payload.get("allowed_override_fields") or [],
            }
        )
        if args.apply:
            payload["allowed_override_fields"] = contracts["allowed_override_fields"]
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE mp_execution_profile_versions SET parameter_contract_version_id=?, payload_json=? WHERE id=?",
                    (contracts["parameter_contract_version_id"], json.dumps(payload, ensure_ascii=False), str(row["id"])),
                )

    for item in plan:
        print(
            f"{item['capability']:16} {item['profile_version_id'][:8]} contract {item['old_contract'][:8]} -> {item['new_contract'][:8]} "
            f"inputs={len(item['inputs'])} overrides={item['allowed_override_fields']}"
        )
    print(f"\nprofiles: {len(plan)} | applied: {args.apply}")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/comfy-profile-contract-repair.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
