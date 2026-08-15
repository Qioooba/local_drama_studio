from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.errors import api_error_from_domain
from local_drama.application.read_models import ProductionReadModelService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["production-read-model"])


def service(request: Request) -> ProductionReadModelService:
    return ProductionReadModelService(request.app.state.database)


@router.get("/episodes/{episode_id}/production", operation_id="getEpisodeProduction")
async def episode_production(episode_id: str, request: Request, q: str | None = None, limit: int = 200) -> dict[str, object]:
    try:
        return service(request).episode(episode_id, q, limit)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/production/summary", operation_id="getEpisodeProductionSummary")
async def episode_summary(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"summary": service(request).summary(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}/production-detail", operation_id="getShotProductionDetail")
async def shot_detail(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"shot": service(request).shot_detail(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}/continuity-context", operation_id="getShotContinuityContext")
async def continuity_context(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"continuity": service(request).continuity_context(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
