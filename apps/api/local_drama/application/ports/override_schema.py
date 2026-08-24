"""Port-level contract helpers for Profile-scoped runtime overrides."""

from __future__ import annotations

from typing import Any

from local_drama.domain.errors import DomainRuleError


def default_override_schema(capability: str) -> dict[str, Any]:
    normalized = capability.strip().upper()
    if not normalized.startswith("VIDEO_"):
        return {"schema_version": "localdrama.profile-overrides.v1", "fields": {}, "additional_properties": False}
    native_audio_locked = normalized in {"VIDEO_REFERENCE", "VIDEO_REF2V", "VIDEO_REF2VA"}
    return {
        "schema_version": "localdrama.profile-overrides.v1",
        "additional_properties": False,
        "fields": {
            "production_tier": {
                "type": "enum", "label": "生产档位", "default": "DRAFT",
                "options": ["FAST", "DRAFT", "SCREEN", "PRODUCTION", "MASTER"],
                "legacy_aliases": {"PREVIEW": "FAST", "BALANCED": "DRAFT"},
                "scopes": ["PROJECT", "SHOT", "RUN"],
                "runtime_binding": "H3_PRODUCTION_TIER",
            },
            "sigma_points": {
                "type": "integer", "label": "生成步数", "default": 50, "minimum": 2, "maximum": 1000,
                "step": 1, "scopes": ["PROJECT", "SHOT", "RUN"], "runtime_binding": "SAMPLER_STEPS",
            },
            "acceleration": {
                "type": "enum", "label": "加速模式", "default": "OFF", "options": ["OFF", "TURBO_LORA"],
                "scopes": ["PROJECT", "SHOT", "RUN"], "runtime_binding": "H3_ACCELERATION",
            },
            "lora_strength": {
                "type": "number", "label": "LoRA 强度", "default": 1.0, "minimum": 0, "maximum": 2,
                "step": 0.05, "scopes": ["PROJECT", "SHOT", "RUN"],
                "visible_if": {"field": "acceleration", "equals": "TURBO_LORA"},
                "runtime_binding": "LORA_MODEL_STRENGTH",
            },
            "native_audio": {
                "type": "boolean", "label": "生成视频原生音轨", "default": True,
                "editable": not native_audio_locked,
                **({"locked_reason": "当前 Ref2V 节点要求 Audio VAE，关闭开关前不能提交"} if native_audio_locked else {}),
                "scopes": ["PROJECT", "SHOT", "RUN"], "runtime_binding": "NATIVE_AUDIO",
            },
            "take_count": {
                "type": "integer", "label": "候选数量", "default": 1, "minimum": 1, "maximum": 8,
                "step": 1, "scopes": ["PROJECT", "SHOT", "RUN"], "runtime_binding": "TAKE_COUNT",
            },
        },
    }


def effective_schema(profile: dict[str, Any]) -> dict[str, Any]:
    schema = profile.get("override_schema")
    if isinstance(schema, dict) and isinstance(schema.get("fields"), dict):
        return schema
    return default_override_schema(str(profile.get("capability") or ""))


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
            aliases = field.get("legacy_aliases") if isinstance(field.get("legacy_aliases"), dict) else {}
            if isinstance(value, str) and value.upper() in aliases:
                value = aliases[value.upper()]
            if value not in field.get("options", []):
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_ENUM_INVALID", "运行参数选项无效", {"field": key, "options": field.get("options", [])})
        if field.get("editable") is False:
            if value != field.get("default"):
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_LOCKED", "当前 Profile 暂不允许覆盖该运行参数", {"field": key, "reason": field.get("locked_reason")})
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if field.get("minimum") is not None and value < field["minimum"]:
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_RANGE_INVALID", "运行参数低于允许范围", {"field": key, "minimum": field["minimum"]})
            if field.get("maximum") is not None and value > field["maximum"]:
                raise DomainRuleError("GENERATION_PREFERENCE_SETTING_RANGE_INVALID", "运行参数超过允许范围", {"field": key, "maximum": field["maximum"]})
        normalized[str(key)] = value

    acceleration = normalized.get("acceleration")
    if "lora_strength" in normalized and acceleration != "TURBO_LORA":
        raise DomainRuleError("GENERATION_PREFERENCE_SETTING_DEPENDENCY_INVALID", "只有启用 TURBO_LORA 时才能设置 LoRA 强度")
    return normalized
