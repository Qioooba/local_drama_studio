from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.projects import ProjectService
from local_drama.application.storyboard_generation_batches import StoryboardGenerationBatchService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project_episode_shots(workspace, database, code: str):
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
    episode = projects.list_episodes(str(season["id"]))[0]
    shots = [projects.create_shot(str(episode["id"]), f"S{index:03d}", 4_000) for index in range(1, 3)]
    return project, episode, shots


def _ready_preflight(project_id: str, episode_id: str, shots: list[dict]) -> dict:
    items = [
        {
            "shot_id": str(shot["id"]),
            "shot_code": str(shot["code"]),
            "shot_revision": int(shot["revision"]),
            "shot_revision_id": str(shot["current_revision_id"]),
            "prompt": f"镜头 {shot['code']}，统一视觉修饰：雨夜，冷色调",
            "prompt_modifiers": ["雨夜", "冷色调"],
            "profile_version_id": "profile-video",
            "keyframe_media_version_id": f"keyframe-{shot['id']}",
            "first_frame_role": "FIRST_FRAME",
            "status": "READY",
            "blockers": [],
        }
        for shot in shots
    ]
    source = {"episode_id": episode_id, "project_id": project_id, "items": items}
    return {
        **source,
        "status": "READY",
        "input_fingerprint": hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
        "runtime_contacted": False,
        "network_contacted": False,
        "mutated": False,
    }


def test_batch_plan_and_submit_create_recoverable_selected_shot_workflow(workspace, database, monkeypatch) -> None:
    project, episode, shots = _project_episode_shots(workspace, database, "story_batch_dispatch")
    preflight = _ready_preflight(str(project["id"]), str(episode["id"]), shots)
    monkeypatch.setattr(EpisodeWorkerActionService, "video_generation_preflight", lambda self, episode_id, *, target_shot_ids: preflight)
    targets = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])} for shot in shots]
    service = StoryboardGenerationBatchService(
        EpisodeWorkerActionService(database, workspace),
        AutomationWorkflowService(database),
    )

    plan = service.plan(str(episode["id"]), targets)
    assert plan["valid"] is True
    assert plan["summary"] == {"selected": 2, "ready": 2, "blocked": 0}
    result = service.submit(
        str(episode["id"]),
        targets,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="storyboard-batch-1",
    )
    replay = service.submit(
        str(episode["id"]),
        targets,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="storyboard-batch-1",
    )
    assert replay["run_id"] == result["run_id"]
    assert replay["idempotent_replay"] is True
    workflow = AutomationWorkflowService(database).get_workflow(str(result["workflow_id"]))
    batch_items = workflow["definition"]["batch_items"]
    assert [item["payload"]["target_shot_ids"] for item in batch_items] == [[str(shots[0]["id"])], [str(shots[1]["id"])]]
    assert all(item["payload"]["force_new_take"] is True for item in batch_items)
    assert workflow["definition"]["nodes"][0]["metadata"]["workflow_scope"] == "STORYBOARD_SELECTED_SHOTS"


def test_batch_plan_reports_revision_conflict_and_submit_rejects_stale_plan(workspace, database, monkeypatch) -> None:
    project, episode, shots = _project_episode_shots(workspace, database, "story_batch_stale")
    preflight = _ready_preflight(str(project["id"]), str(episode["id"]), shots)
    preflight["items"] = preflight["items"][:1]
    monkeypatch.setattr(EpisodeWorkerActionService, "video_generation_preflight", lambda self, episode_id, *, target_shot_ids: preflight)
    targets = [{"shot_id": str(shots[0]["id"]), "expected_revision": int(shots[0]["revision"]) + 1}]
    service = StoryboardGenerationBatchService(
        EpisodeWorkerActionService(database, workspace),
        AutomationWorkflowService(database),
    )

    plan = service.plan(str(episode["id"]), targets)
    assert plan["valid"] is False
    assert [issue["code"] for issue in plan["issues"]] == ["SHOT_REVISION_CONFLICT"]
    with pytest.raises(DomainRuleError) as stale:
        service.submit(str(episode["id"]), targets, expected_plan_hash="0" * 64, idempotency_key="stale")
    assert stale.value.code == "SHOT_BATCH_PLAN_STALE"


def test_batch_api_exposes_explicit_plan_then_submit(workspace, database, monkeypatch) -> None:
    project, episode, shots = _project_episode_shots(workspace, database, "story_batch_api")
    preflight = _ready_preflight(str(project["id"]), str(episode["id"]), shots)
    monkeypatch.setattr(EpisodeWorkerActionService, "video_generation_preflight", lambda self, episode_id, *, target_shot_ids: preflight)
    targets = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])} for shot in shots]
    with TestClient(create_app(workspace)) as client:
        planned = client.post(
            f"/api/v2/episodes/{episode['id']}/storyboard-generation-batches:plan",
            json={"targets": targets},
        )
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]
        submitted = client.post(
            f"/api/v2/episodes/{episode['id']}/storyboard-generation-batches:submit",
            json={"targets": targets, "expected_plan_hash": plan["plan_hash"], "idempotency_key": "api-batch-1"},
        )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["batch"]["selected_shot_ids"] == [str(shot["id"]) for shot in shots]
