from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import JsonObject, ShotReadinessFact, StrictModel

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
    shot_readiness: ShotReadinessFact
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
    # The overview is the creator-facing source of truth while a run is
    # paused.  Keep the persisted gate/context visible so the page can tell a
    # configured checkpoint from a machine-found blocker without inventing a
    # second status model.
    pending_gate: JsonObject = Field(default_factory=dict)
    machine_context: JsonObject = Field(default_factory=dict)


class EpisodePlanningJobFact(StrictModel):
    id: str
    state: str
    error_code: str | None = None
    error_message: str | None = None


class EpisodeProductionOverviewFact(StrictModel):
    episode_id: str
    episode_revision: int = Field(ge=1)
    project_id: str
    episode_code: str
    episode_title: str | None = None
    episode_summary: str | None = None
    key_characters: list[str]
    key_scenes: list[str]
    shot_count: int = Field(ge=0)
    attention_count: int = Field(ge=0)
    active_job_count: int = Field(ge=0)
    planning_job: EpisodePlanningJobFact | None = None
    next_action: str
    state_counts: dict[str, int]
    active_run: EpisodeProductionRunSummary | None = None
    allowed_actions: list[str]
    # Resolved episode contract.  These fields are optional for compatibility
    # with older read-model fixtures, but live responses always populate them.
    target_duration_ms: int = Field(default=0, ge=0)
    planned_duration_ms: int = Field(default=0, ge=0)
    production_plan_version_id: str | None = None
    production_plan_version_no: int | None = None
    resolved_presentation: JsonObject | None = None
    resolved_production_spec: JsonObject | None = None
    plan_stale: bool = False
    replan_required: bool = False
    replan_reasons: list[JsonObject] = []
    replan_draft: JsonObject | None = None
    replan_job: JsonObject | None = None


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


class EpisodeProductionPrepareCommand(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionPrepareFact(StrictModel):
    status: Literal["READY", "QUEUED", "APPLIED"]
    episode_id: str
    shot_count: int = Field(ge=0)
    job_id: str | None = None
    draft_id: str | None = None


class EpisodeProductionPrepareResponse(StrictModel):
    preparation: EpisodeProductionPrepareFact


class EpisodeProductionReplanRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class EpisodeProductionReplanApplyRequest(StrictModel):
    expected_episode_revision: int = Field(ge=1)
    expected_plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class EpisodeProductionReplanFact(StrictModel):
    """Review/apply projection for the current episode target and source scope."""

    status: str
    episode_id: str
    project_id: str | None = None
    job_id: str | None = None
    job: JsonObject | None = None
    draft_id: str | None = None
    draft_revision: int | None = None
    effective_draft_revision_id: str | None = None
    expected_episode_revision: int | None = None
    target_duration_ms: int | None = None
    planned_duration_ms: int | None = None
    source_scope: JsonObject | None = None
    summary: dict[str, int] = {}
    diff: list[JsonObject] = []
    valid: bool | None = None
    issues: list[JsonObject] = []
    plan_hash: str | None = None
    production_plan: JsonObject | None = None
    idempotent_replay: bool | None = None
    requires_generation: bool | None = None
    historical_media_preserved: bool | None = None
    created_shot_ids: list[str] = []
    modified_shot_ids: list[str] = []
    archived_shot_ids: list[str] = []
    protected_shot_ids: list[str] = []
    stale_timeline_revisions: int | None = None


class EpisodeProductionReplanResponse(StrictModel):
    replan: EpisodeProductionReplanFact


class EpisodeProductionReplanApplyResponse(StrictModel):
    apply: EpisodeProductionReplanFact


class EpisodeProductionShotsReadyRequest(StrictModel):
    expected_episode_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    auto_heal: bool = Field(default=False)


class EpisodeProductionShotsReadyFact(StrictModel):
    status: Literal["READY"]
    episode_id: str
    project_id: str
    profile_version_id: str
    episode_revision: int = Field(ge=1)
    ready_shot_ids: list[str]
    ready_shot_count: int = Field(ge=0)
    failed_shots: list[JsonObject] = []
    idempotent_replay: bool


class EpisodeProductionShotsReadyResponse(StrictModel):
    ready: EpisodeProductionShotsReadyFact


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


class WholeDramaEpisodeStatusFact(StrictModel):
    episode_id: str
    code: str
    title: str
    production_status: str
    total_shots: int = 0
    ready_shots: int = 0
    draft_shots: int = 0
    keyframes_count: int = 0
    videos_count: int = 0
    dialogue_lines: int = 0
    voiced_lines: int = 0
    timeline_status: str
    workflow_run_id: str | None = None
    workflow_run_status: str


class WholeDramaStatusResponse(StrictModel):
    project_id: str
    project_code: str
    project_title: str
    overall_status: str
    total_episodes: int
    episodes: list[WholeDramaEpisodeStatusFact]


class WholeDramaPrepareRequest(StrictModel):
    actor: str = Field(default="whole-drama-orchestrator", max_length=100)


class WholeDramaEpisodePrepareFact(StrictModel):
    episode_id: str
    code: str
    status: str
    confirmed_shots: int | None = None
    detail: str | None = None
    code_error: str | None = None
    message: str | None = None


class WholeDramaPrepareResponse(StrictModel):
    project_id: str
    prepared_episodes: list[WholeDramaEpisodePrepareFact]
    success: bool


class WholeDramaRunRequest(StrictModel):
    tts_enabled: bool = True
    production_mode: str = "BALANCED"
    checkpoint_policy: str = "ON_EXCEPTION"
    min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024
    actor: str = "whole-drama-orchestrator"


class WholeDramaDispatchedRunFact(StrictModel):
    episode_id: str
    code: str
    status: str
    run_id: str | None = None
    run_status: str | None = None
    code_error: str | None = None
    message: str | None = None


class WholeDramaRunResponse(StrictModel):
    project_id: str
    preparation: WholeDramaPrepareResponse
    dispatched_runs: list[WholeDramaDispatchedRunFact]
    total_episodes: int
    dispatched_count: int

