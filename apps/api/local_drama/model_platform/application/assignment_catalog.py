"""Safe read model for configuring V2 SYSTEM CapabilityAssignments.

This is intentionally separate from the legacy generation-preference option
catalog.  It exposes only published V2 Profiles and declarative, non-sensitive
fields that the selected Profile explicitly permits at SYSTEM scope.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.parameters import is_runtime_wiring_field
from local_drama.model_platform.application.scope_ownership import validate_assignment_scope

_SCHEMA_KEYS = frozenset({"type", "minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern", "enum", "default"})


class CapabilityAssignmentCatalogService:
    """Build bounded scope-configuration projections without runtime wiring."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list_system(self) -> list[dict[str, object]]:
        """Compatibility projection for the SYSTEM-only model-center page."""
        return [
            {
                **item,
                "profiles": [
                    {
                        **profile,
                        "system_override_fields": profile.pop("override_fields"),
                    }
                    for profile in item["profiles"]
                ],
            }
            for item in self.list_scope("SYSTEM", "")
        ]

    def list_scope(self, scope_type: str, scope_id: str) -> list[dict[str, object]]:
        """List published V2 options for one verified polymorphic scope.

        The returned parameters are declarative UI/contract fields only.  This
        is the shared read model for future project, episode, shot and
        character configuration surfaces; it deliberately has no legacy
        generation-preference projection.
        """
        normalized_scope = scope_type.strip().upper()
        normalized_id = scope_id.strip()
        if normalized_scope not in {"SYSTEM", "PROJECT", "EPISODE", "SHOT", "CHARACTER"} or (
            normalized_scope == "SYSTEM" and normalized_id
        ) or (normalized_scope != "SYSTEM" and not normalized_id):
            raise DomainRuleError("MP_ASSIGNMENT_SCOPE_INVALID", "CapabilityAssignment 的 scope_type 或 scope_id 无效。")
        with self.database.connect() as connection:
            validate_assignment_scope(connection, scope_type=normalized_scope, scope_id=normalized_id)
            capabilities = connection.execute(
                """SELECT capability.code,capability.title,capability.family,capability.background_only,
                          assignment.resolution_mode,assignment.execution_profile_version_id,assignment.revision,
                          override_set.values_json
                   FROM mp_capability_definitions capability
                   LEFT JOIN mp_capability_assignments assignment
                     ON assignment.capability_definition_id=capability.id
                    AND assignment.scope_type=? AND assignment.scope_id=?
                   LEFT JOIN mp_scope_override_set_versions override_set
                     ON override_set.id=assignment.override_set_version_id
                   ORDER BY capability.family,capability.code""",
                (normalized_scope, normalized_id),
            ).fetchall()
            profiles = connection.execute(
                """SELECT capability.code AS capability_code,profile_version.id AS profile_version_id,
                          profile.code AS profile_code,profile.title AS profile_title,profile_version.version_no,profile_version.payload_json,
                          parameter.schema_json,parameter.ui_schema_json
                   FROM mp_execution_profile_versions profile_version
                   JOIN mp_execution_profiles profile ON profile.id=profile_version.profile_id
                   JOIN mp_capability_definitions capability ON capability.id=profile_version.capability_definition_id
                   JOIN mp_profile_publications publication ON publication.execution_profile_version_id=profile_version.id
                   JOIN mp_parameter_contract_versions parameter ON parameter.id=profile_version.parameter_contract_version_id
                   WHERE publication.status='PUBLISHED'
                   ORDER BY capability.code,publication.published_at DESC,profile_version.version_no DESC"""
            ).fetchall()

        profile_options: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in profiles:
            profile_options[str(row["capability_code"])].append(_profile_option(row, normalized_scope))
        result: list[dict[str, object]] = []
        for row in capabilities:
            capability_code = str(row["code"])
            options = profile_options[capability_code]
            current_profile_id = str(row["execution_profile_version_id"]) if row["execution_profile_version_id"] else None
            selected = next((item for item in options if item["profile_version_id"] == current_profile_id), None)
            allowed_names = {str(item["name"]) for item in selected.get("override_fields", [])} if selected else set()
            persisted = _object(row["values_json"])
            unsafe_persisted = sorted(name for name in persisted if name not in allowed_names)
            result.append({
                "capability_code": capability_code,
                "title": str(row["title"]),
                "family": str(row["family"]),
                "background_only": bool(row["background_only"]),
                "assignment": {
                    "resolution_mode": str(row["resolution_mode"]) if row["resolution_mode"] else "AUTO",
                    "execution_profile_version_id": current_profile_id,
                    "revision": int(row["revision"]) if row["revision"] is not None else None,
                    "overrides": {name: persisted[name] for name in sorted(allowed_names & set(persisted))},
                    "has_unrenderable_override": bool(unsafe_persisted),
                },
                "profiles": options,
            })
        return result


def _profile_option(row: Mapping[str, Any], scope_type: str) -> dict[str, object]:
    schema = _object(row["schema_json"])
    ui_schema = _object(row["ui_schema_json"])
    payload = _object(row["payload_json"])
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    ui_properties = ui_schema.get("properties") if isinstance(ui_schema.get("properties"), Mapping) else {}
    allowed = {str(value) for value in payload.get("allowed_override_fields", ()) if isinstance(value, str) and value}
    locked_values = payload.get("locked_values", payload.get("locks", {}))
    locked = set(locked_values) if isinstance(locked_values, Mapping) else set()
    fields: list[dict[str, object]] = []
    for raw_name, raw_schema in properties.items():
        name = str(raw_name)
        ui = ui_properties.get(name) if isinstance(ui_properties.get(name), Mapping) else {}
        scopes = ui.get("scopes") if isinstance(ui.get("scopes"), list) else []
        if (
            not isinstance(raw_schema, Mapping)
            or name not in allowed
            or name in locked
            or scope_type not in {str(scope).upper() for scope in scopes}
            or is_runtime_wiring_field(name)
            or ui.get("sensitive") is True
        ):
            continue
        field = {key: raw_schema[key] for key in _SCHEMA_KEYS if key in raw_schema}
        fields.append({
            "name": name,
            "label": str(ui.get("label") or name),
            "help": str(ui.get("help") or ""),
            "schema": field,
        })
    return {
        "profile_version_id": str(row["profile_version_id"]),
        "profile_code": str(row["profile_code"]),
        "profile_title": str(row["profile_title"]),
        "version_no": int(row["version_no"]),
        "override_fields": fields,
    }
def _object(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value) if value is not None else "{}")
    except (TypeError, ValueError):
        return {}
    return {str(key): item for key, item in decoded.items()} if isinstance(decoded, dict) else {}
