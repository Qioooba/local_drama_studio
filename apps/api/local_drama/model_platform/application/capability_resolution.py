"""V2 capability assignment and fail-closed Profile resolution."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.parameters import (
    ParameterContract,
    ParameterOverride,
    ParameterResolutionService,
    ProfileParameterPolicy,
)
from local_drama.model_platform.application.scope_ownership import (
    validate_assignment_scope,
    validate_scope_context,
)

_SCOPE_ORDER = ("SHOT", "CHARACTER", "EPISODE", "PROJECT", "SYSTEM")
_SCOPES = frozenset(_SCOPE_ORDER)


@dataclass(frozen=True, slots=True)
class CapabilityScopeContext:
    project_id: str | None = None
    episode_id: str | None = None
    shot_id: str | None = None
    character_id: str | None = None

    def scoped_ids(self) -> Mapping[str, str]:
        result: dict[str, str] = {"SYSTEM": ""}
        for scope, value in (
            ("PROJECT", self.project_id),
            ("EPISODE", self.episode_id),
            ("SHOT", self.shot_id),
            ("CHARACTER", self.character_id),
        ):
            if value and value.strip():
                result[scope] = value.strip()
        return result


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentRequest:
    scope_type: str
    scope_id: str
    capability_code: str
    resolution_mode: str
    execution_profile_version_id: str | None = None
    overrides: Mapping[str, Any] | None = None
    reason: str = ""
    actor: str = "local-user"


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    capability_code: str
    execution_profile_version_id: str | None
    resolution_reason: str
    assignment_chain: tuple[Mapping[str, object], ...]
    assignment_overrides: Mapping[str, Any]
    assignment_override_scope: str | None
    blocked_reason: str | None = None


class CapabilityAssignmentService:
    """Owns mutable scope preferences, never Profile or model payloads."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def put(self, request: CapabilityAssignmentRequest) -> None:
        scope_type = request.scope_type.strip().upper()
        mode = request.resolution_mode.strip().upper()
        scope_id = request.scope_id.strip()
        if scope_type not in _SCOPES or (scope_type == "SYSTEM" and scope_id) or (scope_type != "SYSTEM" and not scope_id):
            raise DomainRuleError("MP_ASSIGNMENT_SCOPE_INVALID", "CapabilityAssignment 的 scope_type 或 scope_id 无效。")
        if mode not in {"AUTO", "EXPLICIT"}:
            raise DomainRuleError("MP_ASSIGNMENT_MODE_INVALID", "resolution_mode 仅支持 AUTO 或 EXPLICIT。")
        if mode == "EXPLICIT" and not request.execution_profile_version_id:
            raise DomainRuleError("MP_ASSIGNMENT_PROFILE_REQUIRED", "EXPLICIT 分配必须引用已发布 ProfileVersion。")
        if mode == "AUTO" and request.execution_profile_version_id is not None:
            raise DomainRuleError("MP_ASSIGNMENT_AUTO_PROFILE_FORBIDDEN", "AUTO 分配不能绑定某个 ProfileVersion。")
        overrides = dict(request.overrides or {})
        if mode == "AUTO" and overrides:
            raise DomainRuleError(
                "MP_ASSIGNMENT_AUTO_OVERRIDE_FORBIDDEN",
                "AUTO 分配没有固定 Profile，不能保存 Profile 私有的范围参数。",
            )
        now = _utc_now()
        reason = request.reason.strip()
        actor = request.actor.strip()
        if overrides and (not reason or not actor):
            raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_AUDIT_REQUIRED", "保存范围参数必须填写变更理由和操作人。")
        if len(reason) > 1000 or len(actor) > 120:
            raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_AUDIT_INVALID", "范围参数的变更理由或操作人长度无效。")
        with self.database.transaction() as connection:
            validate_assignment_scope(connection, scope_type=scope_type, scope_id=scope_id)
            capability = connection.execute(
                "SELECT id FROM mp_capability_definitions WHERE code=?", (request.capability_code.strip().upper(),)
            ).fetchone()
            if capability is None:
                raise DomainRuleError("MP_ASSIGNMENT_CAPABILITY_NOT_FOUND", "Capability 不存在。")
            capability_id = str(capability["id"])
            if mode == "EXPLICIT":
                profile = _published_profile_parameter_contract(
                    connection, str(request.execution_profile_version_id), capability_id
                )
                if profile is None:
                    raise DomainRuleError("MP_ASSIGNMENT_PROFILE_NOT_PUBLISHED", "只能显式分配该能力的已发布 ProfileVersion。")
                _validate_assignment_overrides(
                    profile=profile,
                    scope_type=scope_type,
                    overrides=overrides,
                )
            override_set_version_id = _create_scope_override_set_version(
                connection,
                scope_type=scope_type,
                scope_id=scope_id,
                capability_id=capability_id,
                execution_profile_version_id=str(request.execution_profile_version_id) if request.execution_profile_version_id else None,
                values=overrides,
                reason=reason,
                actor=actor,
                now=now,
            )
            connection.execute(
                """INSERT INTO mp_capability_assignments
                (id,scope_type,scope_id,capability_definition_id,resolution_mode,execution_profile_version_id,override_json,override_set_version_id,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scope_type,scope_id,capability_definition_id) DO UPDATE SET
                  resolution_mode=excluded.resolution_mode,
                  execution_profile_version_id=excluded.execution_profile_version_id,
                  override_json=excluded.override_json,
                  override_set_version_id=excluded.override_set_version_id,
                  revision=mp_capability_assignments.revision+1,
                  updated_at=excluded.updated_at""",
                (
                    str(uuid.uuid4()),
                    scope_type,
                    scope_id,
                    capability_id,
                    mode,
                    request.execution_profile_version_id,
                    "{}",
                    override_set_version_id,
                    1,
                    now,
                    now,
                ),
            )

    def resolve(self, capability_code: str, context: CapabilityScopeContext) -> CapabilityResolution:
        canonical_code = capability_code.strip().upper()
        with self.database.connect() as connection:
            capability = connection.execute(
                "SELECT id FROM mp_capability_definitions WHERE code=?", (canonical_code,)
            ).fetchone()
            if capability is None:
                raise DomainRuleError("MP_ASSIGNMENT_CAPABILITY_NOT_FOUND", "Capability 不存在。")
            capability_id = str(capability["id"])
            scoped_ids = context.scoped_ids()
            validate_scope_context(connection, scoped_ids)
            chain: list[Mapping[str, object]] = []
            for scope_type in _SCOPE_ORDER:
                scope_id = scoped_ids.get(scope_type)
                if scope_id is None:
                    continue
                assignment = connection.execute(
                    """SELECT resolution_mode,execution_profile_version_id,override_set_version_id,revision
                    FROM mp_capability_assignments WHERE scope_type=? AND scope_id=? AND capability_definition_id=?""",
                    (scope_type, scope_id, capability_id),
                ).fetchone()
                if assignment is None:
                    continue
                mode = str(assignment["resolution_mode"])
                profile_id = assignment["execution_profile_version_id"]
                chain.append({
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "resolution_mode": mode,
                    "revision": int(assignment["revision"]),
                })
                if mode == "EXPLICIT":
                    if not profile_id or not _is_published_profile(connection, str(profile_id), capability_id):
                        return CapabilityResolution(
                            canonical_code,
                            None,
                            "EXPLICIT_ASSIGNMENT",
                            tuple(chain),
                            {},
                            None,
                            "EXPLICIT_ASSIGNMENT_PROFILE_NOT_PUBLISHED",
                        )
                    overrides = _load_scope_override_values(
                        connection,
                        assignment["override_set_version_id"],
                        scope_type=scope_type,
                        scope_id=scope_id,
                        capability_id=capability_id,
                        execution_profile_version_id=str(profile_id),
                    )
                    return CapabilityResolution(
                        canonical_code,
                        str(profile_id),
                        "EXPLICIT_ASSIGNMENT",
                        tuple(chain),
                        overrides,
                        scope_type if overrides else None,
                    )
            default = connection.execute(
                """SELECT version.id FROM mp_execution_profile_versions version
                JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
                WHERE version.capability_definition_id=? AND publication.status='PUBLISHED'
                ORDER BY publication.published_at DESC, version.version_no DESC LIMIT 1""",
                (capability_id,),
            ).fetchone()
        if default is None:
            return CapabilityResolution(canonical_code, None, "NO_PUBLISHED_DEFAULT", tuple(chain), {}, None, "NO_PUBLISHED_PROFILE")
        return CapabilityResolution(canonical_code, str(default["id"]), "LATEST_PUBLISHED_DEFAULT", tuple(chain), {}, None)


def _is_published_profile(connection, profile_version_id: str, capability_id: str) -> bool:
    return connection.execute(
        """SELECT 1 FROM mp_execution_profile_versions version
        JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
        WHERE version.id=? AND version.capability_definition_id=? AND publication.status='PUBLISHED'""",
        (profile_version_id, capability_id),
    ).fetchone() is not None


def _published_profile_parameter_contract(connection, profile_version_id: str, capability_id: str):
    return connection.execute(
        """SELECT profile.id AS profile_id,profile.payload_json,parameter.schema_json,parameter.ui_schema_json
        FROM mp_execution_profile_versions profile
        JOIN mp_profile_publications publication ON publication.execution_profile_version_id=profile.id
        JOIN mp_parameter_contract_versions parameter ON parameter.id=profile.parameter_contract_version_id
        WHERE profile.id=? AND profile.capability_definition_id=? AND publication.status='PUBLISHED'""",
        (profile_version_id, capability_id),
    ).fetchone()


def _validate_assignment_overrides(*, profile, scope_type: str, overrides: Mapping[str, Any]) -> None:
    if not overrides:
        return
    payload = _object_json(profile["payload_json"], "MP_ASSIGNMENT_PROFILE_PARAMETER_POLICY_INVALID")
    contract = ParameterContract(
        capability="assignment",
        schema=_object_json(profile["schema_json"], "MP_ASSIGNMENT_PARAMETER_CONTRACT_INVALID"),
        ui_schema=_object_json(profile["ui_schema_json"], "MP_ASSIGNMENT_PARAMETER_CONTRACT_INVALID"),
    )
    allowed = payload.get("allowed_override_fields", ())
    if not isinstance(allowed, (list, tuple)):
        raise DomainRuleError("MP_ASSIGNMENT_PROFILE_PARAMETER_POLICY_INVALID", "Profile 的 allowed_override_fields 必须是数组。")
    defaults = _mapping(payload.get("defaults"), "MP_ASSIGNMENT_PROFILE_PARAMETER_POLICY_INVALID")
    locked = _mapping(payload.get("locked_values", payload.get("locks", {})), "MP_ASSIGNMENT_PROFILE_PARAMETER_POLICY_INVALID")
    policy = ProfileParameterPolicy(
        profile_version_id=str(profile["profile_id"]),
        defaults=defaults,
        locked_values=locked,
        allowed_override_fields=frozenset(str(value) for value in allowed if str(value)),
    )
    ParameterResolutionService().resolve(
        contract,
        policy,
        (ParameterOverride(scope=scope_type, values=overrides, profile_version_id=str(profile["profile_id"])),),
    )


def _create_scope_override_set_version(
    connection,
    *,
    scope_type: str,
    scope_id: str,
    capability_id: str,
    execution_profile_version_id: str | None,
    values: Mapping[str, Any],
    reason: str,
    actor: str,
    now: str,
) -> str | None:
    if not values:
        return None
    if execution_profile_version_id is None:
        raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_PROFILE_REQUIRED", "范围参数必须绑定明确的已发布 Profile。")
    row = connection.execute(
        """SELECT COALESCE(MAX(version_no),0) AS latest
        FROM mp_scope_override_set_versions
        WHERE scope_type=? AND scope_id=? AND capability_definition_id=?""",
        (scope_type, scope_id, capability_id),
    ).fetchone()
    version_no = int(row["latest"]) + 1
    values_json = _json(values)
    override_set_version_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO mp_scope_override_set_versions
        (id,scope_type,scope_id,capability_definition_id,execution_profile_version_id,version_no,values_json,content_hash,reason,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            override_set_version_id, scope_type, scope_id, capability_id, execution_profile_version_id,
            version_no, values_json, hashlib.sha256(values_json.encode("utf-8")).hexdigest(), reason, actor, now,
        ),
    )
    _audit_override_set(connection, actor, override_set_version_id, reason, {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "capability_definition_id": capability_id,
        "execution_profile_version_id": execution_profile_version_id,
        "version_no": version_no,
    })
    return override_set_version_id


def _load_scope_override_values(
    connection,
    override_set_version_id: object,
    *,
    scope_type: str,
    scope_id: str,
    capability_id: str,
    execution_profile_version_id: str,
) -> Mapping[str, Any]:
    if override_set_version_id is None:
        return {}
    row = connection.execute(
        """SELECT scope_type,scope_id,capability_definition_id,execution_profile_version_id,values_json
        FROM mp_scope_override_set_versions WHERE id=?""",
        (str(override_set_version_id),),
    ).fetchone()
    if row is None:
        raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_SET_NOT_FOUND", "CapabilityAssignment 引用的范围参数版本不存在。")
    expected = (scope_type, scope_id, capability_id, execution_profile_version_id)
    actual = (
        str(row["scope_type"]), str(row["scope_id"]), str(row["capability_definition_id"]),
        str(row["execution_profile_version_id"]),
    )
    if actual != expected:
        raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_SET_MISMATCH", "范围参数版本不属于当前 Assignment/Profile，拒绝解析。")
    return _object_json(row["values_json"], "MP_ASSIGNMENT_OVERRIDE_STORAGE_INVALID")


def _audit_override_set(connection, actor: str, override_set_version_id: str, summary: str, metadata: Mapping[str, Any]) -> None:
    connection.execute(
        """INSERT INTO audit_events
        (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
        VALUES (?,'model_platform','MP_SCOPE_OVERRIDE_SET_CREATED','mp_scope_override_set_version',?,NULL,NULL,?,?)""",
        (actor, override_set_version_id, summary, _json(dict(metadata))),
    )


def _object_json(value: object, error_code: str) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value) if value is not None else "{}")
    except (TypeError, ValueError) as error:
        raise DomainRuleError(error_code, "持久化 V2 参数对象不是合法 JSON。") from error
    if not isinstance(decoded, dict):
        raise DomainRuleError(error_code, "持久化 V2 参数对象必须是 JSON 对象。")
    return {str(key): item for key, item in decoded.items()}


def _mapping(value: object, error_code: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DomainRuleError(error_code, "Profile 参数策略必须是 JSON 对象。")
    return {str(key): item for key, item in value.items()}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
