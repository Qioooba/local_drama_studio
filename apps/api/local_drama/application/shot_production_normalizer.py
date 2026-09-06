"""Shot production specification normalizer and self-healing engine.

Translates incomplete or partially structured creative inputs (from LLM breakdowns,
quick generation, or draft edits) into fully compliant, production-ready shot fields.
Eliminates deadlocks caused by missing technical camera/composition metadata while
preserving all original narrative and visual facts.
"""

from __future__ import annotations

import re
from typing import Any

from local_drama.domain.director_intent import normalize_director_intent_v3, validate_director_intent_v3_payload
from local_drama.domain.generation_contracts import CameraPlan
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS, missing_shot_fields, validate_shot_ready

# Mapping of common Chinese and English shot types to standard cinematic framing
SHOT_TYPE_MAP: dict[str, str] = {
    "大远景": "EXTREME_LONG_SHOT",
    "远景": "LONG_SHOT",
    "全景": "FULL_SHOT",
    "中远景": "MEDIUM_LONG_SHOT",
    "中景": "MEDIUM_SHOT",
    "中近景": "MEDIUM_CLOSE_UP",
    "近景": "CLOSE_UP",
    "特写": "CLOSE_UP",
    "大特写": "EXTREME_CLOSE_UP",
    "微距": "MACRO_SHOT",
    "过肩": "OVER_THE_SHOULDER",
    "主观视角": "POV_SHOT",
    "空镜头": "ESTABLISHING_SHOT",
    "WIDE": "WIDE_SHOT",
    "MEDIUM": "MEDIUM_SHOT",
    "CLOSEUP": "CLOSE_UP",
    "EXTREME_CLOSEUP": "EXTREME_CLOSE_UP",
    "FULL": "FULL_SHOT",
    "LONG": "LONG_SHOT",
    "POV": "POV_SHOT",
    "OTS": "OVER_THE_SHOULDER",
}

# Mapping of common camera movements
MOVEMENT_MAP: dict[str, tuple[str, str, str]] = {
    # keyword: (movement, direction, prompt_text)
    "推": ("SLOW_PUSH", "FORWARD", "缓慢平稳推镜头，聚焦主体"),
    "拉": ("PULL_BACK", "BACKWARD", "平稳拉远镜头，展现全景关系"),
    "摇": ("PAN", "RIGHT", "平稳水平摇移镜头，环顾环境"),
    "移": ("TRUCK", "RIGHT", "平稳横向轨道移动"),
    "升": ("BOOM_UP", "UP", "垂直上升摇臂镜头"),
    "降": ("BOOM_DOWN", "DOWN", "垂直下降镜头"),
    "俯": ("HIGH_ANGLE", "DOWN", "高机位俯视拍摄"),
    "仰": ("LOW_ANGLE", "UP", "低机位仰角拍摄"),
    "跟": ("TRACKING", "FORWARD", "平稳跟随运动拍摄"),
    "静": ("STATIC", "UNSPECIFIED", "固定机位，专注画面内部运动"),
    "定": ("STATIC", "UNSPECIFIED", "固定机位，主体动作清晰"),
    "特写": ("SLOW_PUSH", "FORWARD", "推镜头至主体特写细节"),
}


class ShotProductionSpecNormalizer:
    """Self-heals shot technical specifications to satisfy production-readiness."""

    @classmethod
    def infer_shot_type(cls, raw: Any, text_hints: str = "") -> str:
        if isinstance(raw, str) and raw.strip():
            cleaned = raw.strip().upper()
            if cleaned in SHOT_TYPE_MAP:
                return SHOT_TYPE_MAP[cleaned]
            for key, mapped in SHOT_TYPE_MAP.items():
                if key.upper() in cleaned:
                    return mapped
            return cleaned.replace(" ", "_")

        for key, mapped in SHOT_TYPE_MAP.items():
            if key in text_hints:
                return mapped
        return "MEDIUM_SHOT"

    @classmethod
    def infer_camera_movement(cls, raw: Any, text_hints: str = "") -> tuple[str, str, str]:
        """Return (movement, direction, prompt_text)."""
        if isinstance(raw, dict):
            movement = str(raw.get("movement") or "").strip().upper()
            direction = str(raw.get("direction") or "FORWARD").strip().upper()
            prompt = str(raw.get("prompt_text") or "").strip()
            if movement:
                return movement, direction or "FORWARD", prompt or f"平稳运镜 {movement}"

        if isinstance(raw, str) and raw.strip():
            for key, tuple_val in MOVEMENT_MAP.items():
                if key in raw:
                    return tuple_val
            return "SLOW_PUSH", "FORWARD", f"平稳运镜：{raw.strip()}"

        for key, tuple_val in MOVEMENT_MAP.items():
            if key in text_hints:
                return tuple_val

        return "SLOW_PUSH", "FORWARD", "电影级缓推运镜，聚焦主体与场景光影"

    @classmethod
    def normalize_fields(
        cls,
        fields: dict[str, Any] | None,
        *,
        profile_version_id: str | None = None,
        default_duration_ms: int = 4000,
    ) -> dict[str, Any]:
        """Transform raw shot fields into a valid, production-ready director intent."""
        raw: dict[str, Any] = dict(fields or {})

        # 1. Subject Action & Visual Context
        subject_action = str(
            raw.get("subject_action")
            or raw.get("action")
            or raw.get("visual")
            or raw.get("description")
            or "主体动作与场景环境交互"
        ).strip()

        combined_text_hints = f"{subject_action} {str(raw.get('visual') or '')} {str(raw.get('camera_plan') or '')}"

        # 2. Shot Type
        shot_type = cls.infer_shot_type(raw.get("shot_type"), combined_text_hints)

        # 3. Composition
        comp_raw = raw.get("composition")
        comp_dict = dict(comp_raw) if isinstance(comp_raw, dict) else {}
        composition = {
            "preset": comp_dict.get("preset") or "RULE_OF_THIRDS",
            "framing": comp_dict.get("framing") or (comp_raw if isinstance(comp_raw, str) and comp_raw.strip() else "EYE_LEVEL"),
            "subject_position": comp_dict.get("subject_position") or "CENTER",
            "depth_plan": comp_dict.get("depth_plan") or "NATURAL",
            "lead_room": comp_dict.get("lead_room") or "BALANCED",
            "screen_direction": comp_dict.get("screen_direction") or "NEUTRAL",
            "axis_rule": comp_dict.get("axis_rule") or "STRICT_180",
        }

        # 4. Camera Plan
        movement, direction, prompt_text = cls.infer_camera_movement(raw.get("camera_plan"), combined_text_hints)
        camera_raw = raw.get("camera_plan")
        camera_dict = dict(camera_raw) if isinstance(camera_raw, dict) else {}

        camera_mode = str(camera_dict.get("mode") or "PROMPT_FALLBACK").upper()
        if camera_mode not in {"NATIVE", "PROMPT_FALLBACK"}:
            camera_mode = "PROMPT_FALLBACK"

        camera_shot_type = str(camera_dict.get("shot_type") or shot_type).strip() or shot_type
        camera_movement = str(camera_dict.get("movement") or movement).strip() or movement
        camera_direction = str(camera_dict.get("direction") or direction).strip() or direction
        camera_prompt = str(camera_dict.get("prompt_text") or prompt_text).strip() or prompt_text

        intensity = 0.5
        try:
            if "intensity" in camera_dict and camera_dict["intensity"] is not None:
                intensity = max(0.0, min(1.0, float(camera_dict["intensity"])))
        except (TypeError, ValueError):
            intensity = 0.5

        curve = str(camera_dict.get("curve") or "LINEAR").strip() or "LINEAR"
        resolved_profile_id = str(camera_dict.get("profile_version_id") or profile_version_id or "").strip() or None

        camera_plan_payload = {
            "mode": camera_mode,
            "shot_type": camera_shot_type,
            "movement": camera_movement,
            "prompt_text": camera_prompt,
            "direction": camera_direction,
            "intensity": intensity,
            "curve": curve,
            "profile_version_id": resolved_profile_id,
        }

        # Ensure CameraPlan contract is strictly valid
        validated_camera_plan = CameraPlan.from_payload(camera_plan_payload)

        # 5. Target Duration Ms
        target_duration_ms: int = default_duration_ms
        if isinstance(raw.get("target_duration_ms"), int) and not isinstance(raw.get("target_duration_ms"), bool):
            if int(raw["target_duration_ms"]) > 0:
                target_duration_ms = int(raw["target_duration_ms"])
        elif isinstance(raw.get("duration_seconds"), (int, float)):
            sec = float(raw["duration_seconds"])
            if sec > 0:
                target_duration_ms = max(1000, min(15000, round(sec * 1000)))

        # 6. Dialogue
        dialogue = raw.get("dialogue")
        if dialogue is None:
            dialogue = ""
        elif not isinstance(dialogue, (str, list)):
            dialogue = str(dialogue)

        # 7. Environment
        environment = str(
            raw.get("environment")
            or raw.get("scene")
            or raw.get("location")
            or "自然光照环境，纵深与空间层次分明"
        ).strip()

        # 8. Continuity
        continuity = str(
            raw.get("continuity")
            or "保持场景光线、角色服装与视觉基准的一致性"
        ).strip()

        # 9. Creative Intent
        creative_intent = str(
            raw.get("creative_intent")
            or raw.get("summary")
            or subject_action
            or "推进主线情节发展与角色情绪张力"
        ).strip()

        # 10. Performance
        perf_raw = raw.get("performance")
        perf_dict = dict(perf_raw) if isinstance(perf_raw, dict) else {}
        performance = {
            "emotion": str(perf_dict.get("emotion") or raw.get("emotion") or "专注").strip(),
            "intensity": 0.5,
            "body_action": str(perf_dict.get("body_action") or raw.get("body_action") or subject_action).strip(),
            "facial_action": str(perf_dict.get("facial_action") or raw.get("facial_action") or "表情自然").strip(),
            "eye_line": str(perf_dict.get("eye_line") or raw.get("eye_line") or "视线专注目标").strip(),
            "blocking_summary": str(perf_dict.get("blocking_summary") or raw.get("blocking_summary") or "主体位于画面稳定机位").strip(),
        }

        normalized: dict[str, Any] = {
            **raw,
            "schema_version": "director-intent.v3",
            "shot_type": shot_type,
            "composition": composition,
            "subject_action": subject_action,
            "camera_plan": validated_camera_plan.to_dict(),
            "target_duration_ms": target_duration_ms,
            "dialogue": dialogue,
            "environment": environment,
            "continuity": continuity,
            "creative_intent": creative_intent,
            "performance": performance,
            "prompt_modifiers": list(raw.get("prompt_modifiers") or []),
        }

        # Validate with existing schemas
        validate_director_intent_v3_payload(normalized)
        validate_shot_ready(normalized)

        return normalized

    @classmethod
    def ensure_ready(
        cls,
        fields: dict[str, Any] | None,
        *,
        profile_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Convenience method that normalizes and asserts zero missing fields."""
        normalized = cls.normalize_fields(fields, profile_version_id=profile_version_id)
        missing = missing_shot_fields(normalized)
        if missing:
            raise RuntimeError(f"Unexpected missing fields after normalization: {missing}")
        return normalized
