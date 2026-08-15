from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g3 import BreakdownRequest, DocumentImportCommitRequest, DocumentImportRequest
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


@router.post("/import-sessions/{session_id}:commit", operation_id="commitImportSession")
async def commit_import_session(session_id: str, payload: DocumentImportCommitRequest, request: Request) -> dict[str, object]:
    try:
        return {"commit": service(request).commit(session_id, payload.expected_preview_hash)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/import-sessions/{session_id}:request-breakdown", operation_id="requestScriptBreakdown")
async def request_breakdown(session_id: str, payload: BreakdownRequest, request: Request) -> dict[str, object]:
    try:
        return {"draft": service(request).request_breakdown(session_id, payload.profile_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
