from __future__ import annotations

from typing import Any

from .errors import DomainRuleError


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _object(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bounded(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(0.0, min(1.0, float(value)))


def normalize_director_intent_v3(fields: dict[str, object]) -> dict[str, Any]:
    """Normalize historical flat intent fields without inventing creative facts.

    Unknown fields are retained because source ranges, split lineage and older
    continuity annotations remain historical evidence. Canonical V3 fields are
    always emitted, while absent draft values remain absent/null so readiness
    validation cannot mistake a server default for a director decision.
    """
    source = dict(fields)
    if source.get("schema_version") == "director-intent.v3":
        return source
    composition = _object(source.get("composition"))
    performance = _object(source.get("performance"))
    legacy_composition = _text(source.get("composition"))
    camera_value = source.get("camera_plan")
    camera = _object(camera_value)
    shot_type = _text(source.get("shot_type"))
    normalized = {
        **source,
        "schema_version": "director-intent.v3",
        "shot_type": shot_type,
        "composition": {
            "preset": _text(composition.get("preset")) or legacy_composition,
            "framing": _text(composition.get("framing")),
            "subject_position": _text(composition.get("subject_position")),
            "headroom": _text(composition.get("headroom")),
            "lead_room": _text(composition.get("lead_room")),
            "screen_direction": _text(composition.get("screen_direction")) or _text(source.get("screen_direction")),
            "axis_rule": _text(composition.get("axis_rule")),
            "depth_plan": _text(composition.get("depth_plan")),
        },
        "subject_action": _text(source.get("subject_action")) or _text(source.get("action")),
        "performance": {
            "emotion": _text(performance.get("emotion")) or _text(source.get("emotion")),
            "intensity": _bounded(performance.get("intensity")) if performance.get("intensity") is not None else _bounded(source.get("emotion_intensity")),
            "body_action": _text(performance.get("body_action")) or _text(source.get("body_action")),
            "facial_action": _text(performance.get("facial_action")) or _text(source.get("facial_action")),
            "eye_line": _text(performance.get("eye_line")) or _text(source.get("eye_line")),
            "blocking_summary": _text(performance.get("blocking_summary")) or _text(source.get("blocking_summary")),
        },
        "camera_plan": camera if camera else camera_value if "camera_plan" in source else None,
        "target_duration_ms": source.get("target_duration_ms") if isinstance(source.get("target_duration_ms"), int) and not isinstance(source.get("target_duration_ms"), bool) else None,
        "dialogue": source.get("dialogue") if isinstance(source.get("dialogue"), (str, list)) else None,
        "environment": source.get("environment") if isinstance(source.get("environment"), str) else None,
        "continuity": _text(source.get("continuity")),
        "transition_plan": _object(source.get("transition_plan")) or None,
        "sound_plan": _object(source.get("sound_plan")) or None,
        "creative_intent": _text(source.get("creative_intent")),
        "staging": _object(source.get("staging")) or None,
        "staging_3d": _object(source.get("staging_3d")) or None,
    }
    return normalized


def validate_director_intent_v3_payload(fields: dict[str, object]) -> None:
    if fields.get("schema_version") != "director-intent.v3":
        raise DomainRuleError("DIRECTOR_INTENT_SCHEMA_INVALID", "DirectorIntent 必须使用 director-intent.v3")
    for key in ("composition", "performance"):
        if not isinstance(fields.get(key), dict):
            raise DomainRuleError("DIRECTOR_INTENT_SCHEMA_INVALID", f"DirectorIntent {key} 必须是对象")
    performance = fields["performance"]
    assert isinstance(performance, dict)
    intensity = performance.get("intensity")
    if intensity is not None and (isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not 0 <= float(intensity) <= 1):
        raise DomainRuleError("DIRECTOR_INTENT_INTENSITY_INVALID", "表演强度必须在 0—1 之间")
    target_duration_ms = fields.get("target_duration_ms")
    if target_duration_ms is not None and (isinstance(target_duration_ms, bool) or not isinstance(target_duration_ms, int) or target_duration_ms <= 0):
        raise DomainRuleError("DIRECTOR_INTENT_DURATION_INVALID", "镜头目标时长必须是正整数毫秒")
