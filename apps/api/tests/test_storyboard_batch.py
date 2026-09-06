from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.dialogue import DialogueService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.main import create_app


def _episode(workspace, database):
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(
        code="batch",
        title="分镜批量台",
        episode_count=1,
        aspect_ratio=None,
        fps_num=None,
        fps_den=None,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    shots = [service.create_shot(str(episode["id"]), f"SH-{index:03d}", index * 1_000, "OTHER") for index in range(1, 4)]
    shot_studio_command_service(database).save_draft_revision(str(shots[0]["id"]), {"action": "开门", "dialogue": "谁？"})
    return service, episode, service.list_shots(str(episode["id"]))


def test_batch_reorder_edit_and_copy_keep_identity_and_history(workspace, database) -> None:
    service, episode, shots = _episode(workspace, database)
    episode_id = str(episode["id"])
    original_ids = [str(shot["id"]) for shot in shots]
    original_revision_ids = {str(shot["id"]): str(shot["current_revision_id"]) for shot in shots}
    payload = {
        "ordered_shot_ids": list(reversed(original_ids)),
        "edits": [
            {
                "shot_id": original_ids[0],
                "expected_revision": int(shots[0]["revision"]),
                "target_duration_ms": 4_500,
                "shot_type": "CLOSE_UP",
                "fields": {"action": "推门进入"},
            }
        ],
        "copies": [{"source_shot_id": original_ids[0], "code": "SH-004"}],
    }
    plan = service.plan_storyboard_batch(episode_id, payload)
    assert plan["valid"] is True
    assert plan["summary"] == {"reordered": 2, "edited": 1, "copied": 1}
    result = service.commit_storyboard_batch(episode_id, payload, str(plan["plan_hash"]))
    items = result["storyboard"]["items"]
    assert [str(item["id"]) for item in items[:3]] == list(reversed(original_ids))
    assert {str(item["id"]) for item in items[:3]} == set(original_ids)
    edited = next(item for item in items if str(item["id"]) == original_ids[0])
    copied = next(item for item in items if str(item["id"]) in result["copied_shot_ids"])
    assert edited["target_duration_ms"] == 4_500
    assert edited["fields"]["action"] == "推门进入"
    assert copied["id"] not in original_ids
    assert copied["fields"]["dialogue"] == "谁？"
    with database.connect() as connection:
        history_count = connection.execute("SELECT COUNT(*) FROM shot_revisions WHERE shot_id=?", (original_ids[0],)).fetchone()[0]
        prior_revision = connection.execute("SELECT 1 FROM shot_revisions WHERE id=?", (original_revision_ids[original_ids[0]],)).fetchone()
    assert history_count == 3
    assert prior_revision is not None


def test_batch_plan_reports_item_conflicts_and_commit_is_atomic(workspace, database) -> None:
    service, episode, shots = _episode(workspace, database)
    episode_id = str(episode["id"])
    ids = [str(shot["id"]) for shot in shots]
    invalid = {
        "ordered_shot_ids": [ids[0], ids[0]],
        "edits": [{"shot_id": ids[1], "expected_revision": 99, "target_duration_ms": 2_000}],
        "copies": [{"source_shot_id": ids[2], "code": "SH-001"}],
    }
    plan = service.plan_storyboard_batch(episode_id, invalid)
    assert plan["valid"] is False
    assert {issue["code"] for issue in plan["issues"]} == {"ORDER_SET_MISMATCH", "SHOT_REVISION_CONFLICT", "SHOT_CODE_CONFLICT"}
    before = service.get_storyboard_workspace(episode_id)
    with pytest.raises(DomainRuleError) as error:
        service.commit_storyboard_batch(episode_id, invalid, str(plan["plan_hash"]))
    assert error.value.code == "STORYBOARD_BATCH_INVALID"
    assert service.get_storyboard_workspace(episode_id) == before
    with pytest.raises(DomainRuleError) as stale:
        service.commit_storyboard_batch(episode_id, {"ordered_shot_ids": ids, "edits": [], "copies": []}, "0" * 64)
    assert stale.value.code == "STORYBOARD_PLAN_STALE"


def test_storyboard_projects_latest_structured_dialogue_without_rewriting_director_history(workspace, database) -> None:
    service, episode, shots = _episode(workspace, database)
    episode_id, shot_id = str(episode["id"]), str(shots[0]["id"])
    original = service.get_storyboard_workspace(episode_id)["items"][0]
    dialogue = DialogueService(database, workspace)
    line = dialogue.create_line(episode_id, code="L01", speaker="林晚", text="原句", pronunciation={}, shot_id=shot_id)
    dialogue.revise_text(str(line["id"]), expected_revision_no=1, text="谁发出的文件？", pronunciation={})
    current = service.get_storyboard_workspace(episode_id)["items"]
    assert current[0]["current_dialogue"] == "林晚：谁发出的文件？"
    assert current[0]["fields"] == original["fields"]
    assert current[0]["current_revision_id"] == original["current_revision_id"]
    assert current[1]["current_dialogue"] is None


def test_storyboard_api_exposes_three_views_and_explicit_plan_commit(workspace, database) -> None:
    _, episode, shots = _episode(workspace, database)
    episode_id = str(episode["id"])
    ids = [str(shot["id"]) for shot in shots]
    with TestClient(create_app(workspace)) as client:
        workspace_response = client.get(f"/api/v1/projects/episodes/{episode_id}/storyboard")
        assert workspace_response.status_code == 200
        assert workspace_response.json()["storyboard"]["views"] == ["TABLE", "STORYBOARD", "TIMELINE"]
        payload = {"ordered_shot_ids": list(reversed(ids)), "edits": [], "copies": []}
        plan_response = client.post(f"/api/v1/projects/episodes/{episode_id}/storyboard:plan", json=payload)
        assert plan_response.status_code == 200
        plan = plan_response.json()["plan"]
        committed = client.post(f"/api/v1/projects/episodes/{episode_id}/storyboard:commit", json={**payload, "expected_plan_hash": plan["plan_hash"]})
        assert committed.status_code == 200
        assert [item["id"] for item in committed.json()["result"]["storyboard"]["items"]] == list(reversed(ids))
