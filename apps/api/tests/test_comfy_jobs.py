from __future__ import annotations

import pytest

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker_sessions import WorkerSessionService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError


class _OfflineWorkflowNodes:
    base_url = "http://127.0.0.1:8188"

    def object_info(self) -> dict[str, object]:
        return {"LoadImage": {}, "SaveImage": {}}


def test_quick_generation_parameters_rewrite_only_the_per_job_graph(workspace, database) -> None:
    service = ComfyGenerationService(database, workspace)
    workflow = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1344}},
        "2": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "3": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"width": 480, "height": 832, "length": 107}},
        "4": {"class_type": "CreateVideo", "inputs": {"fps": 24.0}},
    }
    evidence = service._apply_effective_configuration(
        workflow,
        {
            "effective_settings": {
                "width": 832,
                "height": 480,
                "frame_count": 121,
                "fps": 30,
                "steps": 32,
                "cfg": 5.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 0.8,
            },
            "fingerprint": "sha256:test",
        },
    )
    assert evidence["changed"] is True
    assert workflow["1"]["inputs"] == {"width": 832, "height": 480}
    assert workflow["2"]["inputs"] == {
        "steps": 32,
        "cfg": 5.5,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 0.8,
    }
    assert workflow["3"]["inputs"] == {"width": 832, "height": 480, "length": 121}
    assert workflow["4"]["inputs"]["fps"] == 30.0


def _publish_offline(service: WorkflowService, version_id: str) -> None:
    validation = service.validate_against_comfy(version_id, _OfflineWorkflowNodes())  # type: ignore[arg-type]
    service.publish(version_id, str(validation["validation_id"]))


def _active_attempt(workspace, database) -> tuple[ComfyGenerationService, str]:
    workflows = WorkflowService(database)
    version = workflows.register_package(
        "comfy_poll_state",
        "Comfy poll state",
        {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "poll"}}},
        {},
        {},
    )
    _publish_offline(workflows, str(version["id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="comfy_poll_project",
        title="Comfy poll project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]),
        "COMFY_STATE_TEST",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}},
        "comfy-state-test",
    )
    claim = jobs.claim("worker-1", ["GPU_H3"], 60)
    assert claim is not None
    jobs.attach_provider(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "worker-1", "prompt-1")
    return ComfyGenerationService(database, workspace), str(claim["attempt"]["id"])


def test_poll_maps_live_comfy_queue_state_before_history_exists(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)
    monkeypatch.setattr(service.comfy, "history", lambda _prompt_id: {})
    monkeypatch.setattr(service.comfy, "queue", lambda: {"queue_running": [[1, "prompt-1", {}, {}]], "queue_pending": []})
    assert service.poll_attempt(attempt_id, "worker-1")["status"] == "RUNNING"
    monkeypatch.setattr(service.comfy, "queue", lambda: {"queue_running": [], "queue_pending": [[2, "prompt-1", {}, {}]]})
    assert service.poll_attempt(attempt_id, "worker-1")["status"] == "QUEUED"
    monkeypatch.setattr(service.comfy, "queue", lambda: {"queue_running": [], "queue_pending": []})
    assert service.poll_attempt(attempt_id, "worker-1")["status"] == "PROVIDER_UNCONFIRMED"


def _iso_utc_minutes_ago(minutes: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def test_poll_closes_attempt_when_comfy_runtime_dies(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)

    def unavailable(_prompt_id: str) -> dict[str, object]:
        raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "offline")

    monkeypatch.setattr(service.comfy, "history", unavailable)
    result = service.poll_attempt(attempt_id, "worker-1")
    assert result["status"] == "FAILED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT a.state AS attempt_state, a.error_code, j.state AS job_state, j.last_error_code "
            "FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
    assert row["attempt_state"] == "FAILED"
    assert row["error_code"] == "COMFY_RUNTIME_UNAVAILABLE"
    assert row["job_state"] == "QUEUED"
    assert row["last_error_code"] == "COMFY_RUNTIME_UNAVAILABLE"


def test_poll_survives_busy_comfy_runtime_within_grace(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)

    def busy(_prompt_id: str) -> dict[str, object]:
        raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "busy", {"reason": "URLError", "cause": "TimeoutError"})

    monkeypatch.setattr(service.comfy, "history", busy)
    first = service.poll_attempt(attempt_id, "worker-1")
    assert first["status"] == "RUNNING"
    assert first["runtime_busy"] is True
    second = service.poll_attempt(attempt_id, "worker-1")
    assert second["status"] == "RUNNING"
    with database.connect() as connection:
        events = connection.execute(
            "SELECT COUNT(*) FROM provider_execution_events WHERE job_attempt_id=? AND event_type='PROVIDER_BUSY'",
            (attempt_id,),
        ).fetchone()[0]
        state = connection.execute("SELECT state FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()[0]
    assert events == 1
    assert state == "RUNNING"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE provider_execution_events SET occurred_at=? WHERE job_attempt_id=? AND event_type='PROVIDER_BUSY'",
            (_iso_utc_minutes_ago(31), attempt_id),
        )
    expired = service.poll_attempt(attempt_id, "worker-1")
    assert expired["status"] == "FAILED"
    with database.connect() as connection:
        row = connection.execute("SELECT error_code FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
    assert row["error_code"] == "COMFY_RUNTIME_UNAVAILABLE"


def test_busy_grace_clock_resets_after_runtime_responds(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)

    def busy(_prompt_id: str) -> dict[str, object]:
        raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "busy", {"reason": "URLError", "cause": "TimeoutError"})

    monkeypatch.setattr(service.comfy, "history", busy)
    assert service.poll_attempt(attempt_id, "worker-1")["status"] == "RUNNING"
    # The runtime answers again between busy stretches: a normal RUNNING poll
    # must restart the continuous-busy window instead of letting the first
    # busy timestamp eventually fail the attempt.
    monkeypatch.setattr(service.comfy, "history", lambda prompt_id: {prompt_id: {"status": {"status_str": "running"}}})
    answered = service.poll_attempt(attempt_id, "worker-1")
    assert answered["status"] == "running"
    monkeypatch.setattr(service.comfy, "history", busy)
    recovered = service.poll_attempt(attempt_id, "worker-1")
    assert recovered["status"] == "RUNNING"
    with database.connect() as connection:
        stamps = connection.execute(
            "SELECT occurred_at FROM provider_execution_events WHERE job_attempt_id=? AND event_type='PROVIDER_BUSY' ORDER BY sequence_no",
            (attempt_id,),
        ).fetchall()
    assert len(stamps) == 2
    assert stamps[1]["occurred_at"] > stamps[0]["occurred_at"]


def test_poll_closes_attempt_immediately_on_connection_refused(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)

    def refused(_prompt_id: str) -> dict[str, object]:
        raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "refused", {"reason": "URLError", "cause": "ConnectionRefusedError"})

    monkeypatch.setattr(service.comfy, "history", refused)
    result = service.poll_attempt(attempt_id, "worker-1")
    assert result["status"] == "FAILED"
    with database.connect() as connection:
        row = connection.execute("SELECT state FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
    assert row["state"] == "FAILED"


def test_background_recovery_finds_provider_success_without_manual_attempt_id(workspace, database, monkeypatch) -> None:
    service, attempt_id = _active_attempt(workspace, database)
    output = workspace.work_root / "provider-finished.png"
    output.write_bytes(b"provider-result")
    with database.transaction() as connection:
        connection.execute("UPDATE job_attempts SET state='ORPHANED',lease_token=NULL,lease_expires_at=NULL WHERE id=?", (attempt_id,))
        connection.execute("UPDATE jobs SET state='NEEDS_ATTENTION' WHERE id=(SELECT job_id FROM job_attempts WHERE id=?)", (attempt_id,))
    monkeypatch.setattr(service.comfy, "history", lambda prompt_id: {prompt_id: {"status": {"status_str": "success"}}})
    monkeypatch.setattr(service.comfy, "collect_outputs", lambda _item: [output])

    result = service.recover_uncertain_successes()

    assert result["inspected"] == 1
    assert result["recovered"] == 1
    assert result["items"][0]["status"] == "RECOVERED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT a.state AS attempt_state,j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt_id,)
        ).fetchone()
        artifacts = connection.execute("SELECT COUNT(*) FROM artifacts WHERE job_attempt_id=?", (attempt_id,)).fetchone()[0]
    assert row["attempt_state"] == "SUCCEEDED"
    assert row["job_state"] == "SUCCEEDED"
    assert artifacts == 1


@pytest.mark.parametrize("roles", [("FIRST_FRAME",), ("REFERENCE_IMAGE_1", "REFERENCE_IMAGE_2", "REFERENCE_IMAGE_3")])
def test_submit_materializes_verified_media_binding_inside_isolated_input_root(workspace, database, monkeypatch, roles) -> None:
    workspace = workspace.model_copy(update={"comfy_input_root": workspace.work_root / "comfy-production" / "input"})
    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "comfy_media_binding",
        "Comfy media binding",
        {str(i): {"class_type": "LoadImage", "inputs": {"image": "pending.png"}} for i, _ in enumerate(roles, 1)},
        {},
        {role: {"node_id": str(i), "input": "image"} for i, role in enumerate(roles, 1)},
    )
    _publish_offline(workflow_service, str(version["id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="comfy_media_input",
        title="Comfy media input",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "source.png"
    source.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
        )
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    media_ids = [media["media_version_id"]]
    original_bytes = source.read_bytes()
    for i in range(1, len(roles)):
        different = workspace.work_root / f"source-{i}.png"
        different.write_bytes(original_bytes + bytes([i]))
        media_ids.append(MediaService(database, workspace).import_file(str(project["id"]), different, media_kind="IMAGE")["media_version_id"])
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]),
        "I2V",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {
            "workflow_version_id": str(version["id"]),
            "semantic_inputs": {},
            "media_bindings": [{"role": role, "media_version_id": media_id, "ordinal": 0} for role, media_id in zip(roles, media_ids)],
        },
        "comfy-media-binding",
    )
    service = ComfyGenerationService(database, workspace)
    captured = {}

    def queue_prompt(workflow, **_kwargs):
        captured.update(workflow)
        return {"prompt_id": "materialized-prompt"}

    monkeypatch.setattr(service.comfy, "queue_prompt", queue_prompt)
    submitted = service.submit_next("media-worker")
    assert submitted is not None
    filename = captured["1"]["inputs"]["image"]
    assert filename.startswith(str(media["media_version_id"]))
    assert (workspace.comfy_input_root / filename).read_bytes() == source.read_bytes()
    for i, media_id in enumerate(media_ids, 1):
        input_name = captured[str(i)]["inputs"]["image"]
        assert input_name.startswith(media_id)
        assert (workspace.comfy_input_root / input_name).exists()
    assert len({captured[str(i)]["inputs"]["image"] for i in range(1, len(roles) + 1)}) == len(roles)


def test_submit_drops_undeclared_metadata_roles_but_compiles_declared_ones(workspace, database, monkeypatch) -> None:
    """The variant workbench freezes camera_plan/timed_directions/... metadata into
    the job snapshot; those roles must never be compiled into workflow node inputs
    unless the published workflow declares them (FR-CTL-001 integration)."""
    workspace = workspace.model_copy(update={"comfy_input_root": workspace.work_root / "comfy-production" / "input"})
    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "comfy_metadata_roles",
        "Comfy metadata roles",
        {"1": {"class_type": "LoadImage", "inputs": {"image": "pending.png"}}},
        {},
        {"FIRST_FRAME": {"node_id": "1", "input": "image"}},
    )
    _publish_offline(workflow_service, str(version["id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="comfy_metadata_project",
        title="Comfy metadata project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "meta-source.png"
    source.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
        )
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]),
        "GENERATION_VARIANT",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {
            "workflow_version_id": str(version["id"]),
            "semantic_inputs": {
                "camera_plan": {"mode": "NATIVE", "movement": "PUSH_IN"},
                "timed_directions": [],
                "performance_bindings": [],
                "motion_masks": [],
            },
            "media_bindings": [{"role": "FIRST_FRAME", "media_version_id": media["media_version_id"], "ordinal": 0}],
        },
        "comfy-metadata-roles",
    )
    service = ComfyGenerationService(database, workspace)
    captured = {}

    def queue_prompt(workflow, **_kwargs):
        captured.update(workflow)
        return {"prompt_id": "metadata-prompt"}

    monkeypatch.setattr(service.comfy, "queue_prompt", queue_prompt)
    submitted = service.submit_next("metadata-worker")
    assert submitted is not None
    # Undeclared metadata roles are dropped; the declared FIRST_FRAME slot is compiled.
    filename = captured["1"]["inputs"]["image"]
    assert filename.startswith(str(media["media_version_id"]))
    assert "camera_plan" not in captured["1"]["inputs"]
    assert "timed_directions" not in captured["1"]["inputs"]


def test_run_once_binds_worker_session_and_polls_to_success(workspace, database, monkeypatch) -> None:
    import json
    from contextlib import contextmanager
    service, _unused_attempt_id = _active_attempt(workspace, database)
    # _active_attempt claims its fixture, so create a fresh service/database
    # fixture dedicated to the production run_once bridge.
    with database.transaction() as connection:
        connection.execute("DELETE FROM job_resource_leases")
        connection.execute("DELETE FROM job_attempts")
        connection.execute("DELETE FROM jobs")
    workflows = WorkflowService(database)
    version = workflows.register_package(
        "comfy_supervisor_bridge",
        "Comfy supervisor bridge",
        {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "bridge"}}},
        {},
        {},
    )
    _publish_offline(workflows, str(version["id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="comfy_supervisor_project",
        title="Comfy supervisor project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    job = JobService(database, workspace).create_job(
        str(project["id"]),
        "GENERATION_VARIANT",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}},
        "comfy-supervisor-bridge",
    )
    session = WorkerSessionService(database, workspace).start_session(
        "gpu-worker",
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["GPU_H3"],
    )
    service = ComfyGenerationService(database, workspace)
    runtime_events = []

    class WaitingCoordinator:
        @contextmanager
        def session(self, _runtime, *, on_wait, **_kwargs):
            on_wait()
            with database.connect() as connection:
                row = connection.execute("SELECT progress_json FROM jobs WHERE id=?", (job["id"],)).fetchone()
            assert json.loads(row["progress_json"])["phase"] == "WAITING_FOR_GPU"
            runtime_events.append("acquired")
            yield
            runtime_events.append("released")

    service.gpu_coordinator = WaitingCoordinator()

    def queue_after_acquisition(*_args, **_kwargs):
        assert runtime_events == ["acquired"]
        return {"prompt_id": "bridge-prompt"}

    monkeypatch.setattr(service.comfy, "queue_prompt", queue_after_acquisition)
    monkeypatch.setattr(
        service,
        "poll_attempt",
        lambda attempt_id, worker_id: {
            "status": "SUCCEEDED",
            "result": {"job": {"id": job["id"], "state": "SUCCEEDED"}},
            "attempt_id": attempt_id,
            "worker_id": worker_id,
        },
    )
    result = service.run_once("gpu-worker", worker_session_id=str(session["id"]), sleep=lambda _seconds: None)
    assert result is not None and result["poll"]["status"] == "SUCCEEDED"
    assert runtime_events == ["acquired", "released"]
    with database.connect() as connection:
        attempt = connection.execute("SELECT worker_session_id FROM job_attempts WHERE job_id=?", (job["id"],)).fetchone()
    assert attempt is not None and attempt["worker_session_id"] == session["id"]
