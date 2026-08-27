from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CameraPlanResolveRequest(BaseModel):
    shot_type: str = Field(min_length=1, max_length=80)
    movement: str = Field(min_length=1, max_length=200)
    direction: str = Field(min_length=1, max_length=80)
    intensity: float = Field(ge=0, le=1)
    curve: str = Field(min_length=1, max_length=80)
    prompt_text: str = Field(default="", max_length=2000)


class MediaImportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    purpose: str = "IMPORT"
    owner_type: str = "PROJECT"
    owner_id: str | None = None
    media_kind: str | None = None
    stage: str = "IMPORTED"


class MediaIntegrityRepairRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class DocumentImportRequest(BaseModel):
    source_path: str = Field(min_length=1)


class DocumentImportCommitRequest(BaseModel):
    expected_preview_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SourcePassageResponse(BaseModel):
    source_document_version_id: str
    source_start: int
    source_end: int
    requested_end: int
    offset_unit: Literal["UNICODE_CODEPOINT"]
    text: str
    text_sha256: str
    source_text_sha256: str
    total_character_count: int | None
    has_more: bool
    maximum_character_count: int
    read_only: Literal[True]


class BreakdownRequest(BaseModel):
    profile_version_id: str | None = None
    episode_id: str | None = Field(default=None, min_length=1)
    source_paragraph_start: int | None = Field(default=None, ge=1)
    source_paragraph_end: int | None = Field(default=None, ge=1)


class BreakdownDraftApplyRequest(BaseModel):
    episode_id: str = Field(min_length=1)
    scene_nos: list[int] | None = Field(default=None, min_length=1, max_length=500)


class BreakdownDraftShotRevisionInput(BaseModel):
    shot_no: int = Field(ge=1)
    visual: str = Field(default="", max_length=4000)
    action: str = Field(default="", max_length=4000)
    dialogue: Any = ""
    duration_seconds: float = Field(gt=0, le=3600)


class BreakdownDraftSceneRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    change_note: str = Field(min_length=2, max_length=500)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    characters: list[str] = Field(default_factory=list, max_length=100)
    shots: list[BreakdownDraftShotRevisionInput] = Field(min_length=1, max_length=500)


class ProfileBindingRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=120)
    profile_version_id: str = Field(min_length=1)
    confirm_candidate: bool = False


class ProfileEvidencePublishRequest(BaseModel):
    media_version_id: str = Field(min_length=1)
    workflow_version_id: str = Field(min_length=1)


class I2VEvidenceProbeSubmitRequest(BaseModel):
    profile_version_id: str = Field(min_length=1)
    workflow_version_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class I2VEvidenceKeyframePrepareRequest(BaseModel):
    source_media_version_id: str = Field(min_length=1)
    confirm_review_checks: Literal[True]


class I2VEvidenceProbeFinalizeRequest(BaseModel):
    job_id: str = Field(min_length=1)


class ProfileContractDraftRequest(BaseModel):
    expected_source_revision: int = Field(ge=1)
    input_contract: dict[str, Any]
    parameter_schema: dict[str, Any]
    output_contract: dict[str, Any]
    resource_policy: dict[str, Any]


class KeyframeCandidateRequest(BaseModel):
    shot_id: str = Field(min_length=1)


class ProductionPlanRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    plan: dict[str, Any] = Field(default_factory=dict)


class DeliveryTargetRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    transport: str
    spec: dict[str, Any] = Field(default_factory=dict)


class DeliveryTargetFromPresetRequest(BaseModel):
    """Create a LOCAL_FILESYSTEM delivery target by applying one built-in preset (G11 P1-6)."""

    preset_code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)


class DeliveryTargetVersionRequest(BaseModel):
    """Create a new immutable version for an existing delivery target.

    The target id/code remains stable while the complete delivery specification
    is captured in a new version.  No server-side preset is silently applied.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    transport: str = "LOCAL_FILESYSTEM"
    spec: dict[str, Any] = Field(default_factory=dict)
