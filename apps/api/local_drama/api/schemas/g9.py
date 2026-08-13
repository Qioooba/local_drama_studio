from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CanvasLayoutRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    viewport: dict[str, float] = Field(default_factory=dict)


class CanvasPlanRequest(BaseModel):
    mode: str = Field(pattern="^(NODE|FROM|TO|RANGE)$")
    from_node_id: str | None = None
    to_node_id: str | None = None
    max_nodes: int = Field(default=300, ge=1, le=1000)
