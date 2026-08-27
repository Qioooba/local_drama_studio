from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_episode_cockpit_reports_persisted_shot_facts_without_mutation(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="episode_cockpit",
        title="Episode cockpit",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=2000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    projects.create_shot(str(episode["id"]), "S001", 1000)
    undirected = projects.create_shot(str(episode["id"]), "S002", 1000)
    with database.transaction() as connection:
        connection.execute("UPDATE shots SET status='PLANNED',current_revision_id=NULL WHERE id=?", (undirected["id"],))

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v2/episodes/{episode['id']}/production/overview")
        retired = client.get(f"/api/v1/episodes/{episode['id']}/cockpit")

    assert response.status_code == 200
    cockpit = response.json()["overview"]
    assert cockpit["shot_count"] == 2
    assert cockpit["attention_count"] == 2
    assert cockpit["state_counts"] == {"BLOCKED": 2}
    assert response.json()["read_only"] is True
    assert retired.status_code == 404
