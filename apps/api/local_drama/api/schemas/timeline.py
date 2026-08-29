from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from local_drama.api.schemas.common import LocalArtifactReference

# Typed response contracts for the legacy timeline/delivery surface.  The
# documented fields mirror the application services; ``extra="allow"`` is the
# migration guard used by the comfy_lab sample so service-side additions are
# never silently discarded while the remaining v1 surface is being typed.


class _TimelineResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class TimelineStatusRevision(_TimelineResponse):
    id: str
    revision_no: int
    status: str
    created_at: str
    item_count: int


class TimelineStatusSubtitle(_TimelineResponse):
    id: str
    revision_no: int
    format: str
    status: str
    created_at: str
    cue_count: int
    authority_status: str
    source_document_version_id: str | None
    asr_alignment_only: bool


class TimelineStatusRender(_TimelineResponse):
    id: str
    timeline_revision_id: str
    revision: int
    status: str
    duration_ms: int | None
    mime_type: str | None
    sha256: str
    created_at: str
    input_snapshot: dict[str, Any]
    ffmpeg_command: dict[str, Any]
    execution_log: dict[str, Any]
    evidence_status: str


class TimelineStatusDelivery(_TimelineResponse):
    id: str
    episode_render_version_id: str
    timeline_revision_id: str
    status: str
    rel_path: str
    manifest_sha256: str | None
    created_at: str


class TimelineStatusEpisode(_TimelineResponse):
    id: str
    code: str
    title: str
    project_id: str


class TimelineStatusTimelineSummary(_TimelineResponse):
    revision_count: int
    latest: TimelineStatusRevision | None
    latest_frozen: TimelineStatusRevision | None


class TimelineStatusSubtitleSummary(_TimelineResponse):
    revision_count: int
    latest: TimelineStatusSubtitle | None


class TimelineStatusAudioSummary(_TimelineResponse):
    binding_count: int
    verified_local_count: int


class TimelineStatusRenderSummary(_TimelineResponse):
    count: int
    verified_count: int
    latest: TimelineStatusRender | None


class TimelineStatusDeliverySummary(_TimelineResponse):
    count: int
    verified_count: int
    latest: TimelineStatusDelivery | None


class EpisodeTimelineStatus(_TimelineResponse):
    episode: TimelineStatusEpisode
    timeline: TimelineStatusTimelineSummary
    subtitles: TimelineStatusSubtitleSummary
    audio: TimelineStatusAudioSummary
    renders: TimelineStatusRenderSummary
    delivery: TimelineStatusDeliverySummary
    observed_at: str
    read_only: bool
    runtime_contacted: bool
    network_contacted: bool
    mutated: bool


class EpisodeTimelineStatusEnvelope(_TimelineResponse):
    status: EpisodeTimelineStatus


class TimelineJob(_TimelineResponse):
    id: str
    type: str
    project_id: str
    subject_type: str
    subject_id: str
    state: str
    channel: str
    idempotency_key: str
    input_snapshot: dict[str, Any]
    revision: int
    idempotent_replay: bool


class BackgroundOperationResponse(_TimelineResponse):
    job: TimelineJob
    result_type: Literal["ENHANCEMENT", "DELIVERY", "EPISODE_RENDER"] | None
    result: dict[str, Any] | None


class TimelineItem(_TimelineResponse):
    id: str | None = None
    timeline_revision_id: str | None = None
    track_type: str
    media_version_id: str | None
    start_us: int
    end_us: int
    parameters: dict[str, Any]


class TimelineRevision(_TimelineResponse):
    id: str
    episode_id: str
    revision_no: int
    revision_hash: str
    status: str
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    content: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    items: list[TimelineItem]


class TimelineRevisionEnvelope(_TimelineResponse):
    timeline: TimelineRevision


class TimelineIssue(_TimelineResponse):
    code: str
    message: str | None = None


class TimelineRefreshPlan(_TimelineResponse):
    episode_id: str
    status: Literal["READY", "BLOCKED"]
    plan_hash: str
    source_timeline: dict[str, Any] | None
    summary: dict[str, int]
    items: list[dict[str, Any]]
    selected_videos: list[dict[str, Any]]
    audio_binding_ids: list[str]
    subtitle_revision_id: str | None
    blockers: list[TimelineIssue]
    warnings: list[TimelineIssue]
    would_create_status: Literal["FROZEN"]
    requires_confirmation: bool
    read_only: bool
    runtime_contacted: bool
    network_contacted: bool
    mutated: bool


class TimelineRefreshPlanEnvelope(_TimelineResponse):
    plan: TimelineRefreshPlan


class TimelineRefreshCommitResponse(_TimelineResponse):
    timeline: TimelineRevision
    plan_hash: str
    source_timeline_revision_id: str | None
    mutated: bool


class TimelineExportFile(_TimelineResponse):
    rel_path: str
    byte_size: int
    sha256: str


class TimelineExportResult(_TimelineResponse):
    schema_version: Literal["localdrama.timeline-export.v1"]
    status: Literal["EXPORTED"]
    artifact: LocalArtifactReference
    rel_path: str
    manifest_rel_path: str
    files: list[TimelineExportFile]
    export_hash: str
    reused: bool
    database_mutated: bool
    runtime_contacted: bool
    network_contacted: bool


class TimelineExportEnvelope(_TimelineResponse):
    export: TimelineExportResult


class SubtitleCue(_TimelineResponse):
    id: str | None = None
    subtitle_revision_id: str | None = None
    cue_no: int
    start_us: int
    end_us: int
    text: str
    style: dict[str, Any]


class SubtitleRevision(_TimelineResponse):
    id: str
    episode_id: str
    revision_no: int
    format: str
    content_text: str
    content_hash: str
    status: str
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    input_snapshot: dict[str, Any]
    authority_status: str
    cues: list[SubtitleCue]


class SubtitleRevisionEnvelope(_TimelineResponse):
    subtitle: SubtitleRevision


class SubtitleDraftPlan(_TimelineResponse):
    episode_id: str
    status: Literal["READY", "PARTIAL", "BLOCKED"]
    ready_to_load: bool
    source_document_version_id: str | None
    source_document_candidates: list[str]
    authority: dict[str, Any] | None
    cues: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    missing: list[dict[str, Any]]
    blockers: list[TimelineIssue]
    warnings: list[TimelineIssue]
    summary: dict[str, int]
    text_authority: Literal["SCRIPT"]
    timing_authority: Literal["SELECTED_TTS_MEDIA"]
    requires_human_review: bool
    would_create_revision: bool
    read_only: bool
    runtime_contacted: bool
    network_contacted: bool
    mutated: bool


class SubtitleDraftPlanEnvelope(_TimelineResponse):
    plan: SubtitleDraftPlan


class FrameAnchor(_TimelineResponse):
    id: str
    source_media_version_id: str
    source_time_us: int
    source_frame_index: int
    extracted_media_version_id: str
    role_hint: str
    sha256: str
    approval_id: str | None
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    requested_time_us: int | None
    resolved_time_us: int
    source_sha256: str
    extraction_method: str


class FrameAnchorEnvelope(_TimelineResponse):
    frame_anchor: FrameAnchor


class ShotTransitionConstraint(_TimelineResponse):
    id: str
    from_shot_id: str
    to_shot_id: str
    constraint_type: str
    compatibility_status: str


class ShotTransitionConstraintEnvelope(_TimelineResponse):
    constraint: ShotTransitionConstraint


class ShotTransitionValidation(_TimelineResponse):
    constraint_id: str
    status: str
    blockers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class ShotTransitionValidationEnvelope(_TimelineResponse):
    validation: ShotTransitionValidation


class PostProcessRecipe(_TimelineResponse):
    id: str
    code: str
    title: str
    status: str
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    recipe_key: str
    version_no: int
    parent_recipe_id: str | None
    recipe_hash: str
    steps: list[dict[str, Any]]
    capability_contract: dict[str, Any]


class PostProcessRecipeEnvelope(_TimelineResponse):
    recipe: PostProcessRecipe


class PostProcessRecipePage(_TimelineResponse):
    items: list[PostProcessRecipe]


class EnhancementPlan(_TimelineResponse):
    status: Literal["READY"]
    plan_hash: str
    snapshot: dict[str, Any]
    command_preview: dict[str, Any]
    would_create_run: bool
    would_overwrite_input: bool
    runtime_contacted: bool
    network_contacted: bool
    mutated: bool


class EnhancementPlanEnvelope(_TimelineResponse):
    plan: EnhancementPlan


class EnhancementRun(_TimelineResponse):
    id: str
    input_media_version_id: str
    recipe_id: str
    status: str
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    parameters: dict[str, Any]
    execution_snapshot: dict[str, Any]
    qc: dict[str, Any]
    bypass_comparison: dict[str, Any]


class EnhancementRunEnvelope(_TimelineResponse):
    enhancement: EnhancementRun


class EnhancementSubmissionResponse(_TimelineResponse):
    plan: EnhancementPlan
    job: TimelineJob
    idempotent_replay: bool


class EpisodeRender(_TimelineResponse):
    id: str
    episode_id: str
    timeline_revision_id: str
    rel_path: str
    sha256: str
    byte_size: int
    probe: dict[str, Any]
    input_snapshot: dict[str, Any]
    ffmpeg_command: dict[str, Any]
    execution_log: dict[str, Any] | str
    revision: int
    status: str
    compose_fingerprint: str
    idempotent_replay: bool | None = None


class EpisodeRenderEnvelope(_TimelineResponse):
    render: EpisodeRender


class ComposePreflight(_TimelineResponse):
    project_id: str
    episode_id: str
    timeline_revision_id: str
    compose_fingerprint: str
    input_snapshot: dict[str, Any]
    existing_render: EpisodeRender | None
    read_only: bool
    writes_performed: int
    status: Literal["READY", "BLOCKED"]
    disk_gate: dict[str, Any]
    blockers: list[dict[str, Any]]


class ComposePreflightEnvelope(_TimelineResponse):
    preflight: ComposePreflight


class ComposeSubmissionResponse(_TimelineResponse):
    preflight: ComposePreflight
    job: TimelineJob | None
    render: EpisodeRender | None
    idempotent_replay: bool


class SegmentedComposePreflight(_TimelineResponse):
    project_id: str
    episode_id: str
    timeline_revision_id: str
    compose_fingerprint: str
    input_snapshot: dict[str, Any]
    existing_render: EpisodeRender | None
    read_only: bool
    writes_performed: int


class SegmentedComposeSubmissionResponse(_TimelineResponse):
    preflight: SegmentedComposePreflight
    job: TimelineJob | None
    render: EpisodeRender | None
    idempotent_replay: bool


class SubtitleStyleTemplate(_TimelineResponse):
    id: str
    project_id: str
    kind: str
    code: str
    title: str


class SubtitleStyleTemplatePage(_TimelineResponse):
    items: list[SubtitleStyleTemplate]


class SubtitleStyleTemplateEnvelope(_TimelineResponse):
    template: SubtitleStyleTemplate


class SubtitleStyleTemplateDeleted(_TimelineResponse):
    id: str
    deleted: bool


class SubtitleStyleTemplateDeletedEnvelope(_TimelineResponse):
    deleted: SubtitleStyleTemplateDeleted


class DeliveryArtifactFile(_TimelineResponse):
    rel_path: str
    sha256: str
    byte_size: int


class DeliveryFile(DeliveryArtifactFile):
    id: str
    delivery_package_id: str


class DeliveryBuildResult(_TimelineResponse):
    id: str
    status: str
    rel_path: str
    manifest_sha256: str
    files: list[DeliveryArtifactFile]
    controls: dict[str, Any]
    machine_preflight: dict[str, Any]
    human_review_status: str
    platform_review_status: str


class DeliveryBuildEnvelope(_TimelineResponse):
    delivery: DeliveryBuildResult


class DeliverySubmissionPreflight(_TimelineResponse):
    status: Literal["READY"]
    project_id: str
    fingerprint: str
    inputs: dict[str, Any]


class DeliverySubmissionResponse(_TimelineResponse):
    preflight: DeliverySubmissionPreflight
    job: TimelineJob
    idempotent_replay: bool


class DeliveryEvent(_TimelineResponse):
    id: str
    action: str
    manifest_sha256: str | None
    note: str | None
    created_at: str
    created_by: str


class DeliveryPackage(_TimelineResponse):
    id: str
    episode_id: str
    episode_render_version_id: str
    timeline_revision_id: str
    target_version_id: str
    target: dict[str, Any]
    status: str
    artifact: LocalArtifactReference
    rel_path: str
    manifest_sha256: str
    withdrawn_reason: str | None
    machine_preflight_status: str
    machine_preflight: dict[str, Any]
    human_review_status: str
    platform_review_status: str
    created_at: str
    updated_at: str
    revision: int
    files: list[DeliveryFile]
    events: list[DeliveryEvent]
    runtime_contacted: bool
    network_contacted: bool


class DeliveryPackageEnvelope(_TimelineResponse):
    delivery: DeliveryPackage


class DeliveryPackagePage(_TimelineResponse):
    items: list[DeliveryPackage]
    runtime_contacted: bool
    network_contacted: bool


class DeliveryFilePage(_TimelineResponse):
    items: list[DeliveryFile]


class DeliveryVerificationCheck(_TimelineResponse):
    rel_path: str
    expected_sha256: str
    actual_sha256: str | None
    expected_byte_size: int
    actual_byte_size: int | None
    ok: bool


class DeliveryVerification(_TimelineResponse):
    id: str
    status: str
    checks: list[DeliveryVerificationCheck]
    manifest_check: dict[str, Any]
    machine_preflight_status: str
    human_review_status: str
    platform_review_status: str
    withdrawn_reason: str | None


class DeliveryVerificationEnvelope(_TimelineResponse):
    delivery: DeliveryVerification


class DeliveryWithdrawal(_TimelineResponse):
    id: str
    status: Literal["WITHDRAWN"]
    reason: str
    idempotent: bool


class DeliveryWithdrawalEnvelope(_TimelineResponse):
    delivery: DeliveryWithdrawal


class DeliveryReview(_TimelineResponse):
    id: str
    status: str
    machine_preflight_status: str
    human_review_status: str
    platform_review_status: str
    reviewer_type: Literal["HUMAN", "PLATFORM"]
    decision: Literal["APPROVED", "REJECTED"]
    note: str


class DeliveryReviewEnvelope(_TimelineResponse):
    delivery: DeliveryReview
