from __future__ import annotations

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
        assert client.get(f"/api/v1/projects/{project['id']}").status_code == 200
        first_update = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"title": "First", "expected_revision": 1},
        )
        assert first_update.status_code == 200
        stale = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"title": "Stale", "expected_revision": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
