from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewTemplateItemRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    required: bool = True


class ReviewTemplateVersionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    subject_type: str = Field(min_length=1, max_length=40)
    items: list[ReviewTemplateItemRequest] = Field(min_length=1)


class MachineCheckRequest(BaseModel):
    policy_version: str = "g4_media_qc_v1"

