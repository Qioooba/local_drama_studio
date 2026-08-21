from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.qc_policies import QcPolicyPutRequest, VariantQcAttachChildRequest, VariantQcDecisionRequest
from local_drama.application.commands.qc_policies import QcPolicyCommandService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.queries.qc_policies import QcPolicyQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.qc_policy_repository import SqliteQcPolicyRepository
from local_drama.infrastructure.database.sqlite import Database

router = APIRouter(tags=["qc-policies"])
T = TypeVar("T")


def _database(request: Request) -> Database:
    return request.app.state.database


def _query(request: Request, fn: Callable[[QcPolicyQueryService], T]) -> T:
    with _database(request).connect() as connection:
        return fn(QcPolicyQueryService(SqliteQcPolicyRepository(connection)))


def _command(request: Request, fn: Callable[[QcPolicyCommandService], T]) -> T:
    with _database(request).transaction() as connection:
        return fn(QcPolicyCommandService(SqliteQcPolicyRepository(connection)))


@router.get("/projects/{project_id}/qc-policies", operation_id="getQcPolicies")
async def get_qc_policies(
    project_id: str, request: Request, stage: str | None = Query(default=None, max_length=80),
    episode_id: str | None = Query(default=None, max_length=36),
    shot_id: str | None = Query(default=None, max_length=36),
) -> dict[str, object]:
    try:
        if stage:
            return {"resolution": _query(request, lambda service: service.resolve(
                project_id=project_id, stage=stage, episode_id=episode_id, shot_id=shot_id,
            ))}
        return {"items": _query(request, lambda service: service.list_current(project_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/projects/{project_id}/qc-policies", operation_id="putQcPolicy")
async def put_qc_policy(project_id: str, payload: QcPolicyPutRequest, request: Request) -> dict[str, object]:
    try:
        return {"policy": _command(request, lambda service: service.put(project_id=project_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation/variants/{variant_id}/qc:decide", operation_id="decideVariantQcDisposition")
async def decide_variant_qc(variant_id: str, payload: VariantQcDecisionRequest, request: Request) -> dict[str, object]:
    try:
        return {"disposition": _command(request, lambda service: service.decide(variant_id=variant_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation/variants/{variant_id}/qc:attach-child", operation_id="attachVariantQcChild")
async def attach_variant_qc_child(
    variant_id: str, payload: VariantQcAttachChildRequest, request: Request,
) -> dict[str, object]:
    try:
        result = _command(request, lambda service: service.attach_child(parent_variant_id=variant_id, **payload.model_dump()))
        return {"disposition": result}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
