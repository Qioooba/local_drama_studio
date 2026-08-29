"""HTTP contracts for long-form Adaptation Planning."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

AdaptationMode = Literal[
    "COMPLETE_WORK",
    "SERIAL_INCREMENTAL",
    "PRESEGMENTED_SCRIPT",
    "SINGLE_EPISODE",
]
EpisodeStrategy = Literal["AI_ESTIMATE", "FIXED_COUNT"]
SeasonStrategy = Literal["NONE", "AI_SUGGESTED", "FIXED_COUNT", "INHERIT_EXISTING"]


class AdaptationPlanPreflightRequest(BaseModel):
    source_document_version_id: str = Field(min_length=1)
    source_paragraph_start: int | None = Field(default=None, ge=1)
    source_paragraph_end: int | None = Field(default=None, ge=1)
    target_duration_ms: int = Field(default=120_000, ge=10_000, le=3_600_000)

    @model_validator(mode="after")
    def complete_source_range(self) -> "AdaptationPlanPreflightRequest":
        if (self.source_paragraph_start is None) != (self.source_paragraph_end is None):
            raise ValueError("source_paragraph_start 和 source_paragraph_end 必须同时提供")
        return self


class AdaptationPlanCreateRequest(AdaptationPlanPreflightRequest):
    mode: AdaptationMode
    episode_strategy: EpisodeStrategy = "AI_ESTIMATE"
    requested_episode_count: int | None = Field(default=None, ge=1, le=2_000)
    season_strategy: SeasonStrategy = "AI_SUGGESTED"
    requested_season_count: int | None = Field(default=None, ge=1, le=100)
    profile_version_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def complete_strategy_values(self) -> "AdaptationPlanCreateRequest":
        if self.episode_strategy == "FIXED_COUNT" and self.requested_episode_count is None:
            raise ValueError("固定集数模式需要 requested_episode_count")
        if self.episode_strategy != "FIXED_COUNT" and self.requested_episode_count is not None:
            raise ValueError("AI 估算模式不能指定 requested_episode_count")
        if self.season_strategy == "FIXED_COUNT" and self.requested_season_count is None:
            raise ValueError("固定季数模式需要 requested_season_count")
        if self.season_strategy != "FIXED_COUNT" and self.requested_season_count is not None:
            raise ValueError("当前季策略不能指定 requested_season_count")
        return self


class AdaptationPlanPreflightResponse(BaseModel):
    source: dict[str, Any]
    scope: dict[str, Any]
    diagnosis: dict[str, Any]
    execution: dict[str, Any]
    warnings: list[dict[str, str]]


class AdaptationPlanCreateResponse(BaseModel):
    plan_id: str
    artifact_status: str
    revision_id: str
    revision_no: int
    run_id: str
    run_status: str
    created_at: str
    idempotent: bool


class AdaptationPlanListResponse(BaseModel):
    items: list[dict[str, Any]]


class AdaptationPlanWorkspaceResponse(BaseModel):
    plan: dict[str, Any]
    revision: dict[str, Any]
    run: dict[str, Any]
    episodes: list[dict[str, Any]]
    analysis_nodes: list[dict[str, Any]]
    next_action: str


class AdaptationAnalysisManifestResponse(BaseModel):
    run_id: str
    run_status: str
    total_nodes: int
    idempotent: bool


class AdaptationAnalysisReadinessResponse(BaseModel):
    plan_id: str
    revision_id: str
    run_id: str | None
    profile: dict[str, Any] | None
    execution: dict[str, Any]
    blockers: list[dict[str, str]]
    read_only: bool


class AdaptationAnalysisSubmitRequest(BaseModel):
    profile_version_id: str = Field(min_length=1, max_length=36)
    allow_remote_outbound: bool = False


class AdaptationAnalysisSubmitResponse(BaseModel):
    run_id: str
    run_status: str
    job_count: int
    created_job_ids: list[str]
    provider_remote: bool
    idempotent: bool


class AdaptationPlanApproveRequest(BaseModel):
    expected_content_sha256: str = Field(min_length=64, max_length=64)


class AdaptationPlanApproveResponse(BaseModel):
    plan_id: str
    artifact_status: Literal["APPROVED"]
    revision_id: str
    approved_at: str


class AdaptationMaterializationPreflightResponse(BaseModel):
    plan_id: str
    revision_id: str
    artifact_status: str
    strategy: Literal["APPEND_NEW"]
    ready: bool
    blockers: list[dict[str, str]]
    existing_structure: dict[str, int]
    would_create: dict[str, Any]
    manifest_sha256: str
    already_materialized: bool
    mutated: bool


class AdaptationMaterializeRequest(BaseModel):
    expected_content_sha256: str = Field(min_length=64, max_length=64)
    confirm_append: Literal[True]


class AdaptationMaterializeResponse(BaseModel):
    materialization_id: str
    plan_id: str
    revision_id: str
    project_id: str
    strategy: Literal["APPEND_NEW"]
    created_at: str
    items: list[dict[str, Any]]
    idempotent: bool
