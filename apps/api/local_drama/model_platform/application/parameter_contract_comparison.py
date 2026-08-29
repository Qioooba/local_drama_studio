"""Safe, deterministic comparison of legacy and V2 parameter contracts.

The migration path must not infer that two Profiles are interchangeable just
because their capability or model title matches.  This module compares only
declarative parameter semantics and never returns parameter values, model
locators, endpoints, or secrets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from local_drama.application.ports.override_schema import effective_schema
from local_drama.infrastructure.database.sqlite import Database

_FORBIDDEN_NAME_PARTS = frozenset({"path", "endpoint", "url", "secret", "token", "key", "executable", "locator"})
_CONSTRAINT_KEYS = ("minimum", "maximum", "multiple_of", "min_length", "max_length", "pattern", "enum")


@dataclass(frozen=True, slots=True)
class ParameterContractComparison:
    status: str
    legacy_field_count: int
    v2_field_count: int
    common_fields: tuple[str, ...]
    legacy_only_fields: tuple[str, ...]
    v2_only_fields: tuple[str, ...]
    type_mismatch_fields: tuple[str, ...]
    required_mismatch_fields: tuple[str, ...]
    scope_mismatch_fields: tuple[str, ...]
    constraint_mismatch_fields: tuple[str, ...]
    default_mismatch_fields: tuple[str, ...]
    unsafe_field_names: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return self.status == "SHAPE_MATCH"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "matches": self.matches,
            "legacy_field_count": self.legacy_field_count,
            "v2_field_count": self.v2_field_count,
            "common_fields": list(self.common_fields),
            "legacy_only_fields": list(self.legacy_only_fields),
            "v2_only_fields": list(self.v2_only_fields),
            "differences": {
                "type": list(self.type_mismatch_fields),
                "required": list(self.required_mismatch_fields),
                "scope": list(self.scope_mismatch_fields),
                "constraint": list(self.constraint_mismatch_fields),
                "default": list(self.default_mismatch_fields),
            },
            "unsafe_field_names": list(self.unsafe_field_names),
            "values_exposed": False,
        }


def compare_parameter_contracts(
    database: Database,
    legacy_execution_profile_version_id: str,
    v2_execution_profile_version_id: str,
) -> ParameterContractComparison:
    with database.connect() as connection:
        legacy = connection.execute(
            """SELECT capability,model_bundle_json,parameter_schema_json
            FROM execution_profile_versions WHERE id=?""",
            (legacy_execution_profile_version_id,),
        ).fetchone()
        v2 = connection.execute(
            """SELECT parameter.schema_json,parameter.ui_schema_json,profile.payload_json
            FROM mp_execution_profile_versions profile
            JOIN mp_parameter_contract_versions parameter ON parameter.id=profile.parameter_contract_version_id
            WHERE profile.id=?""",
            (v2_execution_profile_version_id,),
        ).fetchone()
    if legacy is None or v2 is None:
        return _unavailable()

    legacy_fields, legacy_unsafe = _legacy_fields(legacy)
    v2_fields, v2_unsafe = _v2_fields(v2)
    unsafe = tuple(sorted(set(legacy_unsafe) | set(v2_unsafe)))
    common = tuple(sorted(set(legacy_fields) & set(v2_fields)))
    legacy_only = tuple(sorted(set(legacy_fields) - set(v2_fields)))
    v2_only = tuple(sorted(set(v2_fields) - set(legacy_fields)))
    type_mismatch = tuple(name for name in common if legacy_fields[name]["type"] != v2_fields[name]["type"])
    required_mismatch = tuple(name for name in common if legacy_fields[name]["required"] != v2_fields[name]["required"])
    scope_mismatch = tuple(name for name in common if legacy_fields[name]["scopes"] != v2_fields[name]["scopes"])
    constraint_mismatch = tuple(name for name in common if legacy_fields[name]["constraints"] != v2_fields[name]["constraints"])
    default_mismatch = tuple(name for name in common if legacy_fields[name]["default_hash"] != v2_fields[name]["default_hash"])
    status = "SHAPE_MATCH" if not (
        unsafe or legacy_only or v2_only or type_mismatch or required_mismatch or scope_mismatch or constraint_mismatch or default_mismatch
    ) else "UNSAFE_FIELD_NAME" if unsafe else "SHAPE_DIFFERENT"
    return ParameterContractComparison(
        status=status,
        legacy_field_count=len(legacy_fields),
        v2_field_count=len(v2_fields),
        common_fields=common,
        legacy_only_fields=legacy_only,
        v2_only_fields=v2_only,
        type_mismatch_fields=type_mismatch,
        required_mismatch_fields=required_mismatch,
        scope_mismatch_fields=scope_mismatch,
        constraint_mismatch_fields=constraint_mismatch,
        default_mismatch_fields=default_mismatch,
        unsafe_field_names=unsafe,
    )


def _legacy_fields(row: Mapping[str, Any]) -> tuple[dict[str, dict[str, object]], tuple[str, ...]]:
    bundle = _object(row["model_bundle_json"])
    parameter_schema = _object(row["parameter_schema_json"])
    override_schema = bundle.get("override_schema")
    if not isinstance(override_schema, Mapping):
        override_schema = parameter_schema.get("override_schema")
    schema = effective_schema({"capability": str(row["capability"]), "override_schema": dict(override_schema or {})})
    raw_fields = schema.get("fields")
    fields = raw_fields if isinstance(raw_fields, Mapping) else {}
    defaults = bundle.get("defaults") if isinstance(bundle.get("defaults"), Mapping) else parameter_schema.get("defaults")
    resolved_defaults = defaults if isinstance(defaults, Mapping) else {}
    result: dict[str, dict[str, object]] = {}
    unsafe: list[str] = []
    for raw_name, raw_field in fields.items():
        name = str(raw_name)
        if not isinstance(raw_field, Mapping):
            continue
        if _unsafe(name):
            unsafe.append(name)
            continue
        default = resolved_defaults[name] if name in resolved_defaults else raw_field.get("default", _missing())
        result[name] = _shape(raw_field, default=default, required=raw_field.get("required") is True)
    return result, tuple(sorted(unsafe))


def _v2_fields(row: Mapping[str, Any]) -> tuple[dict[str, dict[str, object]], tuple[str, ...]]:
    schema = _object(row["schema_json"])
    ui_schema = _object(row["ui_schema_json"])
    payload = _object(row["payload_json"])
    raw_properties = schema.get("properties")
    properties: Mapping[str, Any] = raw_properties if isinstance(raw_properties, Mapping) else {}
    raw_ui_properties = ui_schema.get("properties")
    ui_properties: Mapping[str, Any] = raw_ui_properties if isinstance(raw_ui_properties, Mapping) else {}
    required = {str(item) for item in schema.get("required", []) if isinstance(item, str)}
    raw_defaults = payload.get("defaults")
    defaults: Mapping[str, Any] = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    locks = payload.get("locked_values", payload.get("locks", {}))
    locked = locks if isinstance(locks, Mapping) else {}
    result: dict[str, dict[str, object]] = {}
    unsafe: list[str] = []
    for raw_name, raw_field in properties.items():
        name = str(raw_name)
        if not isinstance(raw_field, Mapping):
            continue
        if _unsafe(name):
            unsafe.append(name)
            continue
        raw_ui_field = ui_properties.get(name)
        ui_field: Mapping[str, Any] = raw_ui_field if isinstance(raw_ui_field, Mapping) else {}
        field = {**dict(raw_field), "scopes": ui_field.get("scopes")}
        default = locked[name] if name in locked else defaults[name] if name in defaults else raw_field.get("default", _missing())
        result[name] = _shape(field, default=default, required=name in required)
    return result, tuple(sorted(unsafe))


def _shape(field: Mapping[str, Any], *, default: object, required: bool) -> dict[str, object]:
    scopes = field.get("scopes")
    scope_items = tuple(sorted({str(item).upper() for item in scopes if str(item).upper()})) if isinstance(scopes, list) else ()
    constraints = {key: field[key] for key in _CONSTRAINT_KEYS if key in field}
    return {
        "type": str(field.get("type") or "").lower(),
        "required": required,
        "scopes": scope_items,
        "constraints": _canonical(constraints),
        "default_hash": _default_hash(default),
    }


def _unsafe(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _FORBIDDEN_NAME_PARTS)


def _default_hash(value: object) -> str | None:
    if isinstance(value, _Missing):
        return None
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _object(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value) if value is not None else "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


@dataclass(frozen=True, slots=True)
class _Missing:
    pass


def _missing() -> _Missing:
    return _Missing()


def _unavailable() -> ParameterContractComparison:
    return ParameterContractComparison("PROFILE_UNAVAILABLE", 0, 0, (), (), (), (), (), (), (), (), ())
