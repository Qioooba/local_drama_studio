from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuickGenerationPresetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    capability: str = Field(min_length=1, max_length=120)
    execution_profile_version_id: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    favorite: bool = True


class QuickGenerationPresetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    execution_profile_version_id: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    favorite: bool
    expected_revision: int = Field(ge=1)


class QuickGenerationPresetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: dict[str, Any]


class QuickGenerationPresetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]


class QuickGenerationPresetDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool
    preset_id: str
