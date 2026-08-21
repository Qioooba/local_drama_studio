from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app
from tests.test_generation_variants import _project


def _episode_context(workspace, database, code: str):
    project = _project(workspace, database, code)
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    scene = service.create_scene(str(project["id"]), "SCENE_001", "便利店夜景")
    shots = [
        service.create_shot(str(episode["id"]), "SHOT_001", 2000, "WIDE"),
        service.create_shot(str(episode["id"]), "SHOT_002", 1600, "CLOSE"),
    ]
    return project, episode, scene, shots


def test_shot_group_vertical_slice_preserves_shots(workspace, database) -> None:
    project, episode, scene, shots = _episode_context(workspace, database, "shot_groups")
    _, _, foreign_scene, _ = _episode_context(workspace, database, "shot_groups_foreign")
    episode_id = str(episode["id"])
    with TestClient(create_app(workspace)) as client:
        assigned = client.post(
            f"/api/v1/shots/{shots[0]['id']}:assign-scene",
            json={"scene_id": scene["id"], "expected_revision": shots[0]["revision"]},
        )
        assert assigned.status_code == 200
        assert assigned.json()["shot"]["scene_id"] == scene["id"]
        rejected = client.post(
            f"/api/v1/shots/{shots[1]['id']}:assign-scene",
            json={"scene_id": foreign_scene["id"], "expected_revision": shots[1]["revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "SHOT_SCENE_SCOPE_INVALID"

        first = client.post(f"/api/v1/episodes/{episode_id}/shot-groups", json={
            "kind": "BEAT", "code": "BEAT_001", "title": "开场冲突",
            "scene_id": scene["id"], "metadata": {"intent": "hook"},
        }).json()["group"]
        second = client.post(f"/api/v1/episodes/{episode_id}/shot-groups", json={
            "kind": "DIALOGUE", "code": "DIALOGUE_001", "title": "第一次对话",
            "scene_id": scene["id"], "metadata": {},
        }).json()["group"]
        grouped = client.put(f"/api/v1/shot-groups/{first['id']}/members", json={
            "shot_ids": [shots[0]["id"], shots[1]["id"]], "expected_revision": first["revision"],
        }).json()["group"]
        assert [member["shot_id"] for member in grouped["members"]] == [shots[0]["id"], shots[1]["id"]]

        moved = client.put(f"/api/v1/shot-groups/{second['id']}/members", json={
            "shot_ids": [shots[1]["id"]], "expected_revision": second["revision"],
        }).json()["group"]
        assert moved["members"][0]["shot_id"] == shots[1]["id"]
        reordered = client.post(f"/api/v1/episodes/{episode_id}/shot-groups:reorder", json={"items": [
            {"group_id": second["id"], "order_key": "0001", "expected_revision": moved["revision"]},
            {"group_id": first["id"], "order_key": "0002", "expected_revision": grouped["revision"]},
        ]})
        assert reordered.status_code == 200
        ordered = reordered.json()["groups"]
        assert [group["id"] for group in ordered] == [second["id"], first["id"]]

        archived = client.post(f"/api/v1/shot-groups/{first['id']}:archive", json={
            "expected_revision": next(group["revision"] for group in ordered if group["id"] == first["id"]),
        })
        assert archived.status_code == 200
        assert archived.json()["group"]["status"] == "ARCHIVED"
        workspace_body = client.get(f"/api/v1/episodes/{episode_id}/shot-groups").json()["workspace"]
        assert workspace_body["episode"]["project_id"] == project["id"]
        assert len(workspace_body["shots"]) == 2
        assert any(group["status"] == "ARCHIVED" for group in workspace_body["groups"])


def test_shot_group_revision_conflict_is_409(workspace, database) -> None:
    _, episode, _, _ = _episode_context(workspace, database, "shot_group_conflict")
    with TestClient(create_app(workspace)) as client:
        group = client.post(f"/api/v1/episodes/{episode['id']}/shot-groups", json={
            "kind": "CUSTOM", "code": "CUSTOM_001", "title": "并发保护",
        }).json()["group"]
        response = client.post(f"/api/v1/shot-groups/{group['id']}:archive", json={"expected_revision": 99})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SHOT_GROUP_REVISION_CONFLICT"
