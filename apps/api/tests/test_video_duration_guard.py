"""Pure unit coverage for timeline source-video duration guards."""

from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.video_duration import video_duration_budget


def _item(duration_us: int, *, source_start_us: int = 0, start_us: int = 0) -> dict[str, object]:
    return {
        "start_us": start_us,
        "end_us": start_us + duration_us,
        "parameters": {"source_start_us": source_start_us},
    }


def _video_probe(**stream: object) -> dict[str, object]:
    return {"streams": [{"codec_type": "video", "avg_frame_rate": "24/1", **stream}]}


def _error_code(probe: dict[str, object], item: dict[str, object]) -> str:
    with pytest.raises(DomainRuleError) as raised:
        video_duration_budget(probe, item)
    return raised.value.code


@pytest.mark.parametrize(
    ("probe", "expected_us"),
    [
        (_video_probe(duration="10.25"), 10_250_000),
        (_video_probe(duration_ts="123", time_base="1/10"), 12_300_000),
        (_video_probe(nb_frames="107"), round(107 / 24 * 1_000_000)),
    ],
)
def test_video_duration_budget_derives_duration_from_video_stream_facts(
    probe: dict[str, object], expected_us: int
) -> None:
    budget = video_duration_budget(probe, _item(expected_us))

    assert set(budget) == {"required_us", "available_us", "tolerance_us", "padding_us"}
    assert budget["required_us"] == expected_us
    assert budget["available_us"] == expected_us
    assert budget["padding_us"] == 0
    assert 0 < budget["tolerance_us"] <= 100_000


def test_video_duration_budget_rejects_107_frames_when_timeline_needs_12_632_seconds() -> None:
    probe = _video_probe(nb_frames="107")

    with pytest.raises(DomainRuleError) as raised:
        video_duration_budget(probe, _item(12_632_000))

    assert raised.value.code == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"
    assert raised.value.details["required_us"] == 12_632_000
    assert raised.value.details["available_us"] == round(107 / 24 * 1_000_000)


def test_video_duration_budget_allows_a_source_that_is_long_enough() -> None:
    budget = video_duration_budget(_video_probe(duration="13.0"), _item(12_632_000))

    assert budget["required_us"] == 12_632_000
    assert budget["available_us"] == 13_000_000
    assert budget["padding_us"] == 0


def test_video_duration_budget_rejects_a_source_offset_that_leaves_insufficient_coverage() -> None:
    code = _error_code(
        _video_probe(duration="13.0"),
        _item(12_000_000, source_start_us=1_500_000),
    )

    assert code == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"


@pytest.mark.parametrize("source_start_us", [-1, 13_000_000])
def test_video_duration_budget_rejects_negative_or_out_of_range_source_offsets(source_start_us: int) -> None:
    code = _error_code(
        _video_probe(duration="13.0"),
        _item(1_000_000, source_start_us=source_start_us),
    )

    assert code == "TIMELINE_SOURCE_RANGE_INVALID"


def test_video_duration_budget_rejects_unknown_video_duration() -> None:
    probe = {"streams": [{"codec_type": "video", "avg_frame_rate": "24/1"}]}

    assert _error_code(probe, _item(1_000_000)) == "TIMELINE_SOURCE_DURATION_UNKNOWN"


def test_video_duration_budget_does_not_use_a_longer_container_or_audio_duration() -> None:
    probe = {
        "streams": [
            {"codec_type": "video", "nb_frames": "107", "avg_frame_rate": "24/1"},
            {"codec_type": "audio", "duration": "12.632"},
        ],
        "format": {"duration": "12.632"},
    }

    with pytest.raises(DomainRuleError) as raised:
        video_duration_budget(probe, _item(12_632_000))

    assert raised.value.code == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"
    assert raised.value.details["available_us"] == round(107 / 24 * 1_000_000)


def test_video_duration_budget_allows_a_gap_within_one_source_frame() -> None:
    # At 24fps one frame is 41,666.666... microseconds.  The 41,666us
    # deficit is within the rounded one-frame tolerance.
    budget = video_duration_budget(_video_probe(duration="10.0"), _item(10_041_666))

    assert budget["padding_us"] == 41_666
    assert budget["padding_us"] <= budget["tolerance_us"] <= 100_000

