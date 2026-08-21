from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from local_drama.api.schemas.g3 import BreakdownDraftApplyRequest, BreakdownRequest, DocumentImportCommitRequest, DocumentImportRequest, SourcePassageResponse
from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.documents import DocumentImportService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["imports"])


def service(request: Request) -> DocumentImportService:
    return DocumentImportService(request.app.state.database, request.app.state.settings)


@router.post("/projects/{project_id}/imports", status_code=201, operation_id="importScriptDocument")
async def import_script(project_id: str, payload: DocumentImportRequest, request: Request) -> dict[str, object]:
    try:
        return {"import": service(request).import_document(project_id, payload.source_path)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/import-sessions/{session_id}", operation_id="getImportSession")
async def get_import_session(session_id: str, request: Request) -> dict[str, object]:
    try:
        return {"session": service(request).get_session(session_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/import-sessions/{session_id}/issues", operation_id="getImportSessionIssues")
async def get_import_session_issues(session_id: str, request: Request) -> dict[str, object]:
    try:
        return {"issues": service(request).get_issues(session_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/source-document-versions/{source_document_version_id}/passage",
    response_model=SourcePassageResponse,
    operation_id="getSourceDocumentPassage",
)
async def get_source_document_passage(
    source_document_version_id: str,
    request: Request,
    start: int = Query(ge=0),
    end: int = Query(gt=0),
) -> dict[str, object]:
    try:
        return service(request).get_passage(source_document_version_id, start, end)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/import-sessions/{session_id}:commit", operation_id="commitImportSession")
async def commit_import_session(session_id: str, payload: DocumentImportCommitRequest, request: Request) -> dict[str, object]:
    try:
        return {"commit": service(request).commit(session_id, payload.expected_preview_hash)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/import-sessions/{session_id}:request-breakdown", status_code=202, operation_id="requestScriptBreakdown")
async def request_breakdown(
    session_id: str,
    payload: BreakdownRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return {
            "job": service(request).request_breakdown(session_id, payload.profile_version_id, idempotency_key or ""),
            "automatic_apply": False,
            "requires_human_action": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/breakdown-drafts/{draft_id}:apply", operation_id="applyScriptBreakdownDraft")
async def apply_breakdown_draft(draft_id: str, payload: BreakdownDraftApplyRequest, request: Request) -> dict[str, object]:
    try:
        apply = BreakdownApplyService(request.app.state.database, request.app.state.settings).apply_draft(draft_id, payload.episode_id)
        return {"apply": apply}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
