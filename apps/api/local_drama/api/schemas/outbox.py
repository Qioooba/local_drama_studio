from __future__ import annotations

from pydantic import BaseModel, Field


class OutboxDeliveryRequest(BaseModel):
    endpoint_url: str = Field(min_length=1, max_length=500)
    project_id: str | None = None
    after_event_id: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
