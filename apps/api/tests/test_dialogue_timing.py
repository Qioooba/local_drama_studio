import pytest

from local_drama.domain.dialogue_timing import dialogue_timing_issues
from local_drama.domain.errors import DomainRuleError
from local_drama.application.timeline import TimelineService


def _items():
    return [
        {"track_type": "VIDEO", "start_us": 0, "end_us": 2_000_000, "parameters": {"shot_id": "a"}},
        {"track_type": "VIDEO", "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"shot_id": "b"}},
        {"track_type": "DIALOGUE", "start_us": 1_500_000, "end_us": 3_500_000, "parameters": {"shot_id": "a", "dialogue_line_id": "line-2"}},
    ]


def test_multiple_lines_cannot_spill_into_the_next_shot_or_episode_tail():
    items = _items()
    assert dialogue_timing_issues(items)[0]["overrun_us"] == 1_500_000
    for params in ({"shot_id": "a", "dialogue_line_id": "line-2"}, {"dialogue_line_id": "legacy-line"}):
        items[-1]["parameters"] = params
        with pytest.raises(DomainRuleError, match="超出") as caught:
            TimelineService._assert_timeline_renderable({"status": "FROZEN", "items": items})
        assert caught.value.code == "DIALOGUE_EXCEEDS_SHOT_DURATION"
    items[-1]["end_us"] = 2_000_000
    assert dialogue_timing_issues(items) == []


def test_intentional_mix_bindings_are_not_mistaken_for_shot_dialogue():
    items = _items()
    items[-1]["parameters"] = {"audio_binding_id": "mix"}
    assert dialogue_timing_issues(items) == []
