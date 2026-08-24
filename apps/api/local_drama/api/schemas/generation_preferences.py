from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerationPreferencePutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_type: Literal["PROJECT", "EPISODE", "SHOT"]
    owner_id: str = Field(min_length=1, max_length=36)
    capability: str = Field(min_length=1, max_length=120)
    resolution_mode: Literal["AUTO", "EXPLICIT"]
    execution_profile_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    settings: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_profile_mode(self) -> "GenerationPreferencePutRequest":
        if self.resolution_mode == "AUTO" and self.execution_profile_version_id is not None:
            raise ValueError("AUTO mode cannot pin execution_profile_version_id")
        if self.resolution_mode == "EXPLICIT" and self.execution_profile_version_id is None:
            raise ValueError("EXPLICIT mode requires execution_profile_version_id")
        return self
