from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g9 import CanvasLayoutRequest, CanvasPlanRequest
from local_drama.application.canvas import ProductionCanvasService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["production-canvas"])


def service(request: Request) -> ProductionCanvasService:
    return ProductionCanvasService(request.app.state.database)


@router.get("/canvas/{scope_type}/{scope_id}", operation_id="getProductionCanvas")
async def get_canvas(scope_type: str, scope_id: str, request: Request, cursor: int = 0, limit: int = 100) -> dict[str, object]:
    try:
        return {"graph": service(request).graph(scope_type, scope_id, cursor=cursor, limit=limit)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/canvas/{scope_type}/{scope_id}/layout", operation_id="saveProductionCanvasLayout")
async def save_layout(scope_type: str, scope_id: str, payload: CanvasLayoutRequest, request: Request) -> dict[str, object]:
    try:
        return {"layout": service(request).save_layout(scope_type, scope_id, payload.model_dump(exclude={"expected_revision"}), payload.expected_revision)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/canvas/{scope_type}/{scope_id}/runs:preflight", status_code=201, operation_id="preflightProductionCanvasRun")
async def preflight_run(scope_type: str, scope_id: str, payload: CanvasPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).preflight(scope_type, scope_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
