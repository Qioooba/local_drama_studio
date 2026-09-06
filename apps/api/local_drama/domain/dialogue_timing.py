"""One shot-boundary rule for assembly, editing, freezing and rendering."""

from typing import Any

from local_drama.domain.errors import DomainRuleError


def dialogue_timing_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    videos = [item for item in items if str(item.get("track_type", "")).upper() == "VIDEO"]
    by_shot = {str((item.get("parameters") or {}).get("shot_id")): item for item in videos}
    issues = []
    for item in items:
        if str(item.get("track_type", "")).upper() != "DIALOGUE":
            continue
        params = item.get("parameters") or {}
        # Free-standing audio bindings may intentionally bridge shots. This
        # rule concerns spoken lines assigned to a shot, not music/mix tracks.
        if not params.get("dialogue_line_id"):
            continue
        start, end = int(item["start_us"]), int(item["end_us"])
        shot_id = str(params.get("shot_id") or "")
        clip = by_shot.get(shot_id) if shot_id else next(
            (video for video in videos if int(video["start_us"]) <= start < int(video["end_us"])), None,
        )
        if clip is None:
            issues.append({"code": "DIALOGUE_SHOT_RANGE_MISSING", "message": "对白没有对应的视频镜头，请重新组装时间线。", "line_id": str(params["dialogue_line_id"]), "shot_id": shot_id, "owner_route": "SHOT_STUDIO"})
            continue
        shot_id = str((clip.get("parameters") or {}).get("shot_id") or shot_id)
        clip_start, clip_end = int(clip["start_us"]), int(clip["end_us"])
        if start < clip_start or end > clip_end:
            excess = max(0, end - clip_end)
            code = str(params.get("line_code") or params["dialogue_line_id"])
            issues.append({
                "code": "DIALOGUE_EXCEEDS_SHOT_DURATION", "line_id": str(params["dialogue_line_id"]), "shot_id": shot_id,
                "message": f"对白 {code} 超出所属镜头 {excess / 1_000_000:.2f} 秒；请缩短台词、调整语速后重生配音，或调整镜头时长。",
                "owner_route": "SHOT_STUDIO", "duration_us": end - start,
                "shot_duration_us": clip_end - clip_start, "overrun_us": excess,
            })
    return issues


def assert_dialogue_timing(items: list[dict[str, Any]]) -> None:
    issues = dialogue_timing_issues(items)
    if issues:
        raise DomainRuleError(str(issues[0]["code"]), str(issues[0]["message"]), {"issues": issues})
