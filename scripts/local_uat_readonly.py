"""Run the local read-only UAT baseline against the already running API.

Only GET requests are issued.  The report is deliberately a baseline: it
records truthful gate states and safety flags but never approves a gate or
creates a job, media record, migration, or runtime request.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "local_drama.sqlite3"


def _production_ids() -> tuple[str, str]:
    with sqlite3.connect(DB_PATH) as connection:
        project = connection.execute("SELECT id FROM projects ORDER BY created_at DESC LIMIT 1").fetchone()
        episode = connection.execute("SELECT id FROM episodes ORDER BY created_at DESC LIMIT 1").fetchone()
    if project is None or episode is None:
        raise RuntimeError("production project or episode is missing")
    return str(project[0]), str(episode[0])


def _get(base_url: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"status_code": response.status, "payload": payload}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return {"status_code": None, "error": str(error)}


def run(base_url: str) -> dict[str, Any]:
    project_id, episode_id = _production_ids()
    paths = {
        "health": "/api/v1/health/ready",
        "system_contract": "/api/v1/system/contract",
        "adapters": "/api/v1/adapters/contracts",
        "capacity": f"/api/v1/capacity/snapshot?project_id={project_id}",
        "g7": f"/api/v1/projects/{project_id}/gates/g7",
        "g8": f"/api/v1/projects/{project_id}/gates/g8?episode_id={episode_id}",
        "g9": f"/api/v1/projects/{project_id}/gates/g9?episode_id={episode_id}",
        "model_compatibility": f"/api/v1/projects/{project_id}/model-compatibility",
    }
    checks: dict[str, Any] = {}
    for name, path in paths.items():
        checks[name] = {"path": path, **_get(base_url, path)}
    successful = all(check.get("status_code") == 200 for check in checks.values())
    safety_flags = []
    for check in checks.values():
        payload = check.get("payload", {})
        for value in (payload.get("readiness"), payload.get("compatibility"), payload.get("registry"), payload.get("snapshot")):
            if isinstance(value, dict):
                safety_flags.append(value.get("runtime_contacted") is False and value.get("network_contacted") is False and value.get("mutated") is False)
    return {
        "schema_version": "g10.local_uat_readonly.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if successful and all(safety_flags) else "IN_PROGRESS",
        "project_id": project_id,
        "episode_id": episode_id,
        "checks": checks,
        "summary": {
            "get_requests": len(paths),
            "successful_gets": sum(check.get("status_code") == 200 for check in checks.values()),
            "safety_assertions": len(safety_flags),
            "safety_passed": all(safety_flags),
        },
        "safety": {"runtime_contacted": False, "network_contacted": False, "mutated": False, "jobs_created": False},
        "interpretation": "Read-only local baseline only; it cannot substitute for G7 license evidence, G8 production media, G9 performance/accessibility UAT, or final release approval.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3210")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "local-uat-readonly-2026-08-14.json")
    args = parser.parse_args()
    result = run(args.base_url)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": output.relative_to(ROOT).as_posix(), "status": result["status"], "summary": result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
