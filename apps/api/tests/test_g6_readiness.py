from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.g6_readiness import G6ReadinessService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_g6_readiness_reports_first_real_blocker_without_mutation(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="g6_readiness",
        title="G6 readiness",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )
    before = database.path.stat().st_size
    result = G6ReadinessService(database).inspect(str(project["id"]))
    assert result["status"] == "IN_PROGRESS"
    assert result["next_required_action"] == "APPROVED_KEYFRAME"
    assert result["mutated"] is False
    assert result["checks"][0] == {"code": "APPROVED_KEYFRAME", "passed": False, "count": 0}
    assert database.path.stat().st_size == before

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/gates/g6")
    assert response.status_code == 200
    assert response.json()["readiness"]["next_required_action"] == "APPROVED_KEYFRAME"


def test_g6_readiness_rejects_unknown_project(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects/missing/gates/g6")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
