from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Scheduler progress",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _job(service: JobService, project_id: str, key: str, *, channel: str = "CPU", max_attempts: int = 2) -> dict[str, object]:
    return service.create_job(
        project_id,
        "SCHEDULER_TEST",
        "PROJECT",
        project_id,
        channel,
        {"key": key, "source": "local"},
        key,
        max_attempts=max_attempts,
    )


def test_progress_and_attempt_logs_survive_service_recreation(workspace, database) -> None:
    project = _project(workspace, database, "scheduler_progress")
    jobs = JobService(database, workspace)
    job = _job(jobs, str(project["id"]), "progress-1")
    claim = jobs.claim("progress-worker", ["CPU"])
    assert claim is not None
    attempt = claim["attempt"]
    progress = {"phase": "sampling", "node": "KSampler", "percent": 42, "eta_seconds": 17}
    heartbeat = jobs.heartbeat(str(attempt["id"]), str(attempt["lease_token"]), "progress-worker", progress=progress)
    assert heartbeat["progress"] == progress

    recreated = JobService(database, workspace).get_job(str(job["id"]))
    assert recreated["progress"] == progress
    assert recreated["attempts"][0]["progress"] == progress
    assert recreated["attempts"][0]["lease_token"]  # direct service retains worker credential for completion

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/jobs/{job['id']}/attempts")
        assert response.status_code == 200
        assert response.json()["items"][0]["lease_token"] is None
        logs = client.get(f"/api/v1/job-attempts/{attempt['id']}/logs?limit=10")
        assert logs.status_code == 200
        assert any(item["type"] == "JOB_HEARTBEAT" for item in logs.json()["items"])


def test_gpu_heavy_resource_is_exclusive_but_cpu_is_independent(workspace, database) -> None:
    project = _project(workspace, database, "scheduler_resources")
    jobs = JobService(database, workspace)
    gpu_one = _job(jobs, str(project["id"]), "gpu-1", channel="GPU_H3")
    gpu_two = _job(jobs, str(project["id"]), "gpu-2", channel="GPU_H3")
    cpu = _job(jobs, str(project["id"]), "cpu-1", channel="CPU")

    first = jobs.claim("gpu-worker-1", ["GPU_H3"])
    assert first is not None and first["job"]["id"] == gpu_one["id"]
    assert jobs.claim("gpu-worker-2", ["GPU_H3"]) is None
    cpu_claim = jobs.claim("cpu-worker", ["CPU"])
    assert cpu_claim is not None and cpu_claim["job"]["id"] == cpu["id"]

    jobs.complete(str(cpu_claim["attempt"]["id"]), str(cpu_claim["attempt"]["lease_token"]), "cpu-worker", success=True)
    jobs.complete(str(first["attempt"]["id"]), str(first["attempt"]["lease_token"]), "gpu-worker-1", success=True)
    second = jobs.claim("gpu-worker-2", ["GPU_H3"])
    assert second is not None and second["job"]["id"] == gpu_two["id"]


def test_restart_claim_reconciles_expired_lease_and_retry_clone_keep_history(workspace, database) -> None:
    project = _project(workspace, database, "scheduler_recovery")
    jobs = JobService(database, workspace)
    job = _job(jobs, str(project["id"]), "recover-1", max_attempts=2)
    claim = jobs.claim("crashed-worker", ["CPU"])
    assert claim is not None
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET lease_expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), claim["attempt"]["id"]),
        )
    # A fresh scheduler process automatically reconciles before claiming.
    recovered = JobService(database, workspace).claim("replacement-worker", ["CPU"])
    assert recovered is not None and recovered["job"]["id"] == job["id"]
    assert recovered["attempt"]["attempt_no"] == 2
    jobs.complete(str(recovered["attempt"]["id"]), str(recovered["attempt"]["lease_token"]), "replacement-worker", success=False, error_code="E_TEST", error_detail_redacted="local failure")
    assert jobs.get_job(str(job["id"]))["last_error_code"] == "E_TEST"
    retried = jobs.retry(str(job["id"]))
    assert retried["state"] == "QUEUED"
    clone = jobs.clone(str(job["id"]), "clone-recovery-1", {"seed": 99})
    assert clone["id"] != job["id"]
    assert clone["input_snapshot"]["seed"] == 99


def test_cancel_is_cooperative_and_invalid_progress_is_rejected(workspace, database) -> None:
    project = _project(workspace, database, "scheduler_cancel")
    jobs = JobService(database, workspace)
    job = _job(jobs, str(project["id"]), "cancel-1")
    claim = jobs.claim("cancel-worker", ["CPU"])
    assert claim is not None
    with pytest.raises(DomainRuleError, match="0—100"):
        jobs.heartbeat(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "cancel-worker", progress={"percent": 101})
    assert jobs.cancel(str(job["id"]))["state"] == "CANCEL_REQUESTED"
    result = jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "cancel-worker", success=False, error_code="CANCELLED_BY_USER")
    assert result["job_state"] == "CANCELLED"
