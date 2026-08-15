from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelCompatibilityRequest(BaseModel):
    model_artifact_id: str = Field(min_length=1)
    # The operator must be explicit about the profile capability being
    # checked.  Keeping this optional preserves the read-only registry's
    # backwards-compatible hash/quantization report path; when provided the
    # server performs a hard capability gate.
    required_capability: Literal["T2V", "I2V", "VIDEO", "IMAGE", "AUDIO", "TTS", "TEXT"] | None = None


class LocalModelReferenceRequest(BaseModel):
    code: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    kind: str = Field(min_length=1, max_length=48)
    machine_path_ref: str = Field(min_length=3, max_length=2048)
    license_note: str | None = Field(default=None, max_length=500)


class ModelLicenseEvidenceRequest(BaseModel):
    model_artifact_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1, max_length=512)
    license_name: str = Field(min_length=1, max_length=160)
    license_status: Literal["LOCAL_LICENSE_VERIFIED", "USER_OWNED"]


class ModelRegistryScanRequest(BaseModel):
    """Read-only scan request for user-owned local model files."""

    root_path: str = Field(min_length=3, max_length=2048)
    max_files: int = Field(default=200, ge=1, le=2000)
