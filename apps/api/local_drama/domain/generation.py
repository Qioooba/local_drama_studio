from __future__ import annotations

import uuid
from dataclasses import dataclass

from .errors import DomainRuleError
from .policies import VariantInput, validate_binding_roles, validate_variant_lineage

VALID_VARIANT_TYPES = {
    "BASE",
    "RESAMPLE_NEW_SEED",
    "RESUBMIT_PROVIDER_RANDOM",
    "EXACT_REPLAY",
    "PROMPT_BRANCH",
    "SOURCE_IMAGE_BRANCH",
    "FIRST_LAST_KEYFRAMES",
    "MULTI_KEYFRAME",
    "REFERENCE_BRANCH",
    "PARAMETER_BRANCH",
    "PROFILE_BRANCH",
    "VIDEO_EXTEND",
    "VIDEO_TO_VIDEO",
    "MOTION_CONTROL",
    "PERFORMANCE_DRIVEN",
}


@dataclass(frozen=True)
class VariantPlan:
    variant_type: str
    parent_variant_id: str | None
    branch_reason: str
    prompt_revision_id: str | None
    profile_version_id: str
    parameter_set: dict[str, object]
    seed_policy: str
    explicit_seed: int | None
    bindings: tuple[VariantInput, ...]
    provider_random_nonce: str | None = None

    def validate(self, *, variant_id: str, ancestors: set[str], allowed_roles: set[str]) -> None:
        if self.variant_type not in VALID_VARIANT_TYPES:
            raise DomainRuleError("INVALID_VARIANT_TYPE", "GenerationVariant 类型不受支持")
        validate_variant_lineage(self.parent_variant_id, variant_id, ancestors)
        validate_binding_roles(list(self.bindings), allowed_roles)
        binding_keys = [(binding.role, binding.ordinal) for binding in self.bindings]
        if len(binding_keys) != len(set(binding_keys)):
            raise DomainRuleError("DUPLICATE_INPUT_BINDING", "同一语义角色和 ordinal 只能绑定一个输入")
        if self.seed_policy == "EXPLICIT" and self.explicit_seed is None:
            raise DomainRuleError("SEED_REQUIRED", "EXPLICIT seed policy 必须提供 explicit_seed")
        if self.seed_policy == "PROVIDER_RANDOM":
            if self.explicit_seed is not None or not self.provider_random_nonce:
                raise DomainRuleError("PROVIDER_RANDOM_SNAPSHOT_INVALID", "Provider random 必须冻结 nonce 且不能携带 explicit seed")
            try:
                uuid.UUID(self.provider_random_nonce)
            except ValueError as error:
                raise DomainRuleError("PROVIDER_RANDOM_NONCE_INVALID", "Provider random nonce 必须是有效 UUID") from error
        elif self.provider_random_nonce is not None:
            raise DomainRuleError("PROVIDER_RANDOM_NONCE_UNEXPECTED", "非 Provider random Variant 不能携带随机 nonce")
        if self.variant_type == "RESUBMIT_PROVIDER_RANDOM" and self.seed_policy != "PROVIDER_RANDOM":
            raise DomainRuleError("PROVIDER_RANDOM_POLICY_REQUIRED", "Provider random 重提必须使用 PROVIDER_RANDOM seed policy")
        if self.variant_type == "EXACT_REPLAY" and not self.parent_variant_id:
            raise DomainRuleError("REPLAY_PARENT_REQUIRED", "EXACT_REPLAY 必须引用父 variant")
