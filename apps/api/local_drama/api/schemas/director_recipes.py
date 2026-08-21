from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DirectorRecipeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    recipe: dict[str, Any]
    reason: str = Field(default="", max_length=1000)


class DirectorRecipeVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: dict[str, Any]
    reason: str = Field(default="", max_length=1000)


class DirectorRecipeBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_version_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=1)
