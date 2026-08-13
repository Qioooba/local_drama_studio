from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g3 import DeliveryTargetRequest, ProductionPlanRequest
from local_drama.application.configuration import ConfigurationService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["configuration"])


@router.post("/projects/{project_id}/production-plan", status_code=201, operation_id="bindProductionPlan")
async def bind_production_plan(project_id: str, payload: ProductionPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": ConfigurationService(request.app.state.database).create_plan_binding(project_id, payload.code, payload.title, payload.plan)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/delivery-targets", status_code=201, operation_id="createDeliveryTarget")
async def create_delivery_target(project_id: str, payload: DeliveryTargetRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "target": ConfigurationService(request.app.state.database).create_delivery_target(
                project_id, payload.code, payload.title, payload.transport, payload.spec
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
