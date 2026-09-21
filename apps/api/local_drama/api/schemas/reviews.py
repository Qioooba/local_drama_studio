from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewTemplateItemRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    required: bool = True


class ReviewTemplateVersionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    subject_type: str = Field(min_length=1, max_length=40)
    items: list[ReviewTemplateItemRequest] = Field(min_length=1)


class MachineCheckRequest(BaseModel):
    policy_version: str = "g4_media_qc_v1"


class ReviewCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=80)
    result: str = Field(pattern="^(PASS|FAIL|NOT_APPLICABLE)$")
    comment: str | None = Field(default=None, max_length=1000)


class EpisodeRenderReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_version_id: str = Field(min_length=1, max_length=64)
    decision: str = Field(pattern="^(APPROVED|REJECTED|NEEDS_CHANGES)$")
    expected_subject_revision: int = Field(ge=1)
    checks: list[ReviewCheckRequest] = Field(min_length=1, max_length=30)
    comment: str | None = Field(default=None, max_length=2000)


class EpisodeRenderBatchReviewItemRequest(BaseModel):
    """One independently reviewed render in an atomic episode-review batch."""

    model_config = ConfigDict(extra="forbid")

    render_id: str = Field(min_length=1, max_length=64)
    template_version_id: str = Field(min_length=1, max_length=64)
    decision: str = Field(pattern="^(APPROVED|REJECTED|NEEDS_CHANGES)$")
    expected_subject_revision: int = Field(ge=1)
    checks: list[ReviewCheckRequest] = Field(min_length=1, max_length=30)
    comment: str | None = Field(default=None, max_length=2000)


class EpisodeRenderBatchReviewPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EpisodeRenderBatchReviewItemRequest] = Field(min_length=1, max_length=200)


class EpisodeRenderBatchReviewCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_token: str = Field(min_length=20, max_length=200)
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")


class EpisodeRenderReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subject_type: Literal["EPISODE_RENDER_VERSION"]
    subject_id: str
    decision: Literal["APPROVED", "REJECTED", "NEEDS_CHANGES"]
    is_stale: bool
    subject_revision: int = Field(ge=1)


class EpisodeRenderReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: EpisodeRenderReviewRecord


class EpisodeRenderBatchReviewCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    result: Literal["PASS", "FAIL", "NOT_APPLICABLE"]
    comment: str | None = None


class EpisodeRenderBatchReviewPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_id: str
    episode_id: str
    episode_code: str
    render_revision: int = Field(ge=1)
    render_sha256: str
    root_compose_render_id: str
    root_compose_revision: int = Field(ge=1)
    root_compose_sha256: str
    machine_check_run_id: str | None
    machine_check_status: str | None
    machine_check_policy_version: str | None
    template_version_id: str
    template_version_no: int = Field(ge=1)
    template_items_sha256: str
    previous_review_id: str | None
    decision: Literal["APPROVED", "REJECTED", "NEEDS_CHANGES"]
    checks: list[EpisodeRenderBatchReviewCheck]
    comment: str | None


class EpisodeRenderBatchReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    plan_token: str
    plan_hash: str
    expires_at: str
    status: Literal["READY"]
    items: list[EpisodeRenderBatchReviewPlanItem]
    would_create_review_count: int = Field(ge=1)
    mutated_reviews: Literal[False]


class EpisodeRenderBatchReviewPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: EpisodeRenderBatchReviewPlan


class EpisodeRenderBatchReviewCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    plan_hash: str
    status: Literal["COMMITTED"]
    items: list[EpisodeRenderReviewRecord]
    review_count: int = Field(ge=1)
    atomic: Literal[True]


class EpisodeRenderBatchReviewCommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: EpisodeRenderBatchReviewCommit
