from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.timeline_status import TimelineStatusService
from local_drama.main import create_app


def test_g8_timeline_status_is_read_only_and_truthful_for_empty_episode(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g8_status", title="G8 status", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    with database.connect() as connection:
        before = int(connection.execute("SELECT COUNT(*) FROM timeline_revisions").fetchone()[0])
    status = TimelineStatusService(database).inspect(str(episode["id"]))
    assert status["timeline"]["revision_count"] == 0
    assert status["subtitles"]["revision_count"] == 0
    assert status["audio"]["binding_count"] == 0
    assert status["renders"]["count"] == 0
    assert status["delivery"]["count"] == 0
    assert status["read_only"] is True
    assert status["runtime_contacted"] is False and status["network_contacted"] is False and status["mutated"] is False
    with database.connect() as connection:
        assert int(connection.execute("SELECT COUNT(*) FROM timeline_revisions").fetchone()[0]) == before


def test_g8_timeline_status_route_rejects_unknown_episode(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/episodes/missing/timeline-status")
    assert response.status_code == 404
