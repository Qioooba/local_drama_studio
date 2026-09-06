from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import StrictModel

PostState = Literal["EMPTY", "READY", "ATTENTION", "BLOCKED"]
ReviewTargetKind = Literal["MEDIA_VERSION", "EPISODE_RENDER_VERSION"]
ReviewAnnotationCategory = Literal[
    "IDENTITY", "MOTION", "ARTIFACT", "FLICKER", "AUDIO_SYNC", "SUBTITLE", "CONTINUITY", "OTHER"
]


class PostBlockerFact(StrictModel):
    code: str
    message: str
    owner_route: Literal["REVIEW", "POST_AUDIO", "POST_EDIT", "DELIVERY"]
    repair_action: str


class PostReviewSummary(StrictModel):
    state: PostState
    target_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    approved_render_id: str | None = None


class PostAudioSummary(StrictModel):
    state: PostState
    dialogue_line_count: int = Field(ge=0)
    adopted_tts_count: int = Field(ge=0)
    binding_count: int = Field(ge=0)
    verified_license_count: int = Field(ge=0)


class PostEditSummary(StrictModel):
    state: PostState
    timeline_revision_count: int = Field(ge=0)
    latest_timeline_id: str | None = None
    latest_timeline_revision_no: int | None = None
    latest_timeline_status: str | None = None
    frozen_timeline_id: str | None = None
    subtitle_revision_count: int = Field(ge=0)
    latest_subtitle_id: str | None = None


class PostDeliverySummary(StrictModel):
    state: PostState
    render_count: int = Field(ge=0)
    verified_render_count: int = Field(ge=0)
    latest_render_id: str | None = None
    latest_render_revision: int | None = None
    latest_render_integrity: str | None = None
    package_count: int = Field(ge=0)
    latest_package_id: str | None = None
    latest_package_status: str | None = None


class EpisodePostOverviewFact(StrictModel):
    episode_id: str
    project_id: str
    episode_code: str
    episode_title: str | None = None
    next_action: str
    review: PostReviewSummary
    audio: PostAudioSummary
    edit: PostEditSummary
    delivery: PostDeliverySummary
    blockers: list[PostBlockerFact]
    allowed_actions: list[str]


class EpisodePostOverviewResponse(StrictModel):
    overview: EpisodePostOverviewFact
    read_only: Literal[True]
    request_shape: Literal["episode_post_overview_v2"]


class ReviewTemplateItemFact(StrictModel):
    id: str
    label: str
    required: bool


class ReviewTargetFact(StrictModel):
    target_kind: ReviewTargetKind
    target_id: str
    project_id: str
    episode_id: str
    shot_id: str | None = None
    label: str
    media_kind: str | None = None
    stage: str | None = None
    is_adopted: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    subject_revision: int = Field(ge=0)
    integrity_status: str
    machine_status: str | None = None
    template_version_id: str
    template_code: str
    template_items: list[ReviewTemplateItemFact]
    latest_decision_id: str | None = None
    latest_decision: str | None = None
    latest_decision_revision: int | None = None
    latest_decision_stale: bool
    blocker_codes: list[str]
    allowed_actions: list[str]
    created_at: str


class ReviewTargetPage(StrictModel):
    items: list[ReviewTargetFact]
    cursor: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_cursor: int | None = None
    target_kinds: list[ReviewTargetKind]
    include_resolved: bool
    read_only: Literal[True]
    request_shape: Literal["bounded_episode_review_targets_v2"]


class ReviewDecisionCheckCommand(StrictModel):
    item_id: str = Field(min_length=1, max_length=120)
    result: Literal["PASS", "FAIL", "NA"]
    comment: str | None = Field(default=None, max_length=2000)


class ReviewDecisionCreateCommand(StrictModel):
    target_kind: ReviewTargetKind
    target_id: str = Field(min_length=1, max_length=200)
    template_version_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)
    decision: Literal["APPROVED", "REJECTED", "NEEDS_CHANGES"]
    checks: list[ReviewDecisionCheckCommand] = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReviewDecisionRevokeCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReviewDecisionWriteFact(StrictModel):
    id: str
    target_kind: ReviewTargetKind
    target_id: str
    decision: Literal["APPROVED", "REJECTED", "NEEDS_CHANGES", "VOIDED"]
    subject_revision: int = Field(ge=0)
    revision: int = Field(ge=1)
    is_stale: bool
    created_at: str
    updated_at: str
    idempotent_replay: bool


class ReviewDecisionCommandResponse(StrictModel):
    decision: ReviewDecisionWriteFact


class ReviewAnnotationFact(StrictModel):
    id: str
    target_kind: Literal["MEDIA_VERSION"]
    target_id: str
    timecode_ms: int = Field(ge=0)
    category: ReviewAnnotationCategory
    comment: str
    snapshot_media_version_id: str | None = None
    rework_job_id: str | None = None
    created_at: str
    created_by: str
    idempotent_replay: bool = False


class ReviewAnnotationPage(StrictModel):
    items: list[ReviewAnnotationFact]
    cursor: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_cursor: int | None = None
    read_only: Literal[True]
    request_shape: Literal["bounded_review_annotations_v2"]


class ReviewAnnotationCreateCommand(StrictModel):
    expected_revision: int = Field(ge=0)
    timecode_ms: int = Field(ge=0)
    category: ReviewAnnotationCategory
    comment: str = Field(min_length=1, max_length=4000)
    snapshot_media_version_id: str | None = Field(default=None, min_length=1, max_length=200)
    rework_job_id: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReviewAnnotationCommandResponse(StrictModel):
    annotation: ReviewAnnotationFact
