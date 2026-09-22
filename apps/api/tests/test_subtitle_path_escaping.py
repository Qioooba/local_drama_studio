"""MED-06: subtitle burn-in must work regardless of special characters in the path.

The original defect: the renderer built ``subtitles=filename='<absolute path>'``
after a single ``replace`` pass, so a working directory containing a single quote
made FFmpeg strip the quote and fail to find the file.  The fix runs FFmpeg inside
a controlled attempt directory with a special-character-free relative subtitle
name, which removes the multi-layer escaping problem entirely.

These tests use REAL FFmpeg and verify pixels, not just exit codes: a burn-in that
"returns 0" but writes no subtitle is not acceptance.  Skipped without FFmpeg.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.timeline import TimelineService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="real FFmpeg is required for the subtitle burn-in acceptance",
)

SRT = "1\n00:00:00,000 --> 00:00:03,000\nHELLO SUBTITLE\n\n"

#: Directory names a real user project can legitimately have.
PATH_CASES = [
    ("plain", "renders"),
    ("space", "renders with spaces"),
    ("single-quote", "creator's studio"),
    ("brackets", "studio[demo]"),
    ("comma", "a,b studio"),
    ("chinese", "中文目录"),
    ("mixed", "creator's [中文] studio"),
]


def _source_video(ffmpeg: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "source.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=24:d=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _frame_hashes(ffmpeg: str, video: Path, count: int = 2) -> list[str]:
    """Content hash of the first N one-second frames, from the real file."""
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-vf", "fps=1", "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        check=True, capture_output=True,
    )
    data = result.stdout
    size = 160 * 90
    return [hashlib.sha256(data[index * size : (index + 1) * size]).hexdigest() for index in range(min(count, len(data) // size))]


@pytest.mark.parametrize(("label", "directory_name"), PATH_CASES)
def test_subtitle_burn_in_works_for_every_special_path(tmp_path: Path, label: str, directory_name: str) -> None:
    """Every tested directory shape must produce a real, visibly subtitled file."""

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    render_dir = tmp_path / directory_name
    source = _source_video(ffmpeg, render_dir)
    before = _frame_hashes(ffmpeg, source)
    assert before, "the fixture must produce real frames"

    service = TimelineService.__new__(TimelineService)
    service.settings = type("S", (), {"ffmpeg_path": ffmpeg, "ffprobe_path": shutil.which("ffprobe")})()
    service.cancel_check = None
    service.progress_callback = None

    output = render_dir / "burned.mp4"
    execution = service._burn_subtitle(
        source,
        {"format": "SRT", "content_text": SRT},
        output,
        duration_seconds=3.0,
        filter_cwd=render_dir,
    )
    assert execution["returncode"] == 0
    assert output.is_file() and output.stat().st_size > 0

    after = _frame_hashes(ffmpeg, output)
    assert after, "the burn-in must produce real frames"
    # Pixels changed == the subtitle was actually drawn, not merely "exit 0".
    assert after != before, f"{label}: burn-in reported success but changed no pixels"

    # The temporary subtitle file is gone and nothing partial is left behind.
    assert not list(render_dir.glob("sub-*.srt"))
    assert not list(render_dir.glob(".partial-*"))


def test_subtitle_filter_spec_never_contains_a_host_path(tmp_path: Path) -> None:
    """The filter must receive a bare relative name, not an absolute path."""

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    render_dir = tmp_path / "creator's studio"
    source = _source_video(ffmpeg, render_dir)
    service = TimelineService.__new__(TimelineService)
    service.settings = type("S", (), {"ffmpeg_path": ffmpeg, "ffprobe_path": shutil.which("ffprobe")})()
    service.cancel_check = None
    service.progress_callback = None

    output = render_dir / "burned.mp4"
    execution = service._burn_subtitle(
        source, {"format": "SRT", "content_text": SRT}, output, duration_seconds=2.0, filter_cwd=render_dir
    )
    args = execution["args"]
    spec = args[args.index("-vf") + 1]
    assert spec.startswith("subtitles=filename='"), spec
    # No drive letter, no slash, no apostrophe inside the quoted value.
    quoted = spec[len("subtitles=filename='") : -1]
    assert ":" not in quoted, f"the filter must not carry a drive letter: {quoted!r}"
    assert "/" not in quoted and "\\" not in quoted, f"the filter must not carry a path: {quoted!r}"
    assert "'" not in quoted, f"the filter must not carry a quote: {quoted!r}"


def test_legacy_subtitle_filter_path_escaping_is_still_available() -> None:
    """The old escaping helper is retained for callers outside the renderer."""

    escaped = TimelineService._subtitle_filter_path(Path("C:/a b/creator's studio/sub.srt"))
    assert "\\:" in escaped
    assert "\\'" in escaped
    assert " " in escaped
