"""Dump the V2 Profile / binding / runtime rows for one capability code."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "local_drama.sqlite3"


def _loads(data: dict) -> dict:
    for key in list(data):
        if key.endswith("_json") and data[key]:
            try:
                data[key] = json.loads(data[key])
            except (TypeError, ValueError):
                pass
    return data


def main() -> int:
    capability = (sys.argv[1] if len(sys.argv) > 1 else "IMAGE_CONCEPT").upper()
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    definition = connection.execute(
        "SELECT * FROM mp_capability_definitions WHERE UPPER(code) = ?", (capability,)
    ).fetchone()
    if definition is None:
        print(f"no capability definition for {capability}")
        return 2
    capability_id = definition["id"]
    print("--- capability definition")
    print(dict(definition))
    print("--- bindings")
    for row in connection.execute(
        "SELECT * FROM mp_runtime_model_workflow_bindings WHERE capability_definition_id = ?", (capability_id,)
    ):
        print(json.dumps(_loads(dict(row)), ensure_ascii=False, indent=2))
    print("--- profiles")
    for row in connection.execute(
        "SELECT p.*, a.runtime_kind AS runtime_kind, a.adapter_code AS adapter_code, "
        "a.version_no AS adapter_version, a.content_hash AS adapter_hash, "
        "a.binding_json AS adapter_binding_json, "
        "r.code AS resource_policy_code, r.policy_json AS resource_policy_json, "
        "v.fingerprint AS runtime_fingerprint, v.status AS runtime_status, "
        "v.adapter_code AS runtime_adapter_code, v.configuration_json AS runtime_configuration_json, "
        "i.code AS runtime_code, i.kind AS runtime_kind_installation, i.display_name AS runtime_display_name, "
        "m.native_locator AS runtime_native_locator, m.install_state AS runtime_install_state, "
        "m.metadata_json AS runtime_metadata_json "
        "FROM mp_execution_profile_versions p "
        "LEFT JOIN mp_adapter_binding_contract_versions a ON a.id = p.adapter_binding_contract_version_id "
        "LEFT JOIN mp_resource_policy_versions r ON r.id = p.resource_policy_version_id "
        "LEFT JOIN mp_runtime_installation_versions v ON v.id = p.runtime_installation_version_id "
        "LEFT JOIN mp_runtime_installations i ON i.id = v.runtime_installation_id "
        "LEFT JOIN mp_runtime_model_installations m ON m.runtime_installation_version_id = v.id "
        "WHERE p.capability_definition_id = ?",
        (capability_id,),
    ):
        print(json.dumps(_loads(dict(row)), ensure_ascii=False, indent=2))
    print("--- parameter contracts")
    for row in connection.execute(
        "SELECT * FROM mp_parameter_contract_versions WHERE capability_definition_id = ?", (capability_id,)
    ):
        print(json.dumps(_loads(dict(row)), ensure_ascii=False, indent=2))
    print("--- assignments")
    for row in connection.execute(
        "SELECT * FROM mp_capability_assignments WHERE capability_definition_id = ?", (capability_id,)
    ):
        print(json.dumps(_loads(dict(row)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
