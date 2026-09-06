from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.domain.duration import DEFAULT_PROJECT_TARGET_DURATION_MS
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def make_project(service: ProjectService, code: str, duration_ms: int, episode_count: int = 2) -> dict[str, object]:
    return service.create_project(
        code=code,
        title=code,
        episode_count=episode_count,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=duration_ms,
        allow_unconfigured_capabilities=True,
        width=480,
        height=854,
        primary_language="zh-CN",
        subtitle_mode="NONE",
    )


def episodes_for(service: ProjectService, project: dict[str, object]) -> list[dict[str, object]]:
    seasons = service.list_seasons(str(project["id"]))
    return [episode for season in seasons for episode in service.list_episodes(str(season["id"]))]


def test_project_default_is_authoritative_and_episode_values_are_frozen(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    first = make_project(service, "duration_project_a", 60_000)
    second = make_project(service, "duration_project_b", DEFAULT_PROJECT_TARGET_DURATION_MS, episode_count=1)

    assert first["target_duration_ms"] == 60_000
    assert second["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS
    assert [item["target_duration_ms"] for item in episodes_for(service, first)] == [60_000, 60_000]

    updated = service.update_project_target_duration(str(first["id"]), 120_000, expected_revision=int(first["revision"]))
    assert updated["target_duration_ms"] == 120_000
    # Changing the default is not an implicit replan and never rewrites
    # already-resolved episode targets.
    assert [item["target_duration_ms"] for item in episodes_for(service, first)] == [60_000, 60_000]
    assert service.get_project(str(second["id"]))["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS

    first_episode, second_episode = episodes_for(service, first)
    applied = service.apply_project_target_duration(
        str(first["id"]),
        [str(first_episode["id"])],
        expected_revision=int(updated["revision"]),
    )
    assert applied["episode_ids"] == [first_episode["id"]]
    assert applied["requires_replan"] is True
    assert [item["target_duration_ms"] for item in episodes_for(service, first)] == [120_000, 60_000]
    assert service.get_episode(str(second_episode["id"]))["target_duration_ms"] == 60_000

    inherited = service.append_episode(
        str(first["id"]),
        season_id=str(service.list_seasons(str(first["id"]))[0]["id"]),
        create_new_season=False,
        season_title=None,
        episode_title="继承项目默认",
        target_duration_ms=None,
    )
    assert inherited["episode"]["target_duration_ms"] == 120_000
    explicit = service.append_episode(
        str(first["id"]),
        season_id=str(service.list_seasons(str(first["id"]))[0]["id"]),
        create_new_season=False,
        season_title=None,
        episode_title="本集覆盖",
        target_duration_ms=45_000,
    )
    assert explicit["episode"]["target_duration_ms"] == 45_000

    with pytest.raises(DomainRuleError) as mismatch:
        service.apply_project_target_duration(str(first["id"]), [str(second_episode["id"])], expected_revision=int(updated["revision"]))
    assert mismatch.value.code == "REVISION_CONFLICT"


def test_legacy_null_project_default_uses_product_default_without_first_episode_lookup(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = make_project(service, "duration_legacy_compat", 60_000, episode_count=1)
    episode = episodes_for(service, project)[0]
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET target_duration_ms=NULL WHERE id=?", (project["id"],))

    # A legacy/partially imported row remains usable; resolving a new episode
    # falls back to the product default, never to the existing episode value.
    inherited = service.append_episode(
        str(project["id"]),
        season_id=str(service.list_seasons(str(project["id"]))[0]["id"]),
        create_new_season=False,
        season_title=None,
        episode_title="兼容默认",
        target_duration_ms=None,
    )
    assert episode["target_duration_ms"] == 60_000
    assert inherited["episode"]["target_duration_ms"] == DEFAULT_PROJECT_TARGET_DURATION_MS

    with pytest.raises(DomainRuleError) as error:
        service.update_project_target_duration(str(project["id"]), 0, expected_revision=int(project["revision"]))
    assert error.value.code == "INVALID_TARGET_DURATION"


def test_project_duration_commands_are_explicit_api_operations(workspace, database) -> None:
    project = make_project(ProjectService(database, workspace.projects_root), "duration_api", 60_000, episode_count=1)
    episode = episodes_for(ProjectService(database, workspace.projects_root), project)[0]
    with TestClient(create_app(workspace)) as client:
        updated = client.patch(
            f"/api/v1/projects/{project['id']}/target-duration",
            json={"target_duration_ms": 120_000, "expected_revision": project["revision"]},
        )
        assert updated.status_code == 200
        assert updated.json()["project"]["target_duration_ms"] == 120_000
        assert updated.json()["project"]["revision"] == int(project["revision"]) + 1

        applied = client.post(
            f"/api/v1/projects/{project['id']}/target-duration:apply",
            json={"episode_ids": [episode["id"]], "expected_revision": int(project["revision"]) + 1},
        )
        assert applied.status_code == 200
        assert applied.json()["application"]["requires_replan"] is True
        assert applied.json()["application"]["target_duration_ms"] == 120_000
