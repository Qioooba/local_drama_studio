from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.shot_groups import (
    ExpectedRevisionRequest,
    ShotGroupCreateRequest,
    ShotGroupMembersRequest,
    ShotGroupReorderRequest,
    ShotGroupUpdateRequest,
    ShotSceneAssignmentRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.shot_groups import ShotGroupService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["shot-groups"])


def _service(request: Request) -> ShotGroupService:
    return ShotGroupService(request.app.state.database)


@router.get("/episodes/{episode_id}/shot-groups", operation_id="getShotGroupWorkspace")
async def get_workspace(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"workspace": _service(request).workspace(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}:assign-scene", operation_id="assignShotScene")
async def assign_shot_scene(
    shot_id: str, payload: ShotSceneAssignmentRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"shot": _service(request).assign_scene(shot_id=shot_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/shot-groups", operation_id="createShotGroup")
async def create_shot_group(
    episode_id: str, payload: ShotGroupCreateRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"group": _service(request).create_group(episode_id=episode_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/shot-groups/{group_id}", operation_id="updateShotGroup")
async def update_shot_group(
    group_id: str, payload: ShotGroupUpdateRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"group": _service(request).update_group(group_id=group_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shot-groups/{group_id}:archive", operation_id="archiveShotGroup")
async def archive_shot_group(
    group_id: str, payload: ExpectedRevisionRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"group": _service(request).archive_group(group_id=group_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/shot-groups/{group_id}/members", operation_id="replaceShotGroupMembers")
async def replace_shot_group_members(
    group_id: str, payload: ShotGroupMembersRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"group": _service(request).replace_members(group_id=group_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/shot-groups:reorder", operation_id="reorderShotGroups")
async def reorder_shot_groups(
    episode_id: str, payload: ShotGroupReorderRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"groups": _service(request).reorder_groups(
            episode_id=episode_id, items=[item.model_dump() for item in payload.items],
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
