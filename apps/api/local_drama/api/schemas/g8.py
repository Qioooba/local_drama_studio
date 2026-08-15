from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TimelineItemRequest(BaseModel):
    track_type: str = Field(default="VIDEO", min_length=1, max_length=32)
    media_version_id: str | None = None
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class TimelineRevisionRequest(BaseModel):
    items: list[TimelineItemRequest] = Field(min_length=1)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="DRAFT", min_length=1, max_length=24)


class SubtitleCueRequest(BaseModel):
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    text: str = Field(min_length=1)
    style: dict[str, Any] = Field(default_factory=dict)


class SubtitleAuthorityRequest(BaseModel):
    text_authority: Literal["SCRIPT"]
    source_document_version_id: str = Field(min_length=1)
    asr_alignment_media_version_id: str | None = None
    asr_profile_version_id: str | None = None


class SubtitleRevisionRequest(BaseModel):
    cues: list[SubtitleCueRequest] = Field(min_length=1)
    format: str = Field(default="SRT", min_length=3, max_length=16)
    authority: SubtitleAuthorityRequest


class AudioBindingRequest(BaseModel):
    media_version_id: str = Field(min_length=1)
    track_type: str = Field(default="AUDIO", min_length=1, max_length=32)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    gain_db: float = 0.0
    source_license_status: str = Field(default="VERIFIED_LOCAL", min_length=1, max_length=24)
    license_evidence_path_rel: str = Field(min_length=1)
    loop_enabled: bool = False
    fade_in_us: int = Field(default=0, ge=0)
    fade_out_us: int = Field(default=0, ge=0)


class FrameAnchorRequest(BaseModel):
    source_time_us: int | None = Field(default=None, ge=0)
    source_frame_index: int | None = Field(default=None, ge=0)
    position_mode: str | None = Field(default=None, pattern=r"^(FIRST_FRAME|LAST_FRAME)$")
    role_hint: str = Field(default="LAST_FRAME", min_length=1, max_length=40)


class TransitionConstraintRequest(BaseModel):
    from_shot_id: str = Field(min_length=1)
    to_shot_id: str = Field(min_length=1)
    constraint_type: str = Field(min_length=1, max_length=48)
    from_anchor_id: str | None = None
    to_anchor_id: str | None = None
    enforcement: str = Field(default="HARD", min_length=1, max_length=16)
    note: str | None = None


class PostProcessRecipeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    steps: list[dict[str, Any]] = Field(min_length=1)
    capability_contract: dict[str, Any] = Field(default_factory=dict)
    parent_recipe_id: str | None = None


class EnhancementPlanRequest(BaseModel):
    input_media_version_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class EnhancementRunRequest(BaseModel):
    input_media_version_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class RenderEpisodeRequest(BaseModel):
    timeline_revision_id: str = Field(min_length=1)


class DeliveryBuildRequest(BaseModel):
    episode_render_version_id: str = Field(min_length=1)
    target_version_id: str = Field(min_length=1)


class DeliveryWithdrawRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
