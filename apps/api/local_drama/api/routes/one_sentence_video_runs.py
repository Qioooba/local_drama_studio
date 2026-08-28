from __future__ import annotations

from fastapi import APIRouter, Header, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.one_sentence_video_runs import (
    OneSentenceImageRerollRequest,
    OneSentenceImageSelectRequest,
    OneSentencePromptRegenerateRequest,
    OneSentenceVideoPlanRequest,
    OneSentenceVideoRetryRequest,
)
from local_drama.api.schemas.quick_generations import QuickGenerationRunResponse
from local_drama.application.errors import api_error_from_domain
from local_drama.application.one_sentence_video_runs import OneSentenceVideoRunService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(
    prefix="/one-sentence-video-runs",
    tags=["one-sentence-video-runs"],
    deprecated=True,
)


def service(request: Request) -> OneSentenceVideoRunService:
    return OneSentenceVideoRunService(request.app.state.database, request.app.state.settings)


@router.post(":plan", status_code=201, operation_id="planOneSentenceVideoRun")
async def plan(
    payload: OneSentenceVideoPlanRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).plan, **payload.model_dump(), idempotency_key=idempotency_key)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("", operation_id="listOneSentenceVideoRuns")
async def list_runs(request: Request, limit: int = 8) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).list_recent, limit)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/{run_id}", operation_id="getOneSentenceVideoRun")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).get, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:commit", operation_id="commitOneSentenceVideoRun")
async def commit(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).commit, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:resume", operation_id="resumeOneSentenceVideoRun")
async def resume(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).resume, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:cancel", operation_id="cancelOneSentenceVideoRun")
async def cancel(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).cancel, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:retry", operation_id="retryOneSentenceVideoRun")
async def retry(run_id: str, payload: OneSentenceVideoRetryRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).retry, run_id, payload.mode)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/{run_id}:regenerate-prompt",
    status_code=201,
    operation_id="regenerateOneSentenceVideoPrompt",
    response_model=QuickGenerationRunResponse,
)
async def regenerate_prompt(
    run_id: str,
    payload: OneSentencePromptRegenerateRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return await run_in_threadpool(
            service(request).regenerate_prompt,
            run_id,
            payload.target,
            idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:reroll-images", operation_id="rerollOneSentenceVideoImages")
async def reroll_images(
    run_id: str,
    payload: OneSentenceImageRerollRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return await run_in_threadpool(
            service(request).reroll_images,
            run_id,
            count=payload.count,
            parent_candidate_id=payload.parent_candidate_id,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/{run_id}/candidates/{candidate_id}:select",
    operation_id="selectOneSentenceVideoImageCandidate",
)
async def select_candidate(
    run_id: str,
    candidate_id: str,
    payload: OneSentenceImageSelectRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return await run_in_threadpool(
            service(request).select_image_candidate,
            run_id,
            candidate_id,
            confirm_review_checks=payload.confirm_review_checks,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
