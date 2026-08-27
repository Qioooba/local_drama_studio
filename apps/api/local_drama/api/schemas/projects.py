from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_drama.domain.director_intent import normalize_director_intent_v3


class Rational(BaseModel):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)


class ProjectProfileBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(min_length=1, max_length=120)
    profile_version_id: str = Field(min_length=1, max_length=64)


class ProjectProductionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    plan: dict[str, Any]


class ProjectDeliveryTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    spec: dict[str, Any]


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str = Field(min_length=1, max_length=200)
    episode_count: int = Field(ge=1)
    season_count: int = Field(default=1, ge=1)
    target_duration_ms: int = Field(gt=0)
    aspect_ratio: str | None = None
    fps: Rational | None = None
    width: int | None = Field(default=None, ge=64, le=16384)
    height: int | None = Field(default=None, ge=64, le=16384)
    primary_language: str | None = Field(default=None, min_length=2, max_length=32)
    subtitle_mode: str | None = None
    subtitle_language: str | None = Field(default=None, min_length=2, max_length=32)
    production_plan: ProjectProductionPlanRequest | None = None
    profile_bindings: list[ProjectProfileBindingRequest] = Field(default_factory=list)
    delivery_target: ProjectDeliveryTargetRequest | None = None
    allow_unconfigured_capabilities: bool = False


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class ProjectEpisodeAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season_id: str | None = Field(default=None, min_length=1, max_length=64)
    create_new_season: bool = False
    season_title: str | None = Field(default=None, max_length=200)
    episode_title: str = Field(min_length=1, max_length=200)
    target_duration_ms: int = Field(gt=0, le=86_400_000)

    @model_validator(mode="after")
    def validate_season_target(self) -> "ProjectEpisodeAppendRequest":
        if self.create_new_season == bool(self.season_id):
            raise ValueError("必须选择已有季度，或明确新建季度（二选一）")
        return self


class ProjectTemplateCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str = Field(min_length=1, max_length=200)


class ProjectPackageDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rel_path: str = Field(min_length=1, max_length=500)


class ShotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    target_duration_ms: int = Field(gt=0)
    shot_type: str = "OTHER"


class SceneCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=500)
    time_of_day: str | None = Field(default=None, max_length=120)


class EpisodeSceneRangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    source_label: str | None = Field(default=None, max_length=500)


class DirectorIntentCompositionV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    framing: str | None = None
    subject_position: str | None = None
    headroom: str | None = None
    lead_room: str | None = None
    screen_direction: str | None = None
    axis_rule: str | None = None
    depth_plan: str | None = None


class DirectorIntentPerformanceV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion: str | None = None
    intensity: float | None = Field(default=None, ge=0, le=1, strict=True)
    body_action: str | None = None
    facial_action: str | None = None
    eye_line: str | None = None
    blocking_summary: str | None = None


class DirectorIntentV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["director-intent.v3"]
    shot_type: str | None = None
    composition: DirectorIntentCompositionV3
    subject_action: str | None = None
    performance: DirectorIntentPerformanceV3
    camera_plan: dict[str, Any] | None = None
    target_duration_ms: int | None = Field(default=None, gt=0, strict=True)
    dialogue: list[Any] | str | None = None
    environment: str | None = None
    continuity: str | None = None
    transition_plan: dict[str, Any] | None = None
    sound_plan: dict[str, Any] | None = None
    creative_intent: str | None = None
    staging: dict[str, Any] | None = None
    staging_3d: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schema_version") == "director-intent.v3":
            return value
        return normalize_director_intent_v3(value) if isinstance(value, dict) else value


class StoryboardBatchEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    target_duration_ms: int | None = Field(default=None, gt=0)
    shot_type: str | None = Field(default=None, min_length=1, max_length=64)
    fields: dict[str, Any] | None = None


class StoryboardBatchCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_shot_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)


class StoryboardBatchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered_shot_ids: list[str]
    edits: list[StoryboardBatchEdit] = Field(default_factory=list)
    copies: list[StoryboardBatchCopy] = Field(default_factory=list)


class StoryboardBatchCommitRequest(StoryboardBatchPlanRequest):
    expected_plan_hash: str = Field(pattern="^[0-9a-f]{64}$")
