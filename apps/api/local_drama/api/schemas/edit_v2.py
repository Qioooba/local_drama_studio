from __future__ import annotations

from typing import Literal

from pydantic import Field

from local_drama.api.schemas.common import StrictModel


class EditVideoClipFact(StrictModel):
    shot_id: str
    shot_code: str
    media_version_id: str | None = None
    source_name: str | None = None
    source_duration_ms: int | None = Field(default=None, ge=0)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    source_start_us: int = Field(default=0, ge=0)
    transition_in: Literal["CUT", "DISSOLVE", "FADE"]
    continuity_status: str


class EditAudioClipFact(StrictModel):
    id: str
    lane: Literal["DIALOGUE", "BGM", "SFX", "ENVIRONMENT", "LEGACY"]
    media_version_id: str
    source_name: str
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    gain_db: float
    source_revision: int = Field(ge=0)
    owner_route: Literal["SHOT_STUDIO", "POST_AUDIO"]


class EditSubtitleFact(StrictModel):
    revision_id: str
    revision_no: int = Field(ge=1)
    cue_count: int = Field(ge=0)
    format: str
    content_hash: str


class EditTimelineRevisionFact(StrictModel):
    id: str
    revision_no: int = Field(ge=1)
    status: Literal["DRAFT", "FROZEN", "STALE"]
    revision_hash: str
    duration_us: int = Field(ge=0)
    video_count: int = Field(ge=0)
    audio_count: int = Field(ge=0)
    subtitle_revision_id: str | None = None
    upstream_fingerprint: str | None = None
    created_at: str
    created_by: str


class EditIssueFact(StrictModel):
    code: str
    severity: Literal["BLOCKER", "WARNING"]
    message: str
    subject_id: str | None = None
    owner_route: Literal["SHOT_STUDIO", "POST_AUDIO", "POST_EDIT", "REVIEW"]


class EpisodeEditWorkspaceFact(StrictModel):
    episode_id: str
    project_id: str
    episode_code: str
    episode_title: str | None = None
    freshness: Literal["EMPTY", "CURRENT", "STALE"]
    upstream_fingerprint: str
    latest_revision: EditTimelineRevisionFact | None = None
    history: list[EditTimelineRevisionFact]
    history_has_more: bool
    video_clips: list[EditVideoClipFact]
    audio_clips: list[EditAudioClipFact]
    subtitle: EditSubtitleFact | None = None
    issues: list[EditIssueFact]
    duration_us: int = Field(ge=0)
    allowed_actions: list[str]


class EpisodeEditWorkspaceResponse(StrictModel):
    workspace: EpisodeEditWorkspaceFact
    read_only: Literal[True]
    request_shape: Literal["episode_edit_workspace_v2"]


class EditVideoClipCommand(StrictModel):
    shot_id: str = Field(min_length=1, max_length=200)
    media_version_id: str = Field(min_length=1, max_length=200)
    duration_us: int = Field(ge=100_000, le=3_600_000_000)
    source_start_us: int = Field(default=0, ge=0, le=3_600_000_000)
    transition_in: Literal["CUT", "DISSOLVE", "FADE"] = "CUT"


class TimelineDraftCreateCommand(StrictModel):
    clips: list[EditVideoClipCommand] = Field(min_length=1, max_length=500)
    include_dialogue: bool = True
    include_music_and_sfx: bool = True
    include_subtitles: bool = True
    expected_latest_revision_id: str | None = Field(default=None, max_length=200)
    expected_upstream_fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TimelineFreezeCommand(StrictModel):
    expected_latest_revision_id: str = Field(min_length=1, max_length=200)
    expected_upstream_fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TimelineWriteFact(StrictModel):
    id: str
    episode_id: str
    revision_no: int = Field(ge=1)
    status: Literal["DRAFT", "FROZEN"]
    revision_hash: str
    outcome: Literal["DRAFT_CREATED", "FROZEN"]
    idempotent_replay: bool


class TimelineCommandResponse(StrictModel):
    timeline: TimelineWriteFact
