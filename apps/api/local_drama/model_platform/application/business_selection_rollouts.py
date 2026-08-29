"""Auditable eligibility gates for one-way V1-to-V2 business cutovers.

The gate is deliberately *not* a feature flag that changes execution by
itself.  It records that an operator has approved a particular business
surface/capability/scope combination for the central migration Facade to
evaluate.  The Facade must still verify the current crosswalk, contracts,
readiness and handler before it can select V2.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.domain.capabilities import capability_definition

_LEGACY_SCOPE_TYPES = frozenset({"PROJECT", "EPISODE", "SHOT"})
_STATES = frozenset({"SHADOW", "CUTOVER_APPROVED"})


@dataclass(frozen=True, slots=True)
class BusinessSelectionRolloutRequest:
    business_surface: str
    capability_code: str
    scope_type: str
    state: str
    approval_reason: str
    approved_by: str


@dataclass(frozen=True, slots=True)
class BusinessSelectionRollout:
    id: str | None
    business_surface: str
    capability_code: str
    scope_type: str
    state: str
    approval_reason: str | None
    approved_by: str | None
    approved_at: str | None
    persisted: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "business_surface": self.business_surface,
            "capability_code": self.capability_code,
            "scope_type": self.scope_type,
            "state": self.state,
            "approval_reason": self.approval_reason,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "persisted": self.persisted,
            "execution_switched": False,
        }


class BusinessSelectionRolloutService:
    """Own the durable, reviewable cutover eligibility decision.

    A missing row is always SHADOW.  That default makes fresh deployments and
    newly added capabilities safe without needing a seeded deny-list.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def put(self, request: BusinessSelectionRolloutRequest) -> BusinessSelectionRollout:
        surface, capability, scope, state, reason, actor = _validate(request)
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM mp_capability_definitions WHERE code=?", (capability,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("MP_BUSINESS_ROLLOUT_CAPABILITY_NOT_FOUND", "Capability 不存在，不能设置业务迁移门禁。")
            capability_id = str(row["id"])
            existing = connection.execute(
                """SELECT id FROM mp_business_selection_rollouts
                WHERE business_surface=? AND capability_definition_id=? AND scope_type=?""",
                (surface, capability_id, scope),
            ).fetchone()
            rollout_id = str(existing["id"]) if existing else str(uuid.uuid4())
            if existing:
                connection.execute(
                    """UPDATE mp_business_selection_rollouts
                    SET state=?,approval_reason=?,approved_by=?,approved_at=?,updated_at=? WHERE id=?""",
                    (state, reason, actor, now, now, rollout_id),
                )
            else:
                connection.execute(
                    """INSERT INTO mp_business_selection_rollouts
                    (id,business_surface,capability_definition_id,scope_type,state,approval_reason,approved_by,approved_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (rollout_id, surface, capability_id, scope, state, reason, actor, now, now, now),
                )
            _audit(connection, actor, rollout_id, "MP_BUSINESS_SELECTION_ROLLOUT_SET", reason, {
                "business_surface": surface,
                "capability_code": capability,
                "scope_type": scope,
                "state": state,
            })
        return BusinessSelectionRollout(rollout_id, surface, capability, scope, state, reason, actor, now, True)

    def get(self, business_surface: str, capability_code: str, scope_type: str) -> BusinessSelectionRollout:
        surface, capability, scope = _validate_identity(business_surface, capability_code, scope_type)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT rollout.id,rollout.state,rollout.approval_reason,rollout.approved_by,rollout.approved_at
                FROM mp_business_selection_rollouts rollout
                JOIN mp_capability_definitions capability ON capability.id=rollout.capability_definition_id
                WHERE rollout.business_surface=? AND capability.code=? AND rollout.scope_type=?""",
                (surface, capability, scope),
            ).fetchone()
        if row is None:
            return BusinessSelectionRollout(None, surface, capability, scope, "SHADOW", None, None, None, False)
        return BusinessSelectionRollout(
            str(row["id"]), surface, capability, scope, str(row["state"]),
            str(row["approval_reason"]), str(row["approved_by"]), str(row["approved_at"]), True,
        )

    def list(self, *, business_surface: str | None = None) -> tuple[BusinessSelectionRollout, ...]:
        surface_filter = business_surface.strip() if business_surface and business_surface.strip() else None
        if surface_filter and len(surface_filter) > 80:
            raise DomainRuleError("MP_BUSINESS_ROLLOUT_SURFACE_INVALID", "业务页面标识长度无效。")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT rollout.id,rollout.business_surface,capability.code AS capability_code,rollout.scope_type,
                          rollout.state,rollout.approval_reason,rollout.approved_by,rollout.approved_at
                FROM mp_business_selection_rollouts rollout
                JOIN mp_capability_definitions capability ON capability.id=rollout.capability_definition_id
                WHERE (? IS NULL OR rollout.business_surface=?)
                ORDER BY rollout.business_surface,capability.code,rollout.scope_type""",
                (surface_filter, surface_filter),
            ).fetchall()
        return tuple(
            BusinessSelectionRollout(
                str(row["id"]), str(row["business_surface"]), str(row["capability_code"]), str(row["scope_type"]),
                str(row["state"]), str(row["approval_reason"]), str(row["approved_by"]), str(row["approved_at"]), True,
            )
            for row in rows
        )


def _validate(request: BusinessSelectionRolloutRequest) -> tuple[str, str, str, str, str, str]:
    surface, capability, scope = _validate_identity(request.business_surface, request.capability_code, request.scope_type)
    state = request.state.strip().upper()
    reason = request.approval_reason.strip()
    actor = request.approved_by.strip()
    if state not in _STATES:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_STATE_INVALID", "业务迁移门禁状态仅支持 SHADOW 或 CUTOVER_APPROVED。")
    if not reason or not actor:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_APPROVAL_REQUIRED", "设置业务迁移门禁必须填写审批理由和操作人。")
    if len(reason) > 500 or len(actor) > 120:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_APPROVAL_INVALID", "业务迁移门禁的审批理由或操作人长度无效。")
    return surface, capability, scope, state, reason, actor


def _validate_identity(business_surface: str, capability_code: str, scope_type: str) -> tuple[str, str, str]:
    surface = business_surface.strip()
    scope = scope_type.strip().upper()
    if not surface or len(surface) > 80:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_SURFACE_INVALID", "业务页面标识不能为空且最长 80 字符。")
    if scope not in _LEGACY_SCOPE_TYPES:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_SCOPE_INVALID", "业务迁移门禁仅支持 PROJECT、EPISODE 或 SHOT 继承层级。")
    try:
        definition = capability_definition(capability_code)
    except (KeyError, ValueError) as error:
        raise DomainRuleError("MP_BUSINESS_ROLLOUT_CAPABILITY_INVALID", "Capability 无法规范化，不能设置业务迁移门禁。") from error
    if surface not in definition.business_surfaces:
        raise DomainRuleError(
            "MP_BUSINESS_ROLLOUT_SURFACE_CAPABILITY_MISMATCH",
            "该 Capability 不属于指定业务页面，不能建立迁移门禁。",
        )
    return surface, definition.code, scope


def _audit(connection, actor: str, rollout_id: str, action: str, summary: str, metadata: dict[str, str]) -> None:
    connection.execute(
        """INSERT INTO audit_events
        (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
        VALUES (?,'model_platform',?,'mp_business_selection_rollout',?,NULL,NULL,?,?)""",
        (actor, action, rollout_id, summary, json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
