from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput


class VariantInputRequest(BaseModel):
    role: str = Field(min_length=1, max_length=40)
    media_version_id: str = Field(min_length=1)
    ordinal: int = Field(default=0, ge=0, le=1000)
    weight: float | None = Field(default=None, ge=0.0, le=1.0)


class VariantPlanRequest(BaseModel):
    intent_id: str = Field(min_length=1)
    variant_type: str = Field(min_length=1, max_length=40)
    parent_variant_id: str | None = None
    branch_reason: str = Field(min_length=1, max_length=1000)
    prompt_revision_id: str | None = None
    profile_version_id: str = Field(min_length=1)
    parameter_set: dict[str, Any] = Field(default_factory=dict)
    seed_policy: str = Field(min_length=1, max_length=32)
    explicit_seed: int | None = None
    provider_random_nonce: str | None = Field(default=None, min_length=36, max_length=36)
    expected_effective_configuration_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    bindings: list[VariantInputRequest] = Field(default_factory=list, max_length=100)
    prompt_bundle: dict[str, Any] | None = None

    def to_domain(self) -> VariantPlan:
        return VariantPlan(
            variant_type=self.variant_type,
            parent_variant_id=self.parent_variant_id,
            branch_reason=self.branch_reason,
            prompt_revision_id=self.prompt_revision_id,
            profile_version_id=self.profile_version_id,
            parameter_set=self.parameter_set,
            seed_policy=self.seed_policy,
            explicit_seed=self.explicit_seed,
            bindings=tuple(VariantInput(item.role, item.media_version_id, item.ordinal, item.weight) for item in self.bindings),
            provider_random_nonce=self.provider_random_nonce,
            expected_effective_configuration_fingerprint=self.expected_effective_configuration_fingerprint,
            prompt_bundle=dict(self.prompt_bundle) if self.prompt_bundle is not None else None,
        )


class VariantCreateRequest(VariantPlanRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class VariantSubmitRequest(VariantCreateRequest):
    idempotency_key: str = Field(min_length=1, max_length=200)


class VariantDeriveRequest(BaseModel):
    operation: str = Field(pattern=r"^(RESAMPLE_NEW_SEED|RESUBMIT_PROVIDER_RANDOM|EXACT_REPLAY|PROMPT_BRANCH|SOURCE_IMAGE_BRANCH|PROFILE_BRANCH)$")
    explicit_seed: int | None = None
    prompt_revision_id: str | None = None
    first_frame_media_version_id: str | None = None
    profile_version_id: str | None = None
    branch_reason: str = Field(min_length=1, max_length=1000)


class VariantSeedBatchRequest(BaseModel):
    seeds: list[int] = Field(min_length=1, max_length=24)
    branch_reason: str = Field(min_length=1, max_length=1000)


RerollReasonCode = Literal[
    "USER_REROLL",
    "FACE_FIX",
    "IDENTITY_FIX",
    "COMPOSITION_FIX",
    "MOTION_FIX",
    "CONTINUITY_FIX",
    "FRAME_BRIDGE_FIX",
    "MODEL_COMPARE",
    "PROMPT_TUNE",
    "QC_AUTO_RETRY",
    "OTHER",
]
