from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.shot_editing import ShotEditCommitRequest, ShotEditPlanRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.shot_editing import ShotEditingService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["shot-editing"])


def _service(request: Request) -> ShotEditingService:
    return ShotEditingService(request.app.state.database)


@router.get("/episodes/{episode_id}/shot-edit", operation_id="getShotEditingContext")
async def get_shot_editing_context(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"context": _service(request).context(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/shot-edit:plan", operation_id="planShotEdit")
async def plan_shot_edit(
    episode_id: str, payload: ShotEditPlanRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"plan": _service(request).plan(episode_id, payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/shot-edit:commit", operation_id="commitShotEdit")
async def commit_shot_edit(
    episode_id: str, payload: ShotEditCommitRequest, request: Request,
) -> dict[str, object]:
    try:
        data = payload.model_dump()
        expected_plan_hash = str(data.pop("expected_plan_hash"))
        return {"result": _service(request).commit(episode_id, data, expected_plan_hash)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
