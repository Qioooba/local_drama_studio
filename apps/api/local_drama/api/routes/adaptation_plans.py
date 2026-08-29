"""Version 2 routes for immutable long-form adaptation planning."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from local_drama.api.schemas.adaptation_plans import (
    AdaptationAnalysisManifestResponse,
    AdaptationAnalysisReadinessResponse,
    AdaptationAnalysisSubmitRequest,
    AdaptationAnalysisSubmitResponse,
    AdaptationMaterializationPreflightResponse,
    AdaptationMaterializeRequest,
    AdaptationMaterializeResponse,
    AdaptationPlanApproveRequest,
    AdaptationPlanApproveResponse,
    AdaptationPlanCreateRequest,
    AdaptationPlanCreateResponse,
    AdaptationPlanListResponse,
    AdaptationPlanPreflightRequest,
    AdaptationPlanPreflightResponse,
    AdaptationPlanWorkspaceResponse,
)
from local_drama.application.adaptation_analysis_queue import AdaptationAnalysisQueueService
from local_drama.application.commands.adaptation_plans import AdaptationPlanCommandService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.jobs import JobService
from local_drama.application.queries.adaptation_plans import AdaptationPlanQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.adaptation_plan_repository import SqliteAdaptationPlanRepository

router = APIRouter(tags=["adaptation-plans-v2"])


def _repository(request: Request) -> SqliteAdaptationPlanRepository:
    return SqliteAdaptationPlanRepository(request.app.state.database, request.app.state.settings)


def _commands(request: Request) -> AdaptationPlanCommandService:
    return AdaptationPlanCommandService(_repository(request))


def _queries(request: Request) -> AdaptationPlanQueryService:
    return AdaptationPlanQueryService(_repository(request))


def _analysis_queue(request: Request) -> AdaptationAnalysisQueueService:
    database, settings = request.app.state.database, request.app.state.settings
    return AdaptationAnalysisQueueService(
        SqliteAdaptationPlanRepository(database, settings),
        JobService(database, settings),
        settings,
    )


@router.get(
    "/projects/{project_id}/source-versions",
    response_model=AdaptationPlanListResponse,
    operation_id="listSourceVersionsForAdaptation",
)
async def list_source_versions(project_id: str, request: Request) -> AdaptationPlanListResponse:
    try:
        return AdaptationPlanListResponse.model_validate(_queries(request).source_versions(project_id=project_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/adaptation-plans:preflight",
    response_model=AdaptationPlanPreflightResponse,
    operation_id="preflightAdaptationPlan",
)
async def preflight(
    project_id: str,
    payload: AdaptationPlanPreflightRequest,
    request: Request,
) -> AdaptationPlanPreflightResponse:
    try:
        result = _commands(request).preflight(
            project_id=project_id,
            source_document_version_id=payload.source_document_version_id,
            source_paragraph_start=payload.source_paragraph_start,
            source_paragraph_end=payload.source_paragraph_end,
            target_duration_ms=payload.target_duration_ms,
        )
        return AdaptationPlanPreflightResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/adaptation-plans",
    response_model=AdaptationPlanCreateResponse,
    status_code=201,
    operation_id="createAdaptationPlan",
)
async def create_plan(
    project_id: str,
    payload: AdaptationPlanCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdaptationPlanCreateResponse:
    try:
        result = _commands(request).create(
            project_id=project_id,
            source_document_version_id=payload.source_document_version_id,
            mode=payload.mode,
            source_paragraph_start=payload.source_paragraph_start,
            source_paragraph_end=payload.source_paragraph_end,
            target_duration_ms=payload.target_duration_ms,
            episode_strategy=payload.episode_strategy,
            requested_episode_count=payload.requested_episode_count,
            season_strategy=payload.season_strategy,
            requested_season_count=payload.requested_season_count,
            profile_version_id=payload.profile_version_id,
            idempotency_key=idempotency_key or "",
        )
        return AdaptationPlanCreateResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/adaptation-plans/{plan_id}/analysis-manifest",
    response_model=AdaptationAnalysisManifestResponse,
    operation_id="prepareAdaptationAnalysisManifest",
)
async def prepare_analysis_manifest(plan_id: str, request: Request) -> AdaptationAnalysisManifestResponse:
    try:
        result = _commands(request).prepare_analysis_manifest(plan_id=plan_id)
        return AdaptationAnalysisManifestResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/adaptation-plans/{plan_id}/analysis-readiness",
    response_model=AdaptationAnalysisReadinessResponse,
    operation_id="getAdaptationAnalysisReadiness",
)
async def analysis_readiness(
    plan_id: str,
    request: Request,
    profile_version_id: str = Query(..., min_length=1, max_length=36),
) -> AdaptationAnalysisReadinessResponse:
    try:
        result = _commands(request).analysis_readiness(
            plan_id=plan_id,
            profile_version_id=profile_version_id,
        )
        return AdaptationAnalysisReadinessResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/adaptation-plans/{plan_id}/analysis-runs",
    response_model=AdaptationAnalysisSubmitResponse,
    status_code=202,
    operation_id="submitAdaptationAnalysisRun",
)
async def submit_analysis_run(
    plan_id: str,
    payload: AdaptationAnalysisSubmitRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdaptationAnalysisSubmitResponse:
    try:
        result = _analysis_queue(request).enqueue(
            plan_id=plan_id,
            profile_version_id=payload.profile_version_id,
            allow_remote_outbound=payload.allow_remote_outbound,
            idempotency_key=idempotency_key or "",
        )
        return AdaptationAnalysisSubmitResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/adaptation-plans/{plan_id}:approve",
    response_model=AdaptationPlanApproveResponse,
    operation_id="approveAdaptationPlan",
)
async def approve_plan(
    plan_id: str,
    payload: AdaptationPlanApproveRequest,
    request: Request,
) -> AdaptationPlanApproveResponse:
    try:
        result = _commands(request).approve(
            plan_id=plan_id,
            expected_content_sha256=payload.expected_content_sha256,
        )
        return AdaptationPlanApproveResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/adaptation-plans/{plan_id}/materialization-preflight",
    response_model=AdaptationMaterializationPreflightResponse,
    operation_id="preflightAdaptationMaterialization",
)
async def materialization_preflight(plan_id: str, request: Request) -> AdaptationMaterializationPreflightResponse:
    try:
        return AdaptationMaterializationPreflightResponse.model_validate(
            _commands(request).materialization_preflight(plan_id=plan_id)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/adaptation-plans/{plan_id}/materializations",
    response_model=AdaptationMaterializeResponse,
    status_code=201,
    operation_id="materializeAdaptationPlan",
)
async def materialize_plan(
    plan_id: str,
    payload: AdaptationMaterializeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdaptationMaterializeResponse:
    try:
        return AdaptationMaterializeResponse.model_validate(
            _commands(request).materialize(
                plan_id=plan_id,
                expected_content_sha256=payload.expected_content_sha256,
                idempotency_key=idempotency_key or "",
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/adaptation-plans",
    response_model=AdaptationPlanListResponse,
    operation_id="listAdaptationPlans",
)
async def list_plans(project_id: str, request: Request) -> AdaptationPlanListResponse:
    try:
        return AdaptationPlanListResponse.model_validate(_queries(request).plans(project_id=project_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/adaptation-plans/{plan_id}/workspace",
    response_model=AdaptationPlanWorkspaceResponse,
    operation_id="getAdaptationPlanWorkspace",
)
async def workspace(plan_id: str, request: Request) -> AdaptationPlanWorkspaceResponse:
    try:
        return AdaptationPlanWorkspaceResponse.model_validate(_queries(request).workspace(plan_id=plan_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
