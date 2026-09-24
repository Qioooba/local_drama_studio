"""One conversion from aligner output to the pipeline's canonical word timings.

The Qwen3 ForcedAligner reports a chunk as ``text`` with ``start_time``/``end_time``
in **seconds at the aligner's own input rate** (16 kHz for the locked
``Qwen3-ForcedAligner-0.6B-hf``, declared by its processor config, with an 80 ms
time grid — so a token boundary is quantised to that grid and must never be
documented as sample-accurate).

Every consumer of a word timing in this codebase reads a different vocabulary:
``_match_chunks_to_tokens`` and ``_build_display_mapping`` in
``worker_handlers/narration_align.py`` look for ``token`` carrying
``start_sample``/``end_sample`` in the **take's** 48 kHz time base.  A raw aligner
dictionary therefore contains none of the fields the consumer reads, and feeding
one in produced an alignment revision with an empty ``word_timings`` list that the
stage still reported as successful.

Two call paths exist and both must agree:

* the single-take ``alignment`` task, converted by
  :class:`~local_drama.application.explainers.runtime_adapters.LocalForcedAlignerAdapter`;
* the resident-model ``alignment-batch`` task used by the pipeline align stage.

The conversion lives here so the two cannot drift.  It is deliberately free of any
runtime or database dependency so it can be unit-tested without a GPU.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Rate used only when a timestamp declares neither an explicit sample pair nor
#: its own rate.  The runtime reports the aligner's real rate, so this is a
#: last-resort default rather than the normal path.
DEFAULT_ALIGNER_SAMPLE_RATE_HZ = 16000

#: The aligner's time grid, from its processor config.  Recorded because it is the
#: reason an alignment revision can never claim sample-accurate subtitles.
ALIGNER_TIMESTAMP_GRID_MS = 80

__all__ = [
    "ALIGNER_TIMESTAMP_GRID_MS",
    "DEFAULT_ALIGNER_SAMPLE_RATE_HZ",
    "normalize_aligner_timestamps",
    "timestamp_samples",
]


def _as_int_pair(start: Any, end: Any) -> tuple[int | None, int | None]:
    try:
        return int(start), int(end)
    except (TypeError, ValueError):
        return None, None


def _scaled_pair(start: Any, end: Any, *, scale: float) -> tuple[int | None, int | None]:
    try:
        return int(round(float(start) * scale)), int(round(float(end) * scale))
    except (TypeError, ValueError):
        return None, None


def timestamp_samples(item: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """A timestamp's ``(start_sample, end_sample)`` in the aligner's own time base.

    Accepts an explicit sample pair, a millisecond pair, or a seconds pair.  The
    unit is never inferred from magnitude: 160 ms is 160 ms, and only the
    declared rate turns it into 2560 samples at 16 kHz.  Confusing the two is how
    a subtitle clock silently ends up offset by the microphone's sample rate.
    """

    if item.get("start_sample") is not None and item.get("end_sample") is not None:
        return _as_int_pair(item.get("start_sample"), item.get("end_sample"))
    try:
        rate = int(item.get("sample_rate_hz") or DEFAULT_ALIGNER_SAMPLE_RATE_HZ)
    except (TypeError, ValueError):
        rate = DEFAULT_ALIGNER_SAMPLE_RATE_HZ
    if rate <= 0:
        rate = DEFAULT_ALIGNER_SAMPLE_RATE_HZ
    if item.get("start_ms") is not None and item.get("end_ms") is not None:
        return _scaled_pair(item.get("start_ms"), item.get("end_ms"), scale=rate / 1000.0)
    if item.get("start_time") is not None and item.get("end_time") is not None:
        return _scaled_pair(item.get("start_time"), item.get("end_time"), scale=float(rate))
    return None, None


def normalize_aligner_timestamps(timestamps: Any) -> list[dict[str, Any]]:
    """Aligner output as canonical word timings.

    A timestamp the aligner did not return is simply absent from the result; the
    handler then reports that token as unaligned instead of receiving a fabricated
    value.  A malformed entry is skipped rather than crashing the whole take, but a
    timestamp that ends before it starts raises: that is a real defect and must not
    become a silently reordered cue.
    """

    if timestamps is None:
        return []
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes)):
        raise ValueError("aligner timestamps must be a sequence")
    word_timings: list[dict[str, Any]] = []
    for item in timestamps:
        if not isinstance(item, Mapping):
            continue
        token = str(item.get("token") or item.get("text") or item.get("word") or "").strip()
        start_sample, end_sample = timestamp_samples(item)
        if not token or start_sample is None or end_sample is None:
            continue
        if end_sample < start_sample:
            raise ValueError(
                f"aligner timestamp ends before it starts: token={token!r} "
                f"start_sample={start_sample} end_sample={end_sample}"
            )
        word_timings.append(
            {
                "token": token,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "text_match": True,
                "timestamp_source": "ALIGNER",
            }
        )
    return word_timings
