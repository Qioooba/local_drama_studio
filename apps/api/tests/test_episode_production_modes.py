from __future__ import annotations

import pytest

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError


def _episode(workspace, database, code: str) -> tuple[dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    return project, projects.list_episodes(str(season["id"]))[0]


def test_episode_preflight_rejects_unknown_production_mode(workspace, database) -> None:
    _project, episode = _episode(workspace, database, "episode_mode_invalid")
    service = EpisodeProductionRunService(database, workspace)

    with pytest.raises(DomainRuleError) as error:
        service.preflight(str(episode["id"]), production_mode="turbo", min_free_disk_bytes=1)

    assert error.value.code == "EPISODE_PRODUCTION_MODE_INVALID"


def test_mode_is_fingerprinted_and_frozen_in_workflow_snapshot(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_snapshot")
    service = EpisodeProductionRunService(database, workspace)
    draft = service.preflight(str(episode["id"]), tts_enabled=False, production_mode="draft", min_free_disk_bytes=1)
    quality = service.preflight(str(episode["id"]), tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1)

    assert draft["production_mode"] == "DRAFT"
    assert draft["mode_policy"]["target_take_count"] == 1
    assert quality["mode_policy"]["target_take_count"] == 4
    assert draft["input_fingerprint"] != quality["input_fingerprint"]

    episode_context = {**episode, "project_id": project["id"]}
    draft_workflow = service._workflow_for_snapshot(episode_context, draft, actor="test")
    quality_workflow = service._workflow_for_snapshot(episode_context, quality, actor="test")
    assert draft_workflow["id"] != quality_workflow["id"]
    for workflow, mode, take_count in ((draft_workflow, "DRAFT", 1), (quality_workflow, "QUALITY", 4)):
        metadata = workflow["definition"]["nodes"][0]["metadata"]
        assert metadata["production_mode"] == mode
        assert metadata["mode_policy"]["target_take_count"] == take_count
        assert all(item["payload"]["production_mode"] == mode for item in workflow["definition"]["batch_items"])
        assert all(item["payload"]["mode_policy"]["target_take_count"] == take_count for item in workflow["definition"]["batch_items"])

    # Creating another mode-specific workflow never mutates the prior frozen definition.
    reread_draft = service.automation.get_workflow(str(draft_workflow["id"]))
    assert reread_draft["definition"]["nodes"][0]["metadata"]["mode_policy"]["target_take_count"] == 1


@pytest.mark.parametrize(("mode", "target_take_count"), (("DRAFT", 1), ("BALANCED", 2), ("QUALITY", 4)))
def test_video_action_submits_mode_target_candidate_count(
    workspace, database, monkeypatch: pytest.MonkeyPatch, mode: str, target_take_count: int,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-mode", "code": "SHOT-MODE"}
    submitted_take_indexes: list[int] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-mode", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)

    def submit(_project_id, _shot, _run_id, _task_id, *, take_index=0):
        submitted_take_indexes.append(take_index)
        return {
            "shot_id": "shot-mode",
            "shot_code": "SHOT-MODE",
            "status": "SUBMITTED",
            "variant_id": f"variant-{take_index}",
            "job_id": f"job-{take_index}",
            "take_index": take_index,
        }

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, produced_bytes = service.video_generation(
        "episode-mode", "run-mode", "task-mode", target_take_count=target_take_count,
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert report["machine_check"]["target_take_count"] == target_take_count
    assert submitted_take_indexes == list(range(target_take_count)), mode


def test_recover_uses_original_quality_mode_snapshot(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_recover")
    service = EpisodeProductionRunService(database, workspace)
    preflight = service.preflight(
        str(episode["id"]), tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1_000_000,
    )
    workflow = service._workflow_for_snapshot({**episode, "project_id": project["id"]}, preflight, actor="test")
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="quality-recovery",
    )

    recovered = service.recover(str(run["id"]))

    assert recovered["run"]["production_mode"] == "QUALITY"
    assert recovered["run"]["mode_policy"]["target_take_count"] == 4
    refreshed_workflow = service.automation.get_workflow(str(workflow["id"]))
    assert refreshed_workflow["definition"]["nodes"][0]["metadata"]["production_mode"] == "QUALITY"
    assert refreshed_workflow["definition"]["batch_items"][0]["payload"]["mode_policy"]["target_take_count"] == 4


def test_quality_retry_still_fills_all_remaining_candidate_slots(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-quality-retry", "code": "SHOT-QUALITY"}
    failed = {"id": "failed-job", "variant_id": "failed-variant", "state": "FAILED"}
    submitted_take_indexes: list[int] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-quality", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [failed])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)
    monkeypatch.setattr(service.jobs, "retry", lambda _job_id, actor: {"id": "failed-job"})

    def submit(_project_id, _shot, _run_id, _task_id, *, take_index=0):
        submitted_take_indexes.append(take_index)
        return {
            "shot_id": str(_shot["id"]),
            "shot_code": str(_shot["code"]),
            "status": "SUBMITTED",
            "variant_id": f"variant-{take_index}",
            "job_id": f"job-{take_index}",
            "take_index": take_index,
        }

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, _ = service.video_generation("episode-quality", "run-quality", "task-quality", target_take_count=4)

    item = report["produced"]["items"][0]
    assert report["status"] == "PASS"
    assert item["target_take_count"] == 4
    assert [candidate["job_id"] for candidate in item["submissions"]] == [
        "failed-job",
        "job-1",
        "job-2",
        "job-3",
    ]
    assert submitted_take_indexes == [1, 2, 3]


def test_qc_automation_task_waits_for_every_video_child_job(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_dependencies")
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="mode-dependency-test",
        title="Mode dependency test",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {"key": "VIDEO", "payload": {"action": "VIDEO_GENERATION", "episode_id": episode["id"]}},
            {"key": "QC", "payload": {"action": "QC", "episode_id": episode["id"]}},
        ],
        conditions=[],
        max_iterations=3,
        max_tasks=3,
        max_disk_bytes=1_000_000,
        human_gate="NONE",
    )
    run = automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="mode-dependency-run",
    )
    jobs = JobService(database, workspace)
    video_job_id = str(run["tasks"][0]["job_id"])
    video_claim = jobs.claim("automation-video", ["CPU"])
    assert video_claim is not None and video_claim["job"]["id"] == video_job_id
    jobs.complete(
        str(video_claim["attempt"]["id"]), str(video_claim["attempt"]["lease_token"]), "automation-video", success=True,
    )
    children = [
        jobs.create_job(
            str(project["id"]), "CPU_TEST", "GENERATION_VARIANT", f"variant-{index}", "GPU_H3", {},
            f"mode-child-{index}",
        )
        for index in range(2)
    ]
    report = {
        "machine_check": {"status": "PASS", "ok": True},
        "produced": {"items": [{"status": "SUBMITTED", "submissions": [{"job_id": child["id"]} for child in children]}]},
    }
    worker = LocalMediaWorker(database, workspace)
    assert worker._advance_automation_run(jobs.get_job(video_job_id), report, 0) is None

    advanced = automation.get_run(str(run["id"]))
    qc_job_id = str(advanced["tasks"][1]["job_id"])
    with database.connect() as connection:
        dependency_ids = {
            str(row["depends_on_job_id"])
            for row in connection.execute("SELECT depends_on_job_id FROM job_dependencies WHERE job_id=?", (qc_job_id,))
        }
    assert dependency_ids == {video_job_id, *(str(child["id"]) for child in children)}
    assert jobs.claim("qc-must-wait", ["CPU"]) is None

    for index in range(2):
        claim = jobs.claim(f"gpu-{index}", ["GPU_H3"])
        assert claim is not None
        jobs.complete(
            str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), f"gpu-{index}", success=True,
        )
    qc_claim = jobs.claim("qc-after-children", ["CPU"])
    assert qc_claim is not None and qc_claim["job"]["id"] == qc_job_id


def test_qc_promotes_successful_video_artifacts_before_candidate_lookup(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-qc-promote", "code": "SHOT-QC"}
    promoted = False
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-qc", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [{"id": "gpu-job", "state": "SUCCEEDED"}])

    def promote(jobs):
        nonlocal promoted
        assert [item["id"] for item in jobs] == ["gpu-job"]
        promoted = True
        return ["video-version"]

    def video(_shot_id):
        assert promoted is True
        return {"media_version_id": "video-version", "variant_id": None}

    monkeypatch.setattr(service, "_promote_completed_outputs", promote)
    monkeypatch.setattr(service, "_shot_video", video)
    monkeypatch.setattr(service.reviews, "machine_check", lambda _media_version_id, actor: {"id": "check", "status": "PASS"})

    report, _ = service.qc("episode-qc", "run-qc", "task-qc")

    assert promoted is True
    assert report["status"] == "PASS"
    assert report["produced"]["items"][0]["media_version_id"] == "video-version"
