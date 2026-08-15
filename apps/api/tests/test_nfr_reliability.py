from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.config import Settings


def test_nfr_rel_persistence_and_restart_reconciliation(workspace: Settings, database) -> None:
    """A committed command remains readable after an abrupt worker process exit.

    The child deliberately uses ``os._exit`` after committing a job, claim and
    heartbeat.  The parent then constructs fresh database/service objects,
    verifies the durable job, progress and outbox records, and reconciles the
    expired lease.  This is a deterministic crash-window rehearsal; it does
    not claim to replace the real Windows power-loss and 60-second UAT gates.
    """

    project = ProjectService(database, workspace.projects_root).create_project(
        code="nfr_rel_restart",
        title="NFR reliability restart",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=30000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    api_root = Path(__file__).resolve().parents[1]
    database_path = str(database.path)
    child_code = (
        "import os,sys; "
        f"sys.path.insert(0, {str(api_root)!r}); "
        "from pathlib import Path; "
        "from local_drama.infrastructure.database.sqlite import Database; "
        "from local_drama.application.jobs import JobService; "
        f"db=Database(Path({database_path!r})); "
        "jobs=JobService(db); "
        f"job=jobs.create_job({project_id!r}, 'CPU_TEST', 'PROJECT', {project_id!r}, 'CPU', {{'crash_window': True}}, 'nfr-rel-restart', max_attempts=2); "
        "claim=jobs.claim('crashed-worker', ['CPU'], lease_seconds=60); "
        "assert claim is not None and claim['job']['id'] == job['id']; "
        "jobs.heartbeat(claim['attempt']['id'], claim['attempt']['lease_token'], 'crashed-worker', progress={'phase':'render','node':'sampler','percent':42}); "
        "os._exit(17)"
    )
    child = subprocess.run([sys.executable, "-c", child_code], capture_output=True, text=True, check=False)
    assert child.returncode == 17, child.stderr

    # New service/database instances model an API restart and must observe all
    # records committed before the abrupt process termination.
    restarted = JobService(database, workspace)
    jobs = restarted.list_jobs(project_id)
    assert len(jobs) == 1
    persisted_job = jobs[0]
    assert persisted_job["state"] == "RUNNING"
    assert persisted_job["progress"] == {"node": "sampler", "percent": 42, "phase": "render"}
    replay = restarted.create_job(
        project_id,
        "CPU_TEST",
        "PROJECT",
        project_id,
        "CPU",
        {"crash_window": True},
        "nfr-rel-restart",
        max_attempts=2,
    )
    assert replay["id"] == persisted_job["id"]
    assert replay["idempotent_replay"] is True
    attempts = restarted.list_attempts(str(persisted_job["id"]))
    assert len(attempts) == 1
    attempt_id = str(attempts[0]["id"])
    assert attempts[0]["state"] == "RUNNING"
    assert attempts[0]["progress"] == persisted_job["progress"]
    assert attempts[0]["lease_token"] is None

    outbox = restarted.events(project_id=project_id, after_event_id=0)
    event_types = [str(event["type"]) for event in outbox]
    assert "JOB_QUEUED" in event_types
    assert "JOB_CLAIMED" in event_types
    assert "JOB_HEARTBEAT" in event_types
    attempt_events = restarted.attempt_events(attempt_id, cursor=0, limit=100)
    assert {str(item["type"]) for item in attempt_events["items"]} >= {"JOB_CLAIMED", "JOB_HEARTBEAT"}

    # Advance the scheduler clock rather than sleeping for a minute.  This
    # proves the persisted lease/reconcile transition while leaving the real
    # 60-second Windows restart UAT explicitly outside automated evidence.
    recovery = restarted.reconcile(now=datetime.now(UTC) + timedelta(seconds=61), actor="restart-test")
    assert recovery["reconciled"] == 1
    assert recovery["items"][0]["attempt_id"] == attempt_id
    assert recovery["items"][0]["job_state"] == "QUEUED"
    recovered = restarted.get_job(str(persisted_job["id"]))
    assert recovered["state"] == "QUEUED"
    assert recovered["attempts"][0]["state"] == "ORPHANED"
    with database.connect() as connection:
        lease = connection.execute(
            "SELECT released_at FROM job_resource_leases WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
    assert lease is not None and lease["released_at"] is not None
    assert any(str(event["type"]) == "JOB_RECONCILED" for event in restarted.events(project_id=project_id, after_event_id=0))
