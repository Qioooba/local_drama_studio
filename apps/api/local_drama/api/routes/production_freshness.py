from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.production_freshness import ProductionFreshnessResponse
from local_drama.application.errors import api_error_from_domain
from local_drama.application.queries.production_freshness import ProductionFreshnessService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.production_freshness_repository import SqliteProductionFreshnessRepository

router = APIRouter(tags=["production-freshness"])


def _evaluate(request: Request, scope_type: str, scope_id: str, limit: int) -> dict[str, object]:
    try:
        with request.app.state.database.connect() as connection:
            connection.execute("PRAGMA query_only=ON")
            return ProductionFreshnessService(SqliteProductionFreshnessRepository(connection)).evaluate(
                scope_type=scope_type, scope_id=scope_id, limit=limit,
            )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/production-freshness", response_model=ProductionFreshnessResponse, operation_id="getProjectProductionFreshness")
async def get_project_production_freshness(project_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    return _evaluate(request, "PROJECT", project_id, limit)


@router.get("/episodes/{episode_id}/production-freshness", response_model=ProductionFreshnessResponse, operation_id="getEpisodeProductionFreshness")
async def get_episode_production_freshness(episode_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    return _evaluate(request, "EPISODE", episode_id, limit)


@router.get("/shots/{shot_id}/production-freshness", response_model=ProductionFreshnessResponse, operation_id="getShotProductionFreshness")
async def get_shot_production_freshness(shot_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    return _evaluate(request, "SHOT", shot_id, limit)
