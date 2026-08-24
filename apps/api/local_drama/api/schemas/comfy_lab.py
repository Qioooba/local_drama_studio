from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ComfyLabCaptureRequest(BaseModel):
    title: str = Field(default="Designer capture", min_length=1, max_length=200)
    workflow: dict[str, Any]


class ComfyLabTestRunRequest(BaseModel):
    workflow: dict[str, Any]
    execute: bool = False


class ComfyLabDiscoverRequest(BaseModel):
    apply: bool = False


class ComfyLabConfigureRequest(BaseModel):
    python_path: str = Field(min_length=1, max_length=1024)
    root_path: str = Field(min_length=1, max_length=1024)
    port: int = Field(default=8188, ge=1024, le=65535)
