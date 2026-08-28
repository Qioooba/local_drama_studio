"""Port-level contract helpers for Profile-scoped runtime overrides."""

from __future__ import annotations

from typing import Any

from local_drama.domain.errors import DomainRuleError


def default_override_schema(capability: str) -> dict[str, Any]:
    normalized = capability.strip().upper()
    base = {"schema_version": "localdrama.profile-overrides.v2", "additional_properties": False}
    if normalized == "LLM_STORY_PARSE":
        return {
            **base,
            "fields": {
                "temperature": {
                    "type": "number",
                    "label": "创造性",
                    "description": "值越高，提示词改写越有变化；结构化规划建议保持较低。",
                    "default": 0.0,
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "step": 0.05,
                    "scopes": ["RUN"],
                },
                "top_p": {
                    "type": "number",
                    "label": "候选词范围",
                    "description": "控制文字模型采样范围，通常保持 0.8—1.0。",
                    "default": 0.9,
                    "minimum": 0.05,
                    "maximum": 1.0,
                    "step": 0.05,
                    "scopes": ["RUN"],
                },
                "max_tokens": {
                    "type": "integer",
                    "label": "最大输出长度",
                    "description": "限制规划模型返回的 token 数。",
                    "default": 2048,
                    "minimum": 256,
                    "maximum": 8192,
                    "step": 128,
                    "scopes": ["RUN"],
                },
            },
        }
    if normalized.startswith("IMAGE_"):
        return {
            **base,
            "fields": {
                "width": {
                    "type": "integer",
                    "label": "宽度",
                    "description": "输出像素宽度，需为 16 的倍数。",
                    "minimum": 256,
                    "maximum": 4096,
                    "step": 16,
                    "multiple_of": 16,
                    "scopes": ["RUN"],
                    "runtime_binding": "WIDTH",
                },
                "height": {
                    "type": "integer",
                    "label": "高度",
                    "description": "输出像素高度，需为 16 的倍数。",
                    "minimum": 256,
                    "maximum": 4096,
                    "step": 16,
                    "multiple_of": 16,
                    "scopes": ["RUN"],
                    "runtime_binding": "HEIGHT",
                },
                **_diffusion_fields(),
            },
        }
    if not normalized.startswith("VIDEO_"):
        return {**base, "fields": {}}
    native_audio_locked = normalized in {"VIDEO_REFERENCE", "VIDEO_REF2V", "VIDEO_REF2VA"}
    return {
        **base,
        "fields": {
            "production_tier": {
                "type": "enum",
                "label": "生产档位",
                "default": "DRAFT",
                "options": ["FAST", "DRAFT", "SCREEN", "PRODUCTION", "MASTER"],
                "legacy_aliases": {"PREVIEW": "FAST", "BALANCED": "DRAFT"},
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "H3_PRODUCTION_TIER",
            },
            "sigma_points": {
                "type": "integer",
                "label": "生成步数",
                "default": 50,
                "minimum": 2,
                "maximum": 1000,
                "step": 1,
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "SAMPLER_STEPS",
            },
            "acceleration": {
                "type": "enum",
                "label": "加速模式",
                "default": "OFF",
                "options": ["OFF", "TURBO_LORA"],
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "H3_ACCELERATION",
            },
            "lora_strength": {
                "type": "number",
                "label": "LoRA 强度",
                "default": 1.0,
                "minimum": 0,
                "maximum": 2,
                "step": 0.05,
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "visible_if": {"field": "acceleration", "equals": "TURBO_LORA"},
                "runtime_binding": "LORA_MODEL_STRENGTH",
            },
            "native_audio": {
                "type": "boolean",
                "label": "生成视频原生音轨",
                "default": True,
                "editable": not native_audio_locked,
                **({"locked_reason": "当前 Ref2V 节点要求 Audio VAE，关闭开关前不能提交"} if native_audio_locked else {}),
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "NATIVE_AUDIO",
            },
            "take_count": {
                "type": "integer",
                "label": "候选数量",
                "default": 1,
                "minimum": 1,
                "maximum": 8,
                "step": 1,
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "TAKE_COUNT",
            },
            "width": {
                "type": "integer",
                "label": "视频宽度",
                "description": "输出像素宽度，需为 16 的倍数。",
                "minimum": 256,
                "maximum": 4096,
                "step": 16,
                "multiple_of": 16,
                "scopes": ["RUN"],
                "runtime_binding": "WIDTH",
            },
            "height": {
                "type": "integer",
                "label": "视频高度",
                "description": "输出像素高度，需为 16 的倍数。",
                "minimum": 256,
                "maximum": 4096,
                "step": 16,
                "multiple_of": 16,
                "scopes": ["RUN"],
                "runtime_binding": "HEIGHT",
            },
            "frame_count": {
                "type": "integer",
                "label": "帧数",
                "description": "总帧数决定视频时长，并受模型结构限制。",
                "minimum": 9,
                "maximum": 1001,
                "step": 1,
                "scopes": ["RUN"],
                "runtime_binding": "FRAME_COUNT",
            },
            "fps": {
                "type": "number",
                "label": "帧率",
                "description": "输出播放帧率，不会增加模型生成的帧数。",
                "minimum": 1,
                "maximum": 120,
                "step": 1,
                "scopes": ["RUN"],
                "runtime_binding": "FPS",
            },
            **_diffusion_fields(),
        },
    }


def _diffusion_fields() -> dict[str, Any]:
    return {
        "steps": {
            "type": "integer",
            "label": "采样步数",
            "description": "常见 ComfyUI 采样步数；越高通常越慢。",
            "minimum": 1,
            "maximum": 1000,
            "step": 1,
            "scopes": ["RUN"],
            "runtime_binding": "SAMPLER_STEPS",
        },
        "cfg": {
            "type": "number",
            "label": "CFG",
            "description": "提示词遵循强度；过高可能导致画面生硬。",
            "minimum": 0,
            "maximum": 30,
            "step": 0.1,
            "scopes": ["RUN"],
            "runtime_binding": "CFG",
        },
        "sampler_name": {
            "type": "enum",
            "label": "采样器",
            "description": "对应 ComfyUI 的 sampler_name。",
            "options": ["res_multistep", "euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"],
            "scopes": ["RUN"],
            "runtime_binding": "SAMPLER_NAME",
        },
        "scheduler": {
            "type": "enum",
            "label": "调度器",
            "description": "对应 ComfyUI 的 scheduler。",
            "options": ["simple", "normal", "karras", "exponential", "sgm_uniform", "beta", "ddim_uniform"],
            "scopes": ["RUN"],
            "runtime_binding": "SCHEDULER",
        },
        "denoise": {
            "type": "number",
            "label": "降噪强度",
            "description": "图生图/图生视频中控制保留输入画面的程度。",
            "minimum": 0,
            "maximum": 1,
            "step": 0.01,
            "scopes": ["RUN"],
            "runtime_binding": "DENOISE",
        },
    }


def effective_schema(profile: dict[str, Any]) -> dict[str, Any]:
    fallback = default_override_schema(str(profile.get("capability") or ""))
    schema = profile.get("override_schema")
    if isinstance(schema, dict) and isinstance(schema.get("fields"), dict):
        return {
            **fallback,
            **schema,
            "fields": {**dict(fallback.get("fields") or {}), **dict(schema.get("fields") or {})},
        }
    return fallback


def validate_overrides(settings: dict[str, Any] | None, profile: dict[str, Any], *, scope: str) -> dict[str, Any]:
    values = settings or {}
    if not isinstance(values, dict):
        raise DomainRuleError("GENERATION_PREFERENCE_SETTINGS_INVALID", "运行参数必须是 JSON 对象")
    schema = effective_schema(profile)
    fields = schema.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    unknown = sorted(str(key) for key in values if str(key) not in fields)
    if unknown and schema.get("additional_properties", False) is not True:
        raise DomainRuleError("GENERATION_PREFERENCE_SETTING_UNKNOWN", "运行参数包含 Profile 未声明的字段", {"fields": unknown})
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        field = fields.get(key)
        if not isinstance(field, dict):
            normalized[str(key)] = value
            continue
        scopes = field.get("scopes")
        if isinstance(scopes, list) and scope not in {str(item).upper() for item in scopes}:
            raise DomainRuleError("GENERATION_PREFERENCE_SETTING_SCOPE_FORBIDDEN", "该参数不能在当前作用域覆盖", {"field": key, "scope": scope})
        field_type = str(field.get("type") or "").lower()
        if field_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise DomainRuleError("GENERATION_PREFERENCE_SETTING_TYPE_INVALID", "运行参数类型无效", {"field": key, "expected": "integer"})
        if field_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise DomainRuleError("GENERATION_PREFERENCE_SETTING_TYPE_INVALID", "运行参数类型无效", {"field": key, "expected": "number"})
        if field_type == "boolean" and not isinstance(value, bool):
            raise DomainRuleError("GENERATION_PREFERENCE_SETTING_TYPE_INVALID", "运行参数类型无效", {"field": key, "expected": "boolean"})
        if field_type == "enum":
            raw_aliases = field.get("legacy_aliases")
            aliases: dict[str, Any] = raw_aliases if isinstance(raw_aliases, dict) else {}
            if isinstance(value, str) and value.upper() in aliases:
                value = aliases[value.upper()]
            if value not in field.get("options", []):
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_ENUM_INVALID", "运行参数选项无效", {"field": key, "options": field.get("options", [])})
        if field.get("editable") is False:
            if value != field.get("default"):
                raise DomainRuleError(
                    "GENERATION_PREFERENCE_SETTING_LOCKED", "当前 Profile 暂不允许覆盖该运行参数", {"field": key, "reason": field.get("locked_reason")}
                )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if field.get("minimum") is not None and value < field["minimum"]:
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_RANGE_INVALID", "运行参数低于允许范围", {"field": key, "minimum": field["minimum"]})
            if field.get("maximum") is not None and value > field["maximum"]:
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_RANGE_INVALID", "运行参数超过允许范围", {"field": key, "maximum": field["maximum"]})
            if field.get("multiple_of") is not None and value % field["multiple_of"] != 0:
                raise DomainRuleError(
                    "GENERATION_PREFERENCE_SETTING_STEP_INVALID",
                    "运行参数不符合模型要求的步进值",
                    {"field": key, "multiple_of": field["multiple_of"]},
                )
        normalized[str(key)] = value

    acceleration = normalized.get("acceleration")
    if "lora_strength" in normalized and acceleration != "TURBO_LORA":
        raise DomainRuleError("GENERATION_PREFERENCE_SETTING_DEPENDENCY_INVALID", "只有启用 TURBO_LORA 时才能设置 LoRA 强度")
    return normalized
