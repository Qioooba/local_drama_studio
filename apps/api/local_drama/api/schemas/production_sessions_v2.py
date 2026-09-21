from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import JsonObject, StrictModel

ProductionSessionScope = Literal["SINGLE_EPISODE", "WHOLE_DRAMA"]
ProductionMode = Literal["DRAFT", "BALANCED", "QUALITY"]
CheckpointPolicy = Literal["AUTO_CONTINUE", "AFTER_ASSETS", "AFTER_SHOT_PLAN", "BEFORE_VIDEO", "ON_EXCEPTION"]
ProductionSessionStatus = Literal[
    "READY",
    "RUNNING",
    "PAUSED",
    "WAITING_USER",
    "WAITING_REVIEW",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
ProductionSessionItemState = Literal["PENDING", "RUNNING", "WAITING", "BLOCKED", "FAILED", "COMPLETED", "SKIPPED", "CANCELLED"]


class ProductionSessionPlanRequest(StrictModel):
    scope_type: ProductionSessionScope
    episode_ids: list[str] = Field(default_factory=list, max_length=2000)
    production_mode: ProductionMode = "BALANCED"
    checkpoint_policy: CheckpointPolicy = "ON_EXCEPTION"
    tts_enabled: bool = True
    max_parallel_episodes: int = Field(default=1, ge=1, le=8)
    min_free_disk_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    max_duration_seconds: int = Field(default=24 * 60 * 60, ge=60, le=365 * 24 * 60 * 60)
    max_new_jobs: int = Field(default=600, ge=1, le=1_000_000)
    max_attempts_total: int = Field(default=1_200, ge=1, le=2_000_000)
    max_output_bytes: int = Field(default=100 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    max_queued_gpu_jobs: int = Field(default=8, ge=1, le=128)
    dispatch_shots_per_tick: int = Field(default=4, ge=1, le=100)


class ProductionSessionCreateRequest(ProductionSessionPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionSessionPlanEpisodeFact(StrictModel):
    episode_id: str
    code: str
    title: str | None = None
    ordinal: int = Field(ge=1)
    revision: int = Field(ge=1)
    shot_count: int = Field(ge=0)
    asset_bound_shot_count: int = Field(ge=0)
    missing_asset_binding_count: int = Field(ge=0)
    readiness: Literal["READY", "NEEDS_PREPARATION"]


class ProductionSessionPlanFact(StrictModel):
    project_id: str
    scope_type: ProductionSessionScope
    production_mode: ProductionMode
    checkpoint_policy: CheckpointPolicy
    episode_count: int = Field(ge=1)
    total_shot_count: int = Field(ge=0)
    estimated_candidate_count: int = Field(ge=0)
    can_create: Literal[True]
    plan_hash: str
    configuration: JsonObject
    stages: list[str]
    warnings: list[JsonObject]
    episodes: list[ProductionSessionPlanEpisodeFact]


class ProductionSessionPlanResponse(StrictModel):
    plan: ProductionSessionPlanFact
    read_only: Literal[True]
    request_shape: Literal["production_session_plan_v2"]


class ProductionSessionFact(StrictModel):
    id: str
    project_id: str
    scope_type: ProductionSessionScope
    production_mode: ProductionMode
    checkpoint_policy: CheckpointPolicy
    status: ProductionSessionStatus
    current_stage: str
    plan_hash: str
    configuration: JsonObject
    budget: JsonObject
    counters: JsonObject
    item_count: int = Field(ge=0)
    last_error_code: str | None = None
    last_error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str
    created_by: str
    revision: int = Field(ge=1)
    allowed_actions: list[str]


class ProductionSessionResponse(StrictModel):
    session: ProductionSessionFact
    idempotent_replay: bool = False


class ProductionSessionPage(StrictModel):
    items: list[ProductionSessionFact]
    cursor: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_cursor: int | None = None
    read_only: Literal[True]
    request_shape: Literal["bounded_production_sessions_v2"]


class ProductionSessionItemFact(StrictModel):
    id: str
    session_id: str
    episode_id: str
    episode_code: str
    episode_title: str | None = None
    ordinal: int = Field(ge=1)
    state: ProductionSessionItemState
    current_stage: str
    source_episode_revision: int = Field(ge=1)
    shot_count: int = Field(ge=0)
    progress: JsonObject
    last_error_code: str | None = None
    last_error_message: str | None = None
    updated_at: str
    revision: int = Field(ge=1)


class ProductionSessionItemPage(StrictModel):
    items: list[ProductionSessionItemFact]
    cursor: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_cursor: int | None = None
    read_only: Literal[True]
    request_shape: Literal["bounded_production_session_items_v2"]


class ProductionSessionControlRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionSessionControlResponse(StrictModel):
    session: ProductionSessionFact
    outcome: str
    idempotent_replay: bool = False


class ProductionSessionBudgetExtendRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    max_duration_seconds: int | None = Field(default=None, ge=60, le=365 * 24 * 60 * 60)
    max_new_jobs: int | None = Field(default=None, ge=1, le=1_000_000)
    max_attempts_total: int | None = Field(default=None, ge=1, le=2_000_000)
    max_output_bytes: int | None = Field(default=None, ge=1, le=1 << 50)
    max_queued_gpu_jobs: int | None = Field(default=None, ge=1, le=128)
    dispatch_shots_per_tick: int | None = Field(default=None, ge=1, le=100)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionSessionBudgetExtendResponse(StrictModel):
    session: ProductionSessionFact
    extended: JsonObject
    rearmed_item_count: int = Field(ge=0)
    outcome: Literal["BUDGET_EXTENDED"]
    dispatched_count: int = Field(default=0, ge=0)
    waiting_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    idempotent_replay: bool = False


class ProductionSessionRunResponse(StrictModel):
    session: ProductionSessionFact
    outcome: str
    dispatched_count: int = Field(ge=0)
    waiting_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    idempotent_replay: bool = False


class ProductionSessionReconcileResponse(StrictModel):
    session: ProductionSessionFact
    dispatched_count: int = Field(ge=0)
    waiting_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)


class ProductionSessionReviewItem(StrictModel):
    session_item_id: str
    episode_id: str
    episode_code: str
    episode_title: str | None = None
    ordinal: int = Field(ge=1)
    item_revision: int = Field(ge=1)
    item_state: ProductionSessionItemState
    current_stage: str
    review_status: Literal["GENERATING", "BLOCKED", "READY_FOR_HUMAN_REVIEW", "REVIEWED"]
    choices: list[JsonObject]
    asset_inputs: list[JsonObject]
    timeline: JsonObject | None = None
    timeline_choice_consistency: JsonObject
    preview_render: JsonObject | None = None
    delivery: JsonObject | None = None
    blockers: list[JsonObject]
    repair_plan: JsonObject
    allowed_actions: list[str]


class ProductionSessionReviewPage(StrictModel):
    session_id: str
    project_id: str
    session_status: ProductionSessionStatus
    session_revision: int = Field(ge=1)
    summary: JsonObject
    items: list[ProductionSessionReviewItem]
    cursor: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    next_cursor: int | None = None
    read_only: Literal[True]
    human_approval_written: Literal[False]
    request_shape: Literal["bounded_production_session_review_v2"]


class ProductionSessionApprovalBinding(StrictModel):
    production_choice_id: str = Field(min_length=1, max_length=80)
    review_decision_id: str = Field(min_length=1, max_length=80)


class ProductionSessionEpisodeConfirmRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    approvals: list[ProductionSessionApprovalBinding] = Field(min_length=1, max_length=2000)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionSessionEpisodeConfirmResponse(StrictModel):
    session: ProductionSessionFact
    episode_id: str
    confirmed_choice_count: int = Field(ge=1)
    preview_render_review_decision_id: str
    outcome: Literal["EPISODE_CONFIRMED", "SESSION_COMPLETED"]
    idempotent_replay: bool = False


class ProductionChoiceRerollRequest(StrictModel):
    expected_session_revision: int = Field(ge=1)
    expected_choice_revision: int = Field(ge=1)
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionChoiceRerollResponse(StrictModel):
    choice: JsonObject
    session: ProductionSessionFact
    outcome: Literal["PREVIEW_REBUILD_QUEUED", "VIDEO_REBUILD_REQUIRED"]
    dispatched_count: int = Field(default=0, ge=0)
    waiting_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    idempotent_replay: bool = False


class ProductionSessionItemRetryRequest(StrictModel):
    expected_session_revision: int = Field(ge=1)
    expected_item_revision: int = Field(ge=1)
    strategy: Literal["RETRY_FAILED_STAGE", "RECOMPOSE_ONLY", "FULL_EPISODE"] = "RETRY_FAILED_STAGE"
    actor: str = Field(default="local-user", min_length=1, max_length=120)


class ProductionSessionItemRetryResponse(StrictModel):
    session: ProductionSessionFact
    item: JsonObject
    outcome: Literal["RETRY_QUEUED"]
    dispatched_count: int = Field(default=0, ge=0)
    waiting_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    idempotent_replay: bool = False
