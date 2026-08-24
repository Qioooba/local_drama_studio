from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class FrameBridgeRevisionRequest(BaseModel):
    expected_boundary_revision: int = Field(ge=1)


class FrameBridgeInheritRequest(FrameBridgeRevisionRequest):
    source_anchor_id: str | None = Field(default=None, min_length=1)
    lock: bool | None = None


class FrameBridgeCurrentFrameRequest(FrameBridgeRevisionRequest):
    media_version_id: str | None = Field(default=None, min_length=1)
    frame_anchor_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> "FrameBridgeCurrentFrameRequest":
        if (self.media_version_id is None) == (self.frame_anchor_id is None):
            raise ValueError("必须且只能提供 media_version_id 或 frame_anchor_id")
        return self


class FrameBridgeSourceFrameRequest(FrameBridgeRevisionRequest):
    frame_anchor_id: str = Field(min_length=1)


class DirectorDeskProject(BaseModel):
    id: str
    code: str
    name: str
    aspect_ratio: str | None = None


class DirectorDeskEpisode(BaseModel):
    id: str
    code: str
    title: str | None = None
    status: str
    shot_count: int
    approved_count: int
    blocked_count: int


class ShotNavigatorItem(BaseModel):
    id: str
    code: str
    order_key: str
    scene_id: str | None = None
    scene_code: str | None = None
    scene_title: str | None = None
    group_id: str | None = None
    group_code: str | None = None
    group_title: str | None = None
    thumbnail_media_version_id: str | None = None
    current_video_media_version_id: str | None = None
    status: str
    continuity_status: str
    job_status: str | None = None


class ShotNavigatorWindow(BaseModel):
    items: list[ShotNavigatorItem]
    total: int
    selected_index: int
    window_start: int
    window_end: int
    has_previous: bool
    has_next: bool


class FrameAnchorProjection(BaseModel):
    anchor_id: str
    inherited_from_anchor_id: str | None = None
    source_media_version_id: str
    media_version_id: str
    rel_path: str | None = None
    role_hint: str | None = None
    source: Literal["INHERITED", "EXPLICIT", "GENERATED", "EXTRACTED"]
    status: Literal["AUTO_INHERITED", "EXPLICIT", "GENERATED", "LOCKED", "STALE", "CONFLICT", "MISSING"]
    stale: bool
    stale_reason: str | None = None


class FrameBoundaryProjection(BaseModel):
    transition_id: str
    boundary_revision: int = Field(ge=1)
    from_shot_id: str
    from_shot_code: str
    to_shot_id: str
    to_shot_code: str
    enforcement: str
    compatibility: str
    stale: bool
    stale_reason: str | None = None
    previous_end: FrameAnchorProjection | None = None
    current_start: FrameAnchorProjection | None = None


class FrameBridgeProjection(BaseModel):
    previous: FrameBoundaryProjection | None = None
    current_start: FrameAnchorProjection | None = None
    current_end: FrameAnchorProjection | None = None
    next: FrameBoundaryProjection | None = None
    compatibility: str
    stale: bool


class CurrentShotProjection(BaseModel):
    shot: dict[str, Any]
    current_revision: dict[str, Any] | None
    source_context: dict[str, Any]
    assets: list[dict[str, Any]]
    asset_states: list[dict[str, Any]]
    selected_variant: dict[str, Any] | None
    current_media: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    frame_bridge: FrameBridgeProjection
    qc_summary: dict[str, Any]
    review_summary: dict[str, Any]
    generation_preferences: dict[str, Any]
    active_jobs: list[dict[str, Any]]
    blockers: list[dict[str, Any]]


class DirectorDeskPermissions(BaseModel):
    can_edit: bool = True
    can_generate: bool = True
    can_approve: bool = True


class DirectorDeskResponse(BaseModel):
    project: DirectorDeskProject
    episode: DirectorDeskEpisode
    shot_nav: ShotNavigatorWindow
    current_shot: CurrentShotProjection
    permissions: DirectorDeskPermissions = Field(default_factory=DirectorDeskPermissions)
    read_only: bool = True
    request_shape: Literal["bounded_director_desk_read_model"] = "bounded_director_desk_read_model"
