"""TM-04 and TM-05: one frozen transition plan, and an explicit project frame rate.

TM-04 (audit reproduction).  Three clips of 2 s, 0.5 s and 2 s with DISSOLVE into
the second and third: the frozen plan derived each overlap from the *previous item's
own duration* and promised 4.000 s / 96 frames at 24 fps, while the renderer passed
its *accumulated frame count* into the same helper and executed 0.75 s of overlap
instead of 0.5 s.  The finished film was 3.750 s / 90 frames and was still
registered as VERIFIED.

TM-05 (audit reproduction).  A project explicitly configured for 24 fps opened with
a 30 fps clip: ``_episode`` never selected the project's fps columns, so the first
branch could not fire and the export came out at 30 fps.

Both tests measure the real encoded file with FFmpeg/FFprobe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the frozen-plan acceptance",
)


def _clip(workspace, name: str, *, fps: int, seconds: float = 2.0, colour: str = "navy") -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=48000",
            "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _project_episode(workspace, database, code: str, *, fps_num: int = 24, fps_den: int = 1):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=fps_num, fps_den=fps_den, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    return project, episode


def _stream_facts(path: Path, ffprobe: str, selector: str) -> dict[str, object]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", selector, "-show_entries",
         "stream=avg_frame_rate,nb_frames,nb_read_frames,duration,sample_rate,codec_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def _short_middle_timeline(
    workspace, database, code: str, *, fps_num: int = 24, fps_den: int = 1, media_fps: int | None = None
):
    """2 s + 0.5 s + 2 s with a DISSOLVE into each later clip.

    This is the audit's own layout: the frozen plan derives each overlap from the
    previous item's *own* duration, which the renderer used to substitute with its
    accumulated stream length.  The middle item is a half-second *window of a 2 s
    source* rather than a half-second file, so the source has full coverage and a
    deliberately truncated file cannot stand in for the short-shot case.
    """

    project, episode = _project_episode(workspace, database, code, fps_num=fps_num, fps_den=fps_den)
    media = MediaService(database, workspace)
    source_fps = media_fps or (fps_num if fps_den == 1 else 30)
    imports = [
        media.import_file(
            str(project["id"]),
            _clip(workspace, f"{code}-{index}.mp4", fps=source_fps, colour=colour),
            purpose="SHOT_VIDEO",
            media_kind="VIDEO",
        )
        for index, colour in enumerate(("navy", "maroon", "darkgreen"))
    ]
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {
                "track_type": "VIDEO", "media_version_id": str(imports[0]["media_version_id"]),
                "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"},
            },
            {
                "track_type": "VIDEO", "media_version_id": str(imports[1]["media_version_id"]),
                "start_us": 2_000_000, "end_us": 2_500_000,
                "parameters": {"transition_in": "DISSOLVE", "source_start_us": 500_000},
            },
            {
                "track_type": "VIDEO", "media_version_id": str(imports[2]["media_version_id"]),
                "start_us": 2_500_000, "end_us": 4_500_000,
                "parameters": {"transition_in": "DISSOLVE", "source_start_us": 0},
            },
        ],
        {"source": code, "include_source_audio": True},
    )
    return service, timeline, project


def test_short_middle_shot_renders_the_frozen_frame_count(workspace, database) -> None:
    """The audit's 2/0.5/2 case must really encode the plan's own frame count."""

    service, timeline, project = _short_middle_timeline(workspace, database, "tm04_short")
    preflight = service.preflight_episode_render(str(timeline["id"]))
    plan = preflight["input_snapshot"]["timeline_plan"]

    # Each transition takes half of the 0.5 s short shot: 0.25 s.  The film is
    # 2.000 s + (0.500 - 0.250) s + (2.000 - 0.250) s = 4.000 s = 96 frames.
    assert [entry["duration_frames"] for entry in plan["transitions"]] == [6, 6]
    assert [entry["offset_frames"] for entry in plan["transitions"]] == [42, 48]
    assert plan["expected_video_frames"] == 96
    assert plan["transition_overlap_us"] == 500_000
    assert plan["transition_overlap_us"] == 500_000
    assert plan["fps_source"] == "PROJECT"

    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]

    # The plan travels into the render unchanged...
    assert render["input_snapshot"]["timeline_plan"] == plan
    # ...and the execution log reports the frozen windows, not re-derived ones.
    steps = json.loads(render["execution_log"])["steps"]
    executed = [step for step in steps if step["stage"] == "timeline-transition"]
    assert [(step["duration_frames"], step["offset_frames"]) for step in executed] == [
        (entry["duration_frames"], entry["offset_frames"]) for entry in plan["transitions"]
    ]
    assert all(step.get("frozen") is True for step in executed)

    # The encoded file agrees with the plan: this is what the old defect failed.
    video = _stream_facts(output, workspace.ffprobe_path, "v:0")
    assert int(video["nb_frames"]) == 96, video
    assert abs(float(video["duration"]) - 4.0) <= 0.05, video
    audio = _stream_facts(output, workspace.ffprobe_path, "a:0")
    assert abs(float(audio["duration"]) - float(video["duration"])) <= 0.06, (audio, video)


@pytest.mark.parametrize(("fps_num", "fps_den", "name"), [(24, 1, "24"), (30000, 1001, "2997")])
def test_short_shot_matrix_keeps_plan_and_file_equal(workspace, database, fps_num, fps_den, name) -> None:
    """24 fps and 30000/1001 must both satisfy their own frozen plan."""

    service, timeline, project = _short_middle_timeline(
        workspace, database, f"tm04_{name}", fps_num=fps_num, fps_den=fps_den
    )
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    plan = render["input_snapshot"]["timeline_plan"]
    # The plan is the project's rational rate, never a rounded integer.
    assert (int(plan["fps_num"]), int(plan["fps_den"])) == (fps_num, fps_den), plan
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    video = _stream_facts(output, workspace.ffprobe_path, "v:0")
    assert int(video["nb_frames"]) == int(plan["expected_video_frames"]), (video, plan["expected_video_frames"])
    assert Fraction(str(video["avg_frame_rate"])) == Fraction(fps_num, fps_den), video


def test_a_plan_that_does_not_match_the_file_is_never_registered(workspace, database) -> None:
    """The gate that was missing: a frame-count lie must not become VERIFIED.

    The audit's short-shot defect was registered as VERIFIED while carrying 90
    frames of a promised 96.  A tampered snapshot reproduces the same disagreement
    through the real registration path, with a genuinely encoded file.
    """

    service, timeline, project = _short_middle_timeline(workspace, database, "tm04_gate")
    render_dir = workspace.projects_root / str(project["root_rel"]) / "05_timelines" / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    source = render_dir / "encoded.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=navy:s=160x90:r=24:d=4",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "4", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-y", str(source),
        ],
        check=True, capture_output=True,
    )
    preflight = service.preflight_episode_render(str(timeline["id"]))
    snapshot = dict(preflight["input_snapshot"])
    plan = dict(snapshot["timeline_plan"])
    plan["expected_video_frames"] = 120  # the file really has 96
    snapshot["timeline_plan"] = plan
    with pytest.raises(DomainRuleError) as failure:
        service._register_render(
            episode=service._episode(str(timeline["episode_id"])),
            timeline_revision_id=str(timeline["id"]),
            timeline=timeline,
            render_path=source,
            project_root=workspace.projects_root / str(project["root_rel"]),
            input_snapshot=snapshot,
            ffmpeg_execution={"executable": workspace.ffmpeg_path, "args": [], "returncode": 0, "stdout_tail": "", "stderr_tail": ""},
            actor="local-user",
        )
    assert failure.value.code == "RENDER_FRAME_COUNT_MISMATCH"
    assert failure.value.details["observed_video_frames"] == 96
    assert failure.value.details["expected_video_frames"] == 120
    # The rejected artifact is removed rather than left as a plausible render.
    assert not source.exists()


def test_planned_fps_beats_the_first_clip_frame_rate(workspace, database) -> None:
    """TM-05: a 24 fps project opening with a 30 fps clip still exports 24 fps."""

    project, episode = _project_episode(workspace, database, "tm05_24", fps_num=24)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _clip(workspace, "tm05-first.mp4", fps=30), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _clip(workspace, "tm05-second.mp4", fps=25, colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "DISSOLVE"}},
        ],
        {"source": "tm05_24"},
    )
    preflight = service.preflight_episode_render(str(timeline["id"]))
    plan = preflight["input_snapshot"]["timeline_plan"]
    assert (plan["fps_num"], plan["fps_den"]) == (24, 1), plan
    assert plan["fps_source"] == "PROJECT", plan

    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    assert render["input_snapshot"]["timeline_plan"] == plan
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    assert Fraction(str(_stream_facts(output, workspace.ffprobe_path, "v:0")["avg_frame_rate"])) == Fraction(24, 1)


def test_planned_fps_may_be_higher_than_the_first_clip(workspace, database) -> None:
    """The precedence is the project's, in both directions."""

    project, episode = _project_episode(workspace, database, "tm05_30", fps_num=30)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _clip(workspace, "tm05b-first.mp4", fps=24), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}}],
        {"source": "tm05_30"},
    )
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    plan = render["input_snapshot"]["timeline_plan"]
    assert (plan["fps_num"], plan["fps_den"]) == (30, 1), plan
    assert plan["fps_source"] == "PROJECT", plan
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    assert Fraction(str(_stream_facts(output, workspace.ffprobe_path, "v:0")["avg_frame_rate"])) == Fraction(30, 1)


def test_rational_project_fps_survives_the_whole_render(workspace, database) -> None:
    """30000/1001 must stay rational in the plan, the frames and the output stream."""

    project, episode = _project_episode(workspace, database, "tm05_2997", fps_num=30000, fps_den=1001)
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _clip(workspace, "tm05c-first.mp4", fps=24), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}}],
        {"source": "tm05_2997"},
    )
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    plan = render["input_snapshot"]["timeline_plan"]
    assert (plan["fps_num"], plan["fps_den"]) == (30000, 1001), plan
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    stream = _stream_facts(output, workspace.ffprobe_path, "v:0")
    assert Fraction(str(stream["avg_frame_rate"])) == Fraction(30000, 1001), stream
