"""HTTP-02: a shot write must never ignore the project named in the URL.

Acceptance is asserted against the database, not just the HTTP status: on every
rejected request the shot, revision, audit and outbox row counts must be
unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app

WRITE_TABLES = ("shots", "shot_revisions", "audit_events", "outbox_events", "episode_scene_ranges")


def _project(service: ProjectService, code: str) -> dict[str, object]:
    return service.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _first_episode(service: ProjectService, project: dict[str, object]) -> dict[str, object]:
    season = service.list_seasons(str(project["id"]))[0]
    return service.list_episodes(str(season["id"]))[0]


def _counts(database) -> dict[str, int]:
    with database.connect() as connection:
        return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in WRITE_TABLES}


def test_create_shot_in_the_owning_project_succeeds(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = _project(service, "owner_project")
    episode = _first_episode(service, project)

    with TestClient(create_app(workspace)) as client:
        before = _counts(database)
        response = client.post(
            f"/api/v1/projects/{project['id']}/episodes/{episode['id']}/shots",
            json={"code": "SHOT_001", "target_duration_ms": 4_000},
        )
        after = _counts(database)

    assert response.status_code == 201, response.text
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 1
    assert after["shots"] == before["shots"] + 1
    assert after["shot_revisions"] == before["shot_revisions"] + 1
    assert after["audit_events"] == before["audit_events"]
    assert after["outbox_events"] == before["outbox_events"]


def test_create_shot_rejects_an_episode_from_another_project(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project_a = _project(service, "owner_project_a")
    project_b = _project(service, "owner_project_b")
    episode_a = _first_episode(service, project_a)

    with TestClient(create_app(workspace)) as client:
        before = _counts(database)
        response = client.post(
            f"/api/v1/projects/{project_b['id']}/episodes/{episode_a['id']}/shots",
            json={"code": "SHOT_CROSS", "target_duration_ms": 4_000},
        )
        after = _counts(database)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "EPISODE_PROJECT_MISMATCH"
    assert after == before
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0


def test_create_shot_rejects_an_unknown_project(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = _project(service, "owner_project_known")
    episode = _first_episode(service, project)

    with TestClient(create_app(workspace)) as client:
        before = _counts(database)
        response = client.post(
            f"/api/v1/projects/not-a-project/episodes/{episode['id']}/shots",
            json={"code": "SHOT_ORPHAN", "target_duration_ms": 4_000},
        )
        after = _counts(database)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert after == before


def test_create_shot_rejects_an_unknown_episode(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = _project(service, "owner_project_no_episode")

    with TestClient(create_app(workspace)) as client:
        before = _counts(database)
        response = client.post(
            f"/api/v1/projects/{project['id']}/episodes/not-an-episode/shots",
            json={"code": "SHOT_ORPHAN", "target_duration_ms": 4_000},
        )
        after = _counts(database)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"
    assert after == before


def test_bind_scene_range_uses_the_same_shared_ownership_rule(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project_a = _project(service, "range_project_a")
    project_b = _project(service, "range_project_b")
    episode_a = _first_episode(service, project_a)
    foreign_scene = service.create_scene(str(project_b["id"]), "SCENE_B", "Foreign scene")

    with TestClient(create_app(workspace)) as client:
        before = _counts(database)
        response = client.post(
            f"/api/v1/projects/episodes/{episode_a['id']}/scene-ranges",
            json={"scene_id": foreign_scene["id"], "ordinal": 1, "source_start": 0, "source_end": 10},
        )
        after = _counts(database)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "SCENE_EPISODE_PROJECT_MISMATCH"
    assert after == before
