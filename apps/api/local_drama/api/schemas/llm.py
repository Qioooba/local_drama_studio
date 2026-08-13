from __future__ import annotations

from pydantic import BaseModel, Field


class LLMProfileSyncRequest(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=200)


class LLMProfilePublishRequest(BaseModel):
    profile_version_id: str = Field(min_length=1)
