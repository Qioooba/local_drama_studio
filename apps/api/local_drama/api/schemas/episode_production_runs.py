from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EpisodeCheckpointPolicy = Literal[
    "AUTO_CONTINUE", "AFTER_ASSETS", "AFTER_SHOT_PLAN", "BEFORE_VIDEO", "ON_EXCEPTION"
]


class EpisodeProductionRunRequest(BaseModel):
    tts_enabled: bool = True
    production_mode: Literal["DRAFT", "BALANCED", "QUALITY"] = "BALANCED"
    checkpoint_policy: EpisodeCheckpointPolicy = "ON_EXCEPTION"
    min_free_disk_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1, le=1 << 50)


class EpisodeProductionPauseRequest(BaseModel):
    reason: str = Field(default="MANUAL_PAUSE", min_length=1, max_length=500)


class EpisodeProductionResumeRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2_000)
