"""P1-9 long-shot segmentation: pure planner + real-ffmpeg segmented concat render."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.long_shot import grid_frame_count, plan_segments
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError

FPS = 24.0


def _on_grid(frames: int) -> bool:
    return frames % 17 == 5


def _video(workspace, name: str, seconds: float) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=navy:s=160x90:d={seconds}", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="long_shot",
        title="Long shot",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _project_and_episode(workspace, database) -> tuple[dict[str, object], dict[str, object]]:
    project = _project(workspace, database)
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(str(project["id"]))[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    return project, episode


def test_plan_single_segment_below_or_at_limit(workspace, database) -> None:
    plan = plan_segments(15.0)
    assert plan["segment_count"] == 1
    segment = plan["segments"][0]
    assert segment["segment_no"] == 1
    assert segment["continuation"] is None
    assert segment["frames"] == grid_frame_count(15.0 * FPS) == 362
    assert _on_grid(segment["frames"])
    assert segment["end_seconds"] == plan["duration_seconds"]


def test_plan_splits_16_seconds_into_two_grid_segments(workspace, database) -> None:
    plan = plan_segments(16.0)
    assert plan["segment_count"] == 2
    first, second = plan["segments"]
    assert _on_grid(first["frames"]) and _on_grid(second["frames"])
    # 16s requested = 384 raw frames; 192 (already on grid) + 209 covers it.
    assert first["frames"] == 192
    assert second["frames"] == 209
    assert first["end_seconds"] - 1 / FPS == pytest.approx(second["start_seconds"])
    assert second["continuation"] == {
        "from_segment_no": 1,
        "mode": "LAST_FRAME_TO_FIRST_FRAME",
        "shared_frame_count": 1,
    }
    effective_frames = first["frames"] + second["frames"] - 1
    assert effective_frames >= 384
    assert plan["duration_seconds"] == pytest.approx(effective_frames / FPS)


def test_plan_30_and_45_seconds_map_to_15s_segments(workspace, database) -> None:
    for duration, expected_segments, expected_frames in ((30.0, 2, 362), (45.0, 3, 362)):
        plan = plan_segments(duration)
        assert plan["segment_count"] == expected_segments
        assert [segment["frames"] for segment in plan["segments"]] == [expected_frames] * expected_segments
        assert all(_on_grid(segment["frames"]) for segment in plan["segments"])
        for index in range(1, len(plan["segments"])):
            previous, current = plan["segments"][index - 1], plan["segments"][index]
            assert current["start_seconds"] == pytest.approx(previous["end_seconds"] - 1 / FPS)
            assert current["continuation"]["from_segment_no"] == previous["segment_no"]


def test_plan_continuation_anchors_are_consistent(workspace, database) -> None:
    for duration in (16.0, 30.0, 45.0, 45.001, 60.0, 123.4):
        plan = plan_segments(duration)
        for index in range(1, len(plan["segments"])):
            previous, current = plan["segments"][index - 1], plan["segments"][index]
            assert current["start_seconds"] == pytest.approx(previous["end_seconds"] - 1 / FPS)
            assert current["continuation"]["mode"] == "LAST_FRAME_TO_FIRST_FRAME"
        total = sum(segment["frames"] for segment in plan["segments"]) - (plan["segment_count"] - 1)
        assert plan["duration_seconds"] == pytest.approx(total / FPS)
        assert total >= round(duration * FPS)


def test_plan_rejects_invalid_input(workspace, database) -> None:
    with pytest.raises(ValueError):
        plan_segments(0)
    with pytest.raises(ValueError):
        plan_segments(-5)
    with pytest.raises(ValueError):
        plan_segments(16, max_segment_seconds=0)
    with pytest.raises(ValueError):
        plan_segments(16, fps=0)


def test_render_segmented_episode_concats_two_real_videos(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "long-shot-1.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, "long-shot-2.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "long-shot-test"},
    )
    render = TimelineService(database, workspace).render_segmented_episode(
        str(timeline["id"]),
        [
            {"media_version_id": str(first["media_version_id"]), "segment_no": 1, "start_seconds": 0.0, "end_seconds": 1.0, "frames": 24, "continuation": None},
            {"media_version_id": str(second["media_version_id"]), "segment_no": 2, "start_seconds": 1.0, "end_seconds": 2.0, "frames": 24, "continuation": {"from_segment_no": 1, "mode": "LAST_FRAME_TO_FIRST_FRAME", "shared_frame_count": 1}},
        ],
    )
    assert render["status"] == "VERIFIED"
    assert render["revision"] == 1
    assert render["input_snapshot"]["schema_version"] == "localdrama.episode-render-input.v1"
    assert render["input_snapshot"]["render_mode"] == "SEGMENTED_CONCAT"
    assert len(render["input_snapshot"]["segments"]) == 2
    assert render["ffmpeg_command"]["executor"] == "builtin:ffmpeg"
    assert render["ffmpeg_command"]["returncode"] == 0
    assert 1900 <= render["probe"]["duration_ms"] <= 2100
    assert (project_root / render["rel_path"]).is_file()
    with database.connect() as connection:
        row = connection.execute("SELECT integrity_status, timeline_revision_id FROM episode_render_versions WHERE id=?", (render["id"],)).fetchone()
        assert row["integrity_status"] == "VERIFIED"
        assert row["timeline_revision_id"] == timeline["id"]


def test_render_segmented_episode_requires_segments(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "long-shot-required.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "long-shot-test"},
    )
    with pytest.raises(DomainRuleError) as error:
        TimelineService(database, workspace).render_segmented_episode(str(timeline["id"]), [])
    assert error.value.code == "SEGMENT_VIDEOS_REQUIRED"
