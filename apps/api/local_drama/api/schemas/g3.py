from __future__ import annotations

from typing import Any

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


class DocumentImportRequest(BaseModel):
    source_path: str = Field(min_length=1)


class BreakdownRequest(BaseModel):
    profile_version_id: str | None = None


class ProfileBindingRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=120)
    profile_version_id: str = Field(min_length=1)
    confirm_candidate: bool = False


class ProfileEvidencePublishRequest(BaseModel):
    media_version_id: str = Field(min_length=1)
    workflow_version_id: str = Field(min_length=1)


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
