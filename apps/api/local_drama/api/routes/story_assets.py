from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.story_assets import ShotAssetBindRequest, StoryAssetArchiveRequest, StoryAssetCreateRequest, StoryAssetUpdateRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["story-assets"])


def service(request: Request) -> StoryAssetService:
    return StoryAssetService(request.app.state.database, request.app.state.settings)


@router.post("/projects/{project_id}/story-assets", status_code=201, operation_id="createStoryAsset")
async def create_story_asset(project_id: str, payload: StoryAssetCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"asset": service(request).create_asset(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/story-assets", operation_id="listStoryAssets")
async def list_story_assets(project_id: str, request: Request, kind: str | None = None) -> dict[str, object]:
    try:
        return {"items": service(request).list_assets(project_id, kind)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/story-assets/{asset_id}", operation_id="getStoryAsset")
async def get_story_asset(asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"asset": service(request).get_asset(asset_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.patch("/story-assets/{asset_id}", operation_id="updateStoryAsset")
async def update_story_asset(asset_id: str, payload: StoryAssetUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return {"asset": service(request).update_asset(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}:archive", status_code=201, operation_id="archiveStoryAsset")
async def archive_story_asset(asset_id: str, payload: StoryAssetArchiveRequest, request: Request) -> dict[str, object]:
    try:
        return {"asset": service(request).archive_asset(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}/story-asset-bindings", status_code=201, operation_id="bindStoryAssetToShot")
async def bind_story_asset_to_shot(shot_id: str, payload: ShotAssetBindRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": service(request).bind_asset_to_shot(shot_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}/story-asset-bindings", operation_id="listShotStoryAssets")
async def list_shot_story_assets(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_shot_assets(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/story-asset-bindings/{binding_id}", operation_id="unbindStoryAssetFromShot")
async def unbind_story_asset_from_shot(binding_id: str, request: Request) -> dict[str, object]:
    try:
        return service(request).unbind_asset_from_shot(binding_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
