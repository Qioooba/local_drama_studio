from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkspaceAssetAuthorizationRequest(BaseModel):
    media_version_id: str = Field(min_length=1)


class BrandKitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    tokens: dict[str, Any] = Field(min_length=1)
