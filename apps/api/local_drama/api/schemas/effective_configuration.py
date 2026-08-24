from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EffectiveConfigurationResolveRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=36)
    episode_id: str | None = Field(default=None, max_length=36)
    shot_id: str | None = Field(default=None, max_length=36)
    capability_code: str = Field(min_length=1, max_length=120)
    requested_profile_version_id: str | None = Field(default=None, max_length=36)
    run_overrides: dict[str, Any] = Field(default_factory=dict)
