from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
