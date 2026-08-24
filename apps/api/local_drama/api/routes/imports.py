from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Header, Query, Request

from local_drama.api.schemas.g3 import (
    BreakdownDraftApplyRequest,
    BreakdownDraftSceneRevisionRequest,
    BreakdownRequest,
    DocumentImportCommitRequest,
    DocumentImportRequest,
    SourcePassageResponse,
)
from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_revisions import BreakdownRevisionService
from local_drama.application.documents import DocumentImportService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.media import DOCUMENT_EXTENSIONS
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


@router.post("/projects/{project_id}/imports:upload", status_code=201, operation_id="uploadScriptDocument")
async def upload_script(project_id: str, request: Request) -> dict[str, object]:
    """Register one bounded browser upload for script documents (.txt, .md, .docx)."""
    maximum_bytes = 25 * 1024 * 1024
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > maximum_bytes:
                raise DomainRuleError("DOCUMENT_UPLOAD_TOO_LARGE", "剧本文档不能超过 25 MB")
        except ValueError as error:
            raise api_error_from_domain(DomainRuleError("DOCUMENT_UPLOAD_LENGTH_INVALID", "上传文档长度无效")) from error
    filename = unquote(request.headers.get("x-file-name", "script.txt"))
    safe_filename = Path(filename).name[:180] or "script.txt"
    if Path(safe_filename).suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise api_error_from_domain(DomainRuleError("UNSUPPORTED_DOCUMENT_TYPE", "剧本文档仅支持 TXT、Markdown、DOCX"))
    temporary_directory = request.app.state.settings.work_root / "document-uploads" / uuid.uuid4().hex
    temporary_directory.mkdir(parents=True, exist_ok=False)
    temporary = temporary_directory / safe_filename
    try:
        received_bytes = 0
        with temporary.open("xb") as destination:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received_bytes += len(chunk)
                if received_bytes > maximum_bytes:
                    raise DomainRuleError("DOCUMENT_UPLOAD_TOO_LARGE", "剧本文档不能超过 25 MB")
                destination.write(chunk)
        if received_bytes == 0:
            raise DomainRuleError("DOCUMENT_UPLOAD_EMPTY", "请选择非空剧本文档")
        doc_service = service(request)
        imported = doc_service.import_document(project_id, temporary)
        return {"import": imported}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    finally:
        temporary.unlink(missing_ok=True)
        try:
            temporary_directory.rmdir()
        except OSError:
            pass


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
            "job": service(request).request_breakdown(
                session_id,
                payload.profile_version_id,
                idempotency_key or "",
                target_episode_id=payload.episode_id,
                source_paragraph_start=payload.source_paragraph_start,
                source_paragraph_end=payload.source_paragraph_end,
            ),
            "automatic_apply": False,
            "requires_human_action": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/breakdown-drafts/{draft_id}:apply", operation_id="applyScriptBreakdownDraft")
async def apply_breakdown_draft(draft_id: str, payload: BreakdownDraftApplyRequest, request: Request) -> dict[str, object]:
    try:
        apply = BreakdownApplyService(request.app.state.database, request.app.state.settings).apply_draft(
            draft_id,
            payload.episode_id,
            scene_nos=payload.scene_nos,
        )
        return {"apply": apply}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put(
    "/breakdown-drafts/{draft_id}/scenes/{scene_no}:revise",
    operation_id="reviseScriptBreakdownDraftScene",
)
async def revise_breakdown_draft_scene(
    draft_id: str,
    scene_no: int,
    payload: BreakdownDraftSceneRevisionRequest,
    request: Request,
) -> dict[str, object]:
    try:
        revision = BreakdownRevisionService(request.app.state.database).revise_scene(
            draft_id,
            scene_no,
            payload.model_dump(exclude={"expected_revision", "change_note"}),
            expected_revision=payload.expected_revision,
            change_note=payload.change_note,
        )
        return {"revision": revision}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
