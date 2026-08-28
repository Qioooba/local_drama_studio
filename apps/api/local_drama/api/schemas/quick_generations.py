from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QuickGenerationPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story: str = Field(min_length=2, max_length=2000)
    mode: Literal["TEXT_TO_IMAGE", "TEXT_TO_VIDEO", "TEXT_TO_IMAGE_TO_VIDEO"] = "TEXT_TO_VIDEO"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    llm_profile_version_id: str = Field(min_length=1, max_length=100)
    image_profile_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    video_profile_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    image_candidate_count: int = Field(default=4, ge=1, le=8)
    allow_remote_outbound: bool = False
    llm_parameters: dict[str, Any] = Field(default_factory=dict)
    image_parameters: dict[str, Any] = Field(default_factory=dict)
    video_parameters: dict[str, Any] = Field(default_factory=dict)


class QuickGenerationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["SAME_INPUT", "NEW_SEED"]


class QuickGenerationPromptRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["KEYFRAME", "VIDEO"]


class QuickGenerationImageRerollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=4, ge=1, le=8)
    parent_candidate_id: str | None = Field(default=None, min_length=1, max_length=100)


class QuickGenerationImageSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_review_checks: Literal[True]


class QuickGenerationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: dict[str, Any]


class QuickGenerationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]
