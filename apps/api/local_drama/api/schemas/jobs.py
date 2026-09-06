from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    type: str = Field(min_length=1, max_length=80)
    subject_type: str = Field(min_length=1, max_length=40)
    subject_id: str = Field(min_length=1)
    channel: str = Field(min_length=1, max_length=40)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    execution_profile_version_id: str | None = None
    priority: int = Field(default=100, ge=0, le=10000)
    max_attempts: int = Field(default=3, ge=1, le=20)
    depends_on_job_ids: list[str] = Field(default_factory=list)


class JobClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    channels: list[str] = Field(default_factory=list)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class JobHeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    lease_token: str = Field(min_length=1)
    progress: dict[str, Any] = Field(default_factory=dict)
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class JobCompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    lease_token: str = Field(min_length=1)
    success: bool
    error_code: str | None = None
    error_detail_redacted: str | None = None
    provider_job_id: str | None = None


class ArtifactRegisterRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    sandbox_rel_path: str = Field(min_length=1)


class ArtifactPromoteRequest(BaseModel):
    purpose: str = Field(default="GENERATED_OUTPUT", min_length=1, max_length=64)
    media_kind: str | None = None
    stage: str = Field(default="PROXY", min_length=1, max_length=24)


class ArtifactImageTransformRequest(BaseModel):
    transform: str = Field(default="HORIZONTAL_MIRROR", pattern="^HORIZONTAL_MIRROR$")
    purpose: str = Field(default="ASSET_REFERENCE", min_length=1, max_length=64)
    stage: str = Field(default="KEYFRAME", min_length=1, max_length=24)


class JobCloneRequest(BaseModel):
    input_overrides: dict[str, Any] = Field(default_factory=dict)


class JobBatchActionRequest(BaseModel):
    job_ids: list[str] | None = None
    project_id: str | None = None


class JobBatchDeleteRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
