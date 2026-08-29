from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.quick_generations import (
    QuickGenerationImageRerollRequest,
    QuickGenerationImageSelectRequest,
    QuickGenerationListResponse,
    QuickGenerationPlanRequest,
    QuickGenerationPromptRegenerateRequest,
    QuickGenerationRetryRequest,
    QuickGenerationRunResponse,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.quick_generations import QuickGenerationService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/quick-generations", tags=["quick-generations"])
output_router = APIRouter(prefix="/quick-generation-outputs", tags=["quick-generations"])


def service(request: Request) -> QuickGenerationService:
    database = request.app.state.database
    settings = request.app.state.settings
    return QuickGenerationService(
        database,
        settings,
        profiles=ProfileService(database, settings.manifest_path),
        workflows=WorkflowService(database, settings),
        jobs=JobService(database, settings),
        media=MediaService(database, settings),
        llm=LocalLLMService(database, settings),
    )


@router.post(":plan", status_code=201, operation_id="planQuickGeneration", response_model=QuickGenerationRunResponse)
async def plan(payload: QuickGenerationPlanRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).plan, **payload.model_dump(), idempotency_key=idempotency_key)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("", operation_id="listQuickGenerations", response_model=QuickGenerationListResponse)
async def list_runs(request: Request, limit: int = 12) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).list_recent, limit)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/{run_id}", operation_id="getQuickGeneration", response_model=QuickGenerationRunResponse)
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).get, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:commit", operation_id="commitQuickGeneration", response_model=QuickGenerationRunResponse)
async def commit(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).commit, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:resume", operation_id="resumeQuickGeneration", response_model=QuickGenerationRunResponse)
async def resume(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).resume, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:cancel", operation_id="cancelQuickGeneration", response_model=QuickGenerationRunResponse)
async def cancel(run_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).cancel, run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:retry", operation_id="retryQuickGeneration", response_model=QuickGenerationRunResponse)
async def retry(run_id: str, payload: QuickGenerationRetryRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).retry, run_id, payload.mode)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/{run_id}:regenerate-prompt",
    status_code=201,
    operation_id="regenerateQuickGenerationPrompt",
    response_model=QuickGenerationRunResponse,
)
async def regenerate_prompt(
    run_id: str, payload: QuickGenerationPromptRegenerateRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")
) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).regenerate_prompt, run_id, payload.target, idempotency_key=idempotency_key)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{run_id}:reroll-images", operation_id="rerollQuickGenerationImages", response_model=QuickGenerationRunResponse)
async def reroll_images(run_id: str, payload: QuickGenerationImageRerollRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).reroll_images, run_id, count=payload.count, parent_candidate_id=payload.parent_candidate_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/{run_id}/candidates/{candidate_id}:select",
    operation_id="selectQuickGenerationCandidate",
    response_model=QuickGenerationRunResponse,
)
async def select_candidate(run_id: str, candidate_id: str, payload: QuickGenerationImageSelectRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).select_image_candidate, run_id, candidate_id, confirm_review_checks=payload.confirm_review_checks)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@output_router.get("/{output_id}/content", operation_id="getQuickGenerationOutputContent")
async def output_content(output_id: str, request: Request) -> FileResponse:
    try:
        output, path = await run_in_threadpool(service(request).content_path, output_id)
        return FileResponse(path, media_type=str(output["mime_type"]), headers={"ETag": f'"{output["sha256"]}"'})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@output_router.get("/{output_id}/download", operation_id="downloadQuickGenerationOutput")
async def output_download(output_id: str, request: Request) -> FileResponse:
    try:
        output, path = await run_in_threadpool(service(request).content_path, output_id)
        artifact = output["artifact"]
        return FileResponse(
            path,
            media_type=str(output["mime_type"]),
            filename=str(artifact["download_filename"]),
            headers={"ETag": f'"{output["sha256"]}"'},
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@output_router.get("/{output_id}/thumbnail", operation_id="getQuickGenerationOutputThumbnail")
async def output_thumbnail(output_id: str, request: Request, size: str = "small", frame: str = "poster") -> FileResponse:
    try:
        path, mime = await run_in_threadpool(service(request).thumbnail_path, output_id, size, frame)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
