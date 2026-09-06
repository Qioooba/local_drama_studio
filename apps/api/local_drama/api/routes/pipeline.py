from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.pipeline import (
    ApplyPipelineRequest,
    LLMProbeRequest,
    LLMProbeResponse,
    PipelinePreflightRequest,
    RetryPipelineRequest,
    StartPipelineRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.infrastructure.service_composition import build_pipeline_orchestrator

router = APIRouter(tags=["pipeline"])


def _pipeline(request: Request) -> PipelineOrchestratorService:
    return build_pipeline_orchestrator(request.app.state.database, request.app.state.settings)


@router.post("/pipeline/llm:probe", operation_id="probeLLMConnection", response_model=LLMProbeResponse)
async def probe_llm(payload: LLMProbeRequest, request: Request) -> dict[str, Any]:
    """Test connection and inference capability with a given LLM configuration."""

    def _do_probe() -> dict[str, Any]:
        try:
            client = LocalLLMClient(
                base_url=payload.base_url,
                model=payload.model,
                provider=payload.provider,
                api_key=payload.api_key,
                allow_private_network=request.app.state.settings.allows_private_network,
                timeout_seconds=15.0,
            )
            result = client.probe(load_test=True)
            is_pass = result.get("status") == "PASS"
            detail_msg = "AI 大模型连接成功，推理测试通过！" if is_pass else result.get("probe_levels", {}).get("level_1_network", {}).get("detail", "连接失败")
            return {
                "status": "PASS" if is_pass else "FAILED",
                "message": detail_msg,
                "model": payload.model,
                "provider": payload.provider,
                "available_models": result.get("available_models", []),
            }
        except Exception as exc:
            return {
                "status": "FAILED",
                "message": f"连接失败: {str(exc)}",
                "model": payload.model,
                "provider": payload.provider,
                "available_models": [],
            }

    return await run_in_threadpool(_do_probe)


@router.post("/projects/{project_id}/pipeline:start", operation_id="startOneClickPipeline")
async def start_pipeline(
    project_id: str,
    payload: StartPipelineRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        # run DB-heavy start in threadpool to avoid blocking event loop on sqlite transaction
        run = await run_in_threadpool(
            lambda: service.start_pipeline(
                project_id,
                source_document_version_id=payload.source_document_version_id,
                raw_text=payload.raw_text,
                visual_style=payload.visual_style,
                target_episode_duration_seconds=payload.target_episode_duration_seconds,
                voice_preset=payload.voice_preset,
                auto_run_rendering=False,  # deprecated
                capability_profile_version_id=payload.capability_profile_version_id,
                llm_config=payload.llm_config.model_dump() if payload.llm_config else None,
            )
        )
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline:preflight", operation_id="preflightStoryPipeline")
async def preflight_pipeline(
    project_id: str,
    payload: PipelinePreflightRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        return await run_in_threadpool(
            lambda: service.preflight(
                project_id,
                source_document_version_id=payload.source_document_version_id,
                raw_text=payload.raw_text,
                target_episode_duration_seconds=payload.target_episode_duration_seconds,
                capability_profile_version_id=payload.capability_profile_version_id,
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/pipeline/latest", operation_id="getLatestPipeline")
async def get_latest_pipeline(
    project_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(lambda: service.get_latest_pipeline(project_id))
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/pipeline/runs", operation_id="listPipelineRuns")
async def list_pipeline_runs(
    project_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        runs = await run_in_threadpool(lambda: service.list_pipelines(project_id, limit=20))
        return {"runs": runs}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/pipeline/{run_id}", operation_id="getPipelineRun")
async def get_pipeline_run(
    project_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(lambda: service.get_pipeline(project_id, run_id))
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline/{run_id}:pause", operation_id="pausePipelineRun")
async def pause_pipeline_run(
    project_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(lambda: service.pause_pipeline(project_id, run_id))
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline/{run_id}:resume", operation_id="resumePipelineRun")
async def resume_pipeline_run(
    project_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(lambda: service.resume_pipeline(project_id, run_id))
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline/{run_id}:cancel", operation_id="cancelPipelineRun")
async def cancel_pipeline_run(
    project_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(lambda: service.cancel_pipeline(project_id, run_id))
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline/{run_id}:retry", operation_id="retryPipelineRun")
async def retry_pipeline_run(
    project_id: str,
    run_id: str,
    payload: RetryPipelineRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        run = await run_in_threadpool(
            lambda: service.retry_pipeline(project_id, run_id, payload.expected_revision)
        )
        return {"run": run}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/pipeline/{run_id}:apply", operation_id="applyPipelineRun")
async def apply_pipeline_run(
    project_id: str,
    run_id: str,
    payload: ApplyPipelineRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        service = _pipeline(request)
        return await run_in_threadpool(
            lambda: service.apply_pipeline(
                project_id,
                run_id,
                expected_revision=payload.expected_revision,
                sections=payload.sections,
            )
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
