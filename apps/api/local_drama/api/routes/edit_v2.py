from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.edit_v2 import (
    EpisodeEditWorkspaceResponse,
    TimelineCommandResponse,
    TimelineDraftCreateCommand,
    TimelineFreezeCommand,
)
from local_drama.application.edit import EpisodeEditService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.edit_repository import SqliteEpisodeEditRepository

router = APIRouter(tags=["edit-v2"])


def _service(request: Request) -> EpisodeEditService:
    return EpisodeEditService(SqliteEpisodeEditRepository(request.app.state.database))


@router.get(
    "/episodes/{episode_id}/post/edit",
    response_model=EpisodeEditWorkspaceResponse,
    operation_id="getEpisodeEditWorkspaceV2",
)
async def get_edit_workspace(
    episode_id: str,
    request: Request,
    history_limit: int = Query(default=20, ge=1, le=50),
) -> EpisodeEditWorkspaceResponse:
    try:
        return EpisodeEditWorkspaceResponse.model_validate(
            _service(request).workspace(episode_id, history_limit=history_limit)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/post/edit/timeline-drafts",
    response_model=TimelineCommandResponse,
    status_code=201,
    operation_id="createEpisodeTimelineDraftV2",
)
async def create_timeline_draft(
    episode_id: str,
    payload: TimelineDraftCreateCommand,
    request: Request,
) -> TimelineCommandResponse:
    try:
        return TimelineCommandResponse.model_validate(
            _service(request).create_draft(episode_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/post/edit/timeline-revisions/{timeline_revision_id}:freeze",
    response_model=TimelineCommandResponse,
    status_code=201,
    operation_id="freezeEpisodeTimelineV2",
)
async def freeze_timeline(
    timeline_revision_id: str,
    payload: TimelineFreezeCommand,
    request: Request,
) -> TimelineCommandResponse:
    try:
        return TimelineCommandResponse.model_validate(
            _service(request).freeze(timeline_revision_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
