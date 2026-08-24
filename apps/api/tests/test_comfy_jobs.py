from __future__ import annotations

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
        code="comfy_poll_project", title="Comfy poll project", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]), "COMFY_STATE_TEST", "WORKFLOW_VERSION", str(version["id"]), "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}}, "comfy-state-test",
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
        row = connection.execute("SELECT a.state AS attempt_state,j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt_id,)).fetchone()
        artifacts = connection.execute("SELECT COUNT(*) FROM artifacts WHERE job_attempt_id=?", (attempt_id,)).fetchone()[0]
    assert row["attempt_state"] == "SUCCEEDED"
    assert row["job_state"] == "SUCCEEDED"
    assert artifacts == 1


def test_submit_materializes_verified_media_binding_inside_isolated_input_root(workspace, database, monkeypatch) -> None:
    workspace = workspace.model_copy(update={"comfy_input_root": workspace.work_root / "comfy-production" / "input"})
    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "comfy_media_binding",
        "Comfy media binding",
        {"1": {"class_type": "LoadImage", "inputs": {"image": "pending.png"}}},
        {},
        {"FIRST_FRAME": {"node_id": "1", "input": "image"}},
    )
    _publish_offline(workflow_service, str(version["id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="comfy_media_input", title="Comfy media input", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "source.png"
    source.write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]), "I2V", "WORKFLOW_VERSION", str(version["id"]), "GPU_H3",
        {
            "workflow_version_id": str(version["id"]),
            "semantic_inputs": {},
            "media_bindings": [{"role": "FIRST_FRAME", "media_version_id": media["media_version_id"], "ordinal": 0}],
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
        code="comfy_metadata_project", title="Comfy metadata project", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "meta-source.png"
    source.write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]), "GENERATION_VARIANT", "WORKFLOW_VERSION", str(version["id"]), "GPU_H3",
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
    monkeypatch.setattr(service.comfy, "queue_prompt", lambda *_args, **_kwargs: {"prompt_id": "bridge-prompt"})
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
    with database.connect() as connection:
        attempt = connection.execute("SELECT worker_session_id FROM job_attempts WHERE job_id=?", (job["id"],)).fetchone()
    assert attempt is not None and attempt["worker_session_id"] == session["id"]
