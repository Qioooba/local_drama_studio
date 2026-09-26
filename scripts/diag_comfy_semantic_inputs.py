"""Compare a frozen Comfy execution snapshot with its workflow's semantic contract.

The product refused the execution with ``MP_COMFY_EXECUTION_INPUT_INVALID`` and the
persisted error detail is redacted to a human sentence, so the two lists that name
the real mismatch are recomputed here from the snapshot and the published workflow.

    python scripts/diag_comfy_semantic_inputs.py [snapshot_id]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

DB = Path(__file__).resolve().parents[1] / "data" / "local_drama.sqlite3"


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def main() -> int:
    snapshot_id = sys.argv[1] if len(sys.argv) > 1 else None
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    if snapshot_id:
        row = connection.execute("SELECT * FROM mp_execution_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    else:
        row = connection.execute("SELECT * FROM mp_execution_snapshots ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        print("no execution snapshot")
        return 2
    snapshot = dict(row)
    print(f"snapshot {snapshot['id']} adapter={snapshot.get('adapter_code')} capability={snapshot.get('capability_code')}")
    semantic = _json(snapshot.get("semantic_inputs_json")) or {}
    binding = _json(snapshot.get("execution_binding_json")) or {}
    print("semantic_inputs:")
    print(json.dumps(semantic, ensure_ascii=False, indent=2))
    print("execution_binding:")
    print(json.dumps(binding, ensure_ascii=False, indent=2))

    workflow_version_id = binding.get("workflow_version_id") if isinstance(binding, Mapping) else None
    if not workflow_version_id:
        print("binding carries no workflow_version_id")
        return 3
    workflow = connection.execute(
        "SELECT * FROM workflow_versions WHERE id = ?", (workflow_version_id,)
    ).fetchone()
    if workflow is None:
        print(f"workflow version {workflow_version_id} not found")
        return 4
    workflow = dict(workflow)
    contract = _json(workflow.get("contract_json"))
    node_bindings = _json(workflow.get("node_bindings_json"))
    print(f"workflow {workflow_version_id} status={workflow.get('status')} content_hash={workflow.get('content_hash')}")
    print("contract:")
    print(json.dumps(contract, ensure_ascii=False, indent=2)[:4000])
    print("node_bindings:")
    print(json.dumps(node_bindings, ensure_ascii=False, indent=2)[:4000])

    slots = (contract or {}).get("input_slots") if isinstance(contract, Mapping) else None
    slots = slots if isinstance(slots, Mapping) else {}
    node_bindings = node_bindings if isinstance(node_bindings, Mapping) else {}
    unknown = sorted(str(key) for key in semantic if str(key) not in slots or str(key) not in node_bindings)
    missing = sorted(
        str(key)
        for key, spec in slots.items()
        if isinstance(spec, Mapping) and spec.get("required", True) and str(key) not in semantic
    )
    print(f"\nunknown={unknown}")
    print(f"missing={missing}")
    print(f"slots={sorted(slots)}")
    print(f"bindings={sorted(node_bindings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
