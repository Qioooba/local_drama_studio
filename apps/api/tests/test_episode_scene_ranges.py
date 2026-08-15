from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str, episodes: int = 2) -> tuple[ProjectService, dict[str, object]]:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(
        code=code,
        title="Master scene mapping",
        episode_count=episodes,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return service, project


def test_master_scene_identity_is_reused_across_episode_ranges(workspace, database) -> None:
    service, project = _project(workspace, database, "scene_ranges")
    season = service.list_seasons(str(project["id"]))[0]
    episodes = service.list_episodes(str(season["id"]))
    scene = service.create_scene(str(project["id"]), "SC-001", "老屋重逢", "老屋客厅", "夜")
    first = service.bind_episode_scene_range(str(episodes[0]["id"]), str(scene["id"]), 1, 0, 1_200, "第一集开场")
    second = service.bind_episode_scene_range(str(episodes[1]["id"]), str(scene["id"]), 2, 1_200, 2_500, "第二集续场")
    assert first["scene_id"] == second["scene_id"] == scene["id"]
    assert first["scene_code"] == "SC-001"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM episode_scene_ranges WHERE scene_id=?", (scene["id"],)).fetchone()[0] == 2


def test_scene_range_rejects_cross_project_and_duplicate_episode_mapping(workspace, database) -> None:
    service, project = _project(workspace, database, "scene_ranges_a", 1)
    _, other = _project(workspace, database, "scene_ranges_b", 1)
    episode = service.list_episodes(str(service.list_seasons(str(project["id"]))[0]["id"]))[0]
    scene = service.create_scene(str(other["id"]), "SC-OTHER", "Other")
    with pytest.raises(DomainRuleError, match="必须属于同一项目"):
        service.bind_episode_scene_range(str(episode["id"]), str(scene["id"]), 1, 0, 100)
    local_scene = service.create_scene(str(project["id"]), "SC-LOCAL", "Local")
    service.bind_episode_scene_range(str(episode["id"]), str(local_scene["id"]), 1, 0, 100)
    with pytest.raises(DomainRuleError, match="已关联当前分集"):
        service.bind_episode_scene_range(str(episode["id"]), str(local_scene["id"]), 2, 100, 200)


def test_scene_range_api_creates_and_reads_explicit_source_offsets(workspace, database) -> None:
    service, project = _project(workspace, database, "scene_ranges_api", 1)
    episode = service.list_episodes(str(service.list_seasons(str(project["id"]))[0]["id"]))[0]
    with TestClient(create_app(workspace)) as client:
        created = client.post(f"/api/v1/projects/{project['id']}/scenes", json={"code": "SC-API", "title": "API scene"})
        assert created.status_code == 201
        scene_id = created.json()["scene"]["id"]
        bound = client.post(
            f"/api/v1/projects/episodes/{episode['id']}/scene-ranges",
            json={"scene_id": scene_id, "ordinal": 1, "source_start": 20, "source_end": 80, "source_label": "chars 20-80"},
        )
        assert bound.status_code == 201
        listed = client.get(f"/api/v1/projects/episodes/{episode['id']}/scene-ranges")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["source_label"] == "chars 20-80"
