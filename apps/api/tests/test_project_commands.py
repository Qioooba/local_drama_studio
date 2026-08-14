from __future__ import annotations

import pytest

from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def make_service(workspace, database) -> ProjectService:
    return ProjectService(database, workspace.projects_root)


def test_create_project_materializes_template_and_episodes(workspace, database) -> None:
    service = make_service(workspace, database)
    project = service.create_project(
        code="g2_contract",
        title="G2 Contract",
        episode_count=60,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120000,
        allow_unconfigured_capabilities=True,
        width=1080, height=1920, primary_language="zh-CN", subtitle_mode="SIDECAR", subtitle_language="zh-CN",
    )
    root = workspace.projects_root / "g2_contract"
    assert root.is_dir()
    assert (root / "project.json").is_file()
    assert len(service.list_episodes(service.list_seasons(project["id"])[0]["id"])) == 60
    assert project["root_rel"] == "g2_contract"


def test_create_project_persists_explicit_multiseason_presentation_spec(workspace, database) -> None:
    service = make_service(workspace, database)
    project = service.create_project(
        code="multi_season", title="多季项目", season_count=2, episode_count=3, aspect_ratio="16:9",
        width=1920, height=1080, fps_num=25, fps_den=1, target_duration_ms=75_000,
        primary_language="zh-CN", subtitle_mode="BOTH", subtitle_language="zh-CN",
        allow_unconfigured_capabilities=True,
    )
    seasons = service.list_seasons(str(project["id"]))
    assert len(seasons) == 2
    assert [len(service.list_episodes(str(season["id"]))) for season in seasons] == [3, 3]
    assert service.list_episodes(str(seasons[1]["id"]))[0]["code"] == "EPISODE_004"
    root = workspace.projects_root / "multi_season"
    assert (root / "01_story" / "seasons" / "SEASON_002").is_dir()
    assert (root / "04_media" / "subtitles" / "SEASON_002" / "EPISODE_004").is_dir()
    assert project["width"] == 1920 and project["height"] == 1080
    assert project["primary_language"] == "zh-CN" and project["subtitle_mode"] == "BOTH"


def test_create_failure_leaves_no_half_project(workspace, database) -> None:
    service = make_service(workspace, database)
    with pytest.raises(RuntimeError, match="simulated project creation failure"):
        service.create_project(
            code="g2_rollback",
            title="Rollback",
            episode_count=2,
            aspect_ratio="16:9",
            fps_num=25,
            fps_den=1,
            target_duration_ms=60000,
            allow_unconfigured_capabilities=True,
            width=1920, height=1080, primary_language="zh-CN", subtitle_mode="NONE",
            simulate_failure=True,
        )
    assert not (workspace.projects_root / "g2_rollback").exists()
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM projects WHERE code = 'g2_rollback'").fetchone() is None


def test_episode_reorder_preserves_identity_and_code(workspace, database) -> None:
    service = make_service(workspace, database)
    project = service.create_project(
        code="g2_order",
        title="Order",
        episode_count=2,
        aspect_ratio="1:1",
        fps_num=30,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
        width=1080, height=1080, primary_language="zh-CN", subtitle_mode="NONE",
    )
    season = service.list_seasons(project["id"])[0]
    episodes = service.list_episodes(season["id"])
    moved = service.reorder_episode(episodes[1]["id"], 1)
    assert moved["id"] == episodes[1]["id"]
    assert moved["code"] == "EPISODE_002"
    assert service.get_episode(episodes[0]["id"])["code"] == "EPISODE_001"


def test_expected_revision_conflict_never_overwrites(workspace, database) -> None:
    service = make_service(workspace, database)
    project = service.create_project(
        code="g2_revision",
        title="Revision",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
        width=1080, height=1920, primary_language="zh-CN", subtitle_mode="NONE",
    )
    updated = service.update_project_title(project["id"], "First", expected_revision=1)
    assert updated["revision"] == 2
    with pytest.raises(DomainRuleError) as error:
        service.update_project_title(project["id"], "Stale", expected_revision=1)
    assert error.value.code == "REVISION_CONFLICT"
    assert service.get_project(project["id"])["title"] == "First"


def test_shot_ready_requires_explicit_fields(workspace, database) -> None:
    service = make_service(workspace, database)
    project = service.create_project(
        code="g2_shot",
        title="Shot",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
        width=1080, height=1920, primary_language="zh-CN", subtitle_mode="NONE",
    )
    episode = service.list_episodes(service.list_seasons(project["id"])[0]["id"])[0]
    shot = service.create_shot(episode["id"], "SHOT_001", 4000)
    service.create_shot_revision(shot["id"], {"subject_action": "walk"}, freeze=True)
    with pytest.raises(DomainRuleError) as error:
        service.mark_shot_production_ready(shot["id"])
    assert error.value.code == "SHOT_NOT_PRODUCTION_READY"
