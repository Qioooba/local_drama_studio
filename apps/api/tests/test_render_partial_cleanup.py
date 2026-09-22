"""MED-07: a mid-render failure must not leave a complete-looking partial video.

The original defect: ``_concat_and_mix`` created the concat/subtitle/upscale
outputs *outside* its ``try/finally``, so a failure in any early stage left a
realistic-looking intermediate file in the render directory (the report measured a
4,821-byte ``.partial-....mp4``).  These tests inject real FFmpeg failures at
several stages and assert that only this attempt's files disappear.

Real FFmpeg is used for the happy paths and for the subtitle failure; the
injections that need a specific stage failure monkeypatch the stage itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the partial-cleanup acceptance",
)


def _video(workspace, name: str, *, seconds: float = 2.0) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=navy:s=160x90:r=24:d={seconds}",
            "-pix_fmt", "yuv420p", "-an", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _fixture(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    source = media.import_file(str(project["id"]), _video(workspace, f"{code}.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(source["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}}],
        {"source": code},
    )
    return project, episode, service, timeline


def _render_dirs(project_root: Path) -> tuple[Path, Path]:
    return project_root / "05_timelines" / "renders", project_root / "05_timelines" / "renders"


def _leftovers(render_dir: Path) -> list[Path]:
    return [path for path in render_dir.rglob("*") if path.name.startswith(".partial-") or path.name.startswith(".attempt-")]


def test_failed_subtitle_burn_in_leaves_no_partial_intermediate(workspace, database) -> None:
    """The report's exact reproduction: concat succeeds, subtitle burn-in fails."""

    project, _episode, service, timeline = _fixture(workspace, database, "med07_subtitle")
    project_root = workspace.projects_root / str(project["root_rel"])
    render_dir, _ = _render_dirs(project_root)

    # A real FFmpeg failure: an ASS payload FFmpeg cannot parse as a subtitle file.
    bad_subtitle = {"format": "SRT", "content_text": "this is not a valid subtitle file\n"}

    with pytest.raises(DomainRuleError) as failed:
        service._concat_and_mix(
            [service.media.content_path(str(timeline["items"][0]["media_version_id"]))[1]],
            [],
            render_dir,
            render_dir / "episode-should-not-exist.mp4",
            video_items=[timeline["items"][0]],
            subtitle=bad_subtitle,
            include_source_audio=False,
        )
    # The burn-in really failed (it did not silently succeed and leave a file).
    assert failed.value.code in {"FFMPEG_EXECUTION_FAILED", "FFMPEG_EXECUTION_TIMEOUT"}

    leftovers = _leftovers(render_dir)
    assert leftovers == [], f"a failed render must leave no intermediate files, found {leftovers}"


def test_failed_concat_leaves_no_partial_intermediate(workspace, database, monkeypatch) -> None:
    """A failure in the FIRST stage must also be cleaned up (the original hole)."""

    project, _episode, service, timeline = _fixture(workspace, database, "med07_concat")
    project_root = workspace.projects_root / str(project["root_rel"])
    render_dir, _ = _render_dirs(project_root)
    item = timeline["items"][0]
    media_path = service.media.content_path(str(item["media_version_id"]))[1]

    calls = {"count": 0}
    original = service._run_ffmpeg

    def fail_on_concat(args, *, timeout, cwd=None):
        calls["count"] += 1
        raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "注入的拼接失败")

    monkeypatch.setattr(service, "_run_ffmpeg", fail_on_concat)
    with pytest.raises(DomainRuleError):
        service._concat_and_mix(
            [media_path],
            [],
            render_dir,
            render_dir / "episode-should-not-exist.mp4",
            video_items=[item],
            include_source_audio=False,
        )
    assert calls["count"] >= 1
    assert _leftovers(render_dir) == []
    # The listener is restored by monkeypatch; confirm the original is callable.
    assert callable(original)


def test_failed_mix_stage_is_cleaned_up(workspace, database, monkeypatch) -> None:
    """A late-stage failure must clean the already-produced intermediates."""

    project, _episode, service, timeline = _fixture(workspace, database, "med07_mix")
    project_root = workspace.projects_root / str(project["root_rel"])
    render_dir, _ = _render_dirs(project_root)
    item = timeline["items"][0]
    media_path = service.media.content_path(str(item["media_version_id"]))[1]

    def fail_mix(*_args, **_kwargs):
        raise DomainRuleError("FFMPEG_EXECUTION_FAILED", "注入的混音失败")

    monkeypatch.setattr(service, "_mix_audio", fail_mix)
    with pytest.raises(DomainRuleError):
        service._concat_and_mix(
            [media_path],
            [],
            render_dir,
            render_dir / "episode-should-not-exist.mp4",
            video_items=[item],
            include_source_audio=False,
        )
    assert _leftovers(render_dir) == []
    assert not (render_dir / "episode-should-not-exist.mp4").exists()


def test_cancellation_is_cleaned_up_and_does_not_delete_published_renders(workspace, database, monkeypatch) -> None:
    """Cancellation cleans this attempt only; a previously published render survives."""

    project, _episode, service, timeline = _fixture(workspace, database, "med07_cancel")
    project_root = workspace.projects_root / str(project["root_rel"])
    render_dir, _ = _render_dirs(project_root)

    # Publish one real render first.
    published = service.render_episode(str(timeline["id"]))
    published_path = project_root / published["rel_path"]
    assert published_path.is_file()

    item = timeline["items"][0]
    media_path = service.media.content_path(str(item["media_version_id"]))[1]

    def cancel(*_args, **_kwargs):
        raise DomainRuleError("JOB_CANCELLED", "后台任务已取消")

    monkeypatch.setattr(service, "_mix_audio", cancel)
    with pytest.raises(DomainRuleError) as cancelled:
        service._concat_and_mix(
            [media_path],
            [],
            render_dir,
            render_dir / "episode-cancelled.mp4",
            video_items=[item],
            include_source_audio=False,
        )
    assert cancelled.value.code == "JOB_CANCELLED"

    assert _leftovers(render_dir) == []
    assert not (render_dir / "episode-cancelled.mp4").exists()
    # The already published artifact is untouched.
    assert published_path.is_file()
    assert published_path.stat().st_size > 0


def test_successful_render_still_leaves_no_attempt_directory(workspace, database) -> None:
    """The cleanup must not break the happy path, and must not leave the scratch dir."""

    project, _episode, service, timeline = _fixture(workspace, database, "med07_success")
    project_root = workspace.projects_root / str(project["root_rel"])
    render_dir, _ = _render_dirs(project_root)

    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    assert (project_root / render["rel_path"]).is_file()
    assert _leftovers(render_dir) == []
    assert not list(render_dir.glob(".partial-*"))
