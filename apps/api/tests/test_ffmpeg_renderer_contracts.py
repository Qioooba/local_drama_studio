"""Regression tests for the deterministic composition renderer.

Every case here reproduces a defect that was proven with real FFmpeg on the audit
snapshot:

* ``LDS-05`` / ``TM-09`` — two or more narration tracks produced ``[voice_raw]``
  while the rest of the graph consumed ``[voice]``, so the filtergraph could not
  bind and FFmpeg exited 234.
* ``LDS-06`` / ``TM-07`` — a clip spanning two blocks was read from its own head
  in both blocks, so the second block repeated the first block's pictures.
* ``LDS-18`` / ``TM-10`` — ``atrim=sample_count=`` is not an FFmpeg option, and
  ``narration_start_sample`` was written into the command note and otherwise
  ignored.
* ``TM-08`` — a narration WAV on the same time range as the picture was compiled
  into the video concat, so FFmpeg reported "matches no streams".

The FFmpeg-executing tests are skipped when the binary is missing, but they are
never replaced by a filter-string comparison: the assertion is on decoded
frames / PCM, not on the graph text.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from local_drama.application.composition.manifest import (
    AspectRatio,
    ManifestChunkSpec,
    Ratio,
    build_manifest,
    manifest_clip,
)
from local_drama.application.composition.slices import compute_chunk_slices
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.composition.ffmpeg_renderer import (
    FfmpegCommand,
    FfmpegFilterGraph,
    FfmpegRunner,
    build_chunk_command,
    build_mix_command,
    classify_process_failure,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="需要本机 ffmpeg/ffprobe")

FPS_25 = Ratio(25, 1)
SAMPLE_RATE = 48_000


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def _slate_video(path: Path, *, frames: int, fps: Ratio = FPS_25, pattern: str = "testsrc") -> None:
    """One short video whose every frame carries a readable, unique picture."""

    argv = [
        str(FFMPEG), "-y", "-v", "error",
        "-f", "lavfi",
        "-i", f"{pattern}=size=64x48:rate={fps.num}/{fps.den}:duration={frames * fps.den / fps.num:.6f}",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    ]
    completed = _run(argv)
    assert completed.returncode == 0, completed.stderr


def _tone_wav(path: Path, *, seconds: float, frequency: int, sample_rate: int = SAMPLE_RATE) -> None:
    completed = _run([
        str(FFMPEG), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds:.6f}:sample_rate={sample_rate}",
        "-ac", "2", "-c:a", "pcm_s16le",
        str(path),
    ])
    assert completed.returncode == 0, completed.stderr


def _frame_md5(path: Path) -> list[str]:
    """Decode every frame and return one digest per *decoded* frame.

    ``-fps_mode passthrough`` matters here: without it ffmpeg re-times the output
    to the container rate and reports duplicated frames, which would hide a real
    advance through the source (or invent one).
    """

    completed = _run([
        str(FFMPEG), "-v", "error", "-i", str(path),
        "-fps_mode", "passthrough", "-f", "framemd5", "-",
    ])
    assert completed.returncode == 0, completed.stderr
    digests: list[str] = []
    for line in completed.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 6:
            digests.append(parts[-1].strip())
    return digests


def _frame_pixels(path: Path) -> list[bytes]:
    """Every decoded frame as a 16x16 luma reduction, in decode order.

    Binary output is captured in Python (never through a text-mode pipe), so the
    frame count and the picture content come from the decoder itself rather than
    from the container's frame count.
    """

    completed = subprocess.run(
        [
            str(FFMPEG), "-v", "error", "-i", str(path),
            "-fps_mode", "passthrough",
            "-vf", "scale=16:16,format=gray",
            "-f", "rawvideo", "-",
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    payload = completed.stdout
    frame_bytes = 16 * 16
    assert len(payload) % frame_bytes == 0, len(payload)
    return [payload[index:index + frame_bytes] for index in range(0, len(payload), frame_bytes)]


def _mean_abs_difference(left: bytes, right: bytes) -> float:
    """Mean absolute per-pixel difference of two 16x16 luma reductions.

    Two encodes of the same picture differ only by codec noise, so a small
    threshold distinguishes "the same picture" from "the same small picture
    repeated", which is what a repeated source head produces.
    """

    return sum(abs(a - b) for a, b in zip(left, right)) / max(1, len(left))


def _assert_same_pictures(actual: list[bytes], expected: list[bytes], *, threshold: float = 6.0) -> None:
    assert len(actual) == len(expected), (len(actual), len(expected))
    worst = max((_mean_abs_difference(a, b) for a, b in zip(actual, expected)), default=0.0)
    assert worst <= threshold, f"帧内容不一致：最大平均差 {worst:.2f}"


def _decoded_frame_count(path: Path) -> int:
    """Count the frames ffprobe actually decodes (not the container's guess)."""

    completed = _run([
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries", "stream=nb_read_frames",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    assert completed.returncode == 0, completed.stderr
    return int(completed.stdout.strip())


def _pcm_rms(path: Path, *, start_seconds: float, duration_seconds: float) -> float:
    """Decode a slice of the file and report its RMS through ``astats``."""

    completed = _run([
        str(FFMPEG), "-v", "error", "-i", str(path),
        "-ss", f"{start_seconds:.6f}", "-t", f"{duration_seconds:.6f}",
        "-af", "astats=metadata=1:reset=0", "-f", "null", "-",
    ])
    assert completed.returncode == 0, completed.stderr
    for line in completed.stderr.splitlines():
        if "RMS level dB" in line:
            value = line.split(":", 1)[1].strip()
            if value not in {"-inf", "inf"}:
                return float(value)
    return float("-inf")


def _one_clip_manifest(tmp_path: Path, *, frames: int, source_seconds: float) -> tuple[object, Path]:
    source = tmp_path / "source.mp4"
    _slate_video(source, frames=int(source_seconds * FPS_25.value))
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="c1",
                track="VIDEO",
                item_kind="VIDEO_CLIP",
                start_frame=0,
                end_frame_exclusive=frames,
                fps=FPS_25,
                sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1",
                source_in_us=0,
                source_out_us=int(source_seconds * 1_000_000),
            )
        ],
        chunks=[
            ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=frames, handle_in_frames=0, handle_out_frames=0)
        ],
        validate_structure=False,
    )
    return manifest, source


# --------------------------------------------------------------------------- #
# structural proofs (no FFmpeg needed)
# --------------------------------------------------------------------------- #
def test_filtergraph_refuses_label_without_producer() -> None:
    """The historical ``[voice]`` / ``[voice_raw]`` mismatch must be refused."""

    graph = FfmpegFilterGraph()
    graph.chain(["0:a:0"], ["aformat=sample_rates=48000"], ["voice_raw"])
    graph.chain(["voice_sc"], ["volume=1.000000"], ["voice"])
    with pytest.raises(ExplainerContractError) as error:
        graph.validate(terminal=["voice_raw", "voice"])
    assert error.value.code == "FFMPEG_FILTERGRAPH_INVALID"


def test_filtergraph_refuses_duplicate_producer_and_dangling_output() -> None:
    graph = FfmpegFilterGraph()
    graph.chain(["0:a:0"], ["aformat=sample_rates=48000"], ["x"])
    graph.chain(["1:a:0"], ["aformat=sample_rates=48000"], ["x"])
    with pytest.raises(ExplainerContractError) as duplicate:
        graph.validate(terminal=["x"])
    assert duplicate.value.code == "FFMPEG_FILTERGRAPH_INVALID"

    lonely = FfmpegFilterGraph()
    lonely.chain(["0:a:0"], ["aformat=sample_rates=48000"], ["unused"])
    with pytest.raises(ExplainerContractError) as dangling:
        lonely.validate()
    assert dangling.value.code == "FFMPEG_FILTERGRAPH_INVALID"


def test_filtergraph_label_allocator_never_collides() -> None:
    graph = FfmpegFilterGraph()
    first = graph.label("voice_mix")
    graph.chain(["0:a:0"], ["asplit=2"], [first, graph.label("voice_sc")])
    assert graph.label("voice_mix") != first


def test_atrim_never_emits_an_option_ffmpeg_rejects() -> None:
    """``atrim=sample_count=`` was invalid; it must become a real sample window."""

    spec = FfmpegFilterGraph.atrim(sample_count=24_000)
    assert "sample_count" not in spec
    assert spec == "atrim=start_sample=0:end_sample=24000"
    assert FfmpegFilterGraph.atrim(start_sample=100, end_sample=200) == "atrim=start_sample=100:end_sample=200"


def test_mix_command_rejects_unsupported_non_zero_narration_offset() -> None:
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=25,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="c1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=25, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
            )
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=25)],
        validate_structure=False,
    )
    with pytest.raises(ExplainerContractError) as error:
        build_mix_command(
            manifest=manifest,
            narration_paths=[Path("a.wav")],
            bgm_path=None,
            sfx_paths=[],
            output_path=Path("out.wav"),
            narration_start_sample=48_000,
        )
    assert error.value.code == "NARRATION_START_NOT_SUPPORTED"


def test_chunk_command_refuses_declared_item_kind_it_cannot_compile(tmp_path: Path) -> None:
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=25,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="t1", track="VIDEO", item_kind="TEXT_LAYER",
                start_frame=0, end_frame_exclusive=25, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
            )
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=25)],
        validate_structure=False,
    )
    with pytest.raises(ExplainerContractError) as error:
        build_chunk_command(
            manifest=manifest,
            chunk=manifest.chunks[0],
            output_path=tmp_path / "out.mp4",
            work_dir=tmp_path,
            media_path_resolver=lambda _media, _sha: str(tmp_path / "nope.mp4"),
        )
    assert error.value.code == "RENDER_ITEM_KIND_UNSUPPORTED"


# --------------------------------------------------------------------------- #
# error classification and log bounding (LDS-17)
# --------------------------------------------------------------------------- #
def test_permanent_filter_error_is_not_offered_as_retryable(tmp_path: Path) -> None:
    """A deterministic filtergraph error also produces no file — and no retry.

    The historical ordering classified it as recoverable ``NO_OUTPUT`` because the
    output path was not passed in, so the same impossible graph was retried until
    the budget ran out.
    """

    classified = classify_process_failure(
        returncode=234,
        stderr=(
            "Filter 'concat:out:a0' has output 0 (voice_raw) unconnected\n"
            "Error binding filtergraph inputs/outputs: Invalid argument\n"
        ),
        output_path=tmp_path / "never-written.mp4",
    )
    assert classified["reason"] == "FILTER_INVALID"
    assert classified["recoverable"] is False
    assert classified["status"] == "FAILED"


def test_missing_input_is_a_concrete_non_retryable_diagnosis(tmp_path: Path) -> None:
    classified = classify_process_failure(
        returncode=1,
        stderr="[1:v:0] ... matches no streams\nnope.mp4: No such file or directory\n",
        output_path=tmp_path / "out.mp4",
    )
    assert classified["reason"] == "INPUT_MISSING"
    assert classified["recoverable"] is False


def test_disk_full_and_cancel_are_distinguished(tmp_path: Path) -> None:
    disk = classify_process_failure(
        returncode=1,
        stderr="av_interleaved_write_frame(): No space left on device",
        output_path=tmp_path / "out.mp4",
    )
    assert disk["reason"] == "DISK_FULL" and disk["recoverable"] is True

    cancelled = classify_process_failure(
        returncode=1,
        stderr="Exiting normally, received signal 2.",
        output_path=tmp_path / "out.mp4",
        cancelled=True,
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["reason"] == "USER_CANCELLED"
    assert cancelled["recoverable"] is False


def test_unknown_failure_without_output_stays_recoverable(tmp_path: Path) -> None:
    classified = classify_process_failure(
        returncode=1,
        stderr="something unexpected happened",
        output_path=tmp_path / "out.mp4",
    )
    assert classified["reason"] == "NO_OUTPUT"
    assert classified["recoverable"] is True


@pytest.mark.skipif(FFMPEG is None, reason="需要本机 ffmpeg")
def test_default_runner_bounds_its_log_tail_and_records_the_real_size(tmp_path: Path) -> None:
    """A huge stderr must not grow the in-memory tail without limit.

    The child is the running interpreter, so this exercises the real streaming
    runner without depending on FFmpeg's own verbosity.
    """

    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys\n"
        "for _ in range(4000):\n"
        "    sys.stderr.write('x' * 200 + chr(10))\n"
        "sys.stderr.write('marker-at-the-end' + chr(10))\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    failing = FfmpegCommand(args=(str(script),), purpose="TEST")
    result = FfmpegRunner(ffmpeg=sys.executable, tail_bytes=4096).run(failing)
    assert result["returncode"] == 3
    assert len(result["stderr_tail"]) <= 4096
    assert "marker-at-the-end" in result["stderr_tail"]
    assert result["log_bytes"] is not None and int(result["log_bytes"]) > 100_000
    assert result["log_truncated"] is True


@requires_ffmpeg
def test_default_runner_classifies_a_real_invalid_filter(tmp_path: Path) -> None:
    """A real FFmpeg run with a bad option must not be reported as retryable."""

    runner = FfmpegRunner()
    command = FfmpegCommand(
        args=(
            "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=32x32:rate=25:duration=0.2",
            "-vf", "thisfilterdoesnotexist=1",
            "-frames:v", "2",
            str(tmp_path / "invalid-filter.mp4"),
        ),
        purpose="CHUNK",
    )
    result = runner.run(command)
    assert result["status"] == "FAILED"
    assert result["reason"] in {"FILTER_INVALID", "ARGUMENT_INVALID"}
    assert result["recoverable"] is False


# --------------------------------------------------------------------------- #
# slice arithmetic (no FFmpeg needed)
# --------------------------------------------------------------------------- #
def _cross_block_manifest(*, total_frames: int, source_seconds: float) -> object:
    return build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="c1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=total_frames, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1",
                source_in_us=0, source_out_us=int(source_seconds * 1_000_000),
            )
        ],
        chunks=[
            ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=total_frames // 2),
            ManifestChunkSpec(chunk_no=1, start_frame=total_frames // 2, end_frame_exclusive=total_frames),
        ],
        validate_structure=False,
    )


def test_second_block_advances_into_the_source_instead_of_repeating_the_head() -> None:
    manifest = _cross_block_manifest(total_frames=100, source_seconds=4.0)
    first = compute_chunk_slices(manifest=manifest, chunk=manifest.chunks[0], tracks=("VIDEO",))
    second = compute_chunk_slices(manifest=manifest, chunk=manifest.chunks[1], tracks=("VIDEO",))
    assert len(first) == 1 and len(second) == 1
    assert first[0].local_start_frame == 0
    assert first[0].local_end_frame_exclusive == 50
    # The defect: this used to be 0 again, so block 1 re-decoded frames 0..49.
    assert second[0].local_start_frame == 50
    assert second[0].local_end_frame_exclusive == 100
    assert second[0].source_advance_us == 2_000_000
    assert second[0].source_read_start_us == 2_000_000
    assert second[0].output_frame_count == 50


def test_slices_carry_handles_and_clip_local_windows() -> None:
    manifest = _cross_block_manifest(total_frames=100, source_seconds=4.0)
    chunk = ManifestChunkSpec(
        chunk_no=1, start_frame=50, end_frame_exclusive=100,
        handle_in_frames=10, handle_out_frames=10,
    )
    plans = compute_chunk_slices(manifest=manifest, chunk=chunk, tracks=("VIDEO",))
    assert len(plans) == 1
    plan = plans[0]
    # The decode window is [40, 100); the clip still covers its whole length.
    assert plan.intersection_start_frame == 40
    assert plan.intersection_end_frame_exclusive == 100
    assert plan.local_start_frame == 40
    assert plan.source_read_start_us == 1_600_000
    assert plan.covers_decode_end is True


def test_slices_ignore_audio_tracks() -> None:
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=50,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="v1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=50, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1", source_in_us=0, source_out_us=2_000_000,
            ),
            manifest_clip(
                clip_id="n1", track="NARRATION", item_kind="AUDIO_CLIP",
                start_frame=0, end_frame_exclusive=50, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-2", source_in_us=0, source_out_us=2_000_000,
            ),
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=50)],
        validate_structure=False,
    )
    names = [plan.clip_id for plan in compute_chunk_slices(manifest=manifest, chunk=manifest.chunks[0], tracks=("VIDEO",))]
    assert names == ["v1"]


def test_gap_between_clips_is_planned_as_an_explicit_black_run() -> None:
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=50,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="a", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=20, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1",
            ),
            manifest_clip(
                clip_id="b", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=30, end_frame_exclusive=50, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1",
            ),
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=50)],
        validate_structure=False,
    )
    plans = compute_chunk_slices(manifest=manifest, chunk=manifest.chunks[0], tracks=("VIDEO",))
    assert [plan.clip_id for plan in plans] == ["a", "b"]
    assert plans[1].intersection_start_frame == 30


# --------------------------------------------------------------------------- #
# real FFmpeg execution
# --------------------------------------------------------------------------- #
@requires_ffmpeg
def test_real_ffmpeg_multi_narration_mix_binds_and_produces_audio(tmp_path: Path) -> None:
    """Two narration tracks used to exit 234 with "Invalid stream specifier"."""

    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=50,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=50)],
        validate_structure=False,
    )
    voice_a = tmp_path / "n0.wav"
    voice_b = tmp_path / "n1.wav"
    _tone_wav(voice_a, seconds=1.0, frequency=440)
    _tone_wav(voice_b, seconds=1.0, frequency=880)
    output = tmp_path / "mix.wav"
    command = build_mix_command(
        manifest=manifest,
        narration_paths=[voice_a, voice_b],
        bgm_path=None,
        sfx_paths=[],
        output_path=output,
    )
    completed = _run([str(FFMPEG), *command.to_argv()])
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert output.exists() and output.stat().st_size > 0
    assert "[n0][n1]concat=n=2:v=0:a=1[voice]" in str(command)


@requires_ffmpeg
def test_real_ffmpeg_multi_narration_with_bgm_and_sfx(tmp_path: Path) -> None:
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=50,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=50)],
        validate_structure=False,
    )
    voices = []
    for index, frequency in enumerate((440, 660, 880)):
        path = tmp_path / f"n{index}.wav"
        _tone_wav(path, seconds=0.5, frequency=frequency)
        voices.append(path)
    bgm = tmp_path / "bgm.wav"
    _tone_wav(bgm, seconds=2.0, frequency=110)
    sfx = tmp_path / "sfx.wav"
    _tone_wav(sfx, seconds=0.3, frequency=1320)
    output = tmp_path / "mix.wav"
    command = build_mix_command(
        manifest=manifest,
        narration_paths=voices,
        bgm_path=bgm,
        sfx_paths=[sfx],
        output_path=output,
    )
    completed = _run([str(FFMPEG), *command.to_argv()])
    assert completed.returncode == 0, completed.stderr[-3000:]
    # 50 frames at 25 fps is exactly two seconds; the mix must be that long.
    probe = _run([
        str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(output),
    ])
    assert probe.returncode == 0
    assert abs(float(probe.stdout.strip()) - 2.0) < 0.05


@requires_ffmpeg
def test_real_ffmpeg_cross_block_does_not_repeat_the_first_block(tmp_path: Path) -> None:
    """Block 1 used to re-read the source head; decoded frames must advance."""

    total_frames = 100
    source = tmp_path / "source.mp4"
    _slate_video(source, frames=total_frames, pattern="testsrc")
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="c1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=total_frames, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1", source_in_us=0, source_out_us=4_000_000,
            )
        ],
        chunks=[
            ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=50),
            ManifestChunkSpec(chunk_no=1, start_frame=50, end_frame_exclusive=100),
        ],
        validate_structure=False,
    )
    resolver = lambda _media, _sha: str(source)  # noqa: E731
    # The control: the same film rendered as one block, with no chunk boundary
    # at all.  Comparing against it proves the chunked render is *identical*,
    # which is the acceptance criterion — not merely that the frame count adds up.
    whole_manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=list(manifest.clips),
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=total_frames)],
        validate_structure=False,
    )
    control = tmp_path / "control.mp4"
    control_command = build_chunk_command(
        manifest=whole_manifest,
        chunk=whole_manifest.chunks[0],
        output_path=control,
        work_dir=tmp_path,
        media_path_resolver=resolver,
        has_audio_lookup={"mv-1": False},
    )
    completed = _run([str(FFMPEG), *control_command.to_argv()])
    assert completed.returncode == 0, completed.stderr[-3000:]

    outputs = []
    for chunk in manifest.chunks:
        target = tmp_path / f"chunk_{chunk.chunk_no}.mp4"
        command = build_chunk_command(
            manifest=manifest,
            chunk=chunk,
            output_path=target,
            work_dir=tmp_path,
            media_path_resolver=resolver,
            has_audio_lookup={"mv-1": False},
        )
        completed = _run([str(FFMPEG), *command.to_argv()])
        assert completed.returncode == 0, completed.stderr[-3000:]
        outputs.append(target)
    control_frames = _frame_pixels(control)
    first_frames = _frame_pixels(outputs[0])
    second_frames = _frame_pixels(outputs[1])
    assert len(control_frames) == total_frames, len(control_frames)
    for target, expected in ((outputs[0], 50), (outputs[1], 50)):
        assert _decoded_frame_count(target) == expected
        assert len(_frame_pixels(target)) == expected
    # The whole point: the second block must not be a copy of the first.
    assert first_frames[0] != second_frames[0]
    assert not any(
        _mean_abs_difference(a, b) <= 1.0
        for a in first_frames[:5]
        for b in second_frames[:5]
    )
    # Each block advances through its own pictures instead of freezing on one.
    assert _mean_abs_difference(first_frames[0], first_frames[-1]) > 4.0
    assert _mean_abs_difference(second_frames[0], second_frames[-1]) > 4.0
    # Frame-for-frame the same pictures as the unchunked render of the manifest.
    _assert_same_pictures(first_frames, control_frames[:50])
    _assert_same_pictures(second_frames, control_frames[50:100])


@requires_ffmpeg
def test_real_ffmpeg_narration_wav_is_not_compiled_into_the_picture(tmp_path: Path) -> None:
    """A narration WAV sharing the picture's time range used to break the graph."""

    total_frames = 25
    picture = tmp_path / "picture.mp4"
    _slate_video(picture, frames=total_frames)
    narration = tmp_path / "narration.wav"
    _tone_wav(narration, seconds=1.0, frequency=440)
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="v1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=total_frames, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-video", source_in_us=0, source_out_us=1_000_000,
            ),
            manifest_clip(
                clip_id="n1", track="NARRATION", item_kind="AUDIO_CLIP",
                start_frame=0, end_frame_exclusive=total_frames, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-voice", source_in_us=0, source_out_us=1_000_000,
            ),
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=total_frames)],
        validate_structure=False,
    )
    paths = {"mv-video": picture, "mv-voice": narration}
    target = tmp_path / "chunk.mp4"
    command = build_chunk_command(
        manifest=manifest,
        chunk=manifest.chunks[0],
        output_path=target,
        work_dir=tmp_path,
        media_path_resolver=lambda media, _sha: str(paths[media]),
        has_audio_lookup={"mv-video": False},
    )
    completed = _run([str(FFMPEG), *command.to_argv()])
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert str(narration) not in command.to_argv()
    probe = _run([
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries", "stream=nb_read_frames",
        "-of", "default=nw=1:nk=1", str(target),
    ])
    assert probe.returncode == 0
    assert int(probe.stdout.strip()) == total_frames


@requires_ffmpeg
def test_real_ffmpeg_chunk_reports_the_declared_frame_count(tmp_path: Path) -> None:
    """A single-block render must still produce exactly the manifest frame count."""

    total_frames = 40
    source = tmp_path / "source.mp4"
    _slate_video(source, frames=total_frames)
    manifest = build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=SAMPLE_RATE,
        clips=[
            manifest_clip(
                clip_id="c1", track="VIDEO", item_kind="VIDEO_CLIP",
                start_frame=0, end_frame_exclusive=total_frames, fps=FPS_25, sample_rate_hz=SAMPLE_RATE,
                media_version_id="mv-1", source_in_us=0, source_out_us=1_600_000,
            )
        ],
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=total_frames)],
        validate_structure=False,
    )
    target = tmp_path / "chunk.mp4"
    command = build_chunk_command(
        manifest=manifest,
        chunk=manifest.chunks[0],
        output_path=target,
        work_dir=tmp_path,
        media_path_resolver=lambda _media, _sha: str(source),
        has_audio_lookup={"mv-1": False},
    )
    completed = _run([str(FFMPEG), *command.to_argv()])
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert _decoded_frame_count(target) == total_frames
    # Every rendered frame is distinct, i.e. the block advanced through the
    # source instead of freezing on (or re-reading) its first frame.
    rendered = _frame_pixels(target)
    assert len(rendered) == total_frames
    assert _mean_abs_difference(rendered[0], rendered[-1]) > 4.0
    assert _mean_abs_difference(rendered[0], rendered[1]) > 0.2
