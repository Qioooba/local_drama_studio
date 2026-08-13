from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    owner_type: str = Field(min_length=1, max_length=40)
    owner_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content_text: str = Field(min_length=1)
    structured: dict[str, Any] = Field(default_factory=dict)


class PromptBranchRequest(BaseModel):
    content_text: str = Field(min_length=1)
    structured: dict[str, Any] = Field(default_factory=dict)
