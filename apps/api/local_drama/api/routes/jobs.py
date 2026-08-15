from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from local_drama.api.schemas.jobs import (
    ArtifactPromoteRequest,
    ArtifactRegisterRequest,
    JobClaimRequest,
    JobCloneRequest,
    JobCompleteRequest,
    JobCreateRequest,
    JobHeartbeatRequest,
)
from local_drama.api.schemas.outbox import OutboxDeliveryRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.outbox_delivery import OutboxDeliveryService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["jobs"])


def service(request: Request) -> JobService:
    return JobService(request.app.state.database, request.app.state.settings)


@router.post("/jobs", status_code=201, operation_id="createJob")
async def create_job(payload: JobCreateRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        values = payload.model_dump()
        return {"job": service(request).create_job(job_type=str(values.pop("type")), idempotency_key=idempotency_key, **values)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/jobs", operation_id="listJobs")
async def list_jobs(request: Request, project_id: str | None = None, state: list[str] | None = None, cursor: int = 0, limit: int = 100) -> dict[str, object]:
    return service(request).list_jobs_page(project_id, state, cursor, limit)


@router.get("/jobs/{job_id}", operation_id="getJob")
async def get_job(job_id: str, request: Request) -> dict[str, object]:
    try:
        return {"job": service(request).get_job(job_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/jobs:claim", operation_id="claimJob")
async def claim_job(payload: JobClaimRequest, request: Request) -> dict[str, object]:
    try:
        return {"claim": service(request).claim(payload.worker_id, payload.channels, payload.lease_seconds)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/job-attempts/{attempt_id}:heartbeat", operation_id="heartbeatJobAttempt")
async def heartbeat(attempt_id: str, payload: JobHeartbeatRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "heartbeat": service(request).heartbeat(
                attempt_id, payload.lease_token, payload.worker_id, progress=payload.progress, lease_seconds=payload.lease_seconds
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/job-attempts/{attempt_id}:complete", operation_id="completeJobAttempt")
async def complete(attempt_id: str, payload: JobCompleteRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "result": service(request).complete(attempt_id, payload.lease_token, payload.worker_id, **payload.model_dump(exclude={"lease_token", "worker_id"}))
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/jobs/{job_id}:cancel", operation_id="cancelJob")
async def cancel(job_id: str, request: Request) -> dict[str, object]:
    try:
        return {"job": service(request).cancel(job_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/jobs/{job_id}:retry", operation_id="retryJob")
async def retry(job_id: str, request: Request) -> dict[str, object]:
    try:
        return {"job": service(request).retry(job_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/jobs/{job_id}:clone", status_code=201, operation_id="cloneJob")
async def clone(job_id: str, payload: JobCloneRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        return {"job": service(request).clone(job_id, idempotency_key, payload.input_overrides)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/jobs:reconcile", operation_id="reconcileJobs")
async def reconcile(request: Request) -> dict[str, object]:
    try:
        return {"result": service(request).reconcile()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/job-attempts/{attempt_id}/artifacts", status_code=201, operation_id="registerJobArtifact")
async def register_artifact(attempt_id: str, payload: ArtifactRegisterRequest, request: Request) -> dict[str, object]:
    try:
        return {"artifact": service(request).register_artifact(attempt_id, payload.kind, payload.sandbox_rel_path)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/artifacts/{artifact_id}:promote-media", status_code=201, operation_id="promoteJobArtifactToMedia")
async def promote_artifact(artifact_id: str, payload: ArtifactPromoteRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "media": MediaService(request.app.state.database, request.app.state.settings).promote_job_artifact(
                artifact_id,
                purpose=payload.purpose,
                media_kind=payload.media_kind,
                stage=payload.stage,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/events", operation_id="streamEvents")
async def stream_events(request: Request, after_event_id: int = 0, project_id: str | None = None, limit: int = 100, follow: bool = False) -> StreamingResponse:
    job_service = service(request)

    async def body() -> AsyncIterator[str]:
        cursor = after_event_id
        deadline = monotonic() + 60 if follow else monotonic()
        while True:
            events = job_service.events(after_event_id=cursor, project_id=project_id, limit=limit)
            for event in events:
                cursor = int(event["event_id"])
                yield f"id: {event['event_id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not follow or monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/events:deliver", operation_id="deliverOutboxEvents")
async def deliver_events(payload: OutboxDeliveryRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": OutboxDeliveryService(request.app.state.database).deliver(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
