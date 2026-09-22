"""NP11 regressions: strict shot-duration parsing keeps sign and unit semantics."""

from __future__ import annotations

import math

import pytest

from local_drama.application.breakdown_contracts import (
    strict_duration_seconds,
    validated_breakdown_shot_duration,
)
from local_drama.domain.errors import DomainRuleError

ERROR_CODE = "TEST_DURATION_INVALID"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (15, 15.0),
        (1.0, 1.0),
        (15.0, 15.0),
        (2.5, 2.5),
        ("5", 5.0),
        ("5秒", 5.0),
        ("5s", 5.0),
        ("5 s", 5.0),
        ("1", 1.0),
        ("15", 15.0),
        ("1e1", 10.0),
        ("1E1", 10.0),
        ("+2.5s", 2.5),
        (" 3 秒 ", 3.0),
        (".5", 0.5),
    ],
)
def test_strict_duration_parses_explicit_values(value: object, expected: float) -> None:
    assert strict_duration_seconds(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        -5,
        -1.2,
        "-5",
        "-1.2s",
        "-1.2秒",
        0,
        0.0,
        "0",
        0.999,
        15.001,
        16,
        3600,
        "abc10xyz",
        "1.2ms",
        "10ms",
        "1m",
        "s",
        "",
        "   ",
        "1..2",
        "1,5",
        "１５",
        "NaN",
        "nan",
        "Infinity",
        "-Infinity",
        "inf",
        True,
        False,
        None,
        [5],
        {"seconds": 5},
    ],
)
def test_strict_duration_rejects_garbage_negative_and_out_of_range(value: object) -> None:
    parsed = strict_duration_seconds(value)
    if parsed is not None:
        # A parsed value is never laundered: it stays finite and keeps its sign.
        assert math.isfinite(parsed)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert parsed == float(value)
    with pytest.raises(DomainRuleError) as caught:
        validated_breakdown_shot_duration(value, error_code=ERROR_CODE)
    assert caught.value.code == ERROR_CODE


def test_negative_numbers_are_never_coerced_to_their_absolute_value() -> None:
    assert strict_duration_seconds(-5) == -5.0
    assert strict_duration_seconds(-1.2) == -1.2
    with pytest.raises(DomainRuleError):
        validated_breakdown_shot_duration(-5, error_code=ERROR_CODE)


def test_negative_string_duration_never_becomes_a_positive_number() -> None:
    """The original defect: '-5' was laundered into +5.0."""
    assert strict_duration_seconds("-5") == -5.0
    with pytest.raises(DomainRuleError):
        validated_breakdown_shot_duration("-5", error_code=ERROR_CODE)
    with pytest.raises(DomainRuleError):
        validated_breakdown_shot_duration("-1.2s", error_code=ERROR_CODE)


def test_garbage_string_duration_is_not_stripped_into_a_number() -> None:
    assert strict_duration_seconds("abc10xyz") is None
    assert strict_duration_seconds("1.2ms") is None
    with pytest.raises(DomainRuleError) as caught:
        validated_breakdown_shot_duration("abc10xyz", error_code=ERROR_CODE)
    assert caught.value.details["duration_seconds"] == "abc10xyz"


def test_scientific_notation_is_a_real_number_not_a_laundered_digit_run() -> None:
    # pyproject previously produced 11.0 by stripping the "e"; the strict
    # grammar reads it as 10.0 (or would reject it outright).
    assert strict_duration_seconds("1e1") == 10.0
    assert validated_breakdown_shot_duration("1e1", error_code=ERROR_CODE) == 10.0


def test_duration_boundaries_are_inclusive() -> None:
    assert validated_breakdown_shot_duration(1, error_code=ERROR_CODE) == 1.0
    assert validated_breakdown_shot_duration("15秒", error_code=ERROR_CODE) == 15.0
    with pytest.raises(DomainRuleError):
        validated_breakdown_shot_duration("0.999", error_code=ERROR_CODE)
    with pytest.raises(DomainRuleError):
        validated_breakdown_shot_duration("15.001", error_code=ERROR_CODE)
