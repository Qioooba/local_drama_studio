from __future__ import annotations

from pydantic import BaseModel, Field


class ModelCompatibilityRequest(BaseModel):
    model_artifact_id: str = Field(min_length=1)
