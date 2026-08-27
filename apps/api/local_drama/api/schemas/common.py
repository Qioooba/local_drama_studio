from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

JsonObject = dict[str, Any]


class StrictModel(BaseModel):
    """Shared wire-model policy for typed v2 contracts."""

    model_config = ConfigDict(extra="forbid")
