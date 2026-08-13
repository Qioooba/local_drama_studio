from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GenerationIntentRequest(BaseModel):
    project_id: str = Field(min_length=1)
    owner_type: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    creative_goal: str = Field(min_length=1)


class ExperimentPlanRequest(BaseModel):
    intent_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    axes: dict[str, list[Any]]
    max_parallel: int = Field(default=1, ge=1, le=64)
    resource_estimate: dict[str, Any] = Field(default_factory=dict)


class ExperimentExpandRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)


class ExperimentConfirmRequest(ExperimentExpandRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirm_large_matrix: bool = False
