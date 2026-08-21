from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.director_desk import (
    DirectorDeskResponse,
    FrameBridgeCurrentFrameRequest,
    FrameBridgeInheritRequest,
    FrameBridgeRevisionRequest,
    FrameBridgeSourceFrameRequest,
)
from local_drama.application.director_desk import DirectorDeskReadModelService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["director-desk"])


@router.get(
    "/projects/{project_id}/episodes/{episode_id}/director-desk",
    operation_id="getDirectorDesk",
    response_model=DirectorDeskResponse,
)
async def get_director_desk(
    project_id: str,
    episode_id: str,
    request: Request,
    shot_id: str | None = None,
    nav_radius: int = Query(default=12, ge=2, le=25),
) -> DirectorDeskResponse:
    try:
        projection = DirectorDeskReadModelService(request.app.state.database).get(
            project_id=project_id,
            episode_id=episode_id,
            shot_id=shot_id,
            nav_radius=nav_radius,
        )
        return DirectorDeskResponse.model_validate(projection)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/frame-bridges/{transition_id}/inherit", operation_id="inheritFrameBridge")
async def inherit_frame_bridge(
    transition_id: str, payload: FrameBridgeInheritRequest, request: Request
) -> dict[str, object]:
    try:
        return {
            "frame_bridge": FrameBridgeCommandService(request.app.state.database).inherit(
                transition_id, **payload.model_dump()
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/frame-bridges/{transition_id}/current-frame", operation_id="setFrameBridgeCurrentFrame")
async def set_frame_bridge_current_frame(
    transition_id: str, payload: FrameBridgeCurrentFrameRequest, request: Request
) -> dict[str, object]:
    try:
        return {
            "frame_bridge": FrameBridgeCommandService(request.app.state.database).set_current_frame(
                transition_id, **payload.model_dump()
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/frame-bridges/{transition_id}/source-frame", operation_id="setFrameBridgeSourceFrame")
async def set_frame_bridge_source_frame(
    transition_id: str, payload: FrameBridgeSourceFrameRequest, request: Request
) -> dict[str, object]:
    try:
        return {
            "frame_bridge": FrameBridgeCommandService(request.app.state.database).set_source_frame(
                transition_id, **payload.model_dump()
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _set_frame_bridge_locked(
    transition_id: str, payload: FrameBridgeRevisionRequest, request: Request, *, locked: bool
) -> dict[str, object]:
    return {
        "frame_bridge": FrameBridgeCommandService(request.app.state.database).set_locked(
            transition_id, expected_boundary_revision=payload.expected_boundary_revision, locked=locked
        )
    }


@router.post("/frame-bridges/{transition_id}/lock", operation_id="lockFrameBridge")
async def lock_frame_bridge(
    transition_id: str, payload: FrameBridgeRevisionRequest, request: Request
) -> dict[str, object]:
    try:
        return _set_frame_bridge_locked(transition_id, payload, request, locked=True)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/frame-bridges/{transition_id}/unlock", operation_id="unlockFrameBridge")
async def unlock_frame_bridge(
    transition_id: str, payload: FrameBridgeRevisionRequest, request: Request
) -> dict[str, object]:
    try:
        return _set_frame_bridge_locked(transition_id, payload, request, locked=False)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
