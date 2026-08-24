from __future__ import annotations

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.episode_production_runs import (
    EpisodeCheckpointPolicy,
    EpisodeProductionPauseRequest,
    EpisodeProductionResumeRequest,
    EpisodeProductionRunRequest,
)
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["episode-production-runs"])


def service(request: Request) -> EpisodeProductionRunService:
    return EpisodeProductionRunService(request.app.state.database, request.app.state.settings)


@router.get("/episodes/{episode_id}/production-runs/preflight", operation_id="preflightEpisodeProductionRun")
async def preflight(episode_id: str, request: Request, tts_enabled: bool = True, production_mode: str = "BALANCED", checkpoint_policy: EpisodeCheckpointPolicy = "ON_EXCEPTION", min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024) -> dict[str, object]:
    try:
        return {
            "preflight": service(request).preflight(
                episode_id,
                tts_enabled=tts_enabled,
                production_mode=production_mode,
                checkpoint_policy=checkpoint_policy,
                min_free_disk_bytes=min_free_disk_bytes,
                include_front_half=True,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/production-runs", status_code=201, operation_id="startEpisodeProductionRun")
async def start(episode_id: str, payload: EpisodeProductionRunRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        return {"run": service(request).start(episode_id, idempotency_key=idempotency_key, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episode-production-runs/{run_id}", operation_id="getEpisodeProductionRun")
async def get(run_id: str, request: Request, include_jobs: bool = False) -> dict[str, object]:
    try:
        return {"run": service(request).get(run_id, include_jobs=include_jobs)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episode-production-runs/{run_id}/pause", operation_id="pauseEpisodeProductionRun")
async def pause(run_id: str, payload: EpisodeProductionPauseRequest, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).pause(run_id, reason=payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episode-production-runs/{run_id}/resume", operation_id="resumeEpisodeProductionRun")
async def resume(run_id: str, payload: EpisodeProductionResumeRequest, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).resume(run_id, note=payload.note)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episode-production-runs/{run_id}/cancel", operation_id="cancelEpisodeProductionRun")
async def cancel(run_id: str, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).cancel(run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episode-production-runs/{run_id}/recover", operation_id="recoverEpisodeProductionRun")
async def recover(run_id: str, request: Request) -> dict[str, object]:
    try:
        return service(request).recover(run_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
