from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.generation_preferences import GenerationPreferencePutRequest
from local_drama.application.commands.generation_preferences import GenerationPreferenceCommandService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database

router = APIRouter(tags=["generation-preferences"])
T = TypeVar("T")


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _query(request: Request, fn: Callable[[GenerationPreferenceQueryService], T]) -> T:
    with _database(request).connect() as connection:
        return fn(GenerationPreferenceQueryService(SqliteGenerationPreferenceRepository(connection)))


def _command(request: Request, fn: Callable[[GenerationPreferenceCommandService], T]) -> T:
    with _database(request).transaction() as connection:
        return fn(GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)))


@router.get("/projects/{project_id}/generation-preferences", operation_id="getGenerationPreferences")
async def get_generation_preferences(
    project_id: str,
    request: Request,
    capability: str | None = Query(default=None, max_length=120),
    episode_id: str | None = Query(default=None, max_length=36),
    shot_id: str | None = Query(default=None, max_length=36),
) -> dict[str, object]:
    try:
        if capability:
            resolution = _query(request, lambda service: service.resolve(
                project_id=project_id, capability=capability, episode_id=episode_id, shot_id=shot_id,
            ))
            return {"resolution": resolution}
        return {"items": _query(request, lambda service: service.list_current(project_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/projects/{project_id}/generation-preferences", operation_id="putGenerationPreference")
async def put_generation_preference(
    project_id: str, payload: GenerationPreferencePutRequest, request: Request,
) -> dict[str, object]:
    try:
        preference = _command(request, lambda service: service.put(project_id=project_id, **payload.model_dump()))
        return {"preference": preference}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
