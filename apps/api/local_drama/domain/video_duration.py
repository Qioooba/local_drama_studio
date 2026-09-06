"""Validate real video coverage; container/audio length is not video coverage."""

from fractions import Fraction
from math import ceil
from typing import Any

from local_drama.domain.errors import DomainRuleError


def _positive_fraction(value: Any) -> Fraction | None:
    try:
        number = Fraction(str(value))
        return number if number > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def video_duration_budget(probe: dict[str, Any], item: dict[str, Any]) -> dict[str, int]:
    stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = _positive_fraction(stream.get("avg_frame_rate")) or _positive_fraction(stream.get("r_frame_rate"))
    ticks = _positive_fraction(stream.get("duration_ts"))
    time_base = _positive_fraction(stream.get("time_base"))
    duration = ticks * time_base if ticks and time_base else _positive_fraction(stream.get("duration"))
    frames = _positive_fraction(stream.get("nb_frames"))
    if duration is None and frames and fps:
        duration = frames / fps
    if duration is None:
        raise DomainRuleError("TIMELINE_SOURCE_DURATION_UNKNOWN", "无法确认源视频有效时长，请在镜头页面检查或重新生成素材")
    try:
        required = int(item["end_us"]) - int(item["start_us"])
        offset = int((item.get("parameters") or {}).get("source_start_us") or 0)
    except (KeyError, TypeError, ValueError) as error:
        raise DomainRuleError("TIMELINE_SOURCE_RANGE_INVALID", "镜头起止时间或源视频入点无效") from error
    duration_us = round(duration * 1_000_000)
    if required <= 0 or offset < 0 or offset >= duration_us:
        raise DomainRuleError("TIMELINE_SOURCE_RANGE_INVALID", "镜头时长必须大于零，源视频入点必须位于有效视频范围内")
    available = duration_us - offset
    tolerance = min(100_000, ceil(Fraction(1_000_000, 1) / fps) + 1) if fps else 0
    deficit = max(0, required - available)
    budget = {"required_us": required, "available_us": available, "tolerance_us": tolerance, "padding_us": deficit}
    if deficit > tolerance:
        shot = str((item.get("parameters") or {}).get("shot_code") or "当前镜头")
        raise DomainRuleError(
            "TIMELINE_SOURCE_DURATION_INSUFFICIENT",
            f"{shot}需要 {required / 1_000_000:.3f} 秒，源视频可用 {available / 1_000_000:.3f} 秒，"
            f"缺少 {deficit / 1_000_000:.3f} 秒。请在镜头页面补充生成素材或调整剪辑时长，不能自动用静止尾帧补齐。",
            {**budget, "shot_code": shot, "media_version_id": item.get("media_version_id")},
        )
    return budget
