from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FreshnessScope(BaseModel):
    type: Literal["PROJECT", "EPISODE", "SHOT"]
    id: str
    project_id: str
    episode_id: str | None = None
    shot_id: str | None = None


class FreshnessVersion(BaseModel):
    entity_type: str
    entity_id: str
    revision: int | str | None = None


class FreshnessReason(BaseModel):
    code: str
    message: str
    source_revision: int | str | None = None
    current_revision: int | str | None = None
    propagated_from: str | None = None


class FreshnessRemediationLink(BaseModel):
    rel: str
    href: str
    method: Literal["GET", "POST"]
    label: str


class FreshnessItem(BaseModel):
    id: str
    fact_type: Literal["VARIANT", "FRAME_BRIDGE", "TIMELINE"]
    status: Literal["CURRENT", "STALE"]
    project_id: str
    episode_id: str | None = None
    shot_id: str | None = None
    source: FreshnessVersion | None = None
    current: FreshnessVersion | None = None
    reasons: list[FreshnessReason] = Field(default_factory=list)
    remediation_links: list[FreshnessRemediationLink] = Field(default_factory=list)


class FreshnessSummary(BaseModel):
    returned: int
    stale: int
    current: int
    truncated: bool


class FreshnessAudit(BaseModel):
    read_only: Literal[True]
    writes_performed: Literal[0]
    query_count: int
    query_limit: int


class ProductionFreshnessResponse(BaseModel):
    scope: FreshnessScope
    summary: FreshnessSummary
    items: list[FreshnessItem]
    audit: FreshnessAudit
    local_only: Literal[True]
    network_contacted: Literal[False]
