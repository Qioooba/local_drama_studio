from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _project(workspace, database, code: str, title: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=title,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_project_list_search_status_and_literal_wildcards(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    first = _project(workspace, database, "north_house", "北方小院")
    second = _project(workspace, database, "south_house", "南方小院 100%")
    service.transition_project(str(first["id"]), "ACTIVE")

    assert [item["id"] for item in service.list_projects(search="北方")] == [first["id"]]
    assert [item["id"] for item in service.list_projects(status="ACTIVE")] == [first["id"]]
    assert [item["id"] for item in service.list_projects(search="100%", status="DRAFT")] == [second["id"]]


def test_project_list_http_filters(workspace, database) -> None:
    project = _project(workspace, database, "filter_api", "筛选接口")
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects", params={"search": "筛选", "status": "DRAFT"})
        invalid = client.get("/api/v1/projects", params={"status": "DELETED"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [project["id"]]
    paged = client.get("/api/v1/projects", params={"cursor": 0, "limit": 1})
    assert paged.status_code == 200
    assert paged.json()["page"] == {"cursor": 0, "limit": 1, "next_cursor": None, "has_more": False}
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "PROJECT_STATUS_INVALID"


def test_project_list_cursor_is_bounded_and_stable(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    for number in range(5):
        _project(workspace, database, f"cursor_{number}", f"Cursor {number}")
    first = service.list_projects_page(limit=2, cursor=0)
    second = service.list_projects_page(limit=2, cursor=int(first["page"]["next_cursor"]))
    assert len(first["items"]) == 2
    assert first["page"] == {"cursor": 0, "limit": 2, "next_cursor": 2, "has_more": True}
    assert set(item["id"] for item in first["items"]).isdisjoint(item["id"] for item in second["items"])
