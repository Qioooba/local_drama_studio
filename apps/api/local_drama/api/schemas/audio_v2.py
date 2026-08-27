from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import StrictModel

AudioTrackKind = Literal["BGM", "SFX"]
AudioLicenseStatus = Literal["VERIFIED_LOCAL", "USER_OWNED", "PUBLIC_DOMAIN"]


class AudioDialogueReferenceFact(StrictModel):
    line_id: str
    line_code: str
    shot_id: str | None = None
    shot_code: str | None = None
    speaker: str
    text: str
    text_revision_id: str
    text_revision_no: int = Field(ge=1)
    selected_tts_candidate_id: str | None = None
    selected_media_version_id: str | None = None
    selected_media_duration_ms: int | None = Field(default=None, ge=0)
    selected_candidate_kind: str | None = None
    selection_stale: bool


class AudioTrackFact(StrictModel):
    id: str
    episode_id: str
    media_version_id: str
    source_name: str
    track_kind: str
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    gain_db: float
    loop_enabled: bool
    fade_in_us: int = Field(ge=0)
    fade_out_us: int = Field(ge=0)
    source_duration_ms: int | None = Field(default=None, ge=0)
    license_status: str
    authorization_status: Literal["VERIFIED_EVIDENCE", "LEGACY_INCOMPLETE"]
    machine_status: str | None = None
    latest_review_decision: str | None = None
    revision: int = Field(ge=1)
    allowed_actions: list[str]


class AudioGapFact(StrictModel):
    code: str
    message: str
    owner_route: Literal["SHOT_STUDIO", "POST_AUDIO", "REVIEW"]
    repair_action: str
    subject_id: str | None = None


class EpisodeAudioWorkspaceFact(StrictModel):
    episode_id: str
    project_id: str
    episode_code: str
    episode_title: str | None = None
    mix_revision: int = Field(ge=0)
    mix_status: Literal["DRAFT", "READY"]
    dialogue_references: list[AudioDialogueReferenceFact]
    tracks: list[AudioTrackFact]
    gaps: list[AudioGapFact]
    summary: dict[str, int]
    allowed_actions: list[str]


class EpisodeAudioWorkspaceResponse(StrictModel):
    workspace: EpisodeAudioWorkspaceFact
    read_only: Literal[True]
    request_shape: Literal["episode_audio_workspace_v2"]


class AudioTrackCreateCommand(StrictModel):
    media_version_id: str = Field(min_length=1, max_length=200)
    track_kind: AudioTrackKind
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    gain_db: float = Field(default=0, ge=-60, le=24)
    license_status: AudioLicenseStatus
    license_evidence_path_rel: str = Field(min_length=1, max_length=1000)
    loop_enabled: bool = False
    fade_in_us: int = Field(default=0, ge=0)
    fade_out_us: int = Field(default=0, ge=0)
    expected_mix_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AudioTrackUpdateCommand(StrictModel):
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    gain_db: float = Field(ge=-60, le=24)
    loop_enabled: bool
    fade_in_us: int = Field(ge=0)
    fade_out_us: int = Field(ge=0)
    expected_revision: int = Field(ge=1)
    expected_mix_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AudioTrackRemoveCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    expected_mix_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AudioTrackWriteFact(StrictModel):
    id: str
    episode_id: str
    media_version_id: str
    track_kind: str
    revision: int = Field(ge=1)
    mix_revision: int = Field(ge=1)
    outcome: Literal["CREATED", "UPDATED", "REMOVED"]
    idempotent_replay: bool


class AudioTrackCommandResponse(StrictModel):
    track: AudioTrackWriteFact
