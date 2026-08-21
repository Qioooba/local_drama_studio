from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ShotGroupKind = Literal["BEAT", "DIALOGUE", "ACTION", "MONTAGE", "CUSTOM"]


class ShotSceneAssignmentRequest(BaseModel):
    scene_id: str | None = Field(default=None, max_length=36)
    expected_revision: int = Field(ge=1)


class ShotGroupCreateRequest(BaseModel):
    kind: ShotGroupKind
    code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    scene_id: str | None = Field(default=None, max_length=36)
    metadata: dict[str, Any] = Field(default_factory=dict)
    order_key: str | None = Field(default=None, min_length=1, max_length=64)


class ShotGroupUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    kind: ShotGroupKind
    code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    scene_id: str | None = Field(default=None, max_length=36)
    metadata: dict[str, Any] = Field(default_factory=dict)
    order_key: str = Field(min_length=1, max_length=64)


class ExpectedRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class ShotGroupMembersRequest(ExpectedRevisionRequest):
    shot_ids: list[str] = Field(default_factory=list, max_length=1000)


class ShotGroupReorderItem(BaseModel):
    group_id: str = Field(min_length=1, max_length=36)
    order_key: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)


class ShotGroupReorderRequest(BaseModel):
    items: list[ShotGroupReorderItem] = Field(max_length=1000)
