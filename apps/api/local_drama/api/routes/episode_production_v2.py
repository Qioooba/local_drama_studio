from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.episode_production_v2 import (
    EpisodeProductionChangesResponse,
    EpisodeProductionOverviewResponse,
    EpisodeProductionRunCancelCommand,
    EpisodeProductionRunCommandResponse,
    EpisodeProductionRunPauseCommand,
    EpisodeProductionRunRecoverCommand,
    EpisodeProductionRunResumeCommand,
    EpisodeProductionRunStartCommand,
    EpisodeProductionShotPage,
    ProductionState,
)
from local_drama.application.episode_production import EpisodeProductionQueryService
from local_drama.application.episode_production_commands import EpisodeProductionCommandService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.episode_production_command_repository import (
    SqliteEpisodeProductionTransitionRepository,
)
from local_drama.infrastructure.database.episode_production_repository import (
    SqliteEpisodeProductionReadRepository,
)

CURSOR_QUERY = Query(default=0, ge=0)
LIMIT_QUERY = Query(default=50, ge=1, le=100)
STATES_QUERY = Query(default_factory=list)

router = APIRouter(tags=["episode-production-v2"])


def _queries(request: Request) -> EpisodeProductionQueryService:
    return EpisodeProductionQueryService(
        SqliteEpisodeProductionReadRepository(request.app.state.database)
    )


def _commands(request: Request) -> EpisodeProductionCommandService:
    return EpisodeProductionCommandService(
        EpisodeProductionRunService(request.app.state.database, request.app.state.settings),
        SqliteEpisodeProductionTransitionRepository(request.app.state.database),
    )


@router.get(
    "/episodes/{episode_id}/production/overview",
    response_model=EpisodeProductionOverviewResponse,
    operation_id="getEpisodeProductionOverviewV2",
)
async def overview(episode_id: str, request: Request) -> EpisodeProductionOverviewResponse:
    try:
        return EpisodeProductionOverviewResponse.model_validate(_queries(request).overview(episode_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/production/shots",
    response_model=EpisodeProductionShotPage,
    operation_id="listEpisodeProductionShotsV2",
)
async def shots(
    episode_id: str,
    request: Request,
    cursor: int = CURSOR_QUERY,
    limit: int = LIMIT_QUERY,
    state: list[ProductionState] = STATES_QUERY,
) -> EpisodeProductionShotPage:
    try:
        payload = _queries(request).shots(
            episode_id,
            cursor=cursor,
            limit=limit,
            states=set(state),
        )
        return EpisodeProductionShotPage.model_validate(payload)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/production/changes",
    response_model=EpisodeProductionChangesResponse,
    operation_id="listEpisodeProductionChangesV2",
)
async def changes(
    episode_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> EpisodeProductionChangesResponse:
    try:
        payload = _queries(request).changes(episode_id, after=after, limit=limit)
        return EpisodeProductionChangesResponse.model_validate(payload)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/production-runs",
    response_model=EpisodeProductionRunCommandResponse,
    status_code=201,
    operation_id="startEpisodeProductionRunV2",
)
async def start_run(
    episode_id: str,
    payload: EpisodeProductionRunStartCommand,
    request: Request,
) -> EpisodeProductionRunCommandResponse:
    try:
        result = _commands(request).start(episode_id, payload.model_dump())
        return EpisodeProductionRunCommandResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _transition(
    request: Request,
    run_id: str,
    action: str,
    payload: EpisodeProductionRunPauseCommand
    | EpisodeProductionRunResumeCommand
    | EpisodeProductionRunCancelCommand
    | EpisodeProductionRunRecoverCommand,
) -> EpisodeProductionRunCommandResponse:
    try:
        result = _commands(request).transition(run_id, action, payload.model_dump())
        return EpisodeProductionRunCommandResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-runs/{run_id}:pause",
    response_model=EpisodeProductionRunCommandResponse,
    operation_id="pauseEpisodeProductionRunV2",
)
async def pause_run(
    run_id: str, payload: EpisodeProductionRunPauseCommand, request: Request
) -> EpisodeProductionRunCommandResponse:
    return _transition(request, run_id, "PAUSED", payload)


@router.post(
    "/production-runs/{run_id}:resume",
    response_model=EpisodeProductionRunCommandResponse,
    operation_id="resumeEpisodeProductionRunV2",
)
async def resume_run(
    run_id: str, payload: EpisodeProductionRunResumeCommand, request: Request
) -> EpisodeProductionRunCommandResponse:
    return _transition(request, run_id, "RESUMED", payload)


@router.post(
    "/production-runs/{run_id}:cancel",
    response_model=EpisodeProductionRunCommandResponse,
    operation_id="cancelEpisodeProductionRunV2",
)
async def cancel_run(
    run_id: str, payload: EpisodeProductionRunCancelCommand, request: Request
) -> EpisodeProductionRunCommandResponse:
    return _transition(request, run_id, "CANCELLED", payload)


@router.post(
    "/production-runs/{run_id}:recover",
    response_model=EpisodeProductionRunCommandResponse,
    operation_id="recoverEpisodeProductionRunV2",
)
async def recover_run(
    run_id: str, payload: EpisodeProductionRunRecoverCommand, request: Request
) -> EpisodeProductionRunCommandResponse:
    return _transition(request, run_id, "RECOVERED", payload)
