from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class ShotRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    freeze: bool = False
