from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import JsonObject, StrictModel

ProductionStageCode = Literal["SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC"]
ProductionState = Literal["EMPTY", "READY", "RUNNING", "NEEDS_REVIEW", "BLOCKED", "FAILED", "STALE", "CANCELLED"]


class ProductionBlockerFact(StrictModel):
    code: str
    message: str
    owner_route: Literal["SHOT_STUDIO", "REVIEW", "POST_AUDIO", "POST_EDIT", "SYSTEM_JOBS"]
    repair_action: str


class ProductionStageFact(StrictModel):
    stage_code: ProductionStageCode
    state: ProductionState
    reason_code: str
    active_job_id: str | None = None
    allowed_actions: list[str]


class ProductionMaterialSlotFact(StrictModel):
    kind: Literal["KEYFRAME", "VIDEO", "AUDIO"]
    candidate_count: int = Field(ge=0)
    selected_version_id: str | None = None
    machine_qc_state: str | None = None
    human_decision_id: str | None = None


class ProductionFreshnessEdgeFact(StrictModel):
    source_revision: str
    target_revision: str | None = None
    state: Literal["CURRENT", "STALE"]
    reason: str


class EpisodeProductionShotFact(StrictModel):
    shot_id: str
    shot_code: str
    order_key: str
    overall_state: ProductionState
    next_action: str
    stages: list[ProductionStageFact]
    material_slots: list[ProductionMaterialSlotFact]
    freshness_edges: list[ProductionFreshnessEdgeFact]
    blockers: list[ProductionBlockerFact]


class EpisodeProductionShotPage(StrictModel):
    items: list[EpisodeProductionShotFact]
    cursor: int
    limit: int
    total: int
    next_cursor: int | None = None
    filters: list[ProductionState]
    read_only: Literal[True]
    request_shape: Literal["bounded_episode_production_shots_v2"]


class EpisodeProductionRunSummary(StrictModel):
    id: str
    status: str
    revision: int
    updated_at: str | None = None


class EpisodeProductionOverviewFact(StrictModel):
    episode_id: str
    project_id: str
    episode_code: str
    episode_title: str | None = None
    shot_count: int = Field(ge=0)
    attention_count: int = Field(ge=0)
    active_job_count: int = Field(ge=0)
    next_action: str
    state_counts: dict[str, int]
    active_run: EpisodeProductionRunSummary | None = None
    allowed_actions: list[str]


class EpisodeProductionOverviewResponse(StrictModel):
    overview: EpisodeProductionOverviewFact
    read_only: Literal[True]
    request_shape: Literal["episode_production_overview_v2"]


class EpisodeProductionChangeFact(StrictModel):
    sequence: int
    event_type: str
    subject_type: str
    subject_id: str
    occurred_at: str
    payload: JsonObject


class EpisodeProductionChangesResponse(StrictModel):
    items: list[EpisodeProductionChangeFact]
    after: int
    next_after: int
    has_more: bool
    read_only: Literal[True]
    request_shape: Literal["bounded_episode_production_changes_v2"]


class EpisodeProductionRunStartCommand(StrictModel):
    tts_enabled: bool = True
    production_mode: Literal["DRAFT", "BALANCED", "QUALITY"] = "BALANCED"
    checkpoint_policy: Literal[
        "AUTO_CONTINUE", "AFTER_ASSETS", "AFTER_SHOT_PLAN", "BEFORE_VIDEO", "ON_EXCEPTION"
    ] = "ON_EXCEPTION"
    min_free_disk_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionRunPauseCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionRunResumeCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionRunCancelCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionRunRecoverCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionRunCommandFact(StrictModel):
    id: str
    episode_id: str
    project_id: str
    status: str
    revision: int = Field(ge=1)
    updated_at: str | None = None
    outcome: Literal["STARTED", "PAUSED", "RESUMED", "CANCELLED", "RECOVERED"]
    affected_job_count: int = Field(default=0, ge=0)
    idempotent_replay: bool


class EpisodeProductionRunCommandResponse(StrictModel):
    run: EpisodeProductionRunCommandFact
