from __future__ import annotations

import errno
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.experiments import ExperimentService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str = "g5_project") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="G5 queue project",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )


def _create(service: JobService, project_id: str, key: str, **extra: object) -> dict[str, object]:
    snapshot = extra.pop("input_snapshot", {"case": key})
    job_type = str(extra.pop("job_type", "CPU_TEST"))
    return service.create_job(
        project_id,
        job_type,
        "PROJECT",
        project_id,
        "CPU",
        snapshot,
        key,
        **extra,
    )


def test_persistent_queue_idempotency_dependencies_lease_and_recovery(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    service = JobService(database, workspace)

    first = _create(service, project_id, "job-a")
    replay = _create(service, project_id, "job-a")
    assert replay["id"] == first["id"]
    assert replay["idempotent_replay"] is True
    with pytest.raises(DomainRuleError, match="请求体不一致"):
        service.create_job(project_id, "CPU_TEST", "PROJECT", project_id, "CPU", {"case": "different"}, "job-a")

    dependent = _create(service, project_id, "job-b", depends_on_job_ids=[str(first["id"])])
    claimed = service.claim("worker-a", ["CPU"])
    assert claimed is not None
    assert claimed["job"]["id"] == first["id"]
    assert service.claim("worker-a", ["CPU"]) is None
    attempt = claimed["attempt"]
    heartbeat = service.heartbeat(str(attempt["id"]), str(attempt["lease_token"]), "worker-a", progress={"percent": 50})
    assert heartbeat["state"] == "RUNNING"
    completed = service.complete(str(attempt["id"]), str(attempt["lease_token"]), "worker-a", success=True)
    assert completed["job_state"] == "SUCCEEDED"
    next_claim = service.claim("worker-a", ["CPU"])
    assert next_claim is not None
    assert next_claim["job"]["id"] == dependent["id"]
    service.complete(str(next_claim["attempt"]["id"]), str(next_claim["attempt"]["lease_token"]), "worker-a", success=True)

    expiring = _create(service, project_id, "job-expiring", max_attempts=2)
    expired_claim = service.claim("worker-kill", ["CPU"], lease_seconds=5)
    assert expired_claim is not None
    assert expired_claim["job"]["id"] == expiring["id"]
    with pytest.raises(DomainRuleError, match="lease_token 或 worker_id"):
        service.heartbeat(str(expired_claim["attempt"]["id"]), "wrong-token", "worker-kill")
    recovery = service.reconcile(now=datetime.now(UTC) + timedelta(seconds=10))
    assert recovery["reconciled"] == 1
    assert recovery["items"][0]["job_state"] == "QUEUED"
    assert service.get_job(str(expiring["id"]))["attempts"][0]["state"] == "ORPHANED"
    assert service.cancel(str(expiring["id"]))["state"] == "CANCELLED"

    failed = _create(service, project_id, "job-failed", max_attempts=1)
    failed_claim = service.claim("worker-fail", ["CPU"])
    assert failed_claim is not None
    assert failed_claim["job"]["id"] == failed["id"]
    failed_result = service.complete(
        str(failed_claim["attempt"]["id"]),
        str(failed_claim["attempt"]["lease_token"]),
        "worker-fail",
        success=False,
        error_code="PROCESS_CRASH",
        error_detail_redacted="worker exited",
    )
    assert failed_result["job_state"] == "FAILED"
    assert service.retry(str(failed["id"]))["state"] == "QUEUED"
    assert service.cancel(str(failed["id"]))["state"] == "CANCELLED"

    events = service.events(after_event_id=0, project_id=project_id)
    assert any(event["type"] == "JOB_QUEUED" for event in events)
    assert any(event["type"] == "JOB_RECONCILED" for event in events)


def test_twenty_cpu_jobs_sse_and_artifact_idempotency(workspace, database) -> None:
    project = _project(workspace, database, "g5_scale")
    project_id = str(project["id"])
    service = JobService(database, workspace)
    jobs = [_create(service, project_id, f"cpu-{index:02d}") for index in range(20)]
    processed: list[str] = []
    artifact_path = workspace.work_root / "job-artifacts" / "result.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("real worker output", encoding="utf-8")
    for index in range(20):
        claim = service.claim(f"worker-{index % 2}", ["CPU"])
        assert claim is not None
        attempt = claim["attempt"]
        processed.append(str(claim["job"]["id"]))
        if index == 0:
            artifact = service.register_artifact(str(attempt["id"]), "TEXT_RESULT", "job-artifacts/result.txt")
            replay = service.register_artifact(str(attempt["id"]), "TEXT_RESULT", "job-artifacts/result.txt")
            assert artifact["sha256"] == replay["sha256"]
        service.complete(str(attempt["id"]), str(attempt["lease_token"]), f"worker-{index % 2}", success=True)
    assert set(processed) == {str(item["id"]) for item in jobs}
    assert all(item["state"] == "SUCCEEDED" for item in service.list_jobs(project_id))

    with TestClient(create_app(workspace)) as client:
        created = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "api-job-1"},
            json={
                "project_id": project_id,
                "type": "CPU_TEST",
                "subject_type": "PROJECT",
                "subject_id": project_id,
                "channel": "CPU",
                "input_snapshot": {"source": "api"},
            },
        )
        assert created.status_code == 201
        duplicate = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "api-job-1"},
            json={
                "project_id": project_id,
                "type": "CPU_TEST",
                "subject_type": "PROJECT",
                "subject_id": project_id,
                "channel": "CPU",
                "input_snapshot": {"source": "api"},
            },
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["job"]["idempotent_replay"] is True
        stream = client.get(f"/api/v1/events?project_id={project_id}&after_event_id=0")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
    assert "JOB_QUEUED" in stream.text


def test_jobs_server_pagination_is_bounded_and_cursored(workspace, database) -> None:
    project = _project(workspace, database, "g5_pagination")
    service = JobService(database, workspace)
    for index in range(205):
        _create(service, str(project["id"]), f"page-{index:03d}")
    first = service.list_jobs_page(str(project["id"]), cursor=0, limit=500)
    assert first["limit"] == 100
    assert len(first["items"]) == 100
    assert first["next_cursor"] == 100
    second = service.list_jobs_page(str(project["id"]), cursor=int(first["next_cursor"]), limit=100)
    assert len(second["items"]) == 100
    assert second["next_cursor"] == 200
    last = service.list_jobs_page(str(project["id"]), cursor=int(second["next_cursor"]), limit=100)
    assert len(last["items"]) == 5
    assert last["next_cursor"] is None


def test_real_local_worker_proxy_thumbnail_and_artifact_registration(workspace, database) -> None:
    project = _project(workspace, database, "g5_media")
    source = workspace.work_root / "worker-input.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, stage="IMPORTED")
    jobs = JobService(database, workspace)
    proxy = _create(jobs, str(project["id"]), "worker-proxy", job_type="MEDIA_PROXY", input_snapshot={"media_version_id": str(media["media_version_id"])})
    thumbnail = _create(
        jobs, str(project["id"]), "worker-thumbnail", job_type="MEDIA_THUMBNAIL", input_snapshot={"media_version_id": str(media["media_version_id"])}
    )
    worker = LocalMediaWorker(database, workspace)
    proxy_result = worker.run_once("media-worker")
    assert proxy_result is not None
    assert proxy_result["job"]["id"] == proxy["id"]
    assert proxy_result["result"]["job_state"] == "SUCCEEDED"
    assert proxy_result["artifact"]["status"] == "VERIFIED"
    thumbnail_result = worker.run_once("media-worker")
    assert thumbnail_result is not None
    assert thumbnail_result["job"]["id"] == thumbnail["id"]
    assert thumbnail_result["artifact"]["kind"] == "THUMBNAIL"
    assert Path(workspace.work_root / str(thumbnail_result["artifact"]["sandbox_rel_path"])).is_file()


def test_local_worker_disk_full_fails_closed_and_releases_lease(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "g5_disk_full")
    jobs = JobService(database, workspace)
    job = _create(jobs, str(project["id"]), "disk-full", max_attempts=1)
    original_write_text = Path.write_text

    def fail_after_partial_write(path: Path, *args: object, **kwargs: object) -> int:
        written = original_write_text(path, *args, **kwargs)
        if path.name == ".partial-result.txt":
            raise OSError(errno.ENOSPC, "simulated disk full")
        return written

    monkeypatch.setattr(Path, "write_text", fail_after_partial_write)
    outcome = LocalMediaWorker(database, workspace).run_once("disk-full-worker", ["CPU"])

    assert outcome is not None
    assert outcome["job"]["id"] == job["id"]
    assert outcome["error"] == "DISK_FULL"
    assert outcome["result"]["job_state"] == "FAILED"
    assert not (workspace.work_root / "jobs" / str(job["id"]) / ".partial-result.txt").exists()
    assert jobs.get_job(str(job["id"]))["attempts"][0]["error_code"] == "DISK_FULL"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE job_attempt_id=?", (outcome["attempt"]["id"],)).fetchone()[0] == 0
        released = connection.execute("SELECT released_at FROM job_resource_leases WHERE attempt_id=?", (outcome["attempt"]["id"],)).fetchone()
        assert released is not None and released["released_at"] is not None
    assert jobs.retry(str(job["id"]))["state"] == "QUEUED"


def test_local_worker_oom_is_structured_and_does_not_leave_running_attempt(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "g5_oom")
    jobs = JobService(database, workspace)
    job = _create(jobs, str(project["id"]), "oom", max_attempts=1)
    worker = LocalMediaWorker(database, workspace)
    monkeypatch.setattr(worker, "_atomic_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError()))

    outcome = worker.run_once("oom-worker", ["CPU"])

    assert outcome is not None
    assert outcome["error"] == "WORKER_OUT_OF_MEMORY"
    assert outcome["result"]["job_state"] == "FAILED"
    persisted = jobs.get_job(str(job["id"]))
    assert persisted["state"] == "FAILED"
    assert persisted["attempts"][0]["state"] == "FAILED"
    assert persisted["attempts"][0]["lease_token"] is None
    assert persisted["attempts"][0]["lease_expires_at"] is None


def test_generation_plan_estimate_confirm_lazy_expand_and_cell_cancel(workspace, database) -> None:
    project = _project(workspace, database, "g5_matrix")
    project_id = str(project["id"])
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "MATRIX", "real local CPU matrix")
    experiments = ExperimentService(database)
    plan = experiments.create_plan(
        str(intent["id"]),
        "camera and seed matrix",
        {"seed": [1, 2], "camera": ["wide", "close"]},
        max_parallel=1,
        resource_estimate={"cpu_seconds_per_cell": 3, "disk_bytes_per_cell": 1024, "gpu_slots": 0},
    )
    estimate = experiments.estimate(str(plan["id"]))
    assert estimate["cell_count"] == 4
    assert estimate["would_create_jobs"] is False
    assert JobService(database).list_jobs(project_id) == []
    with pytest.raises(DomainRuleError, match="尚未确认"):
        experiments.expand(str(plan["id"]), limit=2)
    assert JobService(database).list_jobs(project_id) == []
    first = experiments.confirm(str(plan["id"]), str(plan["plan_hash"]), limit=2)
    assert len(first["expanded"]) == 2
    assert first["remaining_count"] == 2
    second = experiments.expand(str(plan["id"]), limit=20)
    assert len(second["expanded"]) == 2
    assert experiments.get_plan(str(plan["id"]))["remaining_count"] == 0
    cancelled = experiments.cancel_cell(str(first["expanded"][0]["id"]))
    assert cancelled["status"] == "CANCELLED"


def test_generation_plan_hash_and_large_matrix_confirmation_gate(workspace, database) -> None:
    project = _project(workspace, database, "g5_large_matrix")
    project_id = str(project["id"])
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "MATRIX", "gated local matrix")
    experiments = ExperimentService(database)
    plan = experiments.create_plan(str(intent["id"]), "25-cell matrix", {"seed": list(range(25))})
    experiment_id = str(plan["id"])

    with pytest.raises(DomainRuleError, match="重新读取 estimate"):
        experiments.confirm(experiment_id, "0" * 64, limit=1)
    with pytest.raises(DomainRuleError, match="必须二次确认"):
        experiments.confirm(experiment_id, str(plan["plan_hash"]), limit=1)
    assert experiments.get_plan(experiment_id)["status"] == "DRAFT"
    assert JobService(database).list_jobs(project_id) == []

    confirmed = experiments.confirm(experiment_id, str(plan["plan_hash"]), limit=1, confirm_large_matrix=True)
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["expanded_count"] == 1
    assert len(JobService(database).list_jobs(project_id)) == 1


def test_generation_confirm_api_requires_current_hash_and_large_matrix_ack(workspace, database) -> None:
    project = _project(workspace, database, "g5_large_matrix_api")
    project_id = str(project["id"])
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "MATRIX", "API confirmation gate")
    plan = ExperimentService(database).create_plan(str(intent["id"]), "25-cell API matrix", {"seed": list(range(25))})
    experiment_id = str(plan["id"])

    with TestClient(create_app(workspace)) as client:
        stale = client.post(
            f"/api/v1/generation-experiments/{experiment_id}:confirm",
            json={"plan_hash": "0" * 64, "limit": 1},
        )
        assert stale.status_code == 422
        assert stale.json()["error"]["code"] == "EXPERIMENT_PLAN_STALE"
        needs_ack = client.post(
            f"/api/v1/generation-experiments/{experiment_id}:confirm",
            json={"plan_hash": plan["plan_hash"], "limit": 1},
        )
        assert needs_ack.status_code == 422
        assert needs_ack.json()["error"]["code"] == "EXPERIMENT_LARGE_MATRIX_CONFIRMATION_REQUIRED"
        confirmed = client.post(
            f"/api/v1/generation-experiments/{experiment_id}:confirm",
            json={"plan_hash": plan["plan_hash"], "limit": 1, "confirm_large_matrix": True},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["result"]["expanded_count"] == 1

    assert len(JobService(database).list_jobs(project_id)) == 1


def test_generation_experiment_cancel_remaining_preserves_terminal_history(workspace, database) -> None:
    project = _project(workspace, database, "g5_cancel_matrix")
    project_id = str(project["id"])
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "MATRIX", "cancel remaining")
    experiments = ExperimentService(database)
    plan = experiments.create_plan(str(intent["id"]), "cancel matrix", {"seed": [1, 2, 3]})
    expanded = experiments.confirm(str(plan["id"]), str(plan["plan_hash"]), limit=2)
    jobs = JobService(database)
    first_claim = jobs.claim("cpu-test", ["CPU"])
    assert first_claim is not None
    jobs.complete(
        str(first_claim["attempt"]["id"]), str(first_claim["attempt"]["lease_token"]), "cpu-test", success=True
    )

    with TestClient(create_app(workspace)) as client:
        cancelled = client.post(f"/api/v1/generation-experiments/{plan['id']}:cancel-remaining")
        assert cancelled.status_code == 200
        result = cancelled.json()["result"]
        assert result["status"] == "CANCELLED"
        assert result["unexpanded_cancelled_count"] == 1
        assert len(result["preserved"]) == 1
        assert len(result["cancelled"]) == 1

    states = {item["id"]: item["state"] for item in jobs.list_jobs(project_id)}
    assert states[str(first_claim["job"]["id"])] == "SUCCEEDED"
    remaining_job_id = next(str(item["job_id"]) for item in expanded["expanded"] if item["job_id"] != first_claim["job"]["id"])
    assert states[remaining_job_id] == "CANCELLED"
    with pytest.raises(DomainRuleError, match="尚未确认"):
        experiments.expand(str(plan["id"]), limit=1)


def test_generation_experiment_job_and_cell_creation_roll_back_together(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "g5_atomic_matrix")
    project_id = str(project["id"])
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "MATRIX", "atomic cell creation")
    experiments = ExperimentService(database)
    plan = experiments.create_plan(str(intent["id"]), "atomic matrix", {"seed": [1]})
    original = experiments.jobs.create_job_in_transaction

    def fail_after_job_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after job insert")

    monkeypatch.setattr(experiments.jobs, "create_job_in_transaction", fail_after_job_insert)
    with pytest.raises(RuntimeError, match="injected failure"):
        experiments.confirm(str(plan["id"]), str(plan["plan_hash"]), limit=1)

    assert experiments.get_plan(str(plan["id"]))["status"] == "CONFIRMED"
    assert experiments.get_plan(str(plan["id"]))["cells"] == []
    assert JobService(database).list_jobs(project_id) == []
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM command_idempotencies").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type='JOB_QUEUED'").fetchone()[0] == 0

    monkeypatch.setattr(experiments.jobs, "create_job_in_transaction", original)
    recovered = experiments.expand(str(plan["id"]), limit=1)
    assert recovered["expanded_count"] == 1
    assert len(JobService(database).list_jobs(project_id)) == 1


def test_worker_process_kill_is_reconciled_without_duplicate_attempt(workspace, database) -> None:
    project = _project(workspace, database, "g5_kill")
    project_id = str(project["id"])
    service = JobService(database, workspace)
    job = _create(service, project_id, "kill-process", max_attempts=2)
    api_root = Path(__file__).resolve().parents[1]
    database_path = str(database.path)
    child_code = (
        "import sys,time; "
        f"sys.path.insert(0, {str(api_root)!r}); "
        "from pathlib import Path; "
        "from local_drama.infrastructure.database.sqlite import Database; "
        "from local_drama.application.jobs import JobService; "
        f"claim=JobService(Database(Path({database_path!r}))).claim('killed-worker',['CPU'],lease_seconds=5); "
        "print(claim['attempt']['id'], flush=True); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert child.stdout is not None
    attempt_id = child.stdout.readline().strip()
    assert attempt_id
    child.kill()
    child.wait(timeout=10)
    recovery = service.reconcile(now=datetime.now(UTC) + timedelta(seconds=10))
    assert recovery["reconciled"] == 1
    assert recovery["items"][0]["attempt_id"] == attempt_id
    recovered_job = service.get_job(str(job["id"]))
    assert recovered_job["attempts"][0]["state"] == "ORPHANED"
    assert recovered_job["state"] == "QUEUED"
