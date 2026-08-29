"""Explicit, auditable migration mapping between legacy and V2 Profiles."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.parameter_contract_comparison import compare_parameter_contracts


@dataclass(frozen=True, slots=True)
class ProfileVersionCrosswalkRequest:
    legacy_execution_profile_version_id: str
    v2_execution_profile_version_id: str
    approval_reason: str
    approved_by: str


@dataclass(frozen=True, slots=True)
class ProfileVersionCrosswalk:
    id: str
    legacy_execution_profile_version_id: str
    v2_execution_profile_version_id: str
    capability_code: str
    status: str
    approval_reason: str
    approved_by: str
    approved_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "legacy_execution_profile_version_id": self.legacy_execution_profile_version_id,
            "v2_execution_profile_version_id": self.v2_execution_profile_version_id,
            "capability_code": self.capability_code,
            "status": self.status,
            "approval_reason": self.approval_reason,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }


class ProfileVersionCrosswalkService:
    """Never infer semantic equivalence from titles, IDs, model names or paths."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def approve(self, request: ProfileVersionCrosswalkRequest) -> ProfileVersionCrosswalk:
        legacy_id = request.legacy_execution_profile_version_id.strip()
        v2_id = request.v2_execution_profile_version_id.strip()
        reason = request.approval_reason.strip()
        actor = request.approved_by.strip()
        if not legacy_id or not v2_id or not reason or not actor:
            raise DomainRuleError("MP_PROFILE_CROSSWALK_FIELDS_REQUIRED", "映射的 Profile、批准理由和操作人均不能为空。")
        now = _utc_now()
        with self.database.transaction() as connection:
            legacy = connection.execute(
                """SELECT version.id,version.capability,version.status
                FROM execution_profile_versions version WHERE version.id=?""",
                (legacy_id,),
            ).fetchone()
            if legacy is None or str(legacy["status"]) != "PUBLISHED":
                raise DomainRuleError("MP_PROFILE_CROSSWALK_LEGACY_NOT_PUBLISHED", "旧 ProfileVersion 必须存在且为 PUBLISHED。")
            try:
                legacy_capability = normalize_capability(str(legacy["capability"]))
            except ValueError as error:
                raise DomainRuleError("MP_PROFILE_CROSSWALK_LEGACY_CAPABILITY_UNKNOWN", "旧 Profile 的能力无法规范化。") from error
            v2 = connection.execute(
                """SELECT version.id,capability.id AS capability_id,capability.code
                FROM mp_execution_profile_versions version
                JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
                WHERE version.id=? AND publication.status='PUBLISHED'""",
                (v2_id,),
            ).fetchone()
            if v2 is None:
                raise DomainRuleError("MP_PROFILE_CROSSWALK_V2_NOT_PUBLISHED", "V2 ProfileVersion 必须存在且为 PUBLISHED。")
            if str(v2["code"]) != legacy_capability:
                raise DomainRuleError(
                    "MP_PROFILE_CROSSWALK_CAPABILITY_MISMATCH",
                    "旧/V2 Profile 的规范能力不一致，不能建立映射。",
                )
            parameter_comparison = compare_parameter_contracts(self.database, legacy_id, v2_id)
            if not parameter_comparison.matches:
                raise DomainRuleError(
                    "MP_PROFILE_CROSSWALK_PARAMETER_CONTRACT_MISMATCH",
                    "旧/V2 Profile 的参数合同形状或有效默认值不一致，不能批准直接切换映射。",
                    {"comparison": parameter_comparison.as_dict()},
                )
            existing = connection.execute(
                """SELECT id FROM mp_legacy_profile_version_crosswalks
                WHERE status='APPROVED' AND (
                    legacy_execution_profile_version_id=? OR v2_execution_profile_version_id=?
                )""",
                (legacy_id, v2_id),
            ).fetchone()
            if existing is not None:
                raise DomainRuleError(
                    "MP_PROFILE_CROSSWALK_ACTIVE_MAPPING_EXISTS",
                    "旧或 V2 ProfileVersion 已有生效映射；请先撤销后再建立新的批准映射。",
                )
            crosswalk_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_legacy_profile_version_crosswalks
                (id,legacy_execution_profile_version_id,v2_execution_profile_version_id,capability_definition_id,status,
                 approval_reason,approved_by,approved_at,created_at,updated_at)
                VALUES (?,?,?,?, 'APPROVED',?,?,?,?,?)""",
                (crosswalk_id, legacy_id, v2_id, str(v2["capability_id"]), reason, actor, now, now, now),
            )
            _audit(connection, actor, "MP_PROFILE_CROSSWALK_APPROVED", crosswalk_id, reason, {
                "legacy_execution_profile_version_id": legacy_id,
                "v2_execution_profile_version_id": v2_id,
                "capability_code": legacy_capability,
            })
        return ProfileVersionCrosswalk(crosswalk_id, legacy_id, v2_id, legacy_capability, "APPROVED", reason, actor, now)

    def revoke(self, crosswalk_id: str, *, revocation_reason: str, revoked_by: str) -> None:
        reason = revocation_reason.strip()
        actor = revoked_by.strip()
        if not reason or not actor:
            raise DomainRuleError("MP_PROFILE_CROSSWALK_FIELDS_REQUIRED", "撤销理由和操作人不能为空。")
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM mp_legacy_profile_version_crosswalks WHERE id=?", (crosswalk_id,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("MP_PROFILE_CROSSWALK_NOT_FOUND", "ProfileVersion 映射不存在。")
            if str(row["status"]) != "APPROVED":
                raise DomainRuleError("MP_PROFILE_CROSSWALK_NOT_APPROVED", "只有生效映射可以撤销。")
            connection.execute(
                """UPDATE mp_legacy_profile_version_crosswalks
                SET status='REVOKED',revoked_by=?,revocation_reason=?,revoked_at=?,updated_at=? WHERE id=?""",
                (actor, reason, now, now, crosswalk_id),
            )
            _audit(connection, actor, "MP_PROFILE_CROSSWALK_REVOKED", crosswalk_id, reason, {})


def _audit(connection: sqlite3.Connection, actor: str, action: str, crosswalk_id: str, summary: str, metadata: dict[str, str]) -> None:
    connection.execute(
        """INSERT INTO audit_events
        (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
        VALUES (?,'model_platform',?,'mp_profile_version_crosswalk',?,NULL,NULL,?,?)""",
        (actor, action, crosswalk_id, summary, json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
