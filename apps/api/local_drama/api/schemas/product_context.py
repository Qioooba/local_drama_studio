from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntitySummary(StrictModel):
    id: str
    code: str
    title: str
    status: str | None = None


class EpisodeContext(StrictModel):
    id: str
    code: str
    title: str
    production_status: str
    season_id: str
    season_code: str
    season_title: str
    project_id: str


class TaskSummary(StrictModel):
    active_worker_count: int
    attention_job_count: int


class AppContextResponse(StrictModel):
    project: EntitySummary | None
    episode: EpisodeContext | None
    task_summary: TaskSummary
    capabilities: dict[Literal["can_edit", "can_run_jobs", "can_manage_system"], bool]
    read_only: Literal[True]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]
    mutated: Literal[False]


class ProjectSummary(EntitySummary):
    revision: int


class EpisodeSummary(StrictModel):
    id: str
    code: str
    title: str
    number: int | None = None
    display_order: int | None = None
    production_status: str
    target_duration_ms: int | None = None
    preview_render_id: str | None = None
    preview_media_version_id: str | None = None


class SeasonSummary(StrictModel):
    id: str
    code: str
    title: str
    number: int | None = None
    display_order: int | None = None
    episodes: list[EpisodeSummary]


class RouteTarget(StrictModel):
    kind: Literal["PROJECT_STRUCTURE", "STORY", "ASSETS", "SETTINGS", "SHOT_STUDIO", "EPISODE_PRODUCTION"]
    project_id: str
    episode_id: str | None = None
    section: str | None = None
    focus: str | None = None


class NextAction(StrictModel):
    title: str
    description: str
    label: str
    reason_code: str | None = None
    target: RouteTarget


class OverviewBlocker(StrictModel):
    code: str
    label: str
    owner: RouteTarget


class ActivitySummary(StrictModel):
    event_id: int
    type: str
    subject_type: str
    subject_id: str
    occurred_at: str


class ProjectOverviewResponse(StrictModel):
    project: ProjectSummary
    next_action: NextAction
    blockers: list[OverviewBlocker]
    seasons: list[SeasonSummary]
    recent_activity: list[ActivitySummary]
    observed_at: str
    read_only: Literal[True]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]
    mutated: Literal[False]
