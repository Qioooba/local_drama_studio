"""Pydantic request/response contracts for the Explainer Factory API (design §14).

These schemas describe shape only.  Business invariants live in
``local_drama.domain.explainers`` and the application services; a request that
validates here is not thereby authorized (design §14: a JSON schema pass is not
model readiness and not a publish authorization).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RationalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num: int = Field(gt=0, le=120_000)
    den: int = Field(gt=0, le=1001)


class SourceRefModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=400)
    role: str = Field(default="RESEARCH_SOURCE", max_length=40)


class ExplainerOutputRequest(BaseModel):
    """One output edition: voice language, subtitle languages, aspect, frame rate."""

    model_config = ConfigDict(extra="forbid")
    edition_key: str = Field(min_length=3, max_length=64)
    voice_locale: str = Field(min_length=2, max_length=32)
    subtitle_locales: list[str] = Field(default_factory=list, max_length=4)
    subtitle_mode: Literal["NONE", "BURNED", "SOFT", "BILINGUAL_BURNED"] = "NONE"
    aspect_ratio: Literal["16:9", "9:16", "3:4", "1:1"] = "16:9"
    fps: RationalModel = Field(default_factory=lambda: RationalModel(num=25, den=1))
    duration_policy: Literal["USE_SOURCE_TARGET", "NATURAL_NARRATION", "FIXED_FRAMES"] = "NATURAL_NARRATION"
    allow_soft_subtitle_fallback: bool = False


class ExplainerCreateRequest(BaseModel):
    """``POST /explainers`` — create the EXPLAINER project, the video and inputs."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(default="", max_length=2000)
    content_kind: Literal["FACTUAL_EXPLAINER", "ORIGINAL_FICTION"] = "FACTUAL_EXPLAINER"
    project_code: str | None = Field(default=None, min_length=2, max_length=64)
    input_kind: Literal["TOPIC", "PASTED_SCRIPT", "DOCUMENT_IMPORT", "REFERENCE_LINKS"] = "TOPIC"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRefModel] = Field(default_factory=list)
    reference_urls: list[str] = Field(default_factory=list, max_length=200)
    pasted_text: str | None = Field(default=None, max_length=2_000_000)
    duration_mode: Literal["TARGET", "FIXED"] = "TARGET"
    target_seconds: int = Field(default=300, ge=30, le=7200)
    tolerance_percent: float = Field(default=5.0, ge=0, le=25)
    source_locale: str = Field(default="zh-CN", min_length=2, max_length=32)
    outputs: list[ExplainerOutputRequest] = Field(min_length=1, max_length=8)
    automation_mode: Literal["AUTO_WITH_EXCEPTIONS", "REVIEW_BEFORE_RENDER", "MANUAL_REVIEW"] = "AUTO_WITH_EXCEPTIONS"
    inference_mode: Literal["LOCAL_ONLY", "ALLOW_CONFIGURED_CLOUD"] = "LOCAL_ONLY"
    research_mode: Literal["OFFLINE_IMPORT", "WEB_RESEARCH"] = "OFFLINE_IMPORT"
    allowed_domains: list[str] = Field(default_factory=list, max_length=200)
    channel_profile_id: str | None = Field(default=None, max_length=36)
    channel_profile_version_id: str | None = Field(default=None, max_length=36)
    aspect_ratio: str | None = Field(default=None, max_length=16)
    width: int | None = Field(default=None, ge=64, le=16384)
    height: int | None = Field(default=None, ge=64, le=16384)
    primary_language: str | None = Field(default=None, max_length=32)
    subtitle_mode: str | None = Field(default=None, max_length=24)
    subtitle_language: str | None = Field(default=None, max_length=32)


class ExplainerSourceImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packet_id: str | None = Field(default=None, max_length=36)
    title: str = Field(default="", max_length=300)
    language: str | None = Field(default=None, max_length=32)
    source_kind: Literal["DOCUMENT_IMPORT", "WEB_PAGE", "REFERENCE_LINK", "LICENSED_MEDIA", "AUTHORED_FICTION_PACK"] = "DOCUMENT_IMPORT"
    url: str | None = Field(default=None, max_length=2000)
    rights: dict[str, Any] = Field(default_factory=dict)
    credibility_kind: Literal[
        "PRIMARY", "SECONDARY", "AGGREGATOR", "USER_GENERATED", "AUTHORED_FICTION", "UNKNOWN"
    ] = "UNKNOWN"
    upstream_source_reference: str | None = Field(default=None, max_length=400)
    published_at: str | None = Field(default=None, max_length=40)
    updated_at_source: str | None = Field(default=None, max_length=40)
    event_date: str | None = Field(default=None, max_length=40)


class ExplainerResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["OFFLINE_IMPORT", "WEB_RESEARCH"] = "OFFLINE_IMPORT"
    topic: str = Field(default="", max_length=2000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=200)
    max_external_requests: int = Field(default=0, ge=0, le=1000)
    reference_urls: list[str] = Field(default_factory=list, max_length=200)


class ExplainerClaimPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["SUPPORTED", "DISPUTED", "UNVERIFIED", "EXCLUDED"] | None = None
    statement: str | None = Field(default=None, max_length=4000)
    importance: Literal["CORE", "KEY", "SUPPORTING"] | None = None
    confidence_reason: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=2000)


class ExplainerSegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_segment_id: str = Field(min_length=1, max_length=64)
    display_text: str = Field(min_length=1, max_length=8000)
    spoken_text: str | None = Field(default=None, max_length=8000)
    statement_type: Literal["FACT", "ORIGINAL_EXPLANATION", "TRANSITION", "FICTION", "QUESTION"] = "FACT"
    claim_codes: list[str] = Field(default_factory=list, max_length=64)
    pronunciation_map: list[dict[str, str]] = Field(default_factory=list, max_length=256)
    speaker: str | None = Field(default=None, max_length=120)
    emotion: str | None = Field(default=None, max_length=64)
    pause_after_ms: int = Field(default=0, ge=0, le=60_000)
    target_duration_ms: int | None = Field(default=None, gt=0, le=3_600_000)
    content_locked_by_human: bool = False
    chapter_code: str | None = Field(default=None, max_length=64)


class ExplainerChapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    audience_question: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=8000)
    claim_codes: list[str] = Field(default_factory=list, max_length=256)


class ExplainerScriptRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: str = Field(default="zh-CN", min_length=2, max_length=32)
    title: str = Field(default="", max_length=300)
    status: Literal["DRAFT", "IN_REVIEW", "FROZEN"] = "DRAFT"
    source_script_revision_id: str | None = Field(default=None, max_length=36)
    terminology: dict[str, str] = Field(default_factory=dict)
    chapters: list[ExplainerChapterRequest] = Field(default_factory=list, max_length=200)
    segments: list[ExplainerSegmentRequest] = Field(default_factory=list, max_length=5000)
    parent_plan_id: str | None = Field(default=None, max_length=36)


class ExplainerSegmentPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    expected_script_revision_id: str = Field(min_length=1, max_length=36)
    display_text: str | None = Field(default=None, max_length=8000)
    spoken_text: str | None = Field(default=None, max_length=8000)
    pronunciation_map: list[dict[str, str]] | None = Field(default=None, max_length=256)
    allow_locked: bool = False
    note: str = Field(default="", max_length=2000)


class ExplainerPrerollFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ExplainerPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outputs: list[ExplainerOutputRequest] = Field(default_factory=list, max_length=8)
    budget: dict[str, Any] = Field(default_factory=dict)
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    parent_plan_id: str | None = Field(default=None, max_length=36)


class ExplainerRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_hash: str = Field(min_length=64, max_length=64)
    outputs: list[ExplainerOutputRequest] = Field(default_factory=list, max_length=8)
    budget: dict[str, Any] = Field(default_factory=dict)
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    start_workflow: bool = True


class ExplainerRunControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ExplainerRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_ids: list[str] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)
    budget: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


class ExplainerSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    candidate_id: str = Field(min_length=1, max_length=36)
    edition_id: str | None = Field(default=None, max_length=36)
    lock: bool = False
    actor: str | None = Field(default=None, max_length=120)


class ExplainerBeatSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    selection: ExplainerSelectionRequest


class ExplainerEditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edition_key: str = Field(min_length=3, max_length=64)
    voice_locale: str = Field(min_length=2, max_length=32)
    subtitle_locales: list[str] = Field(default_factory=list, max_length=4)
    subtitle_mode: Literal["NONE", "BURNED", "SOFT", "BILINGUAL_BURNED"] = "NONE"
    aspect_ratio: Literal["16:9", "9:16", "3:4", "1:1"] = "16:9"
    fps: RationalModel = Field(default_factory=lambda: RationalModel(num=25, den=1))
    duration_policy: Literal["USE_SOURCE_TARGET", "NATURAL_NARRATION", "FIXED_FRAMES"] = "NATURAL_NARRATION"
    target_seconds: int | None = Field(default=None, ge=30, le=7200)
    tolerance_percent: float = Field(default=5.0, ge=0, le=25)
    allow_soft_subtitle_fallback: bool = False
    reuse_compatible_media: bool = True


class ExplainerRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    composition_revision_id: str | None = Field(default=None, max_length=36)
    freeze: bool = True
    confirm: bool = False
    budget: dict[str, Any] = Field(default_factory=dict)


class ExplainerDecisionRequest(BaseModel):
    """Human decision, or an explicit request to re-run the frozen policy.

    ``decision_kind`` may never be ``POLICY_ACCEPTED`` from HTTP: machine
    acceptance is created only by the internal policy processor (design §14).
    """

    model_config = ConfigDict(extra="forbid")
    decision_kind: Literal["HUMAN_APPROVED", "REJECTED", "CHANGES_REQUESTED", "PUBLICATION_AUTHORIZED"]
    subject_kind: Literal[
        "COMPOSITION_RENDER",
        "COMPOSITION_REVISION",
        "NARRATION_ALIGNMENT",
        "SUBTITLE_REVISION",
        "VISUAL_BEAT",
        "EDITION",
        "SCRIPT_REVISION",
    ] = "COMPOSITION_RENDER"
    subject_revision_id: str = Field(min_length=1, max_length=64)
    subject_hash: str = Field(min_length=64, max_length=64)
    actor: str = Field(min_length=1, max_length=120)
    reviewed_intervals: list[list[int]] = Field(default_factory=list, max_length=500)
    note: str = Field(default="", max_length=4000)
    rerun_policy: bool = False


class ExplainerExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_id: str | None = Field(default=None, max_length=36)
    edition_id: str | None = Field(default=None, max_length=36)
    platform_code: str | None = Field(default=None, max_length=40)
    intended_territories: list[str] = Field(default_factory=lambda: ["GLOBAL"], max_length=64)
    include_stems: bool = True
    include_subtitles: bool = True
    confirm: bool = False


class ExplainerScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, max_length=36)
    channel_profile_id: str = Field(min_length=1, max_length=36)
    channel_profile_version_id: str = Field(min_length=1, max_length=36)
    code: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="Asia/Shanghai", min_length=2, max_length=64)
    rule: dict[str, Any] = Field(default_factory=dict)
    topic_scope: str = Field(default="", max_length=2000)
    source_allowlist: list[str] = Field(default_factory=list, max_length=500)
    daily_budget: dict[str, Any] = Field(default_factory=dict)
    max_concurrent_runs: int = Field(default=1, ge=1, le=8)
    duplicate_window_hours: int = Field(default=72, ge=0, le=24 * 365)
    insufficient_topic_policy: Literal["SKIP_WITH_REASON", "WAIT_FOR_INPUT", "FAIL"] = "SKIP_WITH_REASON"
    closed_window: dict[str, Any] = Field(default_factory=dict)
    failure_notification: dict[str, Any] = Field(default_factory=dict)
    durations: dict[str, Any] = Field(default_factory=dict)
    outputs: list[ExplainerOutputRequest] = Field(default_factory=list, max_length=8)
    automation_mode: Literal["AUTO_WITH_EXCEPTIONS", "REVIEW_BEFORE_RENDER", "MANUAL_REVIEW"] = "AUTO_WITH_EXCEPTIONS"


class ExplainerSchedulePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["ACTIVE", "PAUSED", "ARCHIVED"] | None = None
    rule: dict[str, Any] | None = None
    topic_scope: str | None = Field(default=None, max_length=2000)
    source_allowlist: list[str] | None = Field(default=None, max_length=500)
    daily_budget: dict[str, Any] | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=8)
    duplicate_window_hours: int | None = Field(default=None, ge=0, le=24 * 365)
    insufficient_topic_policy: Literal["SKIP_WITH_REASON", "WAIT_FOR_INPUT", "FAIL"] | None = None
    closed_window: dict[str, Any] | None = None
    failure_notification: dict[str, Any] | None = None
    durations: dict[str, Any] | None = None
    outputs: list[ExplainerOutputRequest] | None = Field(default=None, max_length=8)
    automation_mode: Literal["AUTO_WITH_EXCEPTIONS", "REVIEW_BEFORE_RENDER", "MANUAL_REVIEW"] | None = None


class PublicationAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_code: str = Field(min_length=1, max_length=64)
    platform_code: str = Field(min_length=1, max_length=40)
    account_ref: str | None = Field(default=None, max_length=120)
    authorized: bool = False
    handoff_note: str = Field(default="", max_length=4000)
