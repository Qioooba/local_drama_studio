from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class GpuRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_runtime: dict[str, Any]
