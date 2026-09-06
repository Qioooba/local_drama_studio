from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class StrictModel(BaseModel):
    """Shared wire-model policy for typed v2 contracts."""

    model_config = ConfigDict(extra="forbid")


class ShotReadinessFact(StrictModel):
    """Shot-level generation readiness, separate from episode-stage progress."""

    status: str
    ready: bool
    allowed_actions: list[str]


class LocalArtifactReference(StrictModel):
    scope: Literal["PROJECT", "DATA"]
    kind: Literal["FILE", "DIRECTORY"]
    display_name: str = Field(min_length=1)
    server_absolute_path: str = Field(min_length=1)
    rel_path: str = Field(min_length=1)
    download_url: str = Field(pattern=r"^/api/v1/")
    download_filename: str = Field(min_length=1)
