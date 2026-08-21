"""Commands for immutable generation-preference version history."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.generation_preferences import GenerationPreferenceRepository
from local_drama.domain.capabilities import CANONICAL_CAPABILITIES, normalize_capability
from local_drama.domain.errors import DomainRuleError

CAPABILITIES = CANONICAL_CAPABILITIES


class GenerationPreferenceCommandService:
    def __init__(self, repository: GenerationPreferenceRepository) -> None:
        self.repository = repository

    def put(
        self, *, project_id: str, owner_type: str, owner_id: str, capability: str,
        resolution_mode: str, execution_profile_version_id: str | None = None,
        settings: dict[str, Any] | None = None, reason: str = "", actor: str = "local-user",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        owner_type = owner_type.upper()
        resolution_mode = resolution_mode.upper()
        try:
            capability = normalize_capability(capability)
        except ValueError as err:
            raise DomainRuleError("GENERATION_CAPABILITY_INVALID", "未知的生成能力", {"capability": capability}) from err

        if owner_type not in {"PROJECT", "EPISODE", "SHOT"}:
            raise DomainRuleError("GENERATION_PREFERENCE_OWNER_INVALID", "偏好 owner_type 必须是 PROJECT、EPISODE 或 SHOT")
        if resolution_mode not in {"AUTO", "EXPLICIT"}:
            raise DomainRuleError("GENERATION_PREFERENCE_MODE_INVALID", "resolution_mode 必须是 AUTO 或 EXPLICIT")
        resolved_project = self.repository.owner_project_id(owner_type, owner_id)
        if resolved_project != project_id:
            raise DomainRuleError("GENERATION_PREFERENCE_OWNER_NOT_FOUND", "偏好所有者不存在或不属于当前项目", {"owner_type": owner_type, "owner_id": owner_id})
        if resolution_mode == "AUTO" and execution_profile_version_id is not None:
            raise DomainRuleError("GENERATION_PREFERENCE_AUTO_PROFILE_FORBIDDEN", "AUTO 模式不能固定执行 Profile")
        if resolution_mode == "EXPLICIT" and not execution_profile_version_id:
            raise DomainRuleError("GENERATION_PREFERENCE_PROFILE_REQUIRED", "EXPLICIT 模式必须选择执行 Profile")
        if execution_profile_version_id:
            profile = self.repository.profile(execution_profile_version_id)
            if profile is None:
                raise DomainRuleError("EXECUTION_PROFILE_VERSION_NOT_FOUND", "执行 Profile 版本不存在")
            if str(profile["status"]) != "PUBLISHED":
                raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只能选择已发布的执行 Profile")
            try:
                profile_cap = normalize_capability(str(profile["capability"]))
            except ValueError:
                profile_cap = str(profile["capability"]).upper()
            if profile_cap != capability:
                raise DomainRuleError(
                    "GENERATION_PREFERENCE_CAPABILITY_MISMATCH", "执行 Profile 不支持所选能力",
                    {"requested": capability, "profile_capability": profile["capability"]},
                )
        return self.repository.put_preference(
            project_id=project_id, owner_type=owner_type, owner_id=owner_id, capability=capability,
            execution_profile_version_id=execution_profile_version_id, resolution_mode=resolution_mode,
            settings=settings or {}, reason=reason.strip(), actor=actor, expected_revision=expected_revision,
        )
