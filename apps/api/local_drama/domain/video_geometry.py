"""Canonical video geometry rules shared by planners and workflow compilers."""

from __future__ import annotations

from local_drama.domain.errors import DomainRuleError

H3_FPS = 24
H3_FRAME_GRID_STEP = 17
H3_FRAME_GRID_OFFSET = 5

H3_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (480, 832),
    "16:9": (864, 480),
    "auto": (480, 832),
}


def h3_frame_count(duration_seconds: float) -> int:
    """Snap a requested duration up to MiniMax H3's ``17k + 5`` frame grid."""

    target = round(float(duration_seconds) * H3_FPS)
    remainder = target % H3_FRAME_GRID_STEP
    if remainder != H3_FRAME_GRID_OFFSET:
        target += (H3_FRAME_GRID_OFFSET - remainder) % H3_FRAME_GRID_STEP
    return target


def h3_resolution(aspect_ratio: str) -> tuple[int, int]:
    """Resolve a supported H3 aspect ratio to its verified canvas size."""

    ratio = str(aspect_ratio).strip().lower()
    try:
        return H3_RESOLUTIONS[ratio]
    except KeyError:
        raise DomainRuleError(
            "H3_ASPECT_RATIO_UNSUPPORTED",
            "H3 分辨率仅支持 16:9 / 9:16 / auto",
            {"aspect_ratio": aspect_ratio},
        ) from None


def h3_render_duration_ms(frame_count: int) -> int:
    """Return the effective render duration for a snapped H3 frame count."""

    return round(int(frame_count) / H3_FPS * 1000)
