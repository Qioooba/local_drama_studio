from __future__ import annotations

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.g3 import BreakdownRequest
from local_drama.api.schemas.llm import LLMProfilePublishRequest, LLMProfileSyncRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.local_llm import LocalLLMService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["local-llm"])


def service(request: Request) -> LocalLLMService:
    return LocalLLMService(request.app.state.database, request.app.state.settings)


@router.get("/local-llm/status", operation_id="getLocalLLMStatus")
async def status(request: Request) -> dict[str, object]:
    try:
        return {"status": service(request).status()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/profile:sync", operation_id="syncLocalLLMProfile")
async def sync_profile(request: Request, payload: LLMProfileSyncRequest | None = None) -> dict[str, object]:
    try:
        return {"profile": service(request).sync_candidate(payload.model if payload else None)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/profile:publish", operation_id="publishLocalLLMProfile")
async def publish_profile(payload: LLMProfilePublishRequest, request: Request) -> dict[str, object]:
    try:
        return {"profile": service(request).publish(payload.profile_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/import-sessions/{session_id}:breakdown-local-llm", status_code=202, operation_id="breakdownWithLocalLLM")
async def breakdown(
    session_id: str,
    payload: BreakdownRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return {
            "job": service(request).enqueue_breakdown(session_id, payload.profile_version_id, idempotency_key or ""),
            "automatic_apply": False,
            "requires_human_action": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/script-breakdown-drafts", operation_id="listScriptBreakdownDrafts")
async def list_breakdown_drafts(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_breakdown_drafts(project_id), "automatic_apply": False, "requires_human_action": True}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
