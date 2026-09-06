from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DialogueLineRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    speaker: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1)
    pronunciation: dict[str, Any] = Field(default_factory=dict)
    shot_id: str | None = None


class DialogueTextRevisionRequest(BaseModel):
    expected_revision_no: int = Field(ge=1)
    text: str = Field(min_length=1)
    pronunciation: dict[str, Any] = Field(default_factory=dict)


class VoiceProfileRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    voice_ref: str = Field(min_length=1)
    license_status: Literal["USER_OWNED", "VERIFIED_LOCAL"]
    license_evidence_path_rel: str = Field(min_length=1)
    provider_profile_version_id: str | None = None


class SapiTTSProfilePublishRequest(BaseModel):
    voice_ref: str = Field(min_length=6)
    smoke_text: str = Field(default="本机语音合成验收通过", min_length=1, max_length=120)


class ProjectSapiVoiceProfileRequest(BaseModel):
    """Create a project-scoped, local-only profile from a real SAPI probe."""

    voice_ref: str = Field(min_length=6)
    smoke_text: str = Field(default="本机语音合成验收通过", min_length=1, max_length=120)


class TTSCandidateRequest(BaseModel):
    voice_profile_version_id: str = Field(min_length=1)
    media_version_id: str = Field(min_length=1)
    emotion: str = Field(min_length=1, max_length=80)
    speech_rate: float = Field(ge=0.5, le=2.0)
    seed: int | None = None
    model_ref: str = Field(min_length=1)
    candidate_kind: Literal["PREVIEW", "FORMAL"]


class TTSJobRequest(BaseModel):
    voice_profile_version_id: str = Field(min_length=1)
    emotion: str = Field(min_length=1, max_length=80)
    speech_rate: float = Field(ge=0.5, le=2.0)


class CharacterVoiceBindRequest(BaseModel):
    character_asset_id: str = Field(min_length=1)
    voice_profile_version_id: str = Field(min_length=1)


class EpisodeTTSBatchRequest(BaseModel):
    idempotency_key_prefix: str = Field(min_length=1, max_length=100)
    emotion: str = Field(default="NEUTRAL", min_length=1, max_length=80)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
