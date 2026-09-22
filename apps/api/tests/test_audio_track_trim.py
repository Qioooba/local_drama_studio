"""MED-02: every audio track — looping or not — is trimmed to its declared end.

The original defect: only the ``loop_enabled`` branch appended
``atrim=duration=...``, so a non-looping BGM/SFX/dialogue binding placed at
0.5-1.5 s kept playing its whole source and bled into every later shot.  The
regression asserts the *correct* behaviour and measures it by sampling the real
mixed WAV, not by inspecting the filter string alone.

Real FFmpeg/FFprobe are required: the module is skipped when the binaries are
not resolvable, so the normal suite stays safe.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for the audio-range acceptance",
)


def _tone(workspace, name: str, *, seconds: float = 3.0, frequency: int = 440) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}",
            "-ar", "48000", "-ac", "1", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _silent_video(workspace, name: str, seconds: float = 4.0) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=navy:s=160x90:d={seconds}",
            "-pix_fmt", "yuv420p", "-an", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _pcm(workspace, path: Path, *, start_s: float, duration_s: float) -> array:
    """Decode one window of a media file to 16-bit PCM samples."""
    result = subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_s:.6f}", "-t", f"{duration_s:.6f}", "-i", str(path),
            "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "-",
        ],
        check=True,
        capture_output=True,
    )
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    return samples


def _rms(samples: array) -> float:
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        total += float(value) * float(value)
    return math.sqrt(total / len(samples))


def _project_and_episode(workspace, database, code: str):
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(str(project["id"]))[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    return project, episode


def _bindings_for(workspace, database, episode_id: str, media_version_id: str, *, loop: bool):
    """The binding shape the v3 timeline path hands to the renderer."""
    return [
        {
            "id": "binding-1",
            "media_version_id": media_version_id,
            "track_type": "BGM",
            "start_us": 500_000,
            "end_us": 1_500_000,
            "gain_db": 0.0,
            "loop_enabled": loop,
            "fade_in_us": 0,
            "fade_out_us": 0,
            "source_start_us": 0,
        }
    ]


def _binding(media_version_id: str, **overrides) -> dict:
    binding = {
        "id": "binding-1",
        "media_version_id": media_version_id,
        "track_type": "BGM",
        "start_us": 0,
        "end_us": 1_000_000,
        "gain_db": 0.0,
        "loop_enabled": False,
        "fade_in_us": 0,
        "fade_out_us": 0,
        "source_start_us": 0,
    }
    binding.update(overrides)
    return binding


@pytest.mark.parametrize("loop_enabled", [False, True])
def test_non_looping_and_looping_tracks_both_stop_at_end_us(workspace, database, loop_enabled: bool) -> None:
    """Acceptance: BGM sampled after its declared end is silent — for both modes."""

    code = f"med02_{'loop' if loop_enabled else 'once'}"
    project, episode = _project_and_episode(workspace, database, code)
    media = MediaService(database, workspace)
    video = media.import_file(str(project["id"]), _silent_video(workspace, f"{code}-video.mp4", 4.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    bgm = media.import_file(str(project["id"]), _tone(workspace, f"{code}-bgm.wav", seconds=3.0, frequency=440), purpose="AUDIO", media_kind="AUDIO")

    service = TimelineService(database, workspace)
    service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(video["media_version_id"]), "start_us": 0, "end_us": 4_000_000, "parameters": {}}],
        {"source": "med02"},
    )
    bindings = _bindings_for(workspace, database, str(episode["id"]), str(bgm["media_version_id"]), loop=loop_enabled)

    # Mix against the real project-owned source path, not the work copy.
    _, video_path = media.content_path(str(video["media_version_id"]))
    mixed = workspace.work_root / f"{code}-mix.wav"
    service._mix_audio(video_path, bindings, mixed, 4.0)
    assert mixed.is_file()

    # Sample well inside the binding: the 0.5-1.5 s window straddles the 1 ms
    # adelay grid, so the region just after 0.5 s is the honest place to measure
    # "the track is audible where the editor placed it".
    inside = _rms(_pcm(workspace, mixed, start_s=0.6, duration_s=0.4))
    after = _rms(_pcm(workspace, mixed, start_s=2.0, duration_s=0.5))
    before = _rms(_pcm(workspace, mixed, start_s=0.0, duration_s=0.3))

    assert inside > 100, f"BGM must be audible inside 0.5-1.5 s (rms={inside})"
    assert before < 50, f"BGM must not start before start_us (rms={before})"
    # The acceptance criterion for MED-02: after its declared end, silence.
    assert after < 50, f"BGM must be silent after end_us (rms={after}) — loop_enabled={loop_enabled}"


def test_looping_track_still_fills_its_whole_declared_range(workspace, database) -> None:
    """Looping decides only whether the source repeats — not whether end_us applies."""

    project, _episode = _project_and_episode(workspace, database, "med02_fill")
    media = MediaService(database, workspace)
    video = media.import_file(str(project["id"]), _silent_video(workspace, "med02-fill-video.mp4", 2.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    # A 0.3 s source under a 1.0 s binding must be repeated to cover the range.
    bgm = media.import_file(str(project["id"]), _tone(workspace, "med02-short-bgm.wav", seconds=0.3), purpose="AUDIO", media_kind="AUDIO")
    service = TimelineService(database, workspace)
    bindings = [_binding(str(bgm["media_version_id"]), loop_enabled=True)]
    _, fill_video_path = media.content_path(str(video["media_version_id"]))
    mixed = workspace.work_root / "med02-fill-mix.wav"
    service._mix_audio(fill_video_path, bindings, mixed, 2.0)
    # The looped source covers the whole declared second, not just its first 0.3 s.
    assert _rms(_pcm(workspace, mixed, start_s=0.6, duration_s=0.3)) > 100
    # ... and it still stops at end_us.
    assert _rms(_pcm(workspace, mixed, start_s=1.2, duration_s=0.3)) < 50


def test_source_offset_and_fades_are_anchored_to_the_trimmed_binding(workspace, database) -> None:
    """Non-zero source offset plus fade-out must be applied inside the range."""

    project, _episode = _project_and_episode(workspace, database, "med02_offset")
    media = MediaService(database, workspace)
    video = media.import_file(str(project["id"]), _silent_video(workspace, "med02-offset-video.mp4", 2.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    bgm = media.import_file(str(project["id"]), _tone(workspace, "med02-offset-bgm.wav", seconds=3.0), purpose="AUDIO", media_kind="AUDIO")
    service = TimelineService(database, workspace)
    bindings = [
        _binding(
            str(bgm["media_version_id"]),
            fade_in_us=200_000,
            fade_out_us=200_000,
            source_start_us=1_000_000,
        )
    ]
    _, offset_video_path = media.content_path(str(video["media_version_id"]))
    mixed = workspace.work_root / "med02-offset-mix.wav"
    service._mix_audio(offset_video_path, bindings, mixed, 2.0)

    head = _rms(_pcm(workspace, mixed, start_s=0.0, duration_s=0.05))
    middle = _rms(_pcm(workspace, mixed, start_s=0.4, duration_s=0.2))
    tail = _rms(_pcm(workspace, mixed, start_s=0.95, duration_s=0.05))
    after = _rms(_pcm(workspace, mixed, start_s=1.2, duration_s=0.3))

    assert middle > 500, f"middle of the binding must be at full level (rms={middle})"
    assert head < middle / 2, f"fade-in must attenuate the head (rms={head})"
    assert tail < middle / 2, f"fade-out must attenuate the tail (rms={tail})"
    assert after < 50, f"the track must be silent after end_us (rms={after})"


def test_inverted_audio_range_is_rejected_instead_of_silently_mixed(workspace, database) -> None:
    """An invalid range must be an explicit error, not a silently ignored one."""

    project, _episode = _project_and_episode(workspace, database, "med02_bad_range")
    media = MediaService(database, workspace)
    video = media.import_file(str(project["id"]), _silent_video(workspace, "med02-bad-video.mp4", 2.0), purpose="SHOT_VIDEO", media_kind="VIDEO")
    bgm = media.import_file(str(project["id"]), _tone(workspace, "med02-bad-bgm.wav", seconds=1.0), purpose="AUDIO", media_kind="AUDIO")
    service = TimelineService(database, workspace)
    from local_drama.domain.errors import DomainRuleError

    _, bad_video_path = media.content_path(str(video["media_version_id"]))
    with pytest.raises(DomainRuleError) as invalid:
        service._mix_audio(
            bad_video_path,
            [_binding(str(bgm["media_version_id"]), start_us=1_000_000, end_us=500_000)],
            workspace.work_root / "med02-bad-mix.wav",
            2.0,
        )
    assert invalid.value.code == "TIMELINE_AUDIO_RANGE_INVALID"
