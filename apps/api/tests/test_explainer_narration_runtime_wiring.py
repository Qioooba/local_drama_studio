"""Narration wiring fixes from the RTX 3090 Ti lightweight-parameter plan.

Reproduced defects on the audit snapshot (all silent successes):

* ``LocalAiNarrationTtsRuntime`` hardcoded ``speed_applied_natively: True``, so a
  requested speech rate the model never applied natively looked already handled
  and its declared atempo post-process was skipped;
* the aligner's batch path forwarded raw aligner dictionaries
  (``text``/``start_time``/``end_time`` seconds) into ``word_timings``, but the
  consumer reads ``token``/``start_sample``/``end_sample`` — every token was
  dropped and the take still got an ``ALIGNED`` revision with no word time;
* ``LocalAsrAdapter`` returned only ``transcription`` while the review consumer
  reads ``text``, so independent ASR compared an empty transcript to the script;
* the production align stage passed ``asr=None``, recording
  ``ASR_REVIEW_NOT_CONFIGURED`` for every take while still reporting a pass;
* ``SUBTITLE_BUILD`` recorded alignment revision ids and then timed every cue by
  an even character-proportional split of the segment anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.explainers.aligner_timestamps import (
    normalize_aligner_timestamps,
    timestamp_samples,
)
from local_drama.application.explainers.production_pipeline import (
    _aligned_cue_times,
    _split_cue_chunks,
    _subtitle_alignment_facts,
)
from local_drama.application.explainers.runtime_adapters import (
    LocalAiNarrationTtsRuntime,
    LocalAsrAdapter,
    LocalForcedAlignerAdapter,
)
from local_drama.config import Settings
from local_drama.infrastructure.local_ai_subprocess import LocalAiExecution, LocalAiSubprocessRuntime

# --------------------------------------------------------------------------- #
# aligner timestamps
# --------------------------------------------------------------------------- #


def test_aligner_seconds_are_converted_at_the_declared_rate() -> None:
    """0.160 s at the aligner's own 16 kHz is 2560 samples, never 160."""

    start, end = timestamp_samples(
        {"text": "好", "start_time": 0.160, "end_time": 0.320, "sample_rate_hz": 16000}
    )
    assert (start, end) == (2560, 5120)


def test_aligner_milliseconds_and_samples_are_not_confused() -> None:
    assert timestamp_samples({"start_ms": 160, "end_ms": 320, "sample_rate_hz": 48000}) == (7680, 15360)
    assert timestamp_samples({"start_sample": 7680, "end_sample": 15360}) == (7680, 15360)


def test_aligner_timestamps_become_the_canonical_word_timing_vocabulary() -> None:
    """The consumer reads ``token``/``start_sample``; the aligner says ``text``/seconds."""

    word_timings = normalize_aligner_timestamps(
        [
            {"text": "解", "start_time": 0.0, "end_time": 0.08, "sample_rate_hz": 16000},
            {"text": "说", "start_time": 0.08, "end_time": 0.16, "sample_rate_hz": 16000},
        ]
    )
    assert [item["token"] for item in word_timings] == ["解", "说"]
    assert [item["start_sample"] for item in word_timings] == [0, 1280]
    assert all(item["timestamp_source"] == "ALIGNER" for item in word_timings)


def test_aligner_timestamps_drop_unusable_entries_but_reject_reversed_time() -> None:
    kept = normalize_aligner_timestamps(
        [{"text": "", "start_time": 0.0, "end_time": 0.1}, {"text": "好", "start_time": None}]
    )
    assert kept == []
    with pytest.raises(ValueError):
        normalize_aligner_timestamps([{"text": "好", "start_time": 0.5, "end_time": 0.1}])


def test_single_take_aligner_adapter_uses_the_shared_conversion() -> None:
    class _Runtime:
        def align(self, audio_path: Path, transcript: str, *, language: str = "Chinese") -> LocalAiExecution:
            del audio_path, transcript, language
            return LocalAiExecution(
                command=("python",),
                payload={
                    "status": "PASS",
                    "network_used": False,
                    "model": "aligner",
                    "timestamps": [
                        {"text": "解", "start_time": 0.0, "end_time": 0.08, "sample_rate_hz": 16000}
                    ],
                },
            )

    result = LocalForcedAlignerAdapter(_Runtime()).align(  # type: ignore[arg-type]
        media_path=Path("take.wav"),
        media_sha256="a" * 64,
        spoken_text="解说",
        display_text="解说",
        locale="zh-CN",
        sample_offset=0,
    )
    assert result["alignment_status"] == "ALIGNED"
    assert result["word_timings"][0]["token"] == "解"
    assert result["word_timings"][0]["start_sample"] == 0


# --------------------------------------------------------------------------- #
# ASR transcript field
# --------------------------------------------------------------------------- #


def test_asr_adapter_exposes_the_transcript_under_the_name_the_consumer_reads() -> None:
    class _Runtime:
        def transcribe(self, audio_path: Path) -> LocalAiExecution:
            del audio_path
            return LocalAiExecution(
                command=("python",),
                payload={
                    "status": "PASS",
                    "network_used": False,
                    "model": "asr",
                    "language": "Chinese",
                    "transcription": "这是一段旁白",
                },
            )

    result = LocalAsrAdapter(_Runtime()).transcribe(  # type: ignore[arg-type]
        media_path=Path("take.wav"), locale="zh-CN", timeout_seconds=60
    )
    assert result["text"] == "这是一段旁白"
    assert result["transcription"] == "这是一段旁白"
    assert result["rewrites_script"] is False


def test_local_ai_subprocess_requests_the_raised_asr_ceiling() -> None:
    """512 replaces 128; the ceiling only matters if it is actually requested."""

    runtime = LocalAiSubprocessRuntime(Settings())
    captured: dict[str, object] = {}

    def run_task(task: str, arguments=(), *, timeout: float = 1800) -> LocalAiExecution:
        captured["task"] = task
        captured["arguments"] = tuple(arguments)
        return LocalAiExecution(command=("python",), payload={"status": "PASS", "network_used": False})

    runtime.run_task = run_task  # type: ignore[method-assign]
    runtime.transcribe(Path("take.wav"))
    assert captured["task"] == "asr"
    assert "--asr-max-new-tokens" in captured["arguments"]  # type: ignore[operator]
    assert "512" in captured["arguments"]  # type: ignore[operator]


# --------------------------------------------------------------------------- #
# TTS speed reporting and the narration parameter settings
# --------------------------------------------------------------------------- #


def test_narration_tts_runtime_reports_the_speed_flag_instead_of_assuming_it() -> None:
    class _Runtime:
        def synthesize(self, text, output_path, **kwargs):  # noqa: ANN001, ANN003
            del text, output_path, kwargs
            return LocalAiExecution(
                command=("python",),
                payload={
                    "status": "PASS",
                    "network_used": False,
                    "elapsed_seconds": 1.0,
                    "speed_applied_natively": False,
                    "parameters": {"max_len": 256},
                },
            )

    class _Media:
        def content_path(self, media_version_id: str) -> tuple[dict[str, object], Path]:
            del media_version_id
            return {"sha256": "b" * 64}, Path("reference.wav")

    port = LocalAiNarrationTtsRuntime(_Runtime(), media=_Media())  # type: ignore[arg-type]
    result = port.synthesize(
        text="旁白",
        voice_ref="voxcpm2:media:ref-1|参考文本",
        model_ref="VoxCPM2",
        output=Path("out.wav"),
        speech_rate=1.5,
        timeout_seconds=60,
    )
    # The runtime said it did not apply the rate natively; the caller must be able
    # to see that so it does not silently skip its atempo post-process.
    assert result["speed_applied_natively"] is False


def test_narration_parameters_are_forwarded_to_both_tts_tasks() -> None:
    settings = Settings(explainer_tts_inference_timesteps=10, explainer_tts_max_len=256)
    runtime = LocalAiSubprocessRuntime(settings)
    captured: list[tuple[str, tuple[str, ...]]] = []

    def run_task(task: str, arguments=(), *, timeout: float = 1800) -> LocalAiExecution:
        del timeout
        captured.append((task, tuple(arguments)))
        return LocalAiExecution(
            command=("python",),
            payload={"status": "PASS", "network_used": False, "items": [], "parameters": {}},
        )

    runtime.run_task = run_task  # type: ignore[method-assign]
    runtime.synthesize("旁白", Path("out.wav"))
    runtime.synthesize_batch(
        [{"id": "s1", "text": "旁白"}],
        manifest_path=Path("manifest.json"),
        output_dir=Path("outputs"),
    )
    assert [task for task, _arguments in captured] == ["voxcpm2", "voxcpm2-batch"]
    for _task, arguments in captured:
        assert arguments[arguments.index("--inference-timesteps") + 1] == "10"
        assert arguments[arguments.index("--max-len") + 1] == "256"


def test_narration_tts_defaults_stay_at_the_declared_baseline() -> None:
    settings = Settings()
    assert settings.explainer_tts_inference_timesteps == 4
    assert settings.explainer_tts_max_len == 256


def test_tts_settings_come_from_the_machine_config_runtime_block(tmp_path: Path) -> None:
    from local_drama.bootstrap.config_loader import load_machine_config, settings_values

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "instance_id": "default",
                "runtime": {"explainer_tts_inference_timesteps": 10, "explainer_tts_max_len": 384},
            }
        ),
        encoding="utf-8",
    )
    config = load_machine_config(config_path, release_root=tmp_path, instance_root=tmp_path)
    assert config is not None
    values = settings_values(config)
    assert values["explainer_tts_inference_timesteps"] == 10
    assert values["explainer_tts_max_len"] == 384


# --------------------------------------------------------------------------- #
# subtitle cue timing from the alignment clock
# --------------------------------------------------------------------------- #


def _alignment(display_map: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "alignment-1",
        "alignment_status": "ALIGNED",
        "word_timings_json": json.dumps([{"token": "第", "start_sample": 0, "end_sample": 1280}]),
        "display_map_json": json.dumps(display_map),
    }


def test_cue_chunks_carry_display_offsets() -> None:
    chunks = _split_cue_chunks("第一句话。第二句话。", max_chars=100)
    assert [chunk[0] for chunk in chunks] == ["第一句话。", "第二句话。"]
    assert [(chunk[1], chunk[2]) for chunk in chunks] == [(0, 5), (5, 10)]


def test_cue_times_prefer_the_alignment_clock_over_character_proportion() -> None:
    """A cue whose span the aligner placed must use the aligner's time."""

    chunks = _split_cue_chunks("第一句话。第二句话。", max_chars=100)
    alignment = _alignment(
        [
            # 0-2 s for the first sentence's characters, deliberately *not* the
            # even 2.5 s an equal character split would produce.
            {"token": "第", "display_start": 0, "display_end": 1, "start_sample": 0, "end_sample": 16000},
            {"token": "句", "display_start": 3, "display_end": 4, "start_sample": 16000, "end_sample": 32000},
        ]
    )
    times = _aligned_cue_times(
        chunks, alignment=alignment, sample_rate_hz=16000, clip_start_ms=0, clip_end_ms=5000
    )
    assert times[0] == (0, 2000)
    # The second sentence has no mapped span, so it is filled to the clip end.
    assert times[1] == (2000, 5000)


def test_cue_times_fall_back_to_proportional_without_an_alignment() -> None:
    chunks = _split_cue_chunks("第一句话。第二句话。", max_chars=100)
    times = _aligned_cue_times(
        chunks, alignment=None, sample_rate_hz=48000, clip_start_ms=0, clip_end_ms=5000
    )
    assert times == [(0, 2500), (2500, 5000)]


def test_cue_times_are_monotonic_and_cover_the_clip_exactly() -> None:
    chunks = _split_cue_chunks("甲乙丙。丁戊己。庚辛壬。", max_chars=100)
    alignment = _alignment(
        [
            # The third sentence's span starts *before* the second sentence ends:
            # the clock must still come out ordered and inside the clip.
            {"token": "庚", "display_start": 8, "display_end": 9, "start_sample": 8000, "end_sample": 16000},
        ]
    )
    times = _aligned_cue_times(
        chunks, alignment=alignment, sample_rate_hz=16000, clip_start_ms=0, clip_end_ms=1000
    )
    assert times[0][0] == 0
    assert times[-1][1] == 1000
    for previous, current in zip(times, times[1:], strict=False):
        assert current[0] >= previous[0]
        assert current[0] >= previous[1] - 1
        assert current[1] > current[0]


def test_subtitle_alignment_facts_state_how_much_time_came_from_the_aligner() -> None:
    facts = _subtitle_alignment_facts(
        _alignment(
            [{"token": "第", "display_start": 0, "display_end": 1, "start_sample": 0, "end_sample": 16000}]
        )
    )
    assert facts["word_timing_count"] == 1
    assert facts["mapped_span_count"] == 1
    # The 80 ms aligner grid is reported so a revision can never claim
    # sample-accurate caption timing.
    assert facts["aligner_timestamp_grid_ms"] == 80
    assert _subtitle_alignment_facts(None)["mapped_span_count"] == 0
