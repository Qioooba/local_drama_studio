"""Regression coverage for the production segmented-compose path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError


def _video(workspace, name: str, seconds: float) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=navy:s=160x90:d={seconds}", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _project_and_episode(workspace, database) -> tuple[dict[str, object], dict[str, object]]:
    project_service = ProjectService(database, workspace.projects_root)
    project = project_service.create_project(
        code="segmented_render",
        title="Segmented render",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    season = project_service.list_seasons(str(project["id"]))[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    return project, episode


def test_render_segmented_episode_concats_two_real_videos(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "segment-1.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, "segment-2.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "segmented-render-test"},
    )
    segments = [
        {"media_version_id": str(first["media_version_id"]), "segment_no": 1, "start_seconds": 0.0, "end_seconds": 1.0, "frames": 24, "continuation": None},
        {"media_version_id": str(second["media_version_id"]), "segment_no": 2, "start_seconds": 1.0, "end_seconds": 2.0, "frames": 24, "continuation": {"from_segment_no": 1, "mode": "LAST_FRAME_TO_FIRST_FRAME", "shared_frame_count": 1}},
    ]
    submission = BackgroundOperationService(database, workspace).submit_segmented_compose(str(timeline["id"]), segments)
    assert submission["job"]["type"] == "SEGMENTED_EPISODE_COMPOSE"
    replay = BackgroundOperationService(database, workspace).submit_segmented_compose(str(timeline["id"]), segments)
    assert replay["job"]["id"] == submission["job"]["id"] and replay["idempotent_replay"] is True
    outcome = LocalMediaWorker(database, workspace).run_once("segmented-compose-worker", ["CPU"])
    assert outcome is not None and outcome["result"]["job_state"] == "SUCCEEDED"
    operation = BackgroundOperationService(database, workspace).result(str(submission["job"]["id"]))
    assert operation["result_type"] == "EPISODE_RENDER"
    render = operation["result"]
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
    first = media.import_file(str(project["id"]), _video(workspace, "segment-required.mp4", 1.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "segmented-render-test"},
    )
    with pytest.raises(DomainRuleError) as error:
        TimelineService(database, workspace).render_segmented_episode(str(timeline["id"]), [])
    assert error.value.code == "SEGMENT_VIDEOS_REQUIRED"
