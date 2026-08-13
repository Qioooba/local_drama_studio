from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.errors import api_error_from_domain
from local_drama.application.g6_readiness import G6ReadinessService
from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.i2v_probe import I2VProbePlanService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["phase-gates"])


@router.get("/projects/{project_id}/gates/g6", operation_id="getG6Readiness")
async def get_g6_readiness(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"readiness": G6ReadinessService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6/i2v-probe-plan", operation_id="planG6I2VProbe")
async def plan_g6_i2v_probe(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"plan": I2VProbePlanService(request.app.state.database).plan(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g7", operation_id="getG7Readiness")
async def get_g7_readiness(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"readiness": G7ReadinessService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
