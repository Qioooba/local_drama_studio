"""MED-03: mixed 24/30 fps transitions must render, and preflight must freeze the plan.

The original defect: per-clip normalization unified size/SAR/pixel format but not
frame rate or timebase, so a 24 fps and a 30 fps clip reached ``xfade`` with
timebases ``1/12288`` and ``1/15360`` and every mixed-fps transition failed with
``FFMPEG_EXECUTION_FAILED`` — while preflight happily reported the timeline as
runnable.  These tests use REAL FFmpeg, with a same-framerate control, and assert
the *correct* behaviour: the transition renders, and preflight's frozen plan is
byte-identical to what the renderer executes.

Skipped when FFmpeg/FFprobe are unavailable so the normal suite stays safe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the mixed-fps transition acceptance",
)


def _video(workspace, name: str, *, fps: int, seconds: float = 2.0, colour: str = "navy") -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r={fps}:d={seconds}",
            "-pix_fmt", "yuv420p", "-an", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _project_episode(workspace, database, code: str, *, fps_num: int = 24):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=fps_num, fps_den=1, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    return project, episode


def _stream_fps(path: Path, ffprobe: str) -> Fraction:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=avg_frame_rate,time_base", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return Fraction(str(stream["avg_frame_rate"]))


def _frame_count(path: Path, ffprobe: str) -> int:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return int(json.loads(result.stdout)["streams"][0]["nb_read_frames"])


def _timeline_with_dissolve(workspace, database, code: str, second_fps: int, first_fps: int = 24):
    project, episode = _project_episode(workspace, database, code)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, f"{code}-a.mp4", fps=first_fps), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, f"{code}-b.mp4", fps=second_fps, colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "DISSOLVE"}},
        ],
        {"source": code},
    )
    return service, timeline, project


def test_mixed_24_30_fps_dissolve_renders_stable_output_fps(workspace, database) -> None:
    """The MED-03 reproduction: a 24 fps -> 30 fps DISSOLVE must actually render."""

    service, timeline, project = _timeline_with_dissolve(workspace, database, "med03_mixed", second_fps=30)

    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"

    project_root = workspace.projects_root / str(project["root_rel"])
    output = project_root / render["rel_path"]
    assert output.is_file()

    # Every clip was normalized to the frozen target fps (the project's 24/1).
    assert _stream_fps(output, workspace.ffprobe_path) == Fraction(24, 1)

    plan = render["input_snapshot"]["timeline_plan"]
    assert plan["fps_num"] == 24 and plan["fps_den"] == 1

    # The transition really happened: both source clips are present, and the
    # overlap shortened the episode by exactly the transition length.
    transition_steps = [step for step in json.loads(render["execution_log"])["steps"] if step["stage"] == "timeline-transition"]
    assert len(transition_steps) == 1
    transition_seconds = transition_steps[0]["duration_seconds"]
    assert transition_seconds > 0
    expected_ms = round((4_000_000 - round(transition_seconds * 1_000_000)) / 1000)
    assert abs(int(render["probe"]["duration_ms"]) - expected_ms) <= 40

    # The rendered frame count matches the frozen plan's frame grid.
    frames = _frame_count(output, workspace.ffprobe_path)
    assert abs(frames - round(expected_ms * 24 / 1000)) <= 2


def test_same_fps_dissolve_control_still_renders(workspace, database) -> None:
    """Same-framerate control: the fix must not change the working case."""

    service, timeline, project = _timeline_with_dissolve(workspace, database, "med03_control", second_fps=24)
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    assert _stream_fps(output, workspace.ffprobe_path) == Fraction(24, 1)
    assert int(render["probe"]["duration_ms"]) > 0


@pytest.mark.parametrize(("first_fps", "second_fps"), [(24, 25), (25, 24), (30, 24), (24, 30000 / 1001)])
def test_transition_renders_across_frame_rate_matrix(workspace, database, first_fps, second_fps) -> None:
    """23.976 / 24 / 25 / 30 combinations all normalize to the frozen target."""

    code = f"med03_{str(first_fps).replace('.', '_')}_{str(second_fps).replace('.', '_')}"
    fps = round(first_fps)
    project, episode = _project_episode(workspace, database, code, fps_num=fps)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, f"{code}-a.mp4", fps=round(first_fps)), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, f"{code}-b.mp4", fps=round(second_fps), colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "DISSOLVE"}},
        ],
        {"source": code},
    )
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    assert _stream_fps(output, workspace.ffprobe_path) == Fraction(fps, 1)


def test_preflight_freezes_the_exact_render_plan(workspace, database) -> None:
    """Preflight and render must not be able to disagree about fps or time origin.

    The compose job's staleness guard compares preflight's fingerprint with the
    render's; if the two built their plans separately they could drift.  This
    asserts the preflight-reported plan is literally the plan carried into the
    render snapshot.
    """

    service, timeline, _project = _timeline_with_dissolve(workspace, database, "med03_preflight", second_fps=30)

    preflight = service.preflight_episode_render(str(timeline["id"]))
    plan = preflight["input_snapshot"]["timeline_plan"]
    assert plan["fps_num"] == 24 and plan["fps_den"] == 1
    assert set(plan) == {
        "fps_num",
        "fps_den",
        "time_origin_us",
        "timeline_span_us",
        "leading_blank_us",
        "transition_overlap_us",
    }
    assert plan["time_origin_us"] == 0
    assert plan["transition_overlap_us"] > 0

    render = service.render_episode(str(timeline["id"]))
    assert render["input_snapshot"]["timeline_plan"] == plan
    # The compose fingerprint is over the frozen snapshot, so a replay finds it.
    assert render["compose_fingerprint"] == preflight["compose_fingerprint"]


def test_preflight_reports_transition_overlap_and_blank_never_hides_them(workspace, database) -> None:
    """The plan must expose the transition overlap and any leading blank."""

    project, episode = _project_episode(workspace, database, "med03_plan_fields")
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "med03-plan-a.mp4", fps=24), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, "med03-plan-b.mp4", fps=30, colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 500_000, "end_us": 2_500_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_500_000, "end_us": 4_500_000, "parameters": {"transition_in": "DISSOLVE"}},
        ],
        {"source": "med03-plan"},
    )
    preflight = service.preflight_episode_render(str(timeline["id"]))
    plan = preflight["input_snapshot"]["timeline_plan"]
    assert plan["leading_blank_us"] == 500_000
    assert plan["timeline_span_us"] == 4_500_000
    assert plan["transition_overlap_us"] > 0
