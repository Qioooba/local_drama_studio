"""Diagnose why the explainer submit's freshness hash differs from its plan's.

The explainer plans with ``ExecutionPlanningService.preview`` and then submits a
request that re-runs the same preview, comparing ``resolution_hash``.  When the
two disagree the submit is refused with ``MP_EXECUTION_RESOLUTION_STALE`` even
though every input looks identical.  This script records each preview request and
its hash, then prints a per-key diff between the plan-time and submit-time
payloads so the real divergence is visible instead of guessed.

Run with the audit instance environment (no network, no model calls):

    $env:PYTHONPATH="apps/api"; python scripts/diag_explainer_submit_stale.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.model_platform.application import execution_planning as planning  # noqa: E402


def _configure_instance() -> None:
    root = ROOT
    os.environ.setdefault("LOCAL_DRAMA_INSTANCE_ROOT", str(root / ".ui-audit-instance"))
    os.environ.setdefault("LOCAL_DRAMA_DATA_ROOT", str(root / "data"))
    os.environ.setdefault("LOCAL_DRAMA_PROJECTS_ROOT", str(root / "projects"))
    os.environ.setdefault("LOCAL_DRAMA_WORK_ROOT", str(root / "work"))
    os.environ.setdefault("LOCAL_DRAMA_CACHE_ROOT", str(root / "cache"))
    os.environ.setdefault("LOCAL_DRAMA_LOGS_ROOT", str(root / "logs"))
    os.environ.setdefault("LOCAL_DRAMA_BACKUPS_ROOT", str(root / "backups"))
    os.environ.setdefault("LOCAL_DRAMA_NETWORK_MODE", "LOCAL_ONLY")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--beat", required=True)
    parser.add_argument("--mode", default="TEXT_TO_IMAGE")
    parser.add_argument("--purpose", default="KEYFRAME")
    args = parser.parse_args()
    _configure_instance()

    from local_drama.application.explainers.visual_generation import (
        build_explainer_visual_generation_service,
    )
    from local_drama.config import Settings
    from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
    from local_drama.infrastructure.database.sqlite import Database

    settings = Settings.from_env().resolve_dependent_settings()
    database = Database(settings.database_path)
    connection = database.connect()
    repo = ExplainerRepository(connection)
    service = build_explainer_visual_generation_service(repo, database=database, settings=settings)

    recorded: list[dict[str, object]] = []
    hashed: list[dict[str, object]] = []
    submit_started = [False]
    original = planning.ExecutionPlanningService.preview

    def spy_hash(value):  # type: ignore[no-untyped-def]
        result = original_hash(value)
        if isinstance(value, dict) and "capability_code" in value:
            hashed.append({"value": value, "result": result})
        return result

    original_hash = planning._hash
    planning._hash = spy_hash  # type: ignore[assignment]

    def spy(self, request):  # type: ignore[no-untyped-def]
        phase = "plan" if not submit_started[0] else "submit"
        preview = original(self, request)
        record = {
            "phase": phase,
            "capability_code": request.capability_code,
            "explicit_profile": request.execution_profile_version_id,
            "semantic_inputs": dict(request.semantic_inputs),
            "run_overrides": dict(request.run_overrides),
            "expected_resolution_hash": request.expected_resolution_hash,
            "hash": preview.resolution_hash,
            "profile": preview.execution_profile_version_id,
            "assignment_chain": [dict(item) for item in preview.assignment_chain],
        }
        recorded.append(record)
        return preview

    planning.ExecutionPlanningService.preview = spy  # type: ignore[assignment]

    owner = {"kind": "BEAT", "id": args.beat}
    command = {"mode": args.mode, "candidate_count": 1, "purpose": args.purpose}
    plan = service.plan(args.project, owner, command)
    print(f"plan status={plan.status} capability={plan.capability_code} profile={plan.execution_profile_version_id}")
    print(f"plan expected_resolution_hash={plan.expected_resolution_hash}")
    print(f"plan resolution_hash_by_seed={dict(plan.resolution_hash_by_seed)}")
    print(f"plan seeds={list(plan.candidate_seeds)} blockers={[b.get('code') for b in plan.blockers]}")
    plan_records = list(recorded)
    hashed.clear()

    submit_started[0] = True
    try:
        receipt = service.submit(
            args.project,
            owner,
            dict(command),
            idempotency_key="diag-stale-1",
            actor="diag",
        )
        print(f"submit receipt status={receipt.get('status')} accepted={receipt.get('accepted_count')}")
        for item in receipt.get("items") or []:
            print(f"  item ordinal={item.get('ordinal')} seed={item.get('seed')} status={item.get('submission_status')} error={item.get('error')}")
        for blocker in receipt.get("blockers") or []:
            print(f"  blocker {blocker.get('code')}: {blocker.get('message')}")
    except Exception as error:  # noqa: BLE001 - diagnostics
        print(f"submit raised {type(error).__name__}: {getattr(error, 'code', '')} {error}")

    submit_records = list(recorded)

    def key_of(record: dict[str, object]) -> str:
        return json.dumps(
            {"overrides": record["run_overrides"], "semantic": record["semantic_inputs"]},
            sort_keys=True,
            default=str,
        )

    print(f"\npreview calls: plan={len(plan_records)} submit={len(submit_records)}")
    for index, record in enumerate(plan_records + submit_records):
        print(
            f"  [{index}] {'plan  ' if record in plan_records else 'submit'} "
            f"overrides={record['run_overrides']} expected={record['expected_resolution_hash']} actual={record['hash']}"
        )

    # The plan computes one preview without a seed and then one per seed; the
    # submit replans, then replays the per-seed variant.  Compare the repeated
    # requests: an identical request must produce an identical hash.
    groups: dict[str, list[dict[str, object]]] = {}
    for record in plan_records + submit_records:
        groups.setdefault(key_of(record), []).append(record)
    for key, records in groups.items():
        hashes = {str(record["hash"]) for record in records}
        print(f"\nrequest {key[:220]}")
        print(f"  calls={len(records)} distinct_hashes={len(hashes)} -> {sorted(hashes)}")

    print("\nhash-input diffs for the same request (submit phase):")
    seen: dict[str, dict[str, object]] = {}
    for entry in hashed:
        value = entry["value"]
        signature = json.dumps(
            {
                "capability_code": value.get("capability_code"),
                "execution_profile_version_id": value.get("execution_profile_version_id"),
                "semantic_inputs": value.get("semantic_inputs"),
                "run_overrides": value.get("run_overrides"),
            },
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        )
        if signature in seen:
            print(f"  repeated request -> hash {seen[signature]['result']} vs {entry['result']}")
            for field in value:
                if value[field] != seen[signature]["value"].get(field):
                    print(f"    DIFF {field}:\n      first : {json.dumps(seen[signature]['value'].get(field), ensure_ascii=False, sort_keys=True, default=str)[:600]}\n      second: {json.dumps(value[field], ensure_ascii=False, sort_keys=True, default=str)[:600]}")
        else:
            seen[signature] = entry
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
