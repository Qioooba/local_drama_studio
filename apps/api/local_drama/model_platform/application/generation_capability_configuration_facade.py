"""One fail-closed decision boundary for a future V1-to-V2 creator cutover.

This service is intentionally observational today.  It owns the complete
eligibility decision, but it always returns the V1 resolution as the execution
owner until a separately delivered creator command calls a cutover-only entry
point.  That prevents a page-level feature flag from accidentally routing a
legacy Job to a V2 Profile identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.business_selection_rollouts import BusinessSelectionRolloutService
from local_drama.model_platform.application.business_selection_shadow import BusinessSelectionShadowService
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerRegistry
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers
from local_drama.model_platform.domain.capabilities import capability_definition


@dataclass(frozen=True, slots=True)
class GenerationCapabilityConfigurationEvaluation:
    """Audit-friendly eligibility result that never changes execution owner."""

    business_surface: str
    capability_code: str
    scope_type: str
    legacy_execution_profile_version_id: str | None
    v2_execution_profile_version_id: str | None
    rollout_state: str
    decision: str
    blockers: tuple[str, ...]
    execution_owner: str = "LEGACY_V1"
    execution_switched: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "business_surface": self.business_surface,
            "capability_code": self.capability_code,
            "scope_type": self.scope_type,
            "legacy_execution_profile_version_id": self.legacy_execution_profile_version_id,
            "v2_execution_profile_version_id": self.v2_execution_profile_version_id,
            "rollout_state": self.rollout_state,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "execution_owner": self.execution_owner,
            "execution_switched": self.execution_switched,
        }


class GenerationCapabilityConfigurationFacade:
    """Centralize migration eligibility; do not submit or rewrite V1 jobs."""

    def __init__(self, database: Database, handlers: ExecutionHandlerRegistry | None = None) -> None:
        self.database = database
        self.handlers = handlers or production_execution_handlers()
        self.shadow = BusinessSelectionShadowService(database)
        self.rollouts = BusinessSelectionRolloutService(database)
        self.planning = ExecutionPlanningService(database)

    def evaluate(
        self,
        *,
        business_surface: str,
        capability_code: str,
        scope: CapabilityScopeContext,
    ) -> GenerationCapabilityConfigurationEvaluation:
        surface = business_surface.strip()
        try:
            definition = capability_definition(capability_code)
        except (KeyError, ValueError) as error:
            raise DomainRuleError("MP_BUSINESS_FACADE_CAPABILITY_INVALID", "业务 Facade 无法规范化 Capability。") from error
        if not surface or surface not in definition.business_surfaces:
            raise DomainRuleError(
                "MP_BUSINESS_FACADE_SURFACE_CAPABILITY_MISMATCH",
                "该 Capability 不属于指定业务页面，不能进入迁移 Facade。",
            )

        shadow = self.shadow.compare(definition.code, scope)
        scope_type = _effective_scope_type(scope)
        blockers: list[str] = []
        if scope_type == "CHARACTER":
            rollout_state = "SHADOW"
            blockers.append("LEGACY_SCOPE_UNSUPPORTED")
        else:
            rollout = self.rollouts.get(surface, definition.code, scope_type)
            rollout_state = rollout.state
            if rollout.state != "CUTOVER_APPROVED":
                blockers.append("ROLLOUT_NOT_APPROVED")

        if not shadow.comparable:
            blockers.append(f"SHADOW_{shadow.comparison_status}")
        if not bool(shadow.parameter_contract_comparison.get("matches")):
            blockers.append("PARAMETER_CONTRACT_MISMATCH")

        legacy_id = _profile_id(shadow.legacy_resolution)
        v2_id = _profile_id(shadow.v2_resolution)
        if not legacy_id:
            blockers.append("LEGACY_PROFILE_UNAVAILABLE")
        if not v2_id:
            blockers.append("V2_PROFILE_UNAVAILABLE")
        else:
            blockers.extend(self._v2_execution_blockers(definition.code, scope))

        unique_blockers = tuple(dict.fromkeys(blockers))
        return GenerationCapabilityConfigurationEvaluation(
            business_surface=surface,
            capability_code=definition.code,
            scope_type=scope_type,
            legacy_execution_profile_version_id=legacy_id,
            v2_execution_profile_version_id=v2_id,
            rollout_state=rollout_state,
            decision="CUTOVER_CANDIDATE" if not unique_blockers else "LEGACY_ONLY",
            blockers=unique_blockers,
        )

    def _v2_execution_blockers(self, capability_code: str, scope: CapabilityScopeContext) -> tuple[str, ...]:
        try:
            preview = self.planning.preview(ExecutionPreviewRequest(
                capability_code=capability_code,
                scope=scope,
                semantic_inputs={},
                run_overrides={},
            ))
            if not preview.executable or preview.adapter_code is None:
                return tuple(preview.blockers or ("V2_EXECUTION_NOT_READY",))
            self.handlers.resolve(preview.capability_code, preview.adapter_code)
            return ()
        except DomainRuleError as error:
            return (error.code,)


def _effective_scope_type(scope: CapabilityScopeContext) -> str:
    if (scope.character_id or "").strip():
        return "CHARACTER"
    if (scope.shot_id or "").strip():
        return "SHOT"
    if (scope.episode_id or "").strip():
        return "EPISODE"
    return "PROJECT"


def _profile_id(resolution: Mapping[str, object]) -> str | None:
    value = resolution.get("execution_profile_version_id")
    return str(value) if value else None
