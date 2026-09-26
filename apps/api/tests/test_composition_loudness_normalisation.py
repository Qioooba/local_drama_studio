"""The delivered loudness band: two-pass ``loudnorm``, never a hopeful single pass.

The 1962 film was delivered at -18.0 LUFS against a declared -16.0 +/-1 LUFS target
because ``loudnorm`` in single-pass mode is a *dynamic* normaliser.  These tests pin
the correction path: the measurement is parsed from FFmpeg's own output, the fix is a
linear pass built from that measurement, and an unmeasurable file is reported rather
than "fixed" with an invented number.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.composition.ffmpeg_renderer import (
    DEFAULT_LOUDNESS_TARGET_LUFS,
    FfmpegRunner,
    build_loudness_measure_command,
    build_loudness_normalise_command,
    parse_loudness_measurement,
)

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="需要本机 ffmpeg")

MEASURED_JSON = {
    "input_i": "-18.02",
    "input_tp": "-1.90",
    "input_lra": "7.00",
    "input_thresh": "-28.40",
    "output_i": "-15.99",
    "output_tp": "-1.99",
    "output_lra": "6.90",
    "output_thresh": "-26.40",
    "normalization_type": "dynamic",
    "target_offset": "0.02",
}


def _measurement_stderr(payload: dict[str, str] | None = None) -> str:
    body = json.dumps(payload if payload is not None else MEASURED_JSON, indent=2)
    return f"size=N/A time=00:02:05.26 bitrate=N/A speed=115x\n[Parsed_loudnorm_0 @ 0x1]\n{body}\n"


class _FakeRunner:
    """A runner that answers the measurement pass and records every invocation."""

    def __init__(self, *, measurement: str | None = None, correction_returncode: int = 0) -> None:
        self.measurement = _measurement_stderr() if measurement is None else measurement
        self.correction_returncode = correction_returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, timeout_seconds=None, cwd=None):  # noqa: ANN001, ANN202
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "loudnorm" in joined and "print_format=json" in joined:
            return {"returncode": 0, "stdout": "", "stderr": self.measurement}
        return {"returncode": self.correction_returncode, "stdout": "", "stderr": ""}


def _fake_runner(**kwargs) -> _FakeRunner:  # noqa: ANN003
    return _FakeRunner(**kwargs)


# --------------------------------------------------------------------------- #
# parsing and command shape
# --------------------------------------------------------------------------- #
def test_measurement_is_parsed_from_the_end_of_stderr() -> None:
    measured = parse_loudness_measurement(_measurement_stderr())
    assert measured["measured_I"] == "-18.02"
    assert measured["measured_TP"] == "-1.90"
    assert measured["measured_LRA"] == "7.00"
    assert measured["measured_thresh"] == "-28.40"
    assert measured["offset"] == "0.02"


def test_an_unparsable_measurement_is_empty_rather_than_invented() -> None:
    assert parse_loudness_measurement("") == {}
    assert parse_loudness_measurement("[Parsed_loudnorm_0 @ 0x1] {not json") == {}
    assert parse_loudness_measurement('{"input_i": "-18"}') == {}


def test_the_measure_command_only_measures(tmp_path: Path) -> None:
    command = build_loudness_measure_command(
        input_path=tmp_path / "mix.wav", target_lufs=-16.0, true_peak_dbtp=-2.0, lra=11.0
    )
    assert command.purpose == "LOUDNESS_MEASURE"
    assert "print_format=json" in " ".join(command.to_argv())
    assert "-f" in command.to_argv() and "null" in command.to_argv()


def test_the_normalise_command_is_linear_and_carries_the_measurement(tmp_path: Path) -> None:
    command = build_loudness_normalise_command(
        input_path=tmp_path / "mix.wav",
        output_path=tmp_path / "out.wav",
        target_lufs=-16.0,
        true_peak_dbtp=-2.0,
        lra=11.0,
        measured={
            "measured_I": "-18.02",
            "measured_TP": "-1.90",
            "measured_LRA": "7.00",
            "measured_thresh": "-28.40",
            "offset": "0.02",
        },
        duration_seconds=125.32,
        sample_rate=48_000,
    )
    argv = " ".join(command.to_argv())
    assert "linear=true" in argv
    assert "measured_I=-18.02" in argv
    assert "measured_TP=-1.9" in argv
    assert "offset=0.02" in argv
    assert command.purpose == "LOUDNESS_NORMALISE"
    assert argv.count("pcm_s16le") == 1


def test_a_normalise_command_without_a_measurement_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ExplainerContractError) as error:
        build_loudness_normalise_command(
            input_path=tmp_path / "mix.wav",
            output_path=tmp_path / "out.wav",
            target_lufs=-16.0,
            true_peak_dbtp=-2.0,
            lra=11.0,
            measured={"measured_I": "-18.02"},
            duration_seconds=10.0,
            sample_rate=48_000,
        )
    assert "measured_TP" in str(error.value.details)


# --------------------------------------------------------------------------- #
# the runner's decision
# --------------------------------------------------------------------------- #
def test_an_out_of_band_mix_is_corrected_with_a_second_linear_pass(tmp_path: Path) -> None:
    fake = _fake_runner()
    runner = FfmpegRunner(runner=fake)
    result = runner.normalise_loudness(
        input_path=tmp_path / "mix.wav",
        output_path=tmp_path / "mix-normalized.wav",
        duration_seconds=125.32,
        sample_rate=48_000,
    )
    assert result["status"] == "CORRECTED"
    assert result["output_path"].endswith("mix-normalized.wav")
    assert result["before_lufs"] == pytest.approx(-18.02)
    assert len(fake.calls) == 2
    assert "linear=true" in " ".join(fake.calls[1])


def test_a_mix_already_on_target_is_not_rewritten(tmp_path: Path) -> None:
    fake = _fake_runner(measurement=_measurement_stderr({**MEASURED_JSON, "input_i": "-16.2"}))
    runner = FfmpegRunner(runner=fake)
    result = runner.normalise_loudness(
        input_path=tmp_path / "mix.wav",
        output_path=tmp_path / "mix-normalized.wav",
        duration_seconds=10.0,
        sample_rate=48_000,
    )
    assert result["status"] == "WITHIN_TOLERANCE"
    assert result["output_path"].endswith("mix.wav")
    assert len(fake.calls) == 1


def test_an_unmeasurable_mix_is_reported_not_repaired(tmp_path: Path) -> None:
    fake = _fake_runner(measurement="no loudnorm json here")
    runner = FfmpegRunner(runner=fake)
    result = runner.normalise_loudness(
        input_path=tmp_path / "mix.wav",
        output_path=tmp_path / "mix-normalized.wav",
        duration_seconds=10.0,
        sample_rate=48_000,
    )
    assert result["status"] == "UNMEASURED"
    assert result.get("output_path") is None
    assert len(fake.calls) == 1


def test_a_failed_correction_is_not_reported_as_corrected(tmp_path: Path) -> None:
    fake = _fake_runner(correction_returncode=1)
    runner = FfmpegRunner(runner=fake)
    result = runner.normalise_loudness(
        input_path=tmp_path / "mix.wav",
        output_path=tmp_path / "mix-normalized.wav",
        duration_seconds=10.0,
        sample_rate=48_000,
    )
    assert result["status"] == "FAILED"
    assert result["reason"]


# --------------------------------------------------------------------------- #
# the real thing
# --------------------------------------------------------------------------- #
@requires_ffmpeg
def test_real_two_pass_normalisation_lands_on_the_declared_band(tmp_path: Path) -> None:
    """A quiet real mix is brought onto -16 +/-1 LUFS by the shipped code path."""

    quiet = tmp_path / "quiet.wav"
    completed = subprocess.run(
        [
            str(FFMPEG), "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20:sample_rate=48000",
            "-af", "volume=-20dB",
            "-ac", "2", "-c:a", "pcm_s16le",
            str(quiet),
        ],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr

    runner = FfmpegRunner()
    before = runner.measure_loudness(quiet, target_lufs=DEFAULT_LOUDNESS_TARGET_LUFS, true_peak_dbtp=-2.0, lra=11.0)
    assert before["status"] == "OK"
    assert float(before["measured"]["measured_I"]) < -17.0, before["measured"]

    corrected = runner.normalise_loudness(
        input_path=quiet,
        output_path=tmp_path / "normalized.wav",
        true_peak_dbtp=-2.0,
        duration_seconds=20.0,
        sample_rate=48_000,
    )
    assert corrected["status"] == "CORRECTED", corrected

    after = runner.measure_loudness(
        Path(str(corrected["output_path"])),
        target_lufs=DEFAULT_LOUDNESS_TARGET_LUFS,
        true_peak_dbtp=-2.0,
        lra=11.0,
    )
    assert after["status"] == "OK"
    integrated = float(after["measured"]["measured_I"])
    true_peak = float(after["measured"]["measured_TP"])
    assert integrated == pytest.approx(DEFAULT_LOUDNESS_TARGET_LUFS, abs=1.0), after["measured"]
    # The correction must respect the true-peak ceiling it was given, not trade
    # loudness for an over-loud peak.
    assert true_peak <= -1.5, after["measured"]
