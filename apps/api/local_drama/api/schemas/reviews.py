from __future__ import annotations

from pydantic import BaseModel, Field


class SelectionRequest(BaseModel):
    selection_type: str = Field(min_length=1)


class ReviewCheckRequest(BaseModel):
    item_id: str = Field(min_length=1)
    result: str = Field(min_length=1)
    comment: str | None = None


class ReviewRequest(BaseModel):
    template_version_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    expected_subject_revision: int = Field(ge=1)
    checks: list[ReviewCheckRequest]
    comment: str | None = None
    continuity_plan_hash: str | None = Field(default=None, min_length=64, max_length=64)


class MachineCheckRequest(BaseModel):
    policy_version: str = "g4_media_qc_v1"


class BatchItemRequest(BaseModel):
    media_version_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)


class BatchPreflightRequest(BaseModel):
    project_id: str = Field(min_length=1)
    items: list[BatchItemRequest]


class BatchCommitRequest(BaseModel):
    plan_token: str = Field(min_length=1)
    decision: str
    checks: list[ReviewCheckRequest]
    comment: str | None = None
