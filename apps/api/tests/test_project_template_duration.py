"""HTTP-03: a template copy keeps the project default duration.

Copying a project must carry the source's explicit default (so a later appended
episode inherits 90 s instead of silently becoming 120 s) while every episode
keeps the duration it already resolved.  A legacy NULL default is surfaced with
its rule and is never reverse-inferred from the first episode.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.domain.duration import DEFAULT_PROJECT_TARGET_DURATION_MS
from local_drama.main import create_app

MAX_TARGET_DURATION_MS = 86_400_000


def _service(workspace, database) -> ProjectService:
    return ProjectService(database, workspace.projects_root)


def _create(service: ProjectService, code: str, duration_ms: int, episode_count: int = 2) -> dict[str, object]:
    return service.create_project(
        code=code,
        title=code,
        episode_count=episode_count,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=duration_ms,
        allow_unconfigured_capabilities=True,
    )


def _episodes(service: ProjectService, project_id: str) -> list[dict[str, object]]:
    return [episode for season in service.list_seasons(project_id) for episode in service.list_episodes(str(season["id"]))]


def _set_episode_duration(database, episode_id: str, duration_ms: int) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE episodes SET target_duration_ms=? WHERE id=?", (duration_ms, episode_id))


def _append(service: ProjectService, project_id: str, title: str = "新集") -> dict[str, object]:
    return service.append_episode(
        project_id,
        season_id=str(service.list_seasons(project_id)[0]["id"]),
        create_new_season=False,
        season_title=None,
        episode_title=title,
        target_duration_ms=None,
    )


def test_copy_carries_the_default_and_preserves_episode_durations(workspace, database) -> None:
    service = _service(workspace, database)
    source = _create(service, "duration_source_90", 90_000)
    episodes = _episodes(service, str(source["id"]))
    _set_episode_duration(database, str(episodes[0]["id"]), 60_000)

    result = service.copy_as_template(str(source["id"]), code="duration_copy_90", title="副本")
    copied = result["project"]

    assert copied["target_duration_ms"] == 90_000
    assert [item["target_duration_ms"] for item in _episodes(service, str(copied["id"]))] == [60_000, 90_000]
    configuration = result["copy_report"]["configuration"]
    assert configuration["target_duration_ms"] == 90_000
    assert configuration["target_duration_source"] == "SOURCE_PROJECT_DEFAULT"
    assert configuration["new_episode_duration_ms"] == 90_000

    appended = _append(service, str(copied["id"]))
    assert appended["episode"]["target_duration_ms"] == 90_000
    assert appended["target_duration_source"] == "PROJECT_DEFAULT"


def test_copy_carries_the_product_default_when_the_source_used_it(workspace, database) -> None:
    service = _service(workspace, database)
    source = _create(service, "duration_source_default", DEFAULT_PROJECT_TARGET_DURATION_MS, episode_count=1)

    result = service.copy_as_template(str(source["id"]), code="duration_copy_default", title="默认副本")
    copied = result["project"]

    assert copied["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS
    appended = _append(service, str(copied["id"]))
    assert appended["episode"]["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS


def test_copy_preserves_the_24_hour_boundary(workspace, database) -> None:
    service = _service(workspace, database)
    source = _create(service, "duration_source_max", MAX_TARGET_DURATION_MS, episode_count=1)

    result = service.copy_as_template(str(source["id"]), code="duration_copy_max", title="边界副本")
    copied = result["project"]

    assert copied["target_duration_ms"] == MAX_TARGET_DURATION_MS
    appended = _append(service, str(copied["id"]))
    assert appended["episode"]["target_duration_ms"] == MAX_TARGET_DURATION_MS


def test_legacy_null_default_is_surfaced_without_rewriting_history(workspace, database) -> None:
    service = _service(workspace, database)
    source = _create(service, "duration_source_null", 90_000, episode_count=2)
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET target_duration_ms=NULL WHERE id=?", (source["id"],))

    result = service.copy_as_template(str(source["id"]), code="duration_copy_null", title="兼容副本")
    copied = result["project"]
    configuration = result["copy_report"]["configuration"]

    # The copy repeats the source's own NULL instead of inventing a default from
    # the first episode's already-resolved value.
    assert copied["target_duration_ms"] is None
    assert [item["target_duration_ms"] for item in _episodes(service, str(copied["id"]))] == [90_000, 90_000]
    assert configuration["target_duration_source"] == "PRODUCT_DEFAULT_FALLBACK"
    assert configuration["new_episode_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS

    appended = _append(service, str(copied["id"]))
    assert appended["episode"]["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS
    assert appended["target_duration_source"] == "PRODUCT_DEFAULT_FALLBACK"
    with database.connect() as connection:
        assert connection.execute("SELECT target_duration_ms FROM projects WHERE id=?", (source["id"],)).fetchone()[0] is None


def test_copy_template_api_reports_the_duration_rule(workspace, database) -> None:
    service = _service(workspace, database)
    source = _create(service, "duration_api_source", 90_000, episode_count=1)

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{source['id']}:copy-template",
            json={"code": "duration_api_copy", "title": "API 副本"},
        )
        copied_id = str(response.json()["project"]["id"])
        season_id = str(client.get(f"/api/v1/projects/{copied_id}/seasons").json()["items"][0]["id"])
        appended = client.post(
            f"/api/v1/projects/{copied_id}/episodes:append",
            json={"season_id": season_id, "create_new_season": False, "episode_title": "追加", "target_duration_ms": None},
        )

    assert response.status_code == 201, response.text
    assert response.json()["project"]["target_duration_ms"] == 90_000
    assert response.json()["copy_report"]["configuration"]["target_duration_source"] == "SOURCE_PROJECT_DEFAULT"
    assert appended.status_code == 201, appended.text
    assert appended.json()["append"]["episode"]["target_duration_ms"] == 90_000
    assert appended.json()["append"]["target_duration_source"] == "PROJECT_DEFAULT"
