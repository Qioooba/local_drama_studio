"""V2 semantic parameter contracts with one deterministic provenance chain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError


class ParameterSource(str, Enum):
    CAPABILITY_DEFAULT = "CAPABILITY_DEFAULT"
    PROFILE_DEFAULT = "PROFILE_DEFAULT"
    PROFILE_LOCK = "PROFILE_LOCK"
    SYSTEM_OVERRIDE = "SYSTEM_OVERRIDE"
    PROJECT_OVERRIDE = "PROJECT_OVERRIDE"
    EPISODE_OVERRIDE = "EPISODE_OVERRIDE"
    CHARACTER_OVERRIDE = "CHARACTER_OVERRIDE"
    SHOT_OVERRIDE = "SHOT_OVERRIDE"
    RUN_OVERRIDE = "RUN_OVERRIDE"


_SCOPE_SOURCES: Mapping[str, ParameterSource] = MappingProxyType(
    {
        "SYSTEM": ParameterSource.SYSTEM_OVERRIDE,
        "PROJECT": ParameterSource.PROJECT_OVERRIDE,
        "EPISODE": ParameterSource.EPISODE_OVERRIDE,
        "CHARACTER": ParameterSource.CHARACTER_OVERRIDE,
        "SHOT": ParameterSource.SHOT_OVERRIDE,
        "RUN": ParameterSource.RUN_OVERRIDE,
    }
)
_PRECEDENCE: Mapping[str, int] = MappingProxyType(
    {scope: index for index, scope in enumerate(("SYSTEM", "PROJECT", "EPISODE", "CHARACTER", "SHOT", "RUN"))}
)
_RUNTIME_FIELD_PARTS = frozenset({"api", "endpoint", "executable", "key", "locator", "native", "path", "python", "secret", "token", "url"})


@dataclass(frozen=True, slots=True)
class ParameterContract:
    """A capability-level JSON Schema subset plus a separate UI policy schema."""

    capability: str
    schema: Mapping[str, Any]
    ui_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability is required")
        if self.schema.get("type", "object") != "object" or not isinstance(self.schema.get("properties"), Mapping):
            raise ValueError("ParameterContract schema must be an object schema with properties")
        unsafe = sorted(str(name) for name in self.schema["properties"] if is_runtime_wiring_field(str(name)))
        if unsafe:
            raise DomainRuleError(
                "MP_PARAMETER_RUNTIME_FIELD_FORBIDDEN",
                "ParameterContract 不能把路径、端点、密钥或运行时接线字段声明为业务参数。",
                {"fields": unsafe},
            )

    @property
    def properties(self) -> Mapping[str, Mapping[str, Any]]:
        raw = self.schema["properties"]
        return MappingProxyType({str(key): value for key, value in raw.items() if isinstance(value, Mapping)})

    def allowed_scopes(self, name: str) -> frozenset[str]:
        field_ui = self._field_ui(name)
        scopes = field_ui.get("scopes")
        if not isinstance(scopes, list):
            return frozenset(_SCOPE_SOURCES)
        return frozenset(str(scope).upper() for scope in scopes if str(scope).upper() in _SCOPE_SOURCES)

    def is_cross_profile_safe(self, name: str) -> bool:
        return self._field_ui(name).get("cross_profile_safe") is True

    def _field_ui(self, name: str) -> Mapping[str, Any]:
        properties = self.ui_schema.get("properties")
        if isinstance(properties, Mapping) and isinstance(properties.get(name), Mapping):
            field_ui: Mapping[str, Any] = properties[name]
            return field_ui
        return {}


@dataclass(frozen=True, slots=True)
class ProfileParameterPolicy:
    profile_version_id: str
    defaults: Mapping[str, Any]
    locked_values: Mapping[str, Any]
    allowed_override_fields: frozenset[str]

    def __post_init__(self) -> None:
        if not self.profile_version_id.strip():
            raise ValueError("profile_version_id is required")


@dataclass(frozen=True, slots=True)
class ParameterOverride:
    scope: str
    values: Mapping[str, Any]
    profile_version_id: str | None
    auto_mode: bool = False

    def __post_init__(self) -> None:
        normalized_scope = self.scope.strip().upper()
        if normalized_scope not in _SCOPE_SOURCES:
            raise ValueError(f"Unsupported parameter override scope: {self.scope}")
        object.__setattr__(self, "scope", normalized_scope)
        if self.auto_mode and self.profile_version_id is not None:
            raise ValueError("auto_mode overrides cannot also bind a profile_version_id")
        if not self.auto_mode and not (self.profile_version_id or "").strip():
            raise ValueError("profile-bound overrides require profile_version_id")


@dataclass(frozen=True, slots=True)
class ResolvedParameter:
    value: Any
    source: ParameterSource
    locked: bool


class ParameterResolutionService:
    """Applies Profile → scope → run precedence and returns every value's source."""

    def resolve(
        self,
        contract: ParameterContract,
        policy: ProfileParameterPolicy,
        overrides: tuple[ParameterOverride, ...] = (),
    ) -> Mapping[str, ResolvedParameter]:
        properties = contract.properties
        self._validate_values(contract, policy.defaults, source="PROFILE_DEFAULT", allow_partial=True)
        self._validate_values(contract, policy.locked_values, source="PROFILE_LOCK", allow_partial=True)
        unknown_allowed = policy.allowed_override_fields.difference(properties)
        if unknown_allowed:
            raise DomainRuleError("MP_PARAMETER_POLICY_INVALID", "Profile 允许了合同不存在的参数", {"fields": sorted(unknown_allowed)})

        values: dict[str, ResolvedParameter] = {}
        for name, definition in properties.items():
            if "default" in definition:
                values[name] = ResolvedParameter(definition["default"], ParameterSource.CAPABILITY_DEFAULT, False)
        for name, value in policy.defaults.items():
            values[name] = ResolvedParameter(value, ParameterSource.PROFILE_DEFAULT, False)
        for name, value in policy.locked_values.items():
            values[name] = ResolvedParameter(value, ParameterSource.PROFILE_LOCK, True)

        for override in sorted(overrides, key=lambda item: _PRECEDENCE[item.scope]):
            self._apply_override(contract, policy, override, values)
        self._validate_required(contract, values)
        return MappingProxyType(dict(values))

    def _apply_override(
        self,
        contract: ParameterContract,
        policy: ProfileParameterPolicy,
        override: ParameterOverride,
        values: dict[str, ResolvedParameter],
    ) -> None:
        if override.auto_mode:
            unsafe = sorted(name for name in override.values if not contract.is_cross_profile_safe(str(name)))
            if unsafe:
                raise DomainRuleError(
                    "MP_PARAMETER_AUTO_PROFILE_PRIVATE",
                    "AUTO 模式只能保存跨 Profile 的语义参数。",
                    {"fields": unsafe},
                )
        elif override.profile_version_id != policy.profile_version_id:
            raise DomainRuleError(
                "MP_PARAMETER_PROFILE_MISMATCH",
                "参数覆盖引用的 ProfileVersion 与当前解析目标不一致。",
                {"expected": policy.profile_version_id, "actual": override.profile_version_id},
            )
        self._validate_values(contract, override.values, source=override.scope, allow_partial=True)
        for raw_name, value in override.values.items():
            name = str(raw_name)
            if name not in policy.allowed_override_fields:
                raise DomainRuleError("MP_PARAMETER_OVERRIDE_FORBIDDEN", "该参数未被当前 Profile 允许覆盖。", {"field": name})
            if override.scope not in contract.allowed_scopes(name):
                raise DomainRuleError(
                    "MP_PARAMETER_SCOPE_FORBIDDEN", "该参数不能在当前业务范围覆盖。", {"field": name, "scope": override.scope}
                )
            if name in policy.locked_values:
                raise DomainRuleError("MP_PARAMETER_LOCKED", "Profile 已锁定该参数，不能由业务或本次运行改写。", {"field": name})
            values[name] = ResolvedParameter(value, _SCOPE_SOURCES[override.scope], False)

    def _validate_values(
        self,
        contract: ParameterContract,
        values: Mapping[str, Any],
        *,
        source: str,
        allow_partial: bool,
    ) -> None:
        if not isinstance(values, Mapping):
            raise DomainRuleError("MP_PARAMETER_VALUES_INVALID", "参数值必须是 JSON 对象。", {"source": source})
        properties = contract.properties
        unknown = sorted(str(name) for name in values if str(name) not in properties)
        if unknown:
            raise DomainRuleError("MP_PARAMETER_UNKNOWN", "参数合同中不存在该字段。", {"source": source, "fields": unknown})
        for raw_name, value in values.items():
            name = str(raw_name)
            definition = properties[name]
            _validate_field(name, value, definition, source)
        if not allow_partial:
            self._validate_required(contract, {str(name): ResolvedParameter(value, ParameterSource.PROFILE_DEFAULT, False) for name, value in values.items()})

    def _validate_required(self, contract: ParameterContract, values: Mapping[str, ResolvedParameter]) -> None:
        required = contract.schema.get("required", [])
        if not isinstance(required, list):
            return
        missing = sorted(str(name) for name in required if str(name) not in values)
        if missing:
            raise DomainRuleError("MP_PARAMETER_REQUIRED", "缺少能力合同要求的参数。", {"fields": missing})


def _validate_field(name: str, value: Any, definition: Mapping[str, Any], source: str) -> None:
    expected = definition.get("type")
    valid_type = (
        expected is None
        or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (expected == "string" and isinstance(value, str))
        or (expected == "boolean" and isinstance(value, bool))
    )
    if not valid_type:
        raise DomainRuleError("MP_PARAMETER_TYPE_INVALID", "参数类型不符合合同。", {"field": name, "expected": expected, "source": source})
    enum = definition.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise DomainRuleError("MP_PARAMETER_ENUM_INVALID", "参数不在允许枚举中。", {"field": name, "options": enum, "source": source})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if definition.get("minimum") is not None and value < definition["minimum"]:
            raise DomainRuleError("MP_PARAMETER_RANGE_INVALID", "参数低于最小值。", {"field": name, "source": source})
        if definition.get("maximum") is not None and value > definition["maximum"]:
            raise DomainRuleError("MP_PARAMETER_RANGE_INVALID", "参数超过最大值。", {"field": name, "source": source})
        multiple = definition.get("multipleOf")
        if multiple is not None and value % multiple != 0:
            raise DomainRuleError("MP_PARAMETER_MULTIPLE_INVALID", "参数不符合 multipleOf 约束。", {"field": name, "source": source})


def is_runtime_wiring_field(name: str) -> bool:
    # `keyframe` is a valid media semantic term, while `api_key` and
    # `model_path` are runtime wiring. Split camelCase and separators so the
    # decision is deterministic rather than a fragile substring match.
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    parts = {part for part in re.split(r"[^a-z0-9]+", normalized) if part}
    return bool(parts & _RUNTIME_FIELD_PARTS)
