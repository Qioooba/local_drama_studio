from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from local_drama.api.schemas.g3 import (
    BreakdownDraftApplyRequest,
    BreakdownDraftSceneRevisionRequest,
    BreakdownRequest,
    DocumentImportCommitRequest,
    DocumentImportRequest,
    DocumentImportResponse,
    LatestDocumentImportResponse,
    SourceParagraphPageResponse,
    SourcePassageResponse,
)
from local_drama.api.server_paths import require_server_loopback
from local_drama.api.uploading import receive_bounded_upload
from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_revisions import BreakdownRevisionService
from local_drama.application.documents import DocumentImportService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.media import DOCUMENT_EXTENSIONS
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["imports"])


def service(request: Request) -> DocumentImportService:
    return DocumentImportService(request.app.state.database, request.app.state.settings)


@router.post(
    "/projects/{project_id}/imports",
    status_code=201,
    response_model=DocumentImportResponse,
    response_model_exclude_none=True,
    operation_id="importScriptDocument",
)
async def import_script(project_id: str, payload: DocumentImportRequest, request: Request) -> dict[str, object]:
    try:
        require_server_loopback(request, action="按绝对路径导入文档到")
        return {"import": service(request).import_document(project_id, payload.source_path)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/imports:upload",
    status_code=201,
    response_model=DocumentImportResponse,
    response_model_exclude_none=True,
    operation_id="uploadScriptDocument",
)
async def upload_script(project_id: str, request: Request) -> dict[str, object]:
    """Register one bounded browser upload for supported novel/script documents."""
    try:
        maximum_mb = request.app.state.settings.uploads.document_mb
        async with receive_bounded_upload(
            request,
            work_group="document-uploads",
            allowed_suffixes=frozenset(DOCUMENT_EXTENSIONS),
            maximum_bytes=maximum_mb * 1024 * 1024,
            default_filename="script.txt",
            error_prefix="DOCUMENT_UPLOAD",
            type_error_code="UNSUPPORTED_DOCUMENT_TYPE",
            type_error_message="原稿仅支持 TXT、Markdown、DOCX、PDF、EPUB",
            too_large_message=f"剧本文档不能超过 {maximum_mb} MB",
            empty_message="请选择非空剧本文档",
        ) as (temporary, _safe_filename, _received_bytes):
            return {"import": service(request).import_document(project_id, temporary)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/imports/latest",
    response_model=LatestDocumentImportResponse,
    response_model_exclude_none=True,
    operation_id="getLatestProjectScriptImport",
)
async def get_latest_project_script_import(project_id: str, request: Request) -> dict[str, object]:
    """Restore the latest durable preview/commit instead of relying on browser state."""
    try:
        return {"latest": service(request).latest_for_project(project_id)}
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
    "/import-sessions/{session_id}/paragraphs",
    response_model=SourceParagraphPageResponse,
    operation_id="getImportSessionParagraphs",
)
async def get_import_session_paragraphs(
    session_id: str,
    request: Request,
    start: int = Query(default=1, ge=1),
    limit: int = Query(default=40, ge=1, le=100),
) -> dict[str, object]:
    try:
        return service(request).get_paragraphs(session_id, start=start, limit=limit)
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
        return {
            "commit": service(request).commit(
                session_id,
                payload.expected_preview_hash,
                source_paragraph_start=payload.source_paragraph_start,
                source_paragraph_end=payload.source_paragraph_end,
            )
        }
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
