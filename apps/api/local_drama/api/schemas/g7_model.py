from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelCompatibilityRequest(BaseModel):
    model_artifact_id: str = Field(min_length=1)


class ModelLicenseEvidenceRequest(BaseModel):
    model_artifact_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1, max_length=512)
    license_name: str = Field(min_length=1, max_length=160)
    license_status: Literal["LOCAL_LICENSE_VERIFIED", "USER_OWNED"]
