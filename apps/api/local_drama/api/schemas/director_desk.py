from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
