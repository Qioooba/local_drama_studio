import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.timeline_formatting import (
    canonical_track_type,
    normalized_text_with_offsets,
    snapshot_hash,
    subtitle_time,
)


def test_timeline_formatting_is_canonical_and_backward_compatible() -> None:
    assert canonical_track_type("MUSIC") == "BGM"
    assert snapshot_hash({"b": 1, "a": 2}) == snapshot_hash({"a": 2, "b": 1})
    assert subtitle_time(3_723_004_000) == "01:02:03,004"
    assert subtitle_time(3_723_004_000, decimal_separator=".") == "01:02:03.004"


def test_normalized_text_offsets_point_to_authoritative_text() -> None:
    source = "  第一幕\n\t第二幕  "
    normalized, offsets = normalized_text_with_offsets(source)
    assert normalized == "第一幕 第二幕"
    assert "".join(source[index] for index in offsets if not source[index].isspace()) == "第一幕第二幕"


def test_subtitle_time_rejects_negative_values() -> None:
    with pytest.raises(DomainRuleError) as error:
        subtitle_time(-1)
    assert error.value.code == "TIMELINE_TIME_INVALID"
