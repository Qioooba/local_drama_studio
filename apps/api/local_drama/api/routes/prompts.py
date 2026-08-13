from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.prompts import PromptBranchRequest, PromptCreateRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.prompts import PromptService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["prompts"])


def service(request: Request) -> PromptService:
    return PromptService(request.app.state.database)


@router.post("/prompts", status_code=201, operation_id="createPrompt")
async def create_prompt(payload: PromptCreateRequest, request: Request) -> dict[str, object]:
    try:
        return service(request).create_prompt(**payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/prompt-revisions/{revision_id}:branch", status_code=201, operation_id="branchPromptRevision")
async def branch_prompt_revision(revision_id: str, payload: PromptBranchRequest, request: Request) -> dict[str, object]:
    try:
        return {"revision": service(request).branch_revision(revision_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/prompt-revisions/{revision_id}", operation_id="getPromptRevision")
async def get_prompt_revision(revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"revision": service(request).get_revision(revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
