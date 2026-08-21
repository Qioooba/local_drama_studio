from __future__ import annotations

from pydantic import BaseModel, Field


class BeatReplanPlanRequest(BaseModel):
    draft_id: str = Field(min_length=1, max_length=36)
    proposal_scene_no: int = Field(ge=1)
    expected_group_revision: int = Field(ge=1)


class BeatReplanApplyRequest(BeatReplanPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
