"""Shared production contracts for generated and human-edited story breakdowns."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from local_drama.domain.errors import DomainRuleError

BREAKDOWN_MIN_SHOT_SECONDS = 1.0
BREAKDOWN_MAX_SHOT_SECONDS = 15.0
BREAKDOWN_DURATION_TOLERANCE_RATIO = 0.2

# Explicit value/unit grammar: an ASCII finite decimal (optionally signed,
# optionally exponent) with an optional complete "s"/"秒" unit.  Nothing is
# stripped and nothing is guessed, so "-5" stays negative and "10ms" is rejected
# instead of being mistaken for 10 seconds.  ``re.ASCII`` keeps full-width and
# other Unicode digits out of the accepted grammar.
_DURATION_PATTERN = re.compile(
    r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)(?:\s*(?:s|秒))?",
    re.IGNORECASE | re.ASCII,
)

_SHOT_TYPES = {
    "大全景": "ESTABLISHING", "远景": "ESTABLISHING", "establishing": "ESTABLISHING",
    "全景": "WIDE", "wide": "WIDE", "中景": "MEDIUM", "medium": "MEDIUM",
    "中近景": "MEDIUM_CLOSE", "medium close": "MEDIUM_CLOSE", "medium_close": "MEDIUM_CLOSE",
    "近景": "CLOSEUP", "closeup": "CLOSEUP", "close-up": "CLOSEUP",
    "特写": "EXTREME_CLOSEUP", "大特写": "EXTREME_CLOSEUP", "extreme closeup": "EXTREME_CLOSEUP",
    "主观镜头": "POV", "主观": "POV", "pov": "POV", "插入镜头": "INSERT", "插入": "INSERT", "insert": "INSERT",
}
_CAMERA_MOVEMENTS = {
    "固定": "STATIC", "静止": "STATIC", "static": "STATIC",
    "推进": "PUSH_IN", "推镜": "PUSH_IN", "推近": "PUSH_IN", "push in": "PUSH_IN",
    "拉远": "PULL_OUT", "拉镜": "PULL_OUT", "pull out": "PULL_OUT",
    "摇摄": "PAN", "横摇": "PAN", "pan": "PAN", "俯仰": "TILT", "纵摇": "TILT", "tilt": "TILT",
    "横移": "TRUCK", "侧移": "TRUCK", "truck": "TRUCK", "升降": "PEDESTAL", "pedestal": "PEDESTAL",
    "变焦": "ZOOM", "zoom": "ZOOM", "环绕": "ORBIT", "orbit": "ORBIT", "滚转": "ROLL", "roll": "ROLL",
}
_COMPOSITION_BY_SHOT_TYPE = {
    "ESTABLISHING": "CENTER", "WIDE": "CENTER", "MEDIUM": "CENTER", "MEDIUM_CLOSE": "LEFT_THIRD",
    "CLOSEUP": "LEFT_THIRD", "EXTREME_CLOSEUP": "CENTER", "POV": "CENTER", "INSERT": "CENTER", "OTHER": "CENTER",
}


def normalized_entity_name(value: object) -> str:
    """Stable identity key shared by AI proposals and formal shot bindings."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s\-—_·•，。！？、：:；;（）()《》\[\]{}]+", "", text)


def canonical_shot_type(value: object, visual: object = "") -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    allowed = {"ESTABLISHING", "WIDE", "MEDIUM", "MEDIUM_CLOSE", "CLOSEUP", "EXTREME_CLOSEUP", "POV", "INSERT", "OTHER"}
    if raw.upper() in allowed:
        return raw.upper()
    for source in (raw.casefold(), unicodedata.normalize("NFKC", str(visual or "")).casefold()):
        for label, canonical in _SHOT_TYPES.items():
            if label in source:
                return canonical
    return "OTHER"


def canonical_camera_movement(value: object) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    allowed = {"STATIC", "PUSH_IN", "PULL_OUT", "PAN", "TILT", "TRUCK", "PEDESTAL", "ZOOM", "ORBIT", "ROLL"}
    if raw.upper() in allowed:
        return raw.upper()
    folded = raw.casefold()
    for label, canonical in _CAMERA_MOVEMENTS.items():
        if label in folded:
            return canonical
    return "STATIC"


def explicit_camera_movement(value: object) -> str | None:
    """Extract motion only from prose that explicitly names the camera."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if not text:
        return None
    camera_phrases = (
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:推向|推进|推近|推镜|push\s*in)", "PUSH_IN"),
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:拉远|拉开|拉镜|pull\s*out)", "PULL_OUT"),
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:跟随|跟拍|追随|tracking)", "TRACKING"),
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:横移|侧移|truck)", "TRUCK"),
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:摇摄|横摇|pan)", "PAN"),
        (r"(?:镜头|摄影机|camera)[^，。；;]{0,16}(?:升起|上升|升降|pedestal)", "PEDESTAL"),
    )
    for pattern, movement in camera_phrases:
        if re.search(pattern, text):
            return movement
    return None


def director_intent_fields(
    shot: dict[str, Any], scene: dict[str, Any], *, duration_ms: int, source_revision_id: str | None,
) -> dict[str, Any]:
    """Losslessly adapt one breakdown shot to the canonical DirectorIntent v3."""
    visual = str(shot.get("visual") or "").strip()
    action = str(shot.get("action") or visual).strip()
    shot_type = canonical_shot_type(shot.get("shot_type"), visual)
    camera_text = str(shot.get("camera") or "固定").strip()
    movement = canonical_camera_movement(camera_text)
    prose_movement = explicit_camera_movement(f"{action} {visual}")
    if movement == "STATIC" and prose_movement is not None:
        movement = prose_movement
    lighting = str(shot.get("lighting") or scene.get("lighting") or "").strip()
    atmosphere = str(scene.get("atmosphere") or "").strip()
    location = str(scene.get("location") or scene.get("title") or "").strip()
    time_of_day = str(scene.get("time") or "").strip()
    environment = "，".join(part for part in (location, time_of_day, atmosphere, lighting) if part)
    creative_intent = str(shot.get("creative_intent") or visual or scene.get("purpose") or scene.get("summary") or action).strip()
    continuity = str(shot.get("continuity") or scene.get("continuity") or f"保持{location or '本场'}空间、人物造型与动作方向连续").strip()
    emotion = str(shot.get("emotion") or atmosphere or "中性").strip()
    composition = shot.get("composition") if isinstance(shot.get("composition"), dict) else {}
    sound = str(shot.get("sound") or "").strip()
    return {
        "schema_version": "director-intent.v3",
        "shot_type": shot_type,
        "composition": {
            "preset": str(composition.get("preset") or _COMPOSITION_BY_SHOT_TYPE[shot_type]),
            "framing": composition.get("framing"), "subject_position": composition.get("subject_position"),
            "headroom": composition.get("headroom"), "lead_room": composition.get("lead_room"),
            "screen_direction": composition.get("screen_direction"), "axis_rule": composition.get("axis_rule"),
            "depth_plan": composition.get("depth_plan"),
        },
        "subject_action": action,
        "performance": {
            "emotion": emotion,
            "intensity": float(shot.get("emotion_intensity", 0.5)) if isinstance(shot.get("emotion_intensity", 0.5), (int, float)) else 0.5,
            "body_action": action or None, "facial_action": shot.get("facial_action"),
            "eye_line": shot.get("eye_line"), "blocking_summary": shot.get("blocking_summary"),
        },
        "camera_plan": {
            "mode": "UNSUPPORTED", "shot_type": shot_type, "movement": movement,
            "direction": str(shot.get("camera_direction") or "FORWARD"),
            "intensity": float(shot.get("camera_intensity", 0.5)) if isinstance(shot.get("camera_intensity", 0.5), (int, float)) else 0.5,
            "curve": str(shot.get("camera_curve") or "LINEAR"), "prompt_text": "", "profile_version_id": None,
        },
        "target_duration_ms": duration_ms,
        "dialogue": shot.get("dialogue", ""),
        "environment": environment,
        "continuity": continuity,
        "transition_plan": shot.get("transition_plan") if isinstance(shot.get("transition_plan"), dict) else None,
        "sound_plan": {"description": sound} if sound else None,
        "creative_intent": creative_intent,
        "prompt_modifiers": list(dict.fromkeys(part for part in (lighting, atmosphere) if part)),
        "suggestion_sources": {"pipeline": {
            "source_kind": "APPLIED_BREAKDOWN_DRAFT", "source_revision_id": source_revision_id,
            "generated_fields": ["shot_type", "composition", "subject_action", "performance", "camera_plan", "environment", "continuity", "sound_plan", "creative_intent"],
        }},
        "visual": visual, "action": action, "camera": camera_text, "sound": sound, "lighting": lighting,
        "summary": str(scene.get("summary") or "").strip(),
    }


def strict_duration_seconds(value: object) -> float | None:
    """Parse an explicit duration, preserving sign and unit semantics.

    Accepted forms are a finite decimal number with an optional sign and an
    optional ``s``/``秒`` unit (``5``, ``-5``, ``1.2s``, ``5秒``, ``1e1``,
    ``3E-1``).  Anything else - including ``abc10xyz``, ``1.2ms``, ``NaN``,
    ``Infinity``, ``True`` and empty strings - returns ``None`` instead of
    being laundered into a number, so a garbage model output can never silently
    change the value.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if not isinstance(value, str):
        return None
    match = _DURATION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    try:
        parsed = float(match.group(1))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def validated_breakdown_shot_duration(value: Any, *, error_code: str) -> float:
    parsed = strict_duration_seconds(value)
    if parsed is None:
        raise DomainRuleError(
            error_code,
            "镜头时长必须是 1–15 秒之间的有限数值，可带 s/秒 单位；不接受负数、NaN 或垃圾字符串",
            {
                "duration_seconds": value,
                "minimum_duration_seconds": BREAKDOWN_MIN_SHOT_SECONDS,
                "maximum_duration_seconds": BREAKDOWN_MAX_SHOT_SECONDS,
            },
        )
    if not BREAKDOWN_MIN_SHOT_SECONDS <= parsed <= BREAKDOWN_MAX_SHOT_SECONDS:
        raise DomainRuleError(
            error_code,
            "镜头时长必须在 1–15 秒之间",
            {
                "duration_seconds": value,
                "parsed_duration_seconds": parsed,
                "minimum_duration_seconds": BREAKDOWN_MIN_SHOT_SECONDS,
                "maximum_duration_seconds": BREAKDOWN_MAX_SHOT_SECONDS,
            },
        )
    return parsed
