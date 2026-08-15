from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.creative_entries import CreativeEntryCreateRequest, CreativeRestoreRequest, CreativeRevisionCreateRequest
from local_drama.application.creative_entries import CreativeEntryService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["creative-entries"])


def service(request: Request) -> CreativeEntryService:
    return CreativeEntryService(request.app.state.database)


@router.get("/creative-entries", operation_id="listCreativeEntries")
async def list_entries(project_id: str, request: Request, kind: str | None = None) -> dict[str, object]:
    return {"items": service(request).list_entries(project_id, kind)}


@router.post("/creative-entries", status_code=201, operation_id="createCreativeEntry")
async def create_entry(payload: CreativeEntryCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"entry": service(request).create(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/creative-entries/{entry_id}", operation_id="getCreativeEntry")
async def get_entry(entry_id: str, request: Request) -> dict[str, object]:
    try:
        return {"entry": service(request).get(entry_id), "revisions": service(request).revisions(entry_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/creative-entries/{entry_id}/revisions", status_code=201, operation_id="createCreativeEntryRevision")
async def create_revision(entry_id: str, payload: CreativeRevisionCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"revision": service(request).save_revision(entry_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/creative-entries/{entry_id}/compare", operation_id="compareCreativeEntryRevisions")
async def compare_revisions(entry_id: str, left_revision_id: str, right_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"comparison": service(request).compare(entry_id, left_revision_id, right_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/creative-entries/{entry_id}:restore", status_code=201, operation_id="restoreCreativeEntryRevision")
async def restore_revision(entry_id: str, payload: CreativeRestoreRequest, request: Request) -> dict[str, object]:
    try:
        return {"revision": service(request).restore(entry_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
