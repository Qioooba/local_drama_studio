from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ComfyLabCaptureRequest(BaseModel):
    title: str = Field(default="Designer capture", min_length=1, max_length=200)
    workflow: dict[str, Any]


class ComfyLabTestRunRequest(BaseModel):
    workflow: dict[str, Any] | None = None
    capture_id: str | None = Field(default=None, min_length=36, max_length=36)
    execute: bool = False


class ComfyLabPromoteRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    contract: dict[str, Any] = Field(default_factory=dict)
    node_bindings: dict[str, Any] = Field(default_factory=dict)
    runtime_contract: dict[str, Any] = Field(default_factory=lambda: {"transport": "LOOPBACK_HTTP", "candidate": True})


class ComfyLabDiscoverRequest(BaseModel):
    apply: bool = False


class ComfyLabConfigureRequest(BaseModel):
    python_path: str = Field(min_length=1, max_length=1024)
    root_path: str = Field(min_length=1, max_length=1024)
    port: int = Field(default=8188, ge=1024, le=65535)


# ---------------------------------------------------------------------------
# Typed response contracts for the Designer control plane.  ``extra="allow"``
# keeps undocumented service-side keys visible during the §11.3 migration so a
# future field can never be silently dropped by an outdated envelope model;
# the documented core below mirrors application.comfy_lab exactly.
# ---------------------------------------------------------------------------


class _ComfyLabResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class ComfyLabConfiguration(_ComfyLabResponse):
    configured: bool
    python: str | None = None
    root: str | None = None
    port: int
    endpoint: str | None = None
    source: Literal["ENVIRONMENT", "SAVED", "NONE"]


class _ComfyLabStatusFields(_ComfyLabResponse):
    status: Literal["RUNNING", "STOPPED", "STARTING"]
    pid: int | None = None
    session_id: str | None = None
    started_at: str | None = None
    endpoint: str | None = None
    launch_configured: bool
    sandbox_root: str
    formal_project_write: bool
    local_only: bool
    network_contacted: bool
    stale_state: bool


class ComfyLabStatusResponse(_ComfyLabStatusFields):
    pass


class ComfyLabSessionDesigner(_ComfyLabResponse):
    role: Literal["WORKFLOW_DESIGNER"]
    production_isolation: bool
    capture_target: Literal["COMFY_LAB_SANDBOX_ONLY"]
    formal_project_write: bool


class ComfyLabSessionResponse(_ComfyLabResponse):
    session: ComfyLabStatusResponse
    designer: ComfyLabSessionDesigner
    runtime_contacted: bool
    network_contacted: bool
    mutated: bool


class ComfyLabDiscoveryCandidate(_ComfyLabResponse):
    python_path: str
    root_path: str
    port: int


class ComfyLabDiscoveryResponse(_ComfyLabResponse):
    status: Literal["CONFIGURED", "FOUND", "NOT_FOUND"]
    candidates: list[ComfyLabDiscoveryCandidate]
    applied: bool
    configuration: ComfyLabConfiguration
    searched_roots: list[str]
    runtime_contacted: bool
    network_contacted: bool


class ComfyLabConfigureResponse(_ComfyLabResponse):
    configured: bool
    python: str | None = None
    root: str | None = None
    port: int
    endpoint: str | None = None
    source: Literal["ENVIRONMENT", "SAVED", "NONE"]
    persisted: bool
    runtime_contacted: bool
    network_contacted: bool


class ComfyLabLifecycleResponse(_ComfyLabStatusFields):
    runtime_contacted: bool
    idempotent_replay: bool | None = None


class ComfyLabCaptureItem(_ComfyLabResponse):
    capture_id: str
    title: str
    content_hash: str
    sandbox_rel_path: str
    test_evidence: dict[str, Any] | None = None


class ComfyLabCapturesPage(_ComfyLabResponse):
    items: list[ComfyLabCaptureItem]
    runtime_contacted: bool


class ComfyLabCaptureCreated(_ComfyLabResponse):
    status: Literal["CAPTURED"]
    capture_id: str
    content_hash: str
    sandbox_rel_path: str
    formal_project_write: bool
    runtime_contacted: bool
    network_contacted: bool


class ComfyLabCaptureDetail(_ComfyLabResponse):
    schema_version: Literal["localdrama.comfy-lab-capture.v1"]
    capture_id: str
    title: str
    content_hash: str
    workflow: dict[str, Any]
    formal_project_write: bool
    local_only: bool
    test_evidence: dict[str, Any] | None = None
    sandbox_rel_path: str


class ComfyLabCapturePromoted(_ComfyLabResponse):
    # workflow_version comes from WorkflowService.register_package and keeps
    # its full payload until that service gains its own typed contract.
    workflow_version: dict[str, Any]
    source_capture_id: str


class ComfyLabTestPlan(_ComfyLabResponse):
    content_hash: str
    sandbox_root: str
    designer_endpoint: str | None = None
    formal_project_write: bool
    local_only: bool


class ComfyLabTestRunResponse(_ComfyLabResponse):
    status: Literal["READY", "BLOCKED", "PASS"]
    blockers: list[str] = Field(default_factory=list)
    plan: ComfyLabTestPlan
    would_contact_comfyui: bool
    network_contacted: bool
    capture_id: str | None = None
    content_hash: str | None = None
    prompt_id: str | None = None
    runtime_status: str | None = None
    tested_at: str | None = None
    designer_endpoint: str | None = None
    formal_project_write: bool | None = None


# Route-level envelopes mirror exactly what the route handlers return.


class ComfyLabStatusEnvelope(_ComfyLabResponse):
    status: ComfyLabStatusResponse


class ComfyLabLifecycleEnvelope(_ComfyLabResponse):
    status: ComfyLabLifecycleResponse


class ComfyLabDiscoveryEnvelope(_ComfyLabResponse):
    discovery: ComfyLabDiscoveryResponse


class ComfyLabConfigureEnvelope(_ComfyLabResponse):
    configuration: ComfyLabConfigureResponse


class ComfyLabCaptureCreatedEnvelope(_ComfyLabResponse):
    capture: ComfyLabCaptureCreated


class ComfyLabCaptureDetailEnvelope(_ComfyLabResponse):
    capture: ComfyLabCaptureDetail
    runtime_contacted: bool


class ComfyLabTestRunEnvelope(_ComfyLabResponse):
    test_run: ComfyLabTestRunResponse


class ComfyLabCapturePromotedEnvelope(_ComfyLabResponse):
    workflow_version: dict[str, Any]
    source_capture_id: str
