from __future__ import annotations

import pytest

from local_drama.application.video_upscale.delivery_effects import resolve_delivery_effect_application
from local_drama.domain.errors import DomainRuleError


def _snapshot(*, subtitle: bool = False, watermark_id: str | None = None) -> dict[str, object]:
    watermark = {"id": watermark_id, "code": "corner", "version_no": 1, "config": {"text": "LOCAL"}} if watermark_id else None
    return {
        "schema_version": "localdrama.episode-super-resolution-input.v1",
        "applied_effects": {
            "subtitle_burned": subtitle,
            "watermark_profile_snapshot": watermark,
            "evidence_source": "APPROVED_DELIVERY_PACKAGE",
        },
    }


def test_identical_burned_effects_are_reused_instead_of_applied_twice() -> None:
    result = resolve_delivery_effect_application(
        input_snapshot=_snapshot(subtitle=True, watermark_id="watermark-1"),
        target_spec={"subtitles": "BOTH"},
        watermark_snapshot={"id": "watermark-1", "code": "corner", "version_no": 1},
    )

    assert result["subtitle"]["action"] == "REUSE"
    assert result["watermark"]["action"] == "REUSE"


@pytest.mark.parametrize(
    ("target_spec", "watermark", "code"),
    [
        ({"subtitles": "NONE"}, {"id": "watermark-1"}, "DELIVERY_BURNED_SUBTITLE_CONFLICT"),
        ({"subtitles": "BURN_IN"}, None, "DELIVERY_BURNED_WATERMARK_CONFLICT"),
        ({"subtitles": "BURN_IN"}, {"id": "watermark-2"}, "DELIVERY_BURNED_WATERMARK_CONFLICT"),
    ],
)
def test_burned_effect_removal_or_replacement_requires_clean_compose_source(
    target_spec: dict[str, str],
    watermark: dict[str, str] | None,
    code: str,
) -> None:
    with pytest.raises(DomainRuleError) as blocked:
        resolve_delivery_effect_application(
            input_snapshot=_snapshot(subtitle=True, watermark_id="watermark-1"),
            target_spec=target_spec,
            watermark_snapshot=watermark,
        )

    assert blocked.value.code == code
    assert "COMPOSE" in blocked.value.message


def test_clean_source_applies_requested_watermark_once() -> None:
    result = resolve_delivery_effect_application(
        input_snapshot=_snapshot(),
        target_spec={"subtitles": "NONE"},
        watermark_snapshot={"id": "watermark-1", "code": "corner", "version_no": 1},
    )

    assert result["subtitle"]["action"] == "NONE"
    assert result["watermark"]["action"] == "APPLY"
