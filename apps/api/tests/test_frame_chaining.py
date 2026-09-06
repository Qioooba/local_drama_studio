from __future__ import annotations

import subprocess
import pytest

from local_drama.application.frame_chaining import FrameChainingService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService


def test_frame_chaining_same_scene_inherits(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="chain_same",
        title="Chain Same Scene",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot1 = projects.create_shot(str(episode["id"]), "S001", 2000)
    shot2 = projects.create_shot(str(episode["id"]), "S002", 2000)

    # Set both shots to same scene
    with database.transaction() as conn:
        conn.execute("UPDATE shots SET scene_id = 'scene_common' WHERE id IN (?, ?)", (shot1["id"], shot2["id"]))

    tail_file = workspace.work_root / "test_tail.png"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=32x32", "-frames:v", "1", "-y", str(tail_file)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        tail_file,
        owner_type="SHOT",
        owner_id=str(shot1["id"]),
        media_kind="IMAGE",
        purpose="KEYFRAME",
    )

    chaining = FrameChainingService(database)
    res = chaining.auto_chain_shot_tail_to_next(str(shot1["id"]), tail_media_version_id=str(media["media_version_id"]))
    assert res["chained"] is True
    assert res["from_shot_id"] == str(shot1["id"])
    assert res["to_shot_id"] == str(shot2["id"])
    assert res["anchor_id"] is not None


def test_frame_chaining_scene_cut_detected(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="chain_cut",
        title="Chain Scene Cut",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot1 = projects.create_shot(str(episode["id"]), "S001", 2000)
    shot2 = projects.create_shot(str(episode["id"]), "S002", 2000)

    # Different scenes
    with database.transaction() as conn:
        conn.execute("UPDATE shots SET scene_id = 'scene_a' WHERE id = ?", (shot1["id"],))
        conn.execute("UPDATE shots SET scene_id = 'scene_b' WHERE id = ?", (shot2["id"],))

    chaining = FrameChainingService(database)
    res = chaining.auto_chain_shot_tail_to_next(str(shot1["id"]), tail_media_version_id="dummy_media_id")
    assert res["chained"] is False
    assert res["reason"] == "SCENE_CUT_DETECTED"


def test_frame_chaining_no_successor(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="chain_last",
        title="Chain Last Shot",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot1 = projects.create_shot(str(episode["id"]), "S001", 2000)

    chaining = FrameChainingService(database)
    res = chaining.auto_chain_shot_tail_to_next(str(shot1["id"]), tail_media_version_id="dummy_media_id")
    assert res["chained"] is False
    assert res["reason"] == "NO_SUCCESSOR_SHOT"


def test_adopt_video_triggers_frame_chaining(workspace, database) -> None:
    from local_drama.infrastructure.database.shot_studio_command_repository import SqliteShotStudioCommandRepository

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="chain_adopt",
        title="Chain Adopt Video",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot1 = projects.create_shot(str(episode["id"]), "S001", 2000)
    shot2 = projects.create_shot(str(episode["id"]), "S002", 2000)

    # Set both shots to same scene
    with database.transaction() as conn:
        conn.execute("UPDATE shots SET scene_id = 'scene_same' WHERE id IN (?, ?)", (shot1["id"], shot2["id"]))

    video_file = workspace.work_root / "test_video.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-y", str(video_file)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        video_file,
        owner_type="SHOT",
        owner_id=str(shot1["id"]),
        media_kind="VIDEO",
        purpose="SHOT_VIDEO",
        stage="PROXY",
    )

    repo = SqliteShotStudioCommandRepository(database)
    res = repo.adopt_working_version(str(media["media_version_id"]), actor="test-user")
    assert res["status"] == "ADOPTED"
    assert res["slot_type"] == "VIDEO"

    # Verify shot_transition_constraints was created connecting shot1 -> shot2
    with database.connect() as conn:
        constraint = conn.execute(
            "SELECT * FROM shot_transition_constraints WHERE from_shot_id = ? AND to_shot_id = ?",
            (shot1["id"], shot2["id"]),
        ).fetchone()
        assert constraint is not None
        assert constraint["from_anchor_id"] is not None

