from __future__ import annotations

import pytest

from local_drama.api.schemas.video_upscale import UpscaleModelOptions
from local_drama.application.video_upscale.geometry import resolve_sdr_color_pipeline, resolve_upscale_geometry
from local_drama.domain.errors import DomainRuleError


@pytest.mark.parametrize(
    ("width", "height", "target", "scale", "content", "padding"),
    [
        (864, 480, (1920, 1080), 3, (1920, 1066), (0, 7, 0, 7)),
        (854, 480, (1920, 1080), 3, (1920, 1078), (0, 1, 0, 1)),
        (480, 854, (1080, 1920), 3, (1078, 1920), (1, 0, 1, 0)),
        (480, 832, (1080, 1920), 3, (1080, 1872), (0, 24, 0, 24)),
        (640, 480, (1920, 1080), 3, (1440, 1080), (240, 0, 240, 0)),
    ],
)
def test_follow_orientation_contain_geometry(width, height, target, scale, content, padding) -> None:
    result = resolve_upscale_geometry(source_width=width, source_height=height)

    assert (result["target"]["width"], result["target"]["height"]) == target
    assert result["native_scale"] == scale
    assert (result["content_rect"]["width"], result["content_rect"]["height"]) == content
    assert (
        result["padding"]["left"],
        result["padding"]["top"],
        result["padding"]["right"],
        result["padding"]["bottom"],
    ) == padding


def test_cover_geometry_reports_crop_without_stretching() -> None:
    result = resolve_upscale_geometry(source_width=640, source_height=480, fit="COVER")

    assert result["content_rect"] == {"x": 0, "y": -180, "width": 1920, "height": 1440}
    assert result["crop"] == {"left": 0, "right": 0, "top": 180, "bottom": 180}
    assert result["padding"] == {"left": 0, "right": 0, "top": 0, "bottom": 0}


def test_custom_cross_orientation_requires_explicit_confirmation() -> None:
    with pytest.raises(DomainRuleError) as error:
        resolve_upscale_geometry(
            source_width=854,
            source_height=480,
            target_mode="CUSTOM",
            target_width=1080,
            target_height=1920,
        )

    assert error.value.code == "UPSCALE_CROSS_ORIENTATION_CONFIRMATION_REQUIRED"


def test_native_scale_must_be_supported_and_sufficient() -> None:
    with pytest.raises(DomainRuleError) as error:
        resolve_upscale_geometry(source_width=640, source_height=360, explicit_native_scale=2)

    assert error.value.code == "UPSCALE_NATIVE_SCALE_INSUFFICIENT"


def test_tile_fallback_ladder_is_frozen_from_initial_tile() -> None:
    automatic = UpscaleModelOptions(
        model_name="realesr-animevideov3",
        native_scales=[2, 3, 4],
        tile_size=0,
    )
    constrained = UpscaleModelOptions(
        model_name="realesr-animevideov3",
        native_scales=[2, 3, 4],
        tile_size=256,
    )

    assert automatic.tile_fallback_sizes == [256, 128, 64]
    assert constrained.tile_fallback_sizes == [128, 64]

    with pytest.raises(ValueError, match="tile_fallback_sizes"):
        UpscaleModelOptions(
            model_name="realesr-animevideov3",
            native_scales=[2, 3, 4],
            tile_size=256,
            tile_fallback_sizes=[512, 128],
        )


def test_sdr_color_pipeline_preserves_evidence_and_marks_inference() -> None:
    explicit = resolve_sdr_color_pipeline(
        {
            "height": 480,
            "color_space": "smpte170m",
            "color_range": "pc",
            "color_primaries": "smpte170m",
            "color_transfer": "smpte170m",
        }
    )
    inferred = resolve_sdr_color_pipeline({"height": 480})

    assert explicit["source"]["decode_matrix"] == "bt601"
    assert explicit["source"]["range"] == "pc"
    assert explicit["inferred"] == []
    assert explicit["target"] == {
        "color_space": "bt709",
        "color_range": "tv",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
    }
    assert inferred["source"]["decode_matrix"] == "bt601"
    assert set(inferred["inferred"]) == {
        "source_matrix",
        "source_range",
        "source_primaries",
        "source_transfer",
    }
