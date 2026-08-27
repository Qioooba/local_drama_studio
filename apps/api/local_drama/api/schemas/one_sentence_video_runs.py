from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OneSentenceVideoPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story: str = Field(min_length=2, max_length=2000)
    mode: Literal["DIRECT_T2V", "KEYFRAME_I2V"] = "DIRECT_T2V"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    llm_profile_version_id: str = Field(min_length=1, max_length=100)
    image_profile_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    video_profile_version_id: str = Field(min_length=1, max_length=100)
    image_candidate_count: int = Field(default=4, ge=1, le=8)
    allow_remote_outbound: bool = False


class OneSentenceVideoRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["SAME_INPUT", "NEW_SEED"]


class OneSentenceImageRerollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=4, ge=1, le=8)
    parent_candidate_id: str | None = Field(default=None, min_length=1, max_length=100)


class OneSentenceImageSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_review_checks: Literal[True]
