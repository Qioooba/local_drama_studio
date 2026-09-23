"""A cross-dissolve must overlap the sound by the same window as the picture (TM-03).

The picture used ``xfade=offset=current_duration-transition_duration`` so the incoming
clip started *earlier*, while the audio still used ``concat=n=2:v=0:a=1`` and therefore
kept its full source length.  The finished film was then trimmed back to the picture
length, which did two audible things:

* every clip's sound landed *later* than its picture — the sound of a shot kept
  playing under the next shot;
* the removed region was not silence: the third clip's own source tail was deleted
  permanently.

The fix overlaps the audio with ``acrossfade=d=<transition>`` over exactly the
picture's transition window, so the end trim is only a frame/sample rounding guard.
These tests render real films with real FFmpeg and measure the decoded audio's tone
content, so "the sound matches the picture" is an observed property rather than a
filter-graph string comparison.  The plain-concat control shows what the old code
produced.
"""

from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the transition audio-mapping acceptance",
)

SAMPLE_RATE = 48_000
#: One distinct tone per clip, so a window of decoded audio names the clip it came from.
RED_HZ, BLUE_HZ, GREEN_HZ = 440, 800, 1500
SILENT_DB = -70.0


def _clip(workspace, name: str, *, colour: str, frequency: int, seconds: float = 2.0, fps: int = 24) -> Path:
    """One clip that really has an audio track, so the overlap can be heard."""

    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
            "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _episode(workspace, database, code: str, *, fps: int = 24):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=fps, fps_den=1, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    return project, episode


def _three_clip_timeline(workspace, database, code: str):
    project, episode = _episode(workspace, database, code)
    media = MediaService(database, workspace)
    entries = [
        ("red", RED_HZ, "CUT"),
        ("blue", BLUE_HZ, "DISSOLVE"),
        ("green", GREEN_HZ, "DISSOLVE"),
    ]
    items = []
    for index, (colour, frequency, transition) in enumerate(entries):
        imported = media.import_file(
            str(project["id"]),
            _clip(workspace, f"{code}-{colour}.mp4", colour=colour, frequency=frequency),
            purpose="SHOT_VIDEO",
            media_kind="VIDEO",
        )
        items.append(
            {
                "track_type": "VIDEO",
                "media_version_id": str(imported["media_version_id"]),
                "start_us": index * 2_000_000,
                "end_us": (index + 1) * 2_000_000,
                "parameters": {"transition_in": transition},
            }
        )
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        items,
        # Source-model sound is opt-in; the overlap is built in the concat stage and
        # the final mix would otherwise replace it with the deliberate silence policy.
        {"source": code, "include_source_audio": True},
    )
    return service, timeline, project


def _stream_duration_seconds(path: Path, ffprobe: str, selector: str) -> float:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", selector,
            "-show_entries", "stream=duration", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return float(stream["duration"])


def _decode_mono(path: Path, ffmpeg: str) -> list[float]:
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
        ],
        check=True,
        capture_output=True,
    )
    samples = array.array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    return [value / 32768.0 for value in samples]


def _tone_db(samples: list[float], *, start_s: float, seconds: float, frequency: int) -> float:
    """How much of ``frequency`` one window of decoded audio carries, in dBFS.

    A Hamming window and a five-bin search keep the estimate stable for tones that do
    not fall exactly on a DFT bin.  A window with no such tone reports about -90 dB.
    """

    first = int(start_s * SAMPLE_RATE)
    count = min(int(seconds * SAMPLE_RATE), len(samples) - first)
    if count < SAMPLE_RATE // 50:
        raise AssertionError(f"window at {start_s}s is outside the decoded audio")
    window = samples[first : first + count]
    weights = [0.54 - 0.46 * math.cos(2 * math.pi * index / count) for index in range(count)]
    total = sum(weights)
    magnitudes = []
    for offset in range(-2, 3):
        k = frequency + offset
        real = sum(window[i] * weights[i] * math.cos(2 * math.pi * k * i / SAMPLE_RATE) for i in range(count))
        imag = sum(window[i] * weights[i] * math.sin(2 * math.pi * k * i / SAMPLE_RATE) for i in range(count))
        magnitudes.append(2 * math.hypot(real, imag) / total)
    return round(20 * math.log10(max(1e-9, max(magnitudes))), 1)


def _tone_profile(samples: list[float], *, start_s: float, seconds: float) -> dict[str, float]:
    return {
        "red": _tone_db(samples, start_s=start_s, seconds=seconds, frequency=RED_HZ),
        "blue": _tone_db(samples, start_s=start_s, seconds=seconds, frequency=BLUE_HZ),
        "green": _tone_db(samples, start_s=start_s, seconds=seconds, frequency=GREEN_HZ),
    }


def _dominant(profile: dict[str, float]) -> str:
    return max(profile, key=lambda name: profile[name])


def test_picture_and_sound_end_together_after_two_dissolves(workspace, database) -> None:
    """The A/V property the audit measured: one film, one length for both streams."""

    service, timeline, project = _three_clip_timeline(workspace, database, "tm03_sync")
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    assert output.is_file()

    steps = json.loads(render["execution_log"])["steps"]
    transitions = [step for step in steps if step["stage"] == "timeline-transition"]
    assert len(transitions) == 2
    for step in transitions:
        # Every crossfade declares that the sound overlaps by the same window.
        assert step["audio_overlap"] == "ACROSSFADE"
        assert step["audio_overlap_seconds"] == step["duration_seconds"]

    video_seconds = _stream_duration_seconds(output, workspace.ffprobe_path, "v:0")
    audio_seconds = _stream_duration_seconds(output, workspace.ffprobe_path, "a:0")
    overlap = sum(step["duration_seconds"] for step in transitions)
    expected = 6.0 - overlap
    # Both streams end together, and the sound is no longer longer than the picture.
    assert abs(video_seconds - expected) <= 0.1, (video_seconds, expected)
    assert abs(audio_seconds - expected) <= 0.1, (audio_seconds, expected)
    assert abs(audio_seconds - video_seconds) <= 0.1, (audio_seconds, video_seconds)

    # The good case really carries the source sound, so the assertions above compare
    # two audible streams rather than two matching silences, and each clip's tone is
    # in its own picture window.
    samples = _decode_mono(output, workspace.ffmpeg_path)
    assert _dominant(_tone_profile(samples, start_s=0.8, seconds=0.25)) == "red"
    assert _dominant(_tone_profile(samples, start_s=2.5, seconds=0.25)) == "blue"
    assert _tone_profile(samples, start_s=video_seconds - 0.4, seconds=0.25)["green"] > -40.0


def test_the_last_clip_sound_is_not_cut_off_by_the_end_trim(workspace, database) -> None:
    """The audit's tail test: the final clip's own sound must survive the end trim.

    The third clip's own tone lives in its source *tail*.  Plain concat left the mix
    one transition longer than the picture, so trimming to the picture length deleted
    that region and the film's last window carried none of it.
    """

    service, timeline, project = _three_clip_timeline(workspace, database, "tm03_tail")
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]

    video_seconds = _stream_duration_seconds(output, workspace.ffprobe_path, "v:0")
    audio_seconds = _stream_duration_seconds(output, workspace.ffprobe_path, "a:0")
    # The audio used to run to the *unoverlapped* length (6.0 s, one second longer than
    # the picture) and then be trimmed down, which removed real content.
    assert abs(audio_seconds - video_seconds) <= 0.1, (audio_seconds, video_seconds)
    # The picture is 6 s of clips minus the two 0.5 s overlaps: the third clip's
    # picture starts at 3.0 s and the film ends at 5.0 s.
    assert abs(video_seconds - 5.0) <= 0.1, video_seconds

    samples = _decode_mono(output, workspace.ffmpeg_path)
    # The last window of the film carries the third clip's own tone...
    tail = _tone_profile(samples, start_s=video_seconds - 0.35, seconds=0.25)
    assert tail["green"] > -40.0, tail
    # ...in the same measure as just after the crossfade has finished, so the surviving
    # tail is the real signal rather than a residue of something else.
    head = _tone_profile(samples, start_s=3.8, seconds=0.25)
    assert head["green"] > -40.0, head
    assert abs(tail["green"] - head["green"]) <= 12.0, (tail, head)

    # And the first clip's tone has already ended where its picture has: plain concat
    # would still be playing it here, one transition later than the picture.
    assert tail["red"] < SILENT_DB, tail
    assert _tone_profile(samples, start_s=0.3, seconds=0.25)["red"] > -40.0


def test_hard_cut_audio_still_ends_together(workspace, database) -> None:
    """A hard-cut control: with no transition there is no overlap to apply."""

    project, episode = _episode(workspace, database, "tm03_cut")
    media = MediaService(database, workspace)
    items = []
    for index, (colour, frequency) in enumerate((("red", RED_HZ), ("blue", BLUE_HZ))):
        imported = media.import_file(
            str(project["id"]),
            _clip(workspace, f"tm03_cut-{colour}.mp4", colour=colour, frequency=frequency),
            purpose="SHOT_VIDEO",
            media_kind="VIDEO",
        )
        items.append(
            {
                "track_type": "VIDEO",
                "media_version_id": str(imported["media_version_id"]),
                "start_us": index * 2_000_000,
                "end_us": (index + 1) * 2_000_000,
                "parameters": {"transition_in": "CUT"},
            }
        )
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]), items, {"source": "tm03_cut", "include_source_audio": True}
    )
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    steps = json.loads(render["execution_log"])["steps"]
    # No transition steps at all: the hard-cut path is untouched.
    assert [step for step in steps if step["stage"] == "timeline-transition"] == []
    output = workspace.projects_root / str(project["root_rel"]) / render["rel_path"]
    video_seconds = _stream_duration_seconds(output, workspace.ffprobe_path, "v:0")
    assert abs(video_seconds - 4.0) <= 0.1, video_seconds
    assert abs(_stream_duration_seconds(output, workspace.ffprobe_path, "a:0") - video_seconds) <= 0.1
    # Each clip plays its own tone inside its own picture window, so the audio that
    # survives the cut is the audio of the shot on screen.
    samples = _decode_mono(output, workspace.ffmpeg_path)
    assert _dominant(_tone_profile(samples, start_s=0.8, seconds=0.25)) == "red"
    assert _dominant(_tone_profile(samples, start_s=3.0, seconds=0.25)) == "blue"
