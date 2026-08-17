from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkflowPackageRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    workflow: dict[str, Any]
    contract: dict[str, Any] = Field(default_factory=dict)
    node_bindings: dict[str, Any] = Field(default_factory=dict)
    runtime_contract: dict[str, Any] = Field(default_factory=dict)


class WorkflowCompileRequest(BaseModel):
    semantic_inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowPublishRequest(BaseModel):
    validation_id: str = Field(min_length=1, max_length=36)


class WorkflowRevokeRequest(BaseModel):
    """Operator-authored reason for retiring a workflow version."""

    reason: str = Field(min_length=1, max_length=1000)


class H3CandidateWorkflowRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    seed: int = Field(ge=0)
    duration_seconds: float = Field(default=4.0, ge=4.0, le=15.0)
    aspect_ratio: str = "16:9"
    filename_prefix: str = "local_drama/h3_candidate"
    sigma_points: int = Field(default=50, ge=2, le=1000)
    acceleration: str = "off"
    tier: str | None = Field(default=None, description="P1-7 生产档位（FAST/DRAFT/SCREEN/PRODUCTION/MASTER）；提供时覆盖分辨率与帧数")


class H3I2VCandidateWorkflowRequest(H3CandidateWorkflowRequest):
    first_frame: str = "pending-approved-keyframe.png"
    aspect_ratio: str = "auto"
    filename_prefix: str = "local_drama/h3_i2v_candidate"


class ComfyWorkerRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
