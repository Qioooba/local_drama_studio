from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeKind = Literal["TEXT_REF", "STORY_ASSET_REF", "MEDIA_REF", "SHOT_REF", "GENERATION_INTENT", "TRANSFORM_INTENT", "COMPARE_SET", "SEQUENCE_PREVIEW", "OUTPUT_DRAFT", "NOTE", "FRAME"]
EdgeKind = Literal["REFERENCES", "GUIDES", "DERIVES", "COMPARES", "SEQUENCES"]


class VisualLabCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    episode_id: str | None = None


class VisualLabNodeCreateRequest(BaseModel):
    node_kind: NodeKind
    position_x: float = Field(ge=-100000, le=100000)
    position_y: float = Field(ge=-100000, le=100000)
    width: float = Field(default=260, ge=160, le=1200)
    height: float = Field(default=180, ge=80, le=900)
    z_index: int = Field(default=0, ge=-1000, le=1000)
    content: dict[str, Any]
    expected_topology_revision: int = Field(ge=1)


class VisualLabNodeRevisionRequest(BaseModel):
    content: dict[str, Any]
    change_note: str = Field(min_length=1, max_length=1000)
    expected_revision: int = Field(ge=1)


class VisualLabNodeMove(BaseModel):
    id: str
    position_x: float = Field(ge=-100000, le=100000)
    position_y: float = Field(ge=-100000, le=100000)
    width: float | None = Field(default=None, ge=160, le=2400)
    height: float | None = Field(default=None, ge=80, le=1800)
    z_index: int | None = Field(default=None, ge=-1000, le=1000)
    collapsed: bool | None = None
    expected_revision: int = Field(ge=1)


class VisualLabMoveRequest(BaseModel):
    nodes: list[VisualLabNodeMove] = Field(min_length=1, max_length=300)


class VisualLabViewportRequest(BaseModel):
    x: float = Field(ge=-10_000_000, le=10_000_000)
    y: float = Field(ge=-10_000_000, le=10_000_000)
    zoom: float = Field(ge=0.05, le=4)


class VisualLabDuplicateRequest(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=100)
    offset_x: float = Field(default=48, ge=-1000, le=1000)
    offset_y: float = Field(default=48, ge=-1000, le=1000)
    expected_topology_revision: int = Field(ge=1)


class VisualLabBatchDeleteRequest(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=100)
    expected_topology_revision: int = Field(ge=1)


class VisualLabSnapshotRestoreRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class VisualLabDeleteNodeRequest(BaseModel):
    expected_topology_revision: int = Field(ge=1)


class VisualLabEdgeCreateRequest(BaseModel):
    source_node_id: str
    source_port: str = Field(min_length=1, max_length=80)
    target_node_id: str
    target_port: str = Field(min_length=1, max_length=80)
    edge_kind: EdgeKind
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_topology_revision: int = Field(ge=1)


class VisualLabRunRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class VisualLabPromotionRequest(BaseModel):
    source_media_version_id: str
    target_type: Literal["SHOT_CANDIDATE"]
    target_id: str
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class VisualLabPromotionPreflightRequest(BaseModel):
    source_media_version_id: str
    target_type: Literal["SHOT_CANDIDATE"]
    target_id: str
