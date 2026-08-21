from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.beat_replan import BeatReplanApplyRequest, BeatReplanPlanRequest
from local_drama.application.beat_replan import BeatReplanService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["beat-replan"])


def _service(request: Request) -> BeatReplanService:
    return BeatReplanService(request.app.state.database)


@router.post(
    "/episodes/{episode_id}/shot-groups/{group_id}/replan:plan",
    operation_id="planSelectedBeatReplan",
)
async def plan_selected_beat_replan(
    episode_id: str, group_id: str, payload: BeatReplanPlanRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"plan": _service(request).plan(
            episode_id=episode_id, group_id=group_id, **payload.model_dump(),
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/shot-groups/{group_id}/replan:apply",
    operation_id="applySelectedBeatReplan",
)
async def apply_selected_beat_replan(
    episode_id: str, group_id: str, payload: BeatReplanApplyRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"apply": _service(request).apply(
            episode_id=episode_id, group_id=group_id, **payload.model_dump(),
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
