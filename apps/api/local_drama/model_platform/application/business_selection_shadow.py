"""Read-only dual resolution for the V1-to-V2 business selector migration.

Creator pages still submit legacy ``execution_profile_versions``.  V2
profiles deliberately use a different identity and execution contract, so
returning a V2 id to those pages would be unsafe.  This service calculates
both control planes without writing either one and makes the absence of a
version crosswalk explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import (
    CapabilityAssignmentService,
    CapabilityScopeContext,
)
from local_drama.model_platform.application.parameter_contract_comparison import compare_parameter_contracts


@dataclass(frozen=True, slots=True)
class BusinessSelectionShadow:
    """A safe projection of legacy and V2 selection for one business scope."""

    capability_code: str
    scope: Mapping[str, str | None]
    legacy_resolution: Mapping[str, object]
    legacy_project_binding: Mapping[str, object] | None
    v2_resolution: Mapping[str, object]
    profile_version_crosswalk: Mapping[str, object] | None
    parameter_contract_comparison: Mapping[str, object]
    comparison_status: str
    comparable: bool
    comparison_reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "capability_code": self.capability_code,
            "scope": dict(self.scope),
            "legacy_resolution": dict(self.legacy_resolution),
            "legacy_project_binding": dict(self.legacy_project_binding) if self.legacy_project_binding else None,
            "v2_resolution": dict(self.v2_resolution),
            "profile_version_crosswalk": dict(self.profile_version_crosswalk) if self.profile_version_crosswalk else None,
            "parameter_contract_comparison": dict(self.parameter_contract_comparison),
            "comparison": {
                "status": self.comparison_status,
                "comparable": self.comparable,
                "reason": self.comparison_reason,
                "profile_version_crosswalk_available": self.profile_version_crosswalk is not None,
            },
        }


class BusinessSelectionShadowService:
    """Compare the two resolvers without changing business task submission.

    V1 supports PROJECT, EPISODE and SHOT preferences.  A supplied CHARACTER
    scope is intentionally retained for V2 and reported as a semantic gap,
    never silently ignored as though the two answers were equivalent.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def compare(self, capability_code: str, scope: CapabilityScopeContext) -> BusinessSelectionShadow:
        project_id = (scope.project_id or "").strip()
        if not project_id:
            raise DomainRuleError(
                "MP_BUSINESS_SHADOW_PROJECT_REQUIRED",
                "业务选择双读必须提供 project_id；旧业务解析器以项目为根作用域。",
            )

        with self.database.connect() as connection:
            legacy = GenerationPreferenceQueryService(
                SqliteGenerationPreferenceRepository(connection)
            ).resolve(
                project_id=project_id,
                capability=capability_code,
                episode_id=_clean(scope.episode_id),
                shot_id=_clean(scope.shot_id),
            )
            legacy_binding = _legacy_project_binding(connection, project_id, str(legacy["capability"]))

        v2 = CapabilityAssignmentService(self.database).resolve(capability_code, scope)
        legacy_projection = _legacy_projection(legacy)
        v2_projection = {
            "execution_profile_version_id": v2.execution_profile_version_id,
            "resolution_reason": v2.resolution_reason,
            "assignment_chain": list(v2.assignment_chain),
            "blocked_reason": v2.blocked_reason,
            "ready": bool(v2.execution_profile_version_id and not v2.blocked_reason),
        }
        crosswalk = _approved_crosswalk(
            self.database,
            legacy_projection.get("execution_profile_version_id"),
            v2_projection.get("execution_profile_version_id"),
        )
        parameter_comparison = compare_parameter_contracts(
            self.database,
            str(legacy_projection["execution_profile_version_id"]),
            str(v2_projection["execution_profile_version_id"]),
        ).as_dict() if legacy_projection["execution_profile_version_id"] and v2_projection["execution_profile_version_id"] else {
            "status": "PROFILE_UNAVAILABLE",
            "matches": False,
            "legacy_field_count": 0,
            "v2_field_count": 0,
            "common_fields": [],
            "legacy_only_fields": [],
            "v2_only_fields": [],
            "differences": {"type": [], "required": [], "scope": [], "constraint": [], "default": []},
            "unsafe_field_names": [],
            "values_exposed": False,
        }
        status, comparable, reason = _comparison(
            legacy_projection,
            v2_projection,
            character_id=_clean(scope.character_id),
            crosswalk=crosswalk,
        )
        return BusinessSelectionShadow(
            capability_code=v2.capability_code,
            scope={
                "project_id": project_id,
                "episode_id": _clean(scope.episode_id),
                "shot_id": _clean(scope.shot_id),
                "character_id": _clean(scope.character_id),
            },
            legacy_resolution=legacy_projection,
            legacy_project_binding=legacy_binding,
            v2_resolution=v2_projection,
            profile_version_crosswalk=crosswalk,
            parameter_contract_comparison=parameter_comparison,
            comparison_status=status,
            comparable=comparable,
            comparison_reason=reason,
        )


def _legacy_project_binding(connection: Any, project_id: str, capability_code: str) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT binding.execution_profile_version_id,binding.status,version.status AS profile_status
        FROM project_profile_bindings binding
        LEFT JOIN execution_profile_versions version ON version.id=binding.execution_profile_version_id
        WHERE binding.project_id=? AND UPPER(TRIM(binding.capability))=?
        LIMIT 1""",
        (project_id, capability_code),
    ).fetchone()
    if row is None:
        return None
    return {
        "execution_profile_version_id": str(row["execution_profile_version_id"]),
        "binding_status": str(row["status"]),
        "profile_status": str(row["profile_status"]) if row["profile_status"] is not None else None,
    }


def _legacy_projection(resolution: Mapping[str, Any]) -> dict[str, object]:
    profile_version_id = resolution.get("profile_version_id")
    blocked_reason = resolution.get("blocked_reason")
    preference = resolution.get("preference")
    return {
        "execution_profile_version_id": str(profile_version_id) if profile_version_id else None,
        "source": str(resolution.get("source") or "AUTO"),
        "resolution_mode": (
            str(preference.get("resolution_mode"))
            if isinstance(preference, Mapping) and preference.get("resolution_mode")
            else "AUTO"
        ),
        "blocked_reason": str(blocked_reason) if blocked_reason else None,
        "ready": bool(profile_version_id and not blocked_reason),
    }


def _approved_crosswalk(
    database: Database,
    legacy_profile_version_id: object,
    v2_profile_version_id: object,
) -> dict[str, object] | None:
    if not legacy_profile_version_id or not v2_profile_version_id:
        return None
    with database.connect() as connection:
        row = connection.execute(
            """SELECT crosswalk.id,crosswalk.legacy_execution_profile_version_id,
                     crosswalk.v2_execution_profile_version_id,capability.code AS capability_code,
                     crosswalk.approval_reason,crosswalk.approved_by,crosswalk.approved_at
            FROM mp_legacy_profile_version_crosswalks crosswalk
            JOIN mp_capability_definitions capability ON capability.id=crosswalk.capability_definition_id
            WHERE crosswalk.legacy_execution_profile_version_id=?
              AND crosswalk.v2_execution_profile_version_id=? AND crosswalk.status='APPROVED'""",
            (str(legacy_profile_version_id), str(v2_profile_version_id)),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "legacy_execution_profile_version_id": str(row["legacy_execution_profile_version_id"]),
        "v2_execution_profile_version_id": str(row["v2_execution_profile_version_id"]),
        "capability_code": str(row["capability_code"]),
        "status": "APPROVED",
        "approval_reason": str(row["approval_reason"]),
        "approved_by": str(row["approved_by"]),
        "approved_at": str(row["approved_at"]),
    }


def _comparison(
    legacy: Mapping[str, object],
    v2: Mapping[str, object],
    *,
    character_id: str | None,
    crosswalk: Mapping[str, object] | None,
) -> tuple[str, bool, str]:
    if character_id:
        return (
            "LEGACY_SCOPE_UNSUPPORTED",
            False,
            "旧业务选择器没有 CHARACTER 作用域；结果只可用于观察，不能作为切换依据。",
        )
    legacy_id = legacy.get("execution_profile_version_id")
    v2_id = v2.get("execution_profile_version_id")
    if legacy_id and v2_id:
        if crosswalk:
            return (
                "MAPPED_EQUIVALENT",
                True,
                "存在人工批准、能力一致且两侧均为已发布 Profile 的版本映射；仍须在业务 surface 验证参数 preview 后才能切换。",
            )
        return (
            "BOTH_PRESENT_UNMAPPED",
            False,
            "两侧均解析到已发布 Profile，但 V1/V2 ProfileVersion 身份不同，尚未建立版本迁移映射，不能宣称等价。",
        )
    if legacy_id:
        return ("LEGACY_ONLY", True, "旧业务解析到 Profile，但 V2 没有可执行的已发布 Profile。")
    if v2_id:
        return ("V2_ONLY", True, "V2 解析到 Profile，但旧业务没有可执行的已发布 Profile。")
    return ("BOTH_BLOCKED", True, "两侧都没有可执行的已发布 Profile；请分别处理各自的阻塞原因。")


def _clean(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None
