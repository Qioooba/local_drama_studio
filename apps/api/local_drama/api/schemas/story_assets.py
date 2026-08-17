from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STORY_ASSET_KINDS = ("CHARACTER", "SCENE", "PROP", "COSTUME")


class StoryAssetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=16)
    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    canonical_media_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    extra: dict[str, Any] | None = None


class StoryAssetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    canonical_media_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    extra: dict[str, Any] | None = None


class StoryAssetArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ShotAssetBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=36)
    role_in_shot: str = Field(default="main", min_length=1, max_length=120)
