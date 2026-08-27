from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.audio_v2 import (
    AudioTrackCommandResponse,
    AudioTrackCreateCommand,
    AudioTrackRemoveCommand,
    AudioTrackUpdateCommand,
    EpisodeAudioWorkspaceResponse,
)
from local_drama.application.audio import AudioWorkspaceService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.audio_repository import SqliteAudioWorkspaceRepository

router = APIRouter(tags=["audio-v2"])


def _service(request: Request) -> AudioWorkspaceService:
    return AudioWorkspaceService(
        SqliteAudioWorkspaceRepository(request.app.state.database, request.app.state.settings)
    )


@router.get(
    "/episodes/{episode_id}/post/audio",
    response_model=EpisodeAudioWorkspaceResponse,
    operation_id="getEpisodeAudioWorkspaceV2",
)
async def get_audio_workspace(episode_id: str, request: Request) -> EpisodeAudioWorkspaceResponse:
    try:
        return EpisodeAudioWorkspaceResponse.model_validate(_service(request).workspace(episode_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/post/audio/tracks",
    response_model=AudioTrackCommandResponse,
    status_code=201,
    operation_id="createEpisodeAudioTrackV2",
)
async def create_audio_track(
    episode_id: str, payload: AudioTrackCreateCommand, request: Request
) -> AudioTrackCommandResponse:
    try:
        return AudioTrackCommandResponse.model_validate(
            _service(request).create_track(episode_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put(
    "/post/audio/tracks/{binding_id}",
    response_model=AudioTrackCommandResponse,
    operation_id="updateEpisodeAudioTrackV2",
)
async def update_audio_track(
    binding_id: str, payload: AudioTrackUpdateCommand, request: Request
) -> AudioTrackCommandResponse:
    try:
        return AudioTrackCommandResponse.model_validate(
            _service(request).update_track(binding_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/post/audio/tracks/{binding_id}:remove",
    response_model=AudioTrackCommandResponse,
    operation_id="removeEpisodeAudioTrackV2",
)
async def remove_audio_track(
    binding_id: str, payload: AudioTrackRemoveCommand, request: Request
) -> AudioTrackCommandResponse:
    try:
        return AudioTrackCommandResponse.model_validate(
            _service(request).remove_track(binding_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
