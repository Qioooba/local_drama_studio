from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from local_drama.api.schemas.production_sessions_v2 import (
    ProductionChoiceRerollRequest,
    ProductionChoiceRerollResponse,
    ProductionSessionBudgetExtendRequest,
    ProductionSessionBudgetExtendResponse,
    ProductionSessionControlRequest,
    ProductionSessionControlResponse,
    ProductionSessionCreateRequest,
    ProductionSessionEpisodeConfirmRequest,
    ProductionSessionEpisodeConfirmResponse,
    ProductionSessionItemPage,
    ProductionSessionItemRetryRequest,
    ProductionSessionItemRetryResponse,
    ProductionSessionPage,
    ProductionSessionPlanRequest,
    ProductionSessionPlanResponse,
    ProductionSessionReconcileResponse,
    ProductionSessionResponse,
    ProductionSessionReviewPage,
    ProductionSessionRunResponse,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.production_choices import ProductionChoiceService
from local_drama.application.production_session_review import ProductionSessionReviewService
from local_drama.application.production_session_runner import ProductionSessionRunner
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["production-sessions-v2"])


def _service(request: Request) -> ProductionSessionService:
    return ProductionSessionService(request.app.state.database)


def _runner(request: Request) -> ProductionSessionRunner:
    return ProductionSessionRunner(request.app.state.database, request.app.state.settings)


def _review(request: Request) -> ProductionSessionReviewService:
    return ProductionSessionReviewService(request.app.state.database)


def _choices(request: Request) -> ProductionChoiceService:
    return ProductionChoiceService(request.app.state.database)


@router.post(
    "/projects/{project_id}/production-sessions:plan",
    response_model=ProductionSessionPlanResponse,
    operation_id="planProductionSessionV2",
)
async def plan_production_session(
    project_id: str,
    payload: ProductionSessionPlanRequest,
    request: Request,
) -> ProductionSessionPlanResponse:
    try:
        plan = _service(request).plan(project_id, payload.model_dump())
        return ProductionSessionPlanResponse.model_validate({"plan": plan, "read_only": True, "request_shape": "production_session_plan_v2"})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/production-sessions",
    response_model=ProductionSessionResponse,
    status_code=201,
    operation_id="createProductionSessionV2",
)
async def create_production_session(
    project_id: str,
    payload: ProductionSessionCreateRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionResponse:
    try:
        return ProductionSessionResponse.model_validate(
            _service(request).create(
                project_id,
                payload.model_dump(),
                idempotency_key=idempotency_key,
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/production-sessions",
    response_model=ProductionSessionPage,
    operation_id="listProductionSessionsV2",
)
async def list_production_sessions(
    project_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    status: str | None = Query(default=None),
) -> ProductionSessionPage:
    try:
        return ProductionSessionPage.model_validate(_service(request).list_sessions(project_id, cursor=cursor, limit=limit, status=status))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/production-sessions/{session_id}",
    response_model=ProductionSessionResponse,
    operation_id="getProductionSessionV2",
)
async def get_production_session(
    session_id: str,
    request: Request,
) -> ProductionSessionResponse:
    try:
        return ProductionSessionResponse.model_validate({"session": _service(request).get(session_id), "idempotent_replay": False})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/production-sessions/{session_id}/items",
    response_model=ProductionSessionItemPage,
    operation_id="listProductionSessionItemsV2",
)
async def list_production_session_items(
    session_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> ProductionSessionItemPage:
    try:
        return ProductionSessionItemPage.model_validate(_service(request).list_items(session_id, cursor=cursor, limit=limit))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/production-sessions/{session_id}/review",
    response_model=ProductionSessionReviewPage,
    operation_id="getProductionSessionReviewV2",
)
async def get_production_session_review(
    session_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> ProductionSessionReviewPage:
    try:
        return ProductionSessionReviewPage.model_validate(_review(request).inspect(session_id, cursor=cursor, limit=limit))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}/episodes/{episode_id}:confirm",
    response_model=ProductionSessionEpisodeConfirmResponse,
    operation_id="confirmProductionSessionEpisodeV2",
)
async def confirm_production_session_episode(
    session_id: str,
    episode_id: str,
    payload: ProductionSessionEpisodeConfirmRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionEpisodeConfirmResponse:
    try:
        return ProductionSessionEpisodeConfirmResponse.model_validate(
            _review(request).confirm_episode(
                session_id,
                episode_id,
                payload.model_dump(),
                idempotency_key=idempotency_key,
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}/choices/{choice_id}:reroll",
    response_model=ProductionChoiceRerollResponse,
    operation_id="rerollProductionSessionChoiceV2",
)
async def reroll_production_session_choice(
    session_id: str,
    choice_id: str,
    payload: ProductionChoiceRerollRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionChoiceRerollResponse:
    try:
        result = _choices(request).reroll(
            session_id,
            choice_id,
            payload.model_dump(),
            idempotency_key=idempotency_key,
        )
        dispatch = {"dispatched_count": 0, "waiting_count": 0, "blocked_count": 0}
        if result["session"]["status"] == "RUNNING":
            reconciled = _runner(request).reconcile(session_id, actor=payload.actor)
            result["session"] = reconciled["session"]
            dispatch = {
                "dispatched_count": reconciled["dispatched_count"],
                "waiting_count": reconciled["waiting_count"],
                "blocked_count": reconciled["blocked_count"],
            }
        return ProductionChoiceRerollResponse.model_validate({**result, **dispatch})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}/items/{item_id}:retry",
    response_model=ProductionSessionItemRetryResponse,
    operation_id="retryProductionSessionItemV2",
)
async def retry_production_session_item(
    session_id: str,
    item_id: str,
    payload: ProductionSessionItemRetryRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionItemRetryResponse:
    try:
        result = _service(request).retry_item(
            session_id,
            item_id,
            payload.model_dump(),
            idempotency_key=idempotency_key,
        )
        reconciled = _runner(request).reconcile(session_id, actor=payload.actor)
        return ProductionSessionItemRetryResponse.model_validate(
            {
                **result,
                "session": reconciled["session"],
                "dispatched_count": reconciled["dispatched_count"],
                "waiting_count": reconciled["waiting_count"],
                "blocked_count": reconciled["blocked_count"],
            }
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}:extend-budget",
    response_model=ProductionSessionBudgetExtendResponse,
    operation_id="extendProductionSessionBudgetV2",
)
async def extend_production_session_budget(
    session_id: str,
    payload: ProductionSessionBudgetExtendRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionBudgetExtendResponse:
    try:
        result = _service(request).extend_budget(
            session_id,
            payload.model_dump(),
            idempotency_key=idempotency_key,
        )
        dispatch = {"dispatched_count": 0, "waiting_count": 0, "blocked_count": 0}
        if result["session"]["status"] == "RUNNING":
            reconciled = _runner(request).reconcile(session_id, actor=payload.actor)
            result["session"] = reconciled["session"]
            dispatch = {
                "dispatched_count": reconciled["dispatched_count"],
                "waiting_count": reconciled["waiting_count"],
                "blocked_count": reconciled["blocked_count"],
            }
        return ProductionSessionBudgetExtendResponse.model_validate({**result, **dispatch})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}:start",
    response_model=ProductionSessionRunResponse,
    operation_id="startProductionSessionV2",
)
async def start_production_session(
    session_id: str,
    payload: ProductionSessionControlRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionRunResponse:
    try:
        return ProductionSessionRunResponse.model_validate(
            _runner(request).start(
                session_id,
                payload.model_dump(),
                idempotency_key=idempotency_key,
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}:refresh",
    response_model=ProductionSessionReconcileResponse,
    operation_id="refreshProductionSessionV2",
)
async def refresh_production_session(
    session_id: str,
    request: Request,
) -> ProductionSessionReconcileResponse:
    try:
        return ProductionSessionReconcileResponse.model_validate(_runner(request).reconcile(session_id, actor="local-user"))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


async def _control(
    session_id: str,
    action: str,
    payload: ProductionSessionControlRequest,
    request: Request,
    idempotency_key: str,
) -> ProductionSessionControlResponse:
    try:
        result = _service(request).control(
            session_id,
            action,
            payload.model_dump(),
            idempotency_key=idempotency_key,
        )
        return ProductionSessionControlResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/production-sessions/{session_id}:pause",
    response_model=ProductionSessionControlResponse,
    operation_id="pauseProductionSessionV2",
)
async def pause_production_session(
    session_id: str,
    payload: ProductionSessionControlRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionControlResponse:
    return await _control(session_id, "PAUSE", payload, request, idempotency_key)


@router.post(
    "/production-sessions/{session_id}:resume",
    response_model=ProductionSessionControlResponse,
    operation_id="resumeProductionSessionV2",
)
async def resume_production_session(
    session_id: str,
    payload: ProductionSessionControlRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionControlResponse:
    return await _control(session_id, "RESUME", payload, request, idempotency_key)


@router.post(
    "/production-sessions/{session_id}:cancel",
    response_model=ProductionSessionControlResponse,
    operation_id="cancelProductionSessionV2",
)
async def cancel_production_session(
    session_id: str,
    payload: ProductionSessionControlRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ProductionSessionControlResponse:
    return await _control(session_id, "CANCEL", payload, request, idempotency_key)
