"""Real decoder-backed tests for the explainer technical QC layer.

These tests deliberately use the local ``ffmpeg``/``ffprobe`` binaries instead of a
mock: the whole point of the technical layer is that ``decoded_frames`` is a measured
number, so a test that stubs the decoder would prove nothing about the claim being
made.  A synthetic clip is generated once per session and then verified, truncated
and probed for the interesting cases.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.explainers.media_qc import (
    FfmpegDecodeVerifier,
    FfmpegFrameSampler,
    beat_frame_ranges_from_items,
    build_media_qc_readers,
    parse_frame_rate,
)
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, content_hash

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe are required for real decode verification"
)


@pytest.fixture(scope="module")
def synthetic_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 2-second 25fps clip: 50 frames, decodable end to end."""

    directory = tmp_path_factory.mktemp("media-qc")
    path = directory / "clip.mp4"
    subprocess.run(  # noqa: S603 - test fixture builds the command itself
        [
            str(FFMPEG),
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=25:duration=2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _verifier() -> FfmpegDecodeVerifier:
    return FfmpegDecodeVerifier(ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, timeout_seconds=120.0)


# --------------------------------------------------------------------------- rates
def test_parse_frame_rate_keeps_an_exact_rational() -> None:
    assert parse_frame_rate("25/1") == (25, 1)
    # 29.97 must not silently become 30 or 2997/100: the exact rational is kept.
    assert parse_frame_rate("30000/1001") == (30000, 1001)
    assert parse_frame_rate("29.97") == (2997, 100)
    assert parse_frame_rate("") is None
    assert parse_frame_rate("0/1") is None
    assert parse_frame_rate("nonsense") is None


# ------------------------------------------------------------------------ decode
def test_full_decode_measures_every_frame(synthetic_clip: Path) -> None:
    verification = _verifier().verify(synthetic_clip)

    assert verification.total_frames == 50
    assert verification.decoded_frames == 50
    assert verification.decode_error_count == 0
    assert verification.completed is True
    assert verification.decode_complete is True
    assert verification.frame_rate == (25, 1)


def test_decode_timeout_is_not_a_completed_pass(
    synthetic_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decode that ran out of its budget cannot be called complete."""

    import local_drama.application.explainers.media_qc as module

    real_run = module._run

    def fake_run(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "ffprobe" in joined:
            return real_run(command, timeout_seconds=timeout_seconds)
        raise subprocess.TimeoutExpired(command, timeout_seconds)

    monkeypatch.setattr(module, "_run", fake_run)
    verification = _verifier().verify(synthetic_clip)

    assert verification.completed is False
    assert verification.decode_complete is False
    assert verification.failure_reason == "DECODE_TIMEOUT"
    assert verification.total_frames == 50


def test_decode_verification_maps_to_the_technical_layer_input(synthetic_clip: Path) -> None:
    payload = _verifier().verify(synthetic_clip).as_technical_input(
        rel_path="projects/p/x.mp4", media_version_id="mv-1"
    )

    assert payload["total_frames"] == payload["decoded_frames"] == 50
    assert payload["decode_errors"] == []
    assert payload["fps_num"] == 25 and payload["fps_den"] == 1
    # The decode is declared as a real pass, with the tools named as its source.
    assert payload["decode_verification"]["decode_pass"] == "ffmpeg -f null -"
    assert payload["decode_verification"]["frame_count_source"] == "ffprobe -count_frames"
    # The source clip is declared so the report's per-clip coverage check is real.
    assert payload["source_clips"][0]["media_version_id"] == "mv-1"
    assert payload["source_clips"][0]["total_frames"] == 50
    # A file with no audio stream has no loudness measurement, and the mapping does
    # not invent one: the technical layer then reports the band as unverified.
    assert "loudness_lufs" not in payload
    assert "peak_dbtp" not in payload


@pytest.fixture(scope="module")
def quiet_narrated_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clip whose audio is real and quiet: loudness must be measured, not assumed."""

    directory = tmp_path_factory.mktemp("media-qc-audio")
    path = directory / "quiet.mp4"
    subprocess.run(  # noqa: S603 - test fixture builds the command itself
        [
            str(FFMPEG), "-nostdin", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
            "-af", "volume=-20dB",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_the_delivered_loudness_band_is_measured_not_assumed(quiet_narrated_clip: Path) -> None:
    """The technical layer verifies the band the mixer claims to hit."""

    verification = _verifier().verify(quiet_narrated_clip)
    assert verification.audio is not None, "a real audio track must be measured"
    audio = verification.audio
    assert audio["loudness_lufs"] < -17.0, audio
    assert isinstance(audio["peak_dbtp"], float)
    assert audio["measurement_tool"].startswith("ffmpeg ebur128")
    assert audio["band"]["loudness_lufs"] == -16.0

    payload = verification.as_technical_input(rel_path="projects/p/x.mp4", media_version_id="mv-1")
    assert payload["loudness_lufs"] == audio["loudness_lufs"]
    assert payload["peak_dbtp"] == audio["peak_dbtp"]
    assert payload["audio_gaps_ms"] == audio["audio_gaps_ms"]
    assert payload["audio_measurement"]["silence_threshold_db"] == -45.0


def test_a_silent_stretch_is_reported_as_an_audio_gap(tmp_path: Path) -> None:
    """A declared 2 s hole in the audio is a measured fact, not a guess."""

    path = tmp_path / "gap.mp4"
    subprocess.run(  # noqa: S603 - test fixture builds the command itself
        [
            str(FFMPEG), "-nostdin", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000",
            "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
            "-map", "0:v:0", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(path),
        ],
        check=True,
        capture_output=True,
    )

    audio = _verifier().verify(path).audio
    assert audio is not None
    gaps = [gap for gap in audio["audio_gaps_ms"] if gap["duration_ms"] >= 1200]
    assert gaps, audio["audio_gaps_ms"]
    assert gaps[0]["start_ms"] >= 500



def test_truncated_file_fails_loudly_instead_of_passing(synthetic_clip: Path, tmp_path: Path) -> None:
    """A half-written file must never satisfy the full-decode gate.

    On this build a truncated MP4 cannot even be probed (the index is gone), so the
    reader raises instead of returning a frame count.  Either outcome is acceptable;
    silently reporting ``decoded == total`` is not.
    """

    damaged = tmp_path / "damaged.mp4"
    raw = synthetic_clip.read_bytes()
    damaged.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(DomainRuleError):
        _verifier().verify(damaged)


def test_a_decode_error_is_recorded_and_never_reported_as_complete(
    synthetic_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decoder that reports errors blocks the layer even when it exits 0.

    The stderr text is ffmpeg's own wording, so this exercises the real detection
    pattern rather than a simplified stand-in for it.
    """

    verifier = _verifier()
    import local_drama.application.explainers.media_qc as module

    real_run = module._run

    def fake_run(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "ffprobe" in joined:
            return real_run(command, timeout_seconds=timeout_seconds)
        return subprocess.CompletedProcess(
            command,
            0,
            "frame=50\nprogress=end\n",
            "Error while decoding stream #0:0: Invalid data found when processing input\n",
        )

    monkeypatch.setattr(module, "_run", fake_run)
    verification = verifier.verify(synthetic_clip)

    assert verification.completed is True
    assert verification.decode_error_count == 1
    assert verification.decode_complete is False
    payload = verification.as_technical_input(rel_path=str(synthetic_clip), media_version_id="mv")
    # The whole film is declared as the affected interval: the verifier knows the
    # count but not the frame numbers, and inventing a narrow range would be worse.
    assert payload["decode_errors"] == [[0, 50]]
    assert payload["decode_verification"]["error_examples"]


def test_a_non_media_file_fails_loudly_instead_of_passing(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-video.mp4"
    bogus.write_bytes(b"this is not a video at all" * 100)

    with pytest.raises(DomainRuleError):
        _verifier().verify(bogus)


def test_missing_file_is_reported_as_not_found(tmp_path: Path) -> None:
    with pytest.raises(ExplainerContractError):
        _verifier().verify(tmp_path / "absent.mp4")


def test_verifier_without_tools_refuses_rather_than_guessing(synthetic_clip: Path) -> None:
    verifier = FfmpegDecodeVerifier(ffmpeg_path=None, ffprobe_path=None)

    assert verifier.available is False
    with pytest.raises(DomainRuleError) as error:
        verifier.verify(synthetic_clip)
    assert error.value.code == "DECODE_TOOL_UNAVAILABLE"


# ------------------------------------------------------------------------ frames
def test_frame_sampler_extracts_exactly_the_requested_frames(synthetic_clip: Path, tmp_path: Path) -> None:
    sampler = FfmpegFrameSampler(ffmpeg_path=FFMPEG, frame_root=tmp_path / "frames")

    extracted = sampler.extract(
        source=synthetic_clip, frame_ids=[0, 10, 25, 49], frame_rate=(25, 1), namespace="edition-1"
    )

    assert sorted(extracted) == [0, 10, 25, 49]
    for relative in extracted.values():
        target = tmp_path / "frames" / relative
        assert target.is_file() and target.stat().st_size > 0


def test_frame_sampler_skips_an_out_of_range_frame(synthetic_clip: Path, tmp_path: Path) -> None:
    """A frame the source does not contain is omitted, never fabricated."""

    sampler = FfmpegFrameSampler(ffmpeg_path=FFMPEG, frame_root=tmp_path / "frames")

    extracted = sampler.extract(
        source=synthetic_clip, frame_ids=[0, 10_000], frame_rate=(25, 1), namespace="edition-2"
    )

    assert sorted(extracted) == [0]


def test_frame_sampler_reuses_an_already_extracted_frame(synthetic_clip: Path, tmp_path: Path) -> None:
    sampler = FfmpegFrameSampler(ffmpeg_path=FFMPEG, frame_root=tmp_path / "frames")
    first = sampler.extract(source=synthetic_clip, frame_ids=[5], frame_rate=(25, 1), namespace="edition-3")
    stamp = (tmp_path / "frames" / first[5]).stat().st_mtime_ns

    second = sampler.extract(source=synthetic_clip, frame_ids=[5], frame_rate=(25, 1), namespace="edition-3")

    assert second[5] == first[5]
    assert (tmp_path / "frames" / second[5]).stat().st_mtime_ns == stamp


def test_frame_sampler_rejects_a_non_positive_frame_rate(synthetic_clip: Path, tmp_path: Path) -> None:
    sampler = FfmpegFrameSampler(ffmpeg_path=FFMPEG, frame_root=tmp_path / "frames")

    with pytest.raises(ExplainerContractError):
        sampler.extract(source=synthetic_clip, frame_ids=[1], frame_rate=(0, 1), namespace="x")


def test_frame_namespace_is_sanitised_against_path_traversal(synthetic_clip: Path, tmp_path: Path) -> None:
    sampler = FfmpegFrameSampler(ffmpeg_path=FFMPEG, frame_root=tmp_path / "frames")

    extracted = sampler.extract(
        source=synthetic_clip, frame_ids=[1], frame_rate=(25, 1), namespace="../../escape"
    )

    relative = next(iter(extracted.values()))
    resolved = (tmp_path / "frames" / relative).resolve()
    assert resolved.is_relative_to((tmp_path / "frames").resolve())


# ------------------------------------------------------------------- beat ranges
def test_beat_ranges_merge_multiple_items_of_one_beat() -> None:
    ranges = beat_frame_ranges_from_items(
        [
            {"beat_id": "b1", "start_frame": 100, "end_frame_exclusive": 200},
            {"beat_id": "b1", "start_frame": 0, "end_frame_exclusive": 100},
            {"beat_id": "b2", "start_frame": 200, "end_frame_exclusive": 300},
            {"beat_id": "", "start_frame": 300, "end_frame_exclusive": 400},
            {"beat_id": "b3", "start_frame": 400, "end_frame_exclusive": 400},
        ]
    )

    assert ranges == {"b1": [0, 200], "b2": [200, 300]}


# ------------------------------------------------------- readers against the db
def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )


def _readers(tmp_path: Path) -> dict[str, object]:
    settings = _settings(tmp_path)
    settings.ensure_roots()
    return build_media_qc_readers(
        ffmpeg_path=FFMPEG,
        ffprobe_path=FFPROBE,
        frame_root=settings.explainer_frames_root,
        content_path=lambda media_version_id: tmp_path / f"{media_version_id}.mp4",
    )


class _StubRepo:
    """The smallest repository surface the media readers actually touch."""

    def __init__(self, *, media_version_id: str) -> None:
        self.media_version_id = media_version_id

    def require_same_project_media(self, *, project_id: str, media_version_id: str) -> dict[str, str]:
        del project_id
        return {"media_version_id": media_version_id}

    def get(self, table: str, row_id: str) -> dict[str, str]:
        del table, row_id
        return {}

    def find(self, table: str, row_id: str) -> dict[str, str] | None:
        del table, row_id
        return None

    def current_root_render(self, edition_id: str) -> None:
        del edition_id
        return None

    def list_where(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
        del args, kwargs
        return []


def test_technical_reader_decodes_the_declared_media_version(synthetic_clip: Path, tmp_path: Path) -> None:
    media_version_id = "mv-synthetic"
    target = tmp_path / f"{media_version_id}.mp4"
    shutil.copyfile(synthetic_clip, target)
    readers = _readers(tmp_path)

    payload = readers["technical_reader"](  # type: ignore[operator]
        _StubRepo(media_version_id=media_version_id),
        {
            "project_id": "p1",
            "video_id": "v1",
            "media_version_id": media_version_id,
            "subject_kind": "COMPOSITION_RENDER",
            "subject_revision_id": "r1",
        },
    )

    assert payload["total_frames"] == 50
    assert payload["decoded_frames"] == 50
    assert payload["source_clips"][0]["media_version_id"] == media_version_id


def test_technical_reader_reports_not_run_when_no_media_exists(tmp_path: Path) -> None:
    readers = _readers(tmp_path)

    with pytest.raises(ExplainerContractError) as error:
        readers["technical_reader"](  # type: ignore[operator]
            _StubRepo(media_version_id="mv-1"),
            {"project_id": "p1", "video_id": "v1", "subject_kind": "EDITION", "subject_revision_id": "e1"},
        )
    assert error.value.code == "NOT_RUN"


def test_sampling_reader_refuses_to_invent_a_plan_without_beat_ranges(tmp_path: Path) -> None:
    readers = _readers(tmp_path)

    with pytest.raises(ExplainerContractError) as error:
        readers["sampling_reader"](  # type: ignore[operator]
            _StubRepo(media_version_id="mv-1"),
            {
                "project_id": "p1",
                "video_id": "v1",
                "edition_id": "e1",
                "subject_kind": "EDITION",
                "subject_revision_id": "e1",
            },
        )
    assert error.value.code == "NOT_RUN"


def test_sampling_plan_matches_the_hash_run_semantic_check_recomputes(
    synthetic_clip: Path, tmp_path: Path
) -> None:
    """The reader's plan hash must equal the service's independent recomputation.

    ``run_semantic_check`` recomputes the hash from the plan fields and rejects the
    plan when they differ, so a reader whose hash used different fields would fail
    every real run even though both sides "work" in isolation.
    """

    media_version_id = "mv-plan"
    shutil.copyfile(synthetic_clip, tmp_path / f"{media_version_id}.mp4")
    readers = _readers(tmp_path)

    payload = readers["sampling_reader"](  # type: ignore[operator]
        _StubRepo(media_version_id=media_version_id),
        {
            "project_id": "p1",
            "video_id": "v1",
            "edition_id": "e1",
            "media_version_id": media_version_id,
            "subject_kind": "EDITION",
            "subject_revision_id": "e1",
            "beat_frame_ranges": {"b1": [0, 25], "b2": [25, 50]},
            "total_frames": 50,
            "fps_num": 25,
            "fps_den": 1,
            "questions": ["画面是否与旁白一致？"],
        },
    )
    plan = payload["plan"]

    expected = content_hash(
        {
            "video_id": plan["video_id"],
            "edition_id": plan["edition_id"],
            "density": plan["density"],
            "sampled_frame_ids": list(plan["sampled_frame_ids"]),
            "total_frames": plan["total_frames"],
        }
    )
    assert plan["plan_hash"] == expected
    assert plan["total_frames"] == 50
    # Every sampled frame was really extracted, so no sampled frame is unchecked.
    assert payload["frames_not_extracted"] == []
    assert sorted(item["frame_id"] for item in payload["reference_frames"]) == plan["sampled_frame_ids"]
    for frame in payload["reference_frames"]:
        assert (tmp_path / "work" / "explainer_frames" / frame["rel_path"]).is_file()


def test_sampling_reader_rejects_an_unknown_density(synthetic_clip: Path, tmp_path: Path) -> None:
    shutil.copyfile(synthetic_clip, tmp_path / "mv-d.mp4")
    readers = _readers(tmp_path)

    with pytest.raises(ExplainerContractError):
        readers["sampling_reader"](  # type: ignore[operator]
            _StubRepo(media_version_id="mv-d"),
            {
                "project_id": "p1",
                "video_id": "v1",
                "edition_id": "e1",
                "media_version_id": "mv-d",
                "beat_frame_ranges": {"b1": [0, 10]},
                "total_frames": 50,
                "density": "EXTREME",
            },
        )
