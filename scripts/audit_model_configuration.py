"""模型配置审计：每个能力是否有可执行 Profile、参数合同是否可用、工作流/运行模型是否绑定、模型文件是否在位。

用法：python scripts/audit_model_configuration.py
产物：artifacts/model-config-audit.json
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = r"data\local_drama.sqlite3"
COMFY_MODELS = Path(r"F:\AI_Models\LocalDramaStudio\ComfyUI")
MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio")


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    capabilities = con.execute("select id, code, title, family from mp_capability_definitions order by code").fetchall()
    profiles: dict[str, list[dict]] = {}
    for row in con.execute(
        """select v.id, v.capability_definition_id, v.version_no, v.workflow_version_id, v.parameter_contract_version_id,
                  c.schema_json, v.payload_json
             from mp_execution_profile_versions v
             left join mp_parameter_contract_versions c on c.id = v.parameter_contract_version_id"""
    ):
        schema = json.loads(row["schema_json"] or "{}")
        props = sorted((schema.get("properties") or {}).keys())
        payload = json.loads(row["payload_json"] or "{}")
        profiles.setdefault(str(row["capability_definition_id"]), []).append(
            {
                "profile_version_id": str(row["id"]),
                "version_no": row["version_no"],
                "workflow_version_id": row["workflow_version_id"],
                "contract_properties": props,
                "contract_is_empty": not props,
                "allowed_override_fields": payload.get("allowed_override_fields") or [],
            }
        )
    bindings: dict[str, list[dict]] = {}
    for row in con.execute(
        "select capability_definition_id, workflow_version_id, binding_status from mp_runtime_model_workflow_bindings"
    ):
        bindings.setdefault(str(row["capability_definition_id"]), []).append(
            {"workflow_version_id": str(row["workflow_version_id"]), "status": row["binding_status"]}
        )

    report = []
    for capability in capabilities:
        cid = str(capability["id"])
        rows = profiles.get(cid, [])
        report.append(
            {
                "capability": capability["code"],
                "title": capability["title"],
                "family": capability["family"],
                "profiles": rows,
                "workflow_bindings": bindings.get(cid, []),
                "usable": bool(rows) and any(not item["contract_is_empty"] for item in rows),
            }
        )

    files = []
    for pattern in ("diffusion_models/**/*.safetensors", "diffusion_models/**/*.gguf", "loras/**/*.safetensors", "text_encoders/**/*.safetensors", "vae/**/*.safetensors"):
        for path in COMFY_MODELS.glob(pattern):
            files.append({"path": str(path.relative_to(COMFY_MODELS)), "gb": round(path.stat().st_size / 1e9, 2)})
    for path in MODEL_ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".onnx", ".pth", ".param", ".bin"} and path.stat().st_size > 1_000_000:
            files.append({"path": str(path.relative_to(MODEL_ROOT)), "gb": round(path.stat().st_size / 1e9, 2)})

    payload = {
        "capabilities": report,
        "model_files": sorted(files, key=lambda item: -item["gb"])[:40],
        "summary": {
            "capabilities_total": len(report),
            "capabilities_with_profile": sum(1 for item in report if item["profiles"]),
            "capabilities_usable": sum(1 for item in report if item["usable"]),
            "capabilities_with_binding": sum(1 for item in report if item["workflow_bindings"]),
            "profiles_with_empty_contract": sum(
                1 for item in report for profile in item["profiles"] if profile["contract_is_empty"]
            ),
        },
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/model-config-audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("summary:", json.dumps(payload["summary"], ensure_ascii=False))
    print()
    print(f"{'capability':26} {'profiles':8} {'emptyContract':13} {'bound':6} usable")
    for item in report:
        empty = sum(1 for profile in item["profiles"] if profile["contract_is_empty"])
        print(
            f"{item['capability']:26} {len(item['profiles']):<8} {empty:<13} {len(item['workflow_bindings']):<6} {'YES' if item['usable'] else 'NO'}"
        )
    print()
    print("top model files:")
    for item in payload["model_files"][:12]:
        print(f"  {item['gb']:>6} GB  {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
