from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Rational(BaseModel):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str = Field(min_length=1, max_length=200)
    episode_count: int = Field(ge=1)
    target_duration_ms: int = Field(gt=0)
    aspect_ratio: str | None = None
    fps: Rational | None = None
    allow_unconfigured_capabilities: bool = False


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class ShotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    target_duration_ms: int = Field(gt=0)
    shot_type: str = "OTHER"


class ShotRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    freeze: bool = False
