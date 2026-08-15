from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceAssetAuthorizationRequest(BaseModel):
    media_version_id: str = Field(min_length=1)


class ProjectAssetGrantRequest(BaseModel):
    authorization_id: str = Field(min_length=1)
    access_mode: Literal["READ_ONLY", "DERIVED"]


class ProjectAssetGrantRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class BrandKitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    tokens: dict[str, Any] = Field(min_length=1)


class WatermarkProfileRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(min_length=1)


class CompliancePolicyRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    rules: dict[str, Any] = Field(min_length=1)
