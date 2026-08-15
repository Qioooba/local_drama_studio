from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreativeEntryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content: dict[str, Any]
    change_note: str = Field(min_length=1, max_length=500)


class CreativeRevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: dict[str, Any]
    change_note: str = Field(min_length=1, max_length=500)


class CreativeRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1)
    change_note: str = Field(min_length=1, max_length=500)
