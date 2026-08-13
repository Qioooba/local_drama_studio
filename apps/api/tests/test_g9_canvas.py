from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_g9_lazy_canvas_layout_is_separate_from_dependencies_and_preflights(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="g9_canvas",
        title="G9 canvas",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    for number in range(1, 63):
        projects.create_shot(str(episode["id"]), f"S{number:03d}", 1000)

    with TestClient(create_app(workspace)) as client:
        first = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?limit=20")
        assert first.status_code == 200, first.text
        graph = first.json()["graph"]
        assert graph["page"] == {"cursor": 0, "limit": 20, "returned_shots": 20, "total_shots": 62, "next_cursor": 20}
        assert len(graph["nodes"]) == 100
        assert graph["invariants"]["layout_changes_business_dependencies"] is False
        original_edges = graph["edges"]
        node_id = graph["nodes"][0]["id"]

        saved = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"positions": {node_id: {"x": 321.5, "y": 42}}, "groups": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["layout"]["revision"] == 1
        second = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?limit=20").json()["graph"]
        assert second["edges"] == original_edges
        assert second["nodes"][0]["position"] == {"x": 321.5, "y": 42.0}

        stale = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"expected_revision": 99, "positions": {node_id: {"x": 1, "y": 1}}},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        unknown = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"expected_revision": 1, "positions": {"shot:unknown:direct": {"x": 1, "y": 1}}},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "CANVAS_LAYOUT_NODE_UNKNOWN"

        plan = client.post(
            f"/api/v1/canvas/EPISODE/{episode['id']}/runs:preflight",
            json={"mode": "RANGE", "from_node_id": graph["nodes"][0]["id"], "to_node_id": graph["nodes"][4]["id"], "max_nodes": 10},
        )
        assert plan.status_code == 201, plan.text
        result = plan.json()["plan"]
        assert result["submitted"] is False
        assert result["estimate"]["node_count"] == 5
        assert result["estimate"]["max_parallel_gpu"] == 1
        assert result["status"] == "BLOCKED"

        next_page = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?cursor=20&limit=20").json()["graph"]
        assert next_page["page"]["returned_shots"] == 20
        assert next_page["nodes"][0]["shot_code"] == "S021"
