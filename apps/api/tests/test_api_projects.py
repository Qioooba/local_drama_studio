from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.main import create_app


def test_project_api_uses_real_migration_and_returns_conflict(workspace, database) -> None:
    settings = workspace
    assert database.integrity_check() == "ok"
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/projects",
            json={
                "code": "api_g2",
                "title": "API G2",
                "episode_count": 2,
                "target_duration_ms": 60000,
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
                "fps": {"numerator": 24, "denominator": 1},
                "primary_language": "zh-CN",
                "subtitle_mode": "NONE",
                "allow_unconfigured_capabilities": True,
            },
        )
        assert response.status_code == 201
        project = response.json()["project"]
        assert response.json()["blockers"] == ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"]
        detail_response = client.get(f"/api/v1/projects/{project['id']}")
        assert detail_response.status_code == 200
        absolute_root_path = Path(detail_response.json()["project"]["absolute_root_path"])
        assert absolute_root_path.is_absolute()
        assert absolute_root_path == (workspace.projects_root / project["root_rel"]).resolve()
        catalog_response = client.get(f"/api/v1/projects/{project['id']}/episode-catalog")
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()["catalog"]
        assert catalog["read_only"] is True and catalog["runtime_contacted"] is False and catalog["mutated"] is False
        assert len(catalog["seasons"]) == 1
        assert [episode["code"] for episode in catalog["seasons"][0]["episodes"]] == ["EPISODE_001", "EPISODE_002"]
        season_id = catalog["seasons"][0]["id"]
        appended = client.post(
            f"/api/v1/projects/{project['id']}/episodes:append",
            json={
                "season_id": season_id,
                "create_new_season": False,
                "episode_title": "追加篇",
                "target_duration_ms": 90000,
            },
        )
        assert appended.status_code == 201
        assert appended.json()["append"]["season_created"] is False
        assert appended.json()["append"]["episode"]["code"] == "EPISODE_003"
        new_season = client.post(
            f"/api/v1/projects/{project['id']}/episodes:append",
            json={
                "create_new_season": True,
                "season_title": "特别季",
                "episode_title": "特别篇",
                "target_duration_ms": 45000,
            },
        )
        assert new_season.status_code == 201
        assert new_season.json()["append"]["season_created"] is True
        assert new_season.json()["append"]["season"]["code"] == "SEASON_002"
        assert new_season.json()["append"]["episode"]["code"] == "EPISODE_004"
        expanded_catalog = client.get(f"/api/v1/projects/{project['id']}/episode-catalog").json()["catalog"]
        assert [len(item["episodes"]) for item in expanded_catalog["seasons"]] == [3, 1]
        with database.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='PROJECT_EPISODE_APPENDED' AND subject_type='episode'",
            ).fetchone()[0] == 2
        setup_response = client.get(f"/api/v1/projects/{project['id']}/creator-setup")
        assert setup_response.status_code == 200
        setup = setup_response.json()["setup"]
        assert setup["completed_count"] == 1 and setup["total_count"] == 7
        assert setup["milestones"]["episode_count"] == {"ready": True, "count": 4}
        assert setup["milestones"]["shot_intent_count"]["ready"] is False
        assert setup["milestones"]["shot_generation_job_count"]["ready"] is False
        assert setup["operations"] == {"worker_ready": False, "active_worker_count": 0}
        assert setup["read_only"] is True and setup["runtime_contacted"] is False and setup["mutated"] is False
        episode_id = catalog["seasons"][0]["episodes"][0]["id"]
        shot_response = client.post(
            f"/api/v1/projects/{project['id']}/episodes/{episode_id}/shots",
            json={"code": "S001", "target_duration_ms": 3000, "shot_type": "MEDIUM"},
        )
        assert shot_response.status_code == 201
        intent_response = client.post(
            "/api/v1/generation-intents",
            json={
                "project_id": project["id"],
                "owner_type": "SHOT",
                "owner_id": shot_response.json()["shot"]["id"],
                "purpose": "KEYFRAME",
                "creative_goal": "首镜建立人物与空间关系",
            },
        )
        assert intent_response.status_code == 201
        progressed_setup = client.get(f"/api/v1/projects/{project['id']}/creator-setup").json()["setup"]
        assert progressed_setup["completed_count"] == 2
        assert progressed_setup["milestones"]["shot_intent_count"] == {"ready": True, "count": 1}
        first_update = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"title": "First", "expected_revision": 3},
        )
        assert first_update.status_code == 200
        stale = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"title": "Stale", "expected_revision": 3},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
