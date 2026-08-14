from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_model_compatibility_projection_api_is_read_only(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="model_snapshot_api",
        title="Model snapshot API",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    before = database.path.read_bytes()
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/model-compatibility")
    assert response.status_code == 200
    payload = response.json()["compatibility"]
    assert payload["reports"]
    assert all(item["has_report"] is False for item in payload["reports"])
    assert payload["summary"] == {
        "artifact_count": len(payload["reports"]),
        "reported_count": 0,
        "pass_count": 0,
        "blocked_count": 0,
        "missing_license_evidence_count": len(payload["reports"]),
    }
    assert payload["runtime_contacted"] is False
    assert payload["network_contacted"] is False
    assert payload["mutated"] is False
    assert database.path.read_bytes() == before


def test_model_compatibility_projection_rejects_unknown_project(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects/missing/model-compatibility")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
