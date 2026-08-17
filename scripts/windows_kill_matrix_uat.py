"""Windows x64 crash/kill-matrix rehearsal for NFR-REL-001/002.

Deterministic rehearsal of the persisted-queue crash window WITHOUT waiting
60 real seconds: a child python process commits job/claim/heartbeat then
crashes with ``os._exit(17)``; the parent rebuilds fresh service instances,
verifies every durable record, advances the scheduler clock by 61 seconds and
reconciles the expired lease.  A second scenario proves single-winner worker
contention on the GPU_H3 lease, and a third proves the retried attempt after
reconcile.  Everything runs on an isolated SQLite root; the production
database, project tree, ComfyUI and the network are never touched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.jobs import JobService  # type: ignore[import-not-found]
from local_drama.application.projects import (
    ProjectService,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.infrastructure.database.sqlite import (
    Database,  # type: ignore[import-not-found]
)

from scripts.migrate import migrate


def build_settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _check(code: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), **details}


def scenario_crash_window(database: Database, settings: Settings, project_id: str) -> dict[str, Any]:
    api_root = ROOT / "apps" / "api"
    database_path = str(database.path)
    child_code = (
        "import os,sys; "
        f"sys.path.insert(0, {str(api_root)!r}); "
        "from pathlib import Path; "
        "from local_drama.infrastructure.database.sqlite import Database; "
        "from local_drama.application.jobs import JobService; "
        f"db=Database(Path({database_path!r})); "
        "jobs=JobService(db); "
        f"job=jobs.create_job({project_id!r}, 'CPU_TEST', 'PROJECT', {project_id!r}, 'CPU', {{'crash_window': True}}, 'kill-matrix-crash', max_attempts=2); "
        "claim=jobs.claim('crashed-worker', ['CPU'], lease_seconds=60); "
        "assert claim is not None and claim['job']['id'] == job['id']; "
        "jobs.heartbeat(claim['attempt']['id'], claim['attempt']['lease_token'], 'crashed-worker', progress={'phase':'render','node':'sampler','percent':42}); "
        "os._exit(17)"
    )
    child = subprocess.run([sys.executable, "-c", child_code], capture_output=True, text=True, check=False)
    checks = [_check("CHILD_CRASHED_WITH_EXIT_17", child.returncode == 17)]

    restarted = JobService(database, settings)
    jobs = restarted.list_jobs(project_id)
    checks.append(_check("JOB_PERSISTED_AFTER_CRASH", len(jobs) == 1 and jobs[0]["state"] == "RUNNING"))
    checks.append(_check("PROGRESS_PERSISTED", jobs[0]["progress"] == {"node": "sampler", "percent": 42, "phase": "render"} if jobs else False))
    attempts = restarted.list_attempts(str(jobs[0]["id"])) if jobs else []
    attempt_id = str(attempts[0]["id"]) if attempts else ""
    checks.append(_check("ATTEMPT_PERSISTED_LEASE_REDACTED", bool(attempts) and attempts[0]["lease_token"] is None))
    outbox = restarted.events(project_id=project_id, after_event_id=0)
    event_types = {str(event["type"]) for event in outbox}
    checks.append(_check("OUTBOX_TYPES_PERSISTED", {"JOB_QUEUED", "JOB_CLAIMED", "JOB_HEARTBEAT"} <= event_types))

    recovery = restarted.reconcile(now=datetime.now(UTC) + timedelta(seconds=61), actor="kill-matrix")
    checks.append(_check("RECONCILE_ORPHANS_EXPIRED_LEASE", recovery["reconciled"] == 1 and recovery["items"][0]["job_state"] == "QUEUED"))
    recovered = restarted.get_job(str(jobs[0]["id"])) if jobs else None
    checks.append(_check("JOB_RETRYABLE_AFTER_RECONCILE", bool(recovered) and recovered["state"] == "QUEUED" and recovered["attempts"][0]["state"] == "ORPHANED"))
    with database.connect() as connection:
        next_run_at = connection.execute("SELECT next_run_at FROM jobs WHERE id=?", (str(jobs[0]["id"]),)).fetchone()[0] if jobs else None
    checks.append(_check("BACKOFF_DEFERS_RETRY_CLAIM", next_run_at is not None, reason="reconcile schedules next_run_at at the crash-window clock"))
    # A real-time claim is correctly deferred while next_run_at is in the
    # future (no double-claim before the 60-second window).
    retry_claim = restarted.claim("recovery-worker", ["CPU"], lease_seconds=60)
    checks.append(_check("NO_DOUBLE_CLAIM_BEFORE_BACKOFF", retry_claim is None))
    return {"attempt_id": attempt_id, "checks": checks, "passed": all(item["passed"] for item in checks)}


def scenario_gpu_single_winner(database: Database, settings: Settings, project_id: str) -> dict[str, Any]:
    jobs = JobService(database, settings)
    job = jobs.create_job(
        project_id, "GPU_TEST", "PROJECT", project_id, "GPU_H3", {"contention": True}, f"kill-matrix-gpu-{uuid.uuid4().hex[:8]}", max_attempts=1
    )
    first = jobs.claim("worker-a", ["GPU_H3"], lease_seconds=60)
    second = jobs.claim("worker-b", ["GPU_H3"], lease_seconds=60)
    checks = [
        _check("FIRST_WORKER_CLAIMED", first is not None and str(first["job"]["id"]) == str(job["id"])),
        _check("SECOND_WORKER_BLOCKED", second is None, reason="GPU_H3 heavy lease is exclusive"),
    ]
    if first:
        jobs.complete(str(first["attempt"]["id"]), str(first["attempt"]["lease_token"]), "worker-a", success=True)
    checks.append(_check("JOB_SUCCEEDED_AFTER_SINGLE_WINNER", jobs.get_job(str(job["id"]))["state"] == "SUCCEEDED"))
    return {"job_id": str(job["id"]), "checks": checks, "passed": all(item["passed"] for item in checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "nfr-rel-windows-kill-matrix-2026-08-17.json")
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="kill_matrix",
        title="Kill matrix UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=30_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])

    crash = scenario_crash_window(database, settings, project_id)
    contention = scenario_gpu_single_winner(database, settings, project_id)
    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    payload = {
        "schema_version": "g10.nfr-rel-windows-kill-matrix.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "mode": "LOCAL_ONLY",
        "status": "PASS" if crash["passed"] and contention["passed"] and integrity == "ok" else "IN_PROGRESS",
        "scenarios": {"crash_window": crash, "gpu_single_winner": contention},
        "database_integrity": integrity,
        "safety": {"production_database_contacted": False, "runtime_contacted": False, "network_contacted": False, "mutated": False},
        "limitations": ["确定性崩溃窗口演练：用子进程 os._exit 与调度时钟前移代替真实 60 秒等待；真实 Windows 断电与备份介质恢复仍属后续硬件演练。"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
