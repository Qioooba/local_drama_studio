from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AutomationWorkflowRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    mode: Literal["MANUAL", "ASSISTED", "BATCH_AUTOMATED"] = "ASSISTED"
    nodes: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    batch_items: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    max_iterations: int = Field(default=1, ge=1, le=100_000)
    max_tasks: int = Field(default=100, ge=1, le=100_000)
    max_disk_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    human_gate: Literal["NONE", "BEFORE_RUN", "EACH_ITERATION", "ON_CONDITION"] = "ON_CONDITION"
    repeat_batch: bool = False


class AutomationWorkflowRunRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AutomationWorkflowStepRequest(BaseModel):
    machine_context: dict[str, Any] = Field(default_factory=dict)
    ai_scores: dict[str, Any] = Field(default_factory=dict)
    produced_bytes: int = Field(default=0, ge=0, le=1 << 50)


class AutomationWorkflowResumeRequest(BaseModel):
    decision: Literal["HUMAN_APPROVED", "HUMAN_REJECTED"]
    note: str = Field(min_length=1, max_length=2_000)


class AutomationWorkflowPauseRequest(BaseModel):
    reason: str = Field(default="MANUAL_PAUSE", min_length=1, max_length=500)
