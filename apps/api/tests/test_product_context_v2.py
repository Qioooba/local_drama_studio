from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.main import create_app


def test_v2_context_and_overview_are_typed_bounded_projections(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        created = client.post("/api/v1/projects", json={
            "code": "overview_v2", "title": "聚合首页", "episode_count": 1,
            "target_duration_ms": 60000, "aspect_ratio": "9:16", "width": 1080, "height": 1920,
            "fps": {"numerator": 24, "denominator": 1}, "primary_language": "zh-CN",
            "subtitle_mode": "NONE", "allow_unconfigured_capabilities": True,
        })
        assert created.status_code == 201
        project = created.json()["project"]
        catalog = client.get(f"/api/v1/projects/{project['id']}/episode-catalog").json()["catalog"]
        episode = catalog["seasons"][0]["episodes"][0]

        context_response = client.get("/api/v2/app-context", params={"project_id": project["id"], "episode_id": episode["id"]})
        assert context_response.status_code == 200
        context = context_response.json()
        assert context["project"]["title"] == "聚合首页"
        assert context["episode"]["id"] == episode["id"]
        assert context["task_summary"] == {"active_worker_count": 0, "attention_job_count": 0}
        assert context["read_only"] is True and context["runtime_contacted"] is False and context["mutated"] is False

        overview_response = client.get(f"/api/v2/projects/{project['id']}/overview")
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["project"]["title"] == "聚合首页"
        assert overview["next_action"]["reason_code"] == "reviewable_story_draft_count"
        assert overview["next_action"]["target"]["kind"] == "STORY"
        assert len(overview["seasons"]) == 1 and len(overview["seasons"][0]["episodes"]) == 1
        assert overview["network_contacted"] is False and overview["mutated"] is False


def test_v2_context_rejects_cross_project_episode(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        projects = []
        for index in (1, 2):
            response = client.post("/api/v1/projects", json={
                "code": f"context_{index}", "title": f"项目 {index}", "episode_count": 1,
                "target_duration_ms": 60000, "aspect_ratio": "9:16", "width": 1080, "height": 1920,
                "fps": {"numerator": 24, "denominator": 1}, "primary_language": "zh-CN",
                "subtitle_mode": "NONE", "allow_unconfigured_capabilities": True,
            })
            projects.append(response.json()["project"])
        episode = client.get(f"/api/v1/projects/{projects[1]['id']}/episode-catalog").json()["catalog"]["seasons"][0]["episodes"][0]
        mismatch = client.get("/api/v2/app-context", params={"project_id": projects[0]["id"], "episode_id": episode["id"]})
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "EPISODE_PROJECT_MISMATCH"
