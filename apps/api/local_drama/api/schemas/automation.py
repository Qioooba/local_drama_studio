from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AutomationScope = Literal["read", "plan", "submit", "review", "delivery"]


class AutomationClientRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None
    scopes: list[AutomationScope] = Field(min_length=1, max_length=5)


class WebhookSubscriptionRequest(BaseModel):
    endpoint_url: str = Field(min_length=1, max_length=500)
    project_id: str | None = None
    event_types: list[str] = Field(default_factory=list, max_length=50)


class WebhookDeliverRequest(BaseModel):
    subscription_id: str | None = None
    project_id: str | None = None
    after_event_id: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
