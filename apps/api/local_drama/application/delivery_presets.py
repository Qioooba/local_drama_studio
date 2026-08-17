"""Built-in delivery spec presets for mainstream short-video platforms (G11 P1-6).

The preset table is an immutable compile-time constant: it is never mutated and
never persisted as its own row.  A preset is only ever *applied* by copying its
spec into a DeliveryTargetVersion through ``create_delivery_target`` (see
``ConfigurationService.create_delivery_target_from_preset``), so platform specs
stay declarative while all validation/audit/revision logic is reused.
"""

from __future__ import annotations

from typing import Any

from local_drama.domain.errors import DomainRuleError


class DeliveryPreset:
    """One immutable platform delivery preset."""

    __slots__ = ("code", "title", "description", "spec")

    def __init__(self, code: str, title: str, description: str, spec: dict[str, Any]) -> None:
        self.code = code
        self.title = title
        self.description = description
        self.spec = spec


# Field contract shared by every preset spec:
#   path_rel               - project-relative delivery directory (required by create_delivery_target)
#   width / height         - frame size in pixels
#   fps                    - frame rate
#   bitrate_kbps           - recommended encode bitrate ceiling
#   max_duration_seconds   - platform duration ceiling
#   cover_aspect           - recommended cover/thumbnail aspect as "WxH"
#   audio / subtitles      - delivery container hints (same shape as the manual target editor)
DELIVERY_PRESETS: tuple[DeliveryPreset, ...] = (
    DeliveryPreset(
        code="DOUYIN_VERTICAL",
        title="抖音竖屏",
        description="抖音竖屏 1080×1920 @30fps，码率 ≤6Mbps，时长上限 180s，封面 1080×1440",
        spec={
            "path_rel": "06_delivery/douyin_vertical",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bitrate_kbps": 6000,
            "max_duration_seconds": 180,
            "cover_aspect": "1080x1440",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="KUAI_SHOU_VERTICAL",
        title="快手竖屏",
        description="快手竖屏 1080×1920 @30fps，码率 ≤5Mbps，时长上限 180s，封面 1080×1440",
        spec={
            "path_rel": "06_delivery/kuaishou_vertical",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bitrate_kbps": 5000,
            "max_duration_seconds": 180,
            "cover_aspect": "1080x1440",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="XIAOHONGSHU_3_4",
        title="小红书 3:4",
        description="小红书竖版 3:4 1080×1440 @30fps，码率 ≤6Mbps，封面 1080×1440",
        spec={
            "path_rel": "06_delivery/xiaohongshu_3_4",
            "width": 1080,
            "height": 1440,
            "fps": 30,
            "bitrate_kbps": 6000,
            "max_duration_seconds": 300,
            "cover_aspect": "1080x1440",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="WECHAT_CHANNELS",
        title="视频号竖屏",
        description="微信视频号竖屏 1080×1920 @30fps，码率 ≤6Mbps，封面 1080×1260（6:7）",
        spec={
            "path_rel": "06_delivery/wechat_channels",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bitrate_kbps": 6000,
            "max_duration_seconds": 300,
            "cover_aspect": "1080x1260",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="BILIBILI_HORIZONTAL",
        title="B站横屏",
        description="B站横屏 1920×1080 @30fps，码率 ≤8Mbps，封面 1920×1080",
        spec={
            "path_rel": "06_delivery/bilibili_horizontal",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "bitrate_kbps": 8000,
            "max_duration_seconds": 600,
            "cover_aspect": "1920x1080",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="UNIVERSAL_16_9",
        title="通用 16:9",
        description="通用横屏 16:9 1920×1080 @30fps，码率 ≤8Mbps，封面 1920×1080",
        spec={
            "path_rel": "06_delivery/universal_16_9",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "bitrate_kbps": 8000,
            "max_duration_seconds": 3600,
            "cover_aspect": "1920x1080",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
    DeliveryPreset(
        code="UNIVERSAL_9_16",
        title="通用 9:16",
        description="通用竖屏 9:16 1080×1920 @30fps，码率 ≤6Mbps，封面 1080×1920",
        spec={
            "path_rel": "06_delivery/universal_9_16",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bitrate_kbps": 6000,
            "max_duration_seconds": 3600,
            "cover_aspect": "1080x1920",
            "audio": "AAC",
            "subtitles": "SIDECAR",
        },
    ),
)

DELIVERY_PRESET_BY_CODE: dict[str, DeliveryPreset] = {preset.code: preset for preset in DELIVERY_PRESETS}


def require_preset(preset_code: str) -> DeliveryPreset:
    """Return the preset or raise the domain rule error used by the API layer."""
    preset = DELIVERY_PRESET_BY_CODE.get(preset_code)
    if preset is None:
        raise DomainRuleError(
            "DELIVERY_PRESET_UNSUPPORTED",
            f"不支持的交付规格预设：{preset_code}",
            details={"preset_code": preset_code, "supported": sorted(DELIVERY_PRESET_BY_CODE)},
            suggested_action="从预设列表中重新选择平台规格",
        )
    return preset


def preset_items() -> list[dict[str, Any]]:
    return [
        {
            "code": preset.code,
            "title": preset.title,
            "description": preset.description,
            "spec": dict(preset.spec),
        }
        for preset in DELIVERY_PRESETS
    ]
