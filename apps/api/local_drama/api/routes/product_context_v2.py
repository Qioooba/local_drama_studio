from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.product_context import AppContextResponse, ProjectOverviewResponse
from local_drama.application.errors import api_error_from_domain
from local_drama.application.product_context import ProductContextQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.product_context_repository import SqliteProductContextReadRepository

router = APIRouter(tags=["product-context-v2"])


def service(request: Request) -> ProductContextQueryService:
    return ProductContextQueryService(SqliteProductContextReadRepository(request.app.state.database))


@router.get("/app-context", operation_id="getAppContextV2", response_model=AppContextResponse)
async def app_context(
    request: Request,
    project_id: str | None = Query(default=None, max_length=64),
    episode_id: str | None = Query(default=None, max_length=64),
) -> AppContextResponse:
    try:
        return AppContextResponse.model_validate(service(request).app_context(project_id, episode_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/overview", operation_id="getProjectOverviewV2", response_model=ProjectOverviewResponse)
async def project_overview(project_id: str, request: Request) -> ProjectOverviewResponse:
    try:
        return ProjectOverviewResponse.model_validate(service(request).project_overview(project_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
