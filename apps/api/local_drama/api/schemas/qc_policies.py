from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QcPolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_type: Literal["PROJECT", "EPISODE", "SHOT"]
    owner_id: str = Field(min_length=1, max_length=36)
    stage: Literal["IMAGE", "VIDEO", "AUDIO", "CONTINUITY", "DELIVERY"]
    policy: dict[str, Any] = Field(default_factory=dict)
    max_auto_rerolls: int = Field(ge=0, le=10)
    auto_reroll_categories: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=1)


class VariantQcDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine_check_run_id: str = Field(min_length=1, max_length=36)
    category: str = Field(min_length=1, max_length=40)


class VariantQcAttachChildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1, max_length=36)
    child_variant_id: str = Field(min_length=1, max_length=36)
