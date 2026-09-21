"""Deterministic output geometry for video super-resolution.

The AI engine only receives one of its verified native integer scales.  A
separate deterministic resize/pad/crop stage converges that output to the
exact delivery dimensions.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Literal, NotRequired, TypedDict

from local_drama.domain.errors import DomainRuleError

TargetMode = Literal["FOLLOW_ORIENTATION_1080", "CUSTOM"]
FitMode = Literal["CONTAIN", "COVER"]


class Rect(TypedDict):
    x: int
    y: int
    width: int
    height: int


class Insets(TypedDict):
    left: int
    top: int
    right: int
    bottom: int


class UpscaleGeometry(TypedDict):
    source: dict[str, int | str]
    target: dict[str, int | str]
    required_scale: float
    native_scale: int
    ai_output: dict[str, int]
    content_rect: Rect
    padding: Insets
    crop: Insets
    color_pipeline: NotRequired[dict[str, Any]]


def resolve_sdr_color_pipeline(stream: dict[str, Any]) -> dict[str, Any]:
    """Freeze an explicit SDR decode/encode policy for the PNG round trip."""

    height = int(stream.get("height") or 0)
    reported_space = str(stream.get("color_space") or "").lower()
    reported_range = str(stream.get("color_range") or "").lower()
    reported_primaries = str(stream.get("color_primaries") or "").lower()
    reported_transfer = str(stream.get("color_transfer") or "").lower()
    inferred: list[str] = []
    if reported_space in {"bt709"}:
        decode_matrix = "bt709"
    elif reported_space in {"smpte170m", "bt470bg", "bt601"}:
        decode_matrix = "bt601"
    else:
        decode_matrix = "bt601" if 0 < height <= 576 else "bt709"
        inferred.append("source_matrix")
    if reported_range in {"pc", "jpeg"}:
        source_range = "pc"
    elif reported_range in {"tv", "mpeg"}:
        source_range = "tv"
    else:
        source_range = "tv"
        inferred.append("source_range")
    if not reported_primaries:
        inferred.append("source_primaries")
    if not reported_transfer:
        inferred.append("source_transfer")
    return {
        "source": {
            "reported_space": reported_space or None,
            "reported_range": reported_range or None,
            "reported_primaries": reported_primaries or None,
            "reported_transfer": reported_transfer or None,
            "decode_matrix": decode_matrix,
            "range": source_range,
        },
        "target": {
            "color_space": "bt709",
            "color_range": "tv",
            "color_primaries": "bt709",
            "color_transfer": "bt709",
        },
        "inferred": inferred,
        "conversion": "DECODE_TO_RGB_THEN_ENCODE_BT709_LIMITED",
    }


def _orientation(width: int, height: int) -> Literal["LANDSCAPE", "PORTRAIT", "SQUARE"]:
    if width > height:
        return "LANDSCAPE"
    if height > width:
        return "PORTRAIT"
    return "SQUARE"


def _even_floor(value: Fraction) -> int:
    integer = value.numerator // value.denominator
    return max(2, integer - integer % 2)


def _even_ceil(value: Fraction) -> int:
    integer = -(-value.numerator // value.denominator)
    return integer if integer % 2 == 0 else integer + 1


def _resolve_target(
    source_width: int,
    source_height: int,
    mode: TargetMode,
    custom_width: int | None,
    custom_height: int | None,
) -> tuple[int, int]:
    if mode == "FOLLOW_ORIENTATION_1080":
        orientation = _orientation(source_width, source_height)
        if orientation == "LANDSCAPE":
            return 1920, 1080
        if orientation == "PORTRAIT":
            return 1080, 1920
        return 1080, 1080
    if mode != "CUSTOM":
        raise DomainRuleError("UPSCALE_TARGET_MODE_INVALID", "不支持的超分目标模式", {"mode": mode})
    if custom_width is None or custom_height is None:
        raise DomainRuleError("UPSCALE_TARGET_SIZE_REQUIRED", "自定义目标必须同时填写宽和高")
    if not (64 <= custom_width <= 4096 and 64 <= custom_height <= 4096):
        raise DomainRuleError(
            "UPSCALE_TARGET_SIZE_INVALID",
            "目标宽高必须在 64 到 4096 像素之间",
            {"width": custom_width, "height": custom_height},
        )
    if custom_width % 2 or custom_height % 2:
        raise DomainRuleError(
            "UPSCALE_TARGET_SIZE_INVALID",
            "目标宽高必须是偶数",
            {"width": custom_width, "height": custom_height},
        )
    return custom_width, custom_height


def resolve_upscale_geometry(
    *,
    source_width: int,
    source_height: int,
    target_mode: TargetMode = "FOLLOW_ORIENTATION_1080",
    target_width: int | None = None,
    target_height: int | None = None,
    fit: FitMode = "CONTAIN",
    allow_cross_orientation: bool = False,
    native_scales: tuple[int, ...] = (2, 3, 4),
    explicit_native_scale: int | None = None,
) -> UpscaleGeometry:
    """Resolve exact delivery geometry and one supported native AI scale."""

    if source_width <= 0 or source_height <= 0:
        raise DomainRuleError(
            "UPSCALE_SOURCE_GEOMETRY_INVALID",
            "源视频宽高无效",
            {"width": source_width, "height": source_height},
        )
    output_width, output_height = _resolve_target(
        source_width, source_height, target_mode, target_width, target_height
    )
    source_orientation = _orientation(source_width, source_height)
    target_orientation = _orientation(output_width, output_height)
    if (
        source_orientation != "SQUARE"
        and target_orientation != "SQUARE"
        and source_orientation != target_orientation
        and not allow_cross_orientation
    ):
        raise DomainRuleError(
            "UPSCALE_CROSS_ORIENTATION_CONFIRMATION_REQUIRED",
            "目标方向与源视频不同，需要显式确认",
            {"source_orientation": source_orientation, "target_orientation": target_orientation},
        )
    if fit not in {"CONTAIN", "COVER"}:
        raise DomainRuleError("UPSCALE_FIT_INVALID", "不支持的画面适配模式", {"fit": fit})

    width_ratio = Fraction(output_width, source_width)
    height_ratio = Fraction(output_height, source_height)
    required = min(width_ratio, height_ratio) if fit == "CONTAIN" else max(width_ratio, height_ratio)

    scales = tuple(sorted(set(native_scales)))
    if not scales or any(scale <= 0 for scale in scales):
        raise DomainRuleError("UPSCALE_NATIVE_SCALES_INVALID", "模型没有可用的原生倍率")
    if explicit_native_scale is not None:
        if explicit_native_scale not in scales:
            raise DomainRuleError(
                "UPSCALE_NATIVE_SCALE_UNSUPPORTED",
                "所选原生倍率不受当前模型支持",
                {"native_scale": explicit_native_scale, "supported": list(scales)},
            )
        native_scale = explicit_native_scale
        if Fraction(native_scale, 1) < required:
            raise DomainRuleError(
                "UPSCALE_NATIVE_SCALE_INSUFFICIENT",
                "所选原生倍率不足以生成目标尺寸",
                {"native_scale": native_scale, "required_scale": float(required)},
            )
    else:
        native_scale = next((scale for scale in scales if Fraction(scale, 1) >= required), 0)
        if native_scale == 0:
            raise DomainRuleError(
                "UPSCALE_TARGET_EXCEEDS_MODEL_SCALE",
                "目标尺寸超过当前模型已验证的最大倍率",
                {"required_scale": float(required), "supported": list(scales)},
            )

    if fit == "CONTAIN":
        if width_ratio <= height_ratio:
            content_width = output_width
            content_height = _even_floor(Fraction(source_height * output_width, source_width))
        else:
            content_height = output_height
            content_width = _even_floor(Fraction(source_width * output_height, source_height))
        horizontal = output_width - content_width
        vertical = output_height - content_height
        padding: Insets = {
            "left": horizontal // 2,
            "right": horizontal - horizontal // 2,
            "top": vertical // 2,
            "bottom": vertical - vertical // 2,
        }
        crop: Insets = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        content_x, content_y = padding["left"], padding["top"]
    else:
        if width_ratio >= height_ratio:
            content_width = output_width
            content_height = _even_ceil(Fraction(source_height * output_width, source_width))
        else:
            content_height = output_height
            content_width = _even_ceil(Fraction(source_width * output_height, source_height))
        horizontal = content_width - output_width
        vertical = content_height - output_height
        crop = {
            "left": horizontal // 2,
            "right": horizontal - horizontal // 2,
            "top": vertical // 2,
            "bottom": vertical - vertical // 2,
        }
        padding = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        content_x, content_y = -crop["left"], -crop["top"]

    return {
        "source": {"width": source_width, "height": source_height, "orientation": source_orientation},
        "target": {"width": output_width, "height": output_height, "orientation": target_orientation, "fit": fit},
        "required_scale": round(float(required), 6),
        "native_scale": native_scale,
        "ai_output": {"width": source_width * native_scale, "height": source_height * native_scale},
        "content_rect": {"x": content_x, "y": content_y, "width": content_width, "height": content_height},
        "padding": padding,
        "crop": crop,
    }
