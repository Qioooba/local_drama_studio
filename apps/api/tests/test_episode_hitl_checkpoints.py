from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _episode(workspace, database, code: str) -> tuple[dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    return project, projects.list_episodes(str(season["id"]))[0]


def _workflow(service: EpisodeProductionRunService, project: dict, episode: dict, policy: str):
    preflight = service.preflight(
        str(episode["id"]), tts_enabled=False, checkpoint_policy=policy, min_free_disk_bytes=1,
    )
    return preflight, service._workflow_for_snapshot(
        {**episode, "project_id": project["id"]}, preflight, actor="checkpoint-test",
    )


def test_checkpoint_policy_is_validated_fingerprinted_and_frozen(workspace, database) -> None:
    project, episode = _episode(workspace, database, "checkpoint_snapshot")
    service = EpisodeProductionRunService(database, workspace)
    automatic, automatic_workflow = _workflow(service, project, episode, "auto_continue")
    before_video, before_video_workflow = _workflow(service, project, episode, "BEFORE_VIDEO")

    assert automatic["checkpoint_policy"] == "AUTO_CONTINUE"
    assert before_video["checkpoint_policy"] == "BEFORE_VIDEO"
    assert automatic["input_fingerprint"] != before_video["input_fingerprint"]
    assert automatic_workflow["id"] != before_video_workflow["id"]
    definition = before_video_workflow["definition"]
    assert definition["nodes"][0]["metadata"]["checkpoint_policy"] == "BEFORE_VIDEO"
    assert all(item["payload"]["checkpoint_policy"] == "BEFORE_VIDEO" for item in definition["batch_items"])

    with pytest.raises(DomainRuleError) as error:
        service.preflight(str(episode["id"]), checkpoint_policy="EVERYTHING")
    assert error.value.code == "EPISODE_CHECKPOINT_POLICY_INVALID"


@pytest.mark.parametrize("policy", ["AFTER_ASSETS", "AFTER_SHOT_PLAN"])
def test_creator_checkpoint_gates_first_production_task_and_resume_consumes_once(
    workspace, database, policy: str,
) -> None:
    project, episode = _episode(workspace, database, f"checkpoint_{policy.lower()}")
    service = EpisodeProductionRunService(database, workspace)
    _preflight, workflow = _workflow(service, project, episode, policy)
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key=f"run-{policy}",
    )

    assert run["status"] == "PAUSED_HITL"
    assert run["pending_gate"] == {
        "reason": "CONFIGURED_CREATOR_CHECKPOINT",
        "checkpoint_policy": policy,
        "next_action": "KEYFRAME_CHECK",
        "source": "workflow_snapshot",
        "ai_score_ignored": True,
    }
    assert len(run["tasks"]) == 1
    assert run["tasks"][0]["job_state"] == "NEEDS_ATTENTION"

    resumed = service.automation.resume_run(
        str(run["id"]), decision="HUMAN_APPROVED", note=f"approve {policy}",
    )
    assert resumed["status"] == "RUNNING"
    assert resumed["tasks"][0]["job_state"] == "QUEUED"
    with pytest.raises(DomainRuleError) as second_resume:
        service.automation.resume_run(
            str(run["id"]), decision="HUMAN_APPROVED", note="must not consume twice",
        )
    assert second_resume.value.code == "AUTOMATION_HITL_NOT_PENDING"
    assert service.automation.get_run(str(run["id"]))["task_count"] == 1


def test_before_video_checkpoint_parks_video_job_then_continues_to_qc_once(workspace, database) -> None:
    project, episode = _episode(workspace, database, "checkpoint_before_video")
    service = EpisodeProductionRunService(database, workspace)
    _preflight, workflow = _workflow(service, project, episode, "BEFORE_VIDEO")
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="run-before-video",
    )
    jobs = JobService(database, workspace)
    first_job_id = str(run["tasks"][0]["job_id"])
    claim = jobs.claim("checkpoint-keyframe", ["CPU"])
    assert claim is not None and claim["job"]["id"] == first_job_id
    jobs.complete(
        str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "checkpoint-keyframe", success=True,
    )
    gated = service.automation.step_run(
        str(run["id"]), machine_context={"status": "PASS", "machine_check": {"status": "PASS"}},
        expected_completed_job_id=first_job_id,
    )

    assert gated["status"] == "PAUSED_HITL"
    assert gated["pending_gate"]["checkpoint_policy"] == "BEFORE_VIDEO"
    assert gated["pending_gate"]["next_action"] == "VIDEO_GENERATION"
    assert gated["tasks"][1]["job_state"] == "NEEDS_ATTENTION"
    resumed = service.automation.resume_run(
        str(run["id"]), decision="HUMAN_APPROVED", note="approve video generation",
    )
    video_job_id = str(resumed["tasks"][1]["job_id"])
    claim = jobs.claim("checkpoint-video", ["CPU"])
    assert claim is not None and claim["job"]["id"] == video_job_id
    jobs.complete(
        str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "checkpoint-video", success=True,
    )
    continued = service.automation.step_run(
        str(run["id"]), machine_context={"status": "PASS", "machine_check": {"status": "PASS"}},
        expected_completed_job_id=video_job_id,
    )
    assert continued["status"] == "RUNNING"
    assert continued["task_count"] == 3
    assert continued["tasks"][2]["item"]["payload"]["action"] == "QC"


def test_recovery_preserves_checkpoint_gate_and_resume_does_not_duplicate_task(workspace, database) -> None:
    project, episode = _episode(workspace, database, "checkpoint_recovery")
    service = EpisodeProductionRunService(database, workspace)
    _preflight, workflow = _workflow(service, project, episode, "AFTER_SHOT_PLAN")
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="run-checkpoint-recovery",
    )

    recovered = service.recover(str(run["id"]))
    assert recovered["run"]["status"] == "PAUSED_HITL"
    assert recovered["run"]["checkpoint_policy"] == "AFTER_SHOT_PLAN"
    assert recovered["run"]["pending_gate"]["checkpoint_policy"] == "AFTER_SHOT_PLAN"
    assert recovered["run"]["input_fingerprint"] == recovered["recovery"]["input_fingerprint"]
    resumed = service.resume(str(run["id"]), note="resume frozen checkpoint")
    assert resumed["status"] == "RUNNING"
    assert resumed["checkpoint_policy"] == "AFTER_SHOT_PLAN"
    assert len(service.automation.get_run(str(run["id"]))["tasks"]) == 1


def test_recovery_treats_legacy_workflow_without_checkpoint_snapshot_as_on_exception(workspace, database) -> None:
    project, episode = _episode(workspace, database, "checkpoint_legacy_recovery")
    service = EpisodeProductionRunService(database, workspace)
    legacy = service.preflight(
        str(episode["id"]), tts_enabled=False, production_mode="BALANCED",
        checkpoint_policy="ON_EXCEPTION", min_free_disk_bytes=1,
        _include_checkpoint_in_fingerprint=False,
    )
    workflow = service.automation.create_workflow(
        str(project["id"]), code="legacy-checkpoint-workflow", title="Legacy checkpoint workflow",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode-production", "type": "EPISODE_PRODUCTION_TASK", "metadata": {
            "episode_id": str(episode["id"]), "input_fingerprint": legacy["input_fingerprint"],
            "production_mode": "BALANCED", "mode_policy": legacy["mode_policy"],
        }}],
        batch_items=[{"key": "KEYFRAME", "payload": {
            "action": "KEYFRAME_CHECK", "episode_id": str(episode["id"]),
            "input_fingerprint": legacy["input_fingerprint"], "production_mode": "BALANCED",
            "mode_policy": legacy["mode_policy"],
        }}],
        conditions=[{"field": "machine_check.status", "operator": "IN", "value": ["FAILED"], "action": "PAUSE_HITL"}],
        max_iterations=2, max_tasks=2, max_disk_bytes=1_000_000, human_gate="ON_CONDITION",
    )
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="legacy-checkpoint-run",
    )

    recovered = service.recover(str(run["id"]))

    assert recovered["run"]["checkpoint_policy"] == "ON_EXCEPTION"
    assert recovered["recovery"]["input_fingerprint"] == legacy["input_fingerprint"]
    assert recovered["recovery"]["refreshed_task_ids"] == []


def test_checkpoint_policy_api_defaults_and_rejects_unknown_values(workspace, database) -> None:
    _project, episode = _episode(workspace, database, "checkpoint_api")
    with TestClient(create_app(workspace)) as client:
        defaulted = client.get(
            f"/api/v1/episodes/{episode['id']}/production-runs/preflight",
            params={"min_free_disk_bytes": 1},
        )
        assert defaulted.status_code == 200
        assert defaulted.json()["preflight"]["checkpoint_policy"] == "ON_EXCEPTION"
        invalid_query = client.get(
            f"/api/v1/episodes/{episode['id']}/production-runs/preflight",
            params={"checkpoint_policy": "EVERYTHING"},
        )
        invalid_body = client.post(
            f"/api/v1/episodes/{episode['id']}/production-runs",
            headers={"Idempotency-Key": "invalid-checkpoint"},
            json={"checkpoint_policy": "EVERYTHING"},
        )
    assert invalid_query.status_code == 422
    assert invalid_body.status_code == 422
