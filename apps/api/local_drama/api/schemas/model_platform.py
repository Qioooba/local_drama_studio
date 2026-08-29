"""HTTP contracts for the V2 Model Platform control plane."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapabilityAssignmentPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: Literal["SYSTEM", "PROJECT", "EPISODE", "SHOT", "CHARACTER"]
    scope_id: str = Field(default="", max_length=36)
    capability_code: str = Field(min_length=1, max_length=80)
    resolution_mode: Literal["AUTO", "EXPLICIT"]
    execution_profile_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    overrides: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)
    actor: str = Field(default="local-user", min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_mode_and_scope(self) -> "CapabilityAssignmentPutRequest":
        if self.scope_type == "SYSTEM" and self.scope_id:
            raise ValueError("SYSTEM scope must use an empty scope_id")
        if self.scope_type != "SYSTEM" and not self.scope_id:
            raise ValueError("non-SYSTEM scope requires scope_id")
        if self.resolution_mode == "EXPLICIT" and self.execution_profile_version_id is None:
            raise ValueError("EXPLICIT mode requires execution_profile_version_id")
        if self.resolution_mode == "AUTO" and self.execution_profile_version_id is not None:
            raise ValueError("AUTO mode cannot bind execution_profile_version_id")
        if self.overrides and not self.reason.strip():
            raise ValueError("scope overrides require an audit reason")
        return self


class ModelPlatformExecutionPreviewRequest(BaseModel):
    """Business-only V2 execution input; runtime wiring stays server-side."""

    model_config = ConfigDict(extra="forbid")

    capability_code: str = Field(min_length=1, max_length=80)
    project_id: str | None = Field(default=None, max_length=36)
    episode_id: str | None = Field(default=None, max_length=36)
    shot_id: str | None = Field(default=None, max_length=36)
    character_id: str | None = Field(default=None, max_length=36)
    semantic_inputs: dict[str, Any] = Field(default_factory=dict)
    run_overrides: dict[str, Any] = Field(default_factory=dict)
    expected_resolution_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ModelPlatformQuickCreateV2DirectImagePreviewRequest(BaseModel):
    """The first explicit V2-only Quick Create surface; no legacy IDs accepted."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=2, max_length=2000)
    run_overrides: dict[str, Any] = Field(default_factory=dict)


class ModelPlatformQuickCreateV2DirectImageSubmitRequest(ModelPlatformQuickCreateV2DirectImagePreviewRequest):
    expected_resolution_hash: str = Field(min_length=64, max_length=64)


class ModelPlatformQuickCreateV2ImageCandidatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=2, max_length=2000)
    candidate_count: int = Field(ge=1, le=8)


class ModelPlatformQuickCreateV2ImageCandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=1, le=8)
    seed: int = Field(ge=0, lt=2_147_483_647)
    resolution_hash: str = Field(min_length=64, max_length=64)


class ModelPlatformQuickCreateV2ImageCandidateSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=2, max_length=2000)
    candidates: list[ModelPlatformQuickCreateV2ImageCandidateItem] = Field(min_length=1, max_length=8)


class ModelPlatformQuickCreateV2ImageToVideoSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_resolution_hash: str = Field(min_length=64, max_length=64)


class ModelPlatformProfilePublishRequest(BaseModel):
    """Explicit publication confirmation; a successful real smoke is required."""

    model_config = ConfigDict(extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=500)


class ModelPlatformComfyWorkflowBindingRequest(BaseModel):
    """Reference an already published immutable workflow; no graph is sent from UI."""

    model_config = ConfigDict(extra="forbid")

    workflow_version_id: str = Field(min_length=1, max_length=36)


class ModelPlatformComfySmokeSubmissionRequest(BaseModel):
    """Submit one already schema-validated binding to the bounded GPU smoke queue."""

    model_config = ConfigDict(extra="forbid")

    workflow_binding_id: str = Field(min_length=1, max_length=36)


class ModelPlatformProfileProvisionRequest(BaseModel):
    """Comfy needs an explicit real-smoke binding; other runtimes need none."""

    model_config = ConfigDict(extra="forbid")

    workflow_binding_id: str | None = Field(default=None, min_length=1, max_length=36)


class ModelPlatformProfileCrosswalkApprovalRequest(BaseModel):
    """A human-reviewed V1-to-V2 ProfileVersion migration decision."""

    model_config = ConfigDict(extra="forbid")

    legacy_execution_profile_version_id: str = Field(min_length=1, max_length=36)
    v2_execution_profile_version_id: str = Field(min_length=1, max_length=36)
    approval_reason: str = Field(min_length=1, max_length=500)
    approved_by: str = Field(min_length=1, max_length=120)


class ModelPlatformProfileCrosswalkRevocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revocation_reason: str = Field(min_length=1, max_length=500)
    revoked_by: str = Field(min_length=1, max_length=120)


class ModelPlatformBusinessSelectionRolloutPutRequest(BaseModel):
    """Operator approval to let the migration Facade evaluate a V2 cutover."""

    model_config = ConfigDict(extra="forbid")

    business_surface: str = Field(min_length=1, max_length=80)
    capability_code: str = Field(min_length=1, max_length=80)
    scope_type: Literal["PROJECT", "EPISODE", "SHOT"]
    state: Literal["SHADOW", "CUTOVER_APPROVED"]
    approval_reason: str = Field(min_length=1, max_length=500)
    approved_by: str = Field(min_length=1, max_length=120)


class ModelPlatformProjectKnowledgeIndexPrepareRequest(BaseModel):
    """Prepare server-owned source text for a V2 Embedding index run."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    source_document_version_id: str = Field(min_length=1, max_length=36)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ModelPlatformProjectKnowledgeSearchRequest(BaseModel):
    """Server-only semantic query; clients never provide an embedding vector."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=8192)
    limit: int = Field(default=8, ge=1, le=20)


class ModelPlatformExpectedInstallArtifact(BaseModel):
    """A browser may declare integrity facts, never a server-side source path."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(gt=0)


class ModelPlatformOfflineImportPlanRequest(BaseModel):
    """Create a durable plan only; Host-owned import is a separate operation."""

    model_config = ConfigDict(extra="forbid")

    target_library_id: str = Field(min_length=1, max_length=36)
    release_code: str = Field(min_length=1, max_length=140)
    bundle_reference: str = Field(min_length=1, max_length=120)
    license_id: str = Field(min_length=1, max_length=200)
    expected_artifacts: list[ModelPlatformExpectedInstallArtifact] = Field(min_length=1, max_length=100)


class ModelPlatformTrustedDownloadArtifact(ModelPlatformExpectedInstallArtifact):
    """A planned HTTPS source, validated against the machine-owned allowlist."""

    source_url: str = Field(min_length=12, max_length=2048)


class ModelPlatformTrustedDownloadPlanRequest(BaseModel):
    """Persist a download plan only; the Windows Host performs network I/O later."""

    model_config = ConfigDict(extra="forbid")

    target_library_id: str = Field(min_length=1, max_length=36)
    release_code: str = Field(min_length=1, max_length=140)
    bundle_reference: str = Field(min_length=1, max_length=120)
    license_id: str = Field(min_length=1, max_length=200)
    artifacts: list[ModelPlatformTrustedDownloadArtifact] = Field(min_length=1, max_length=100)
