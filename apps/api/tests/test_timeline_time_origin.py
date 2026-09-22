"""MED-05: the rendered file and the OTIO export of one revision must agree on time.

The original defect: `render_episode` produced 2 s for a revision whose only VIDEO
item sat at 1-3 s, while OTIO exported 3 s for the *same* revision, because the
renderer implicitly shifted video to 0 while audio and subtitles stayed on the
absolute axis and the exporter emitted a leading Gap.  These tests assert the
correct behaviour with REAL FFmpeg: one explicit origin (0), so the rendered
duration and the OTIO duration are the same number.

Skipped when FFmpeg/FFprobe are unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_exports import TimelineExportService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the render/OTIO time-origin acceptance",
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


def _fixture(workspace, database, code: str, *, start_us: int, end_us: int):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    source = media.import_file(str(project["id"]), _video(workspace, f"{code}.mp4", seconds=(end_us - start_us) / 1_000_000 + 0.5), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(source["media_version_id"]), "start_us": start_us, "end_us": end_us, "parameters": {"transition_in": "CUT"}}],
        {"source": code},
    )
    return project, episode, service, timeline


def _otio_duration_seconds(export_dir: Path) -> float:
    """Total OTIO duration: sum of every track's children, from the real file."""

    payload = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    otio_file = next(export_dir / str(item["rel_path"]) for item in payload["files"] if str(item["rel_path"]).endswith(".otio"))
    timeline = json.loads(otio_file.read_text(encoding="utf-8"))
    longest = 0.0
    for track in timeline["tracks"]["children"]:
        total = 0.0
        for child in track["children"]:
            total += float(child["source_range"]["duration"]["value"]) / float(child["source_range"]["duration"]["rate"])
        longest = max(longest, total)
    return longest


def test_render_and_otio_agree_when_the_timeline_starts_at_zero(workspace, database) -> None:
    """Control case: a 0-2 s timeline renders 2 s and exports 2 s."""

    project, episode, service, timeline = _fixture(workspace, database, "med05_zero", start_us=0, end_us=2_000_000)
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    rendered_seconds = int(render["probe"]["duration_ms"]) / 1000

    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    project_root = workspace.projects_root / str(project["root_rel"])
    otio_seconds = _otio_duration_seconds(project_root / str(exported["rel_path"]))

    assert abs(rendered_seconds - 2.0) <= 0.05
    assert abs(otio_seconds - rendered_seconds) <= 0.05, f"render={rendered_seconds}s OTIO={otio_seconds}s"
    assert str(episode["id"])


def test_render_and_otio_agree_when_the_timeline_has_leading_blank(workspace, database) -> None:
    """The MED-05 reproduction: a 1-3 s timeline must not render 2 s and export 3 s."""

    project, _episode, service, timeline = _fixture(workspace, database, "med05_blank", start_us=1_000_000, end_us=3_000_000)

    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    rendered_seconds = int(render["probe"]["duration_ms"]) / 1000

    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    project_root = workspace.projects_root / str(project["root_rel"])
    otio_seconds = _otio_duration_seconds(project_root / str(exported["rel_path"]))

    # Same revision, same number.  The single explicit origin is 0 and the
    # leading second is real (black, silent) leader in both.
    assert abs(rendered_seconds - 3.0) <= 0.06, f"render must keep the leading blank (got {rendered_seconds}s)"
    assert abs(otio_seconds - rendered_seconds) <= 0.06, f"render={rendered_seconds}s OTIO={otio_seconds}s"

    # The plan declares the origin and the blank explicitly.
    plan = render["input_snapshot"]["timeline_plan"]
    assert plan["time_origin_us"] == 0
    assert plan["leading_blank_us"] == 1_000_000
    assert plan["timeline_span_us"] == 3_000_000
    assert render["input_snapshot"]["timeline_duration_us"] == 3_000_000

    # The OTIO really carries the matching leading Gap.
    payload = json.loads((project_root / str(exported["rel_path"]) / "manifest.json").read_text(encoding="utf-8"))
    otio_file = next(project_root / str(exported["rel_path"]) / str(item["rel_path"]) for item in payload["files"] if str(item["rel_path"]).endswith(".otio"))
    video_track = next(track for track in json.loads(otio_file.read_text(encoding="utf-8"))["tracks"]["children"] if track["name"] == "VIDEO")
    assert video_track["children"][0]["OTIO_SCHEMA"] == "Gap.1"
    gap_range = video_track["children"][0]["source_range"]["duration"]
    gap_seconds = float(gap_range["value"]) / float(gap_range["rate"])
    assert abs(gap_seconds - 1.0) <= 0.01


def test_leading_blank_is_real_black_video_not_a_shortened_episode(workspace, database) -> None:
    """The leading second must be genuine picture, so the clip is not truncated."""

    project, _episode, service, timeline = _fixture(workspace, database, "med05_leader", start_us=1_000_000, end_us=3_000_000)
    render = service.render_episode(str(timeline["id"]))
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]

    # Sample one frame inside the leader and one inside the clip, then compare
    # their mean luma: the leader is black, the clip is navy, so the leader is
    # picture rather than a missing segment.
    def mean_luma(offset_seconds: float) -> float:
        result = subprocess.run(
            [
                workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-ss", f"{offset_seconds:.3f}", "-i", str(output), "-frames:v", "1",
                "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ],
            check=True, capture_output=True,
        )
        data = result.stdout
        assert data, f"no frame at {offset_seconds}s"
        return sum(data) / len(data)

    leader_luma = mean_luma(0.3)
    clip_luma = mean_luma(2.0)
    assert leader_luma < 12, f"the leader must be black (mean luma={leader_luma})"
    assert clip_luma > leader_luma + 5, f"the clip must be present after the leader (leader={leader_luma}, clip={clip_luma})"

    # The clip is not compressed: the last frame still exists near 2.95 s.
    assert mean_luma(2.9) > leader_luma + 5
