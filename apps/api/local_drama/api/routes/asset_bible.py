from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Request
from pydantic import BaseModel

from local_drama.api.schemas.asset_bible import (
    AssetDetailPreflightRequest,
    AssetDetailSubmitRequest,
    AssetExpressionPreflightRequest,
    AssetExpressionSubmitRequest,
    AssetImageBatchPlanRequest,
    AssetImageBatchSubmitRequest,
    AssetMultiViewPreflightRequest,
    AssetMultiViewPromptDraftRequest,
    AssetMultiViewSubmitRequest,
    EpisodeAssetStateBindRequest,
    ShotAssetStateBindRequest,
    StoryAssetReferenceArchiveRequest,
    StoryAssetReferenceCreateRequest,
    StoryAssetReferenceUpdateRequest,
    StoryAssetStateArchiveRequest,
    StoryAssetStateCreateRequest,
    StoryAssetStateUpdateRequest,
)
from local_drama.application.asset_image_generation import AssetImageGenerationBatchService
from local_drama.application.asset_multiview import AssetDetailService, AssetExpressionService, AssetMultiViewService
from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.queries.asset_bible import AssetBibleQueryService
from local_drama.application.voice_clone import VoiceCloneService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.asset_bible_repository import SqliteAssetBibleRepository
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.service_composition import build_asset_image_batch

router = APIRouter(tags=["asset-bible"])

T = TypeVar("T")


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _asset_project(request: Request, asset_id: str) -> str:
    with _database(request).connect() as connection:
        project_id = SqliteAssetBibleRepository(connection).asset_project_id(asset_id)
    if project_id is None:
        raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
    return project_id


def _run_command(request: Request, fn: Callable[[AssetBibleCommandService], T]) -> T:
    """Run a command inside one short write transaction (commit on success)."""
    with _database(request).transaction() as connection:
        service = AssetBibleCommandService(SqliteAssetBibleRepository(connection))
        return fn(service)


def _run_query(request: Request, fn: Callable[[AssetBibleQueryService], T]) -> T:
    with _database(request).connect() as connection:
        service = AssetBibleQueryService(SqliteAssetBibleRepository(connection))
        return fn(service)


def _multiview(request: Request) -> AssetMultiViewService:
    return AssetMultiViewService(_database(request), request.app.state.settings)


def _expression(request: Request) -> AssetExpressionService:
    return AssetExpressionService(_database(request), request.app.state.settings)


def _detail(request: Request) -> AssetDetailService:
    return AssetDetailService(_database(request), request.app.state.settings)


def _asset_images(request: Request) -> AssetImageGenerationBatchService:
    return build_asset_image_batch(_database(request), request.app.state.settings)


@router.get("/projects/{project_id}/asset-bible", operation_id="getAssetBible")
async def get_asset_bible(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"bible": _run_query(request, lambda service: service.asset_bible(project_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/story-assets/{asset_id}/detail", operation_id="getAssetBibleDetail")
async def get_asset_bible_detail(asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"asset_detail": _run_query(request, lambda service: service.asset_detail(asset_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/asset-image-batches:plan", operation_id="planAssetImageGenerationBatch")
async def plan_asset_image_generation_batch(project_id: str, payload: AssetImageBatchPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": _asset_images(request).plan(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/asset-image-batches:submit", status_code=201, operation_id="submitAssetImageGenerationBatch")
async def submit_asset_image_generation_batch(project_id: str, payload: AssetImageBatchSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return {"batch": _asset_images(request).submit(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/asset-image-batches", operation_id="listAssetImageGenerationBatches")
async def list_asset_image_generation_batches(project_id: str, request: Request, asset_kind: str | None = None, limit: int = 5) -> dict[str, object]:
    try:
        return {"items": _asset_images(request).list_batches(project_id, asset_kind=asset_kind, limit=limit)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/asset-image-batches/{batch_id}", operation_id="getAssetImageGenerationBatch")
async def get_asset_image_generation_batch(batch_id: str, request: Request) -> dict[str, object]:
    try:
        return {"batch": _asset_images(request).get_batch(batch_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-multiview:preflight", operation_id="preflightAssetMultiView")
async def preflight_asset_multiview(asset_id: str, payload: AssetMultiViewPreflightRequest, request: Request) -> dict[str, object]:
    try:
        return {"preflight": _multiview(request).preflight(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-multiview:prompts", operation_id="draftAssetMultiViewPrompts")
def draft_asset_multiview_prompts(asset_id: str, payload: AssetMultiViewPromptDraftRequest, request: Request) -> dict[str, object]:
    try:
        return {"prompt_bundle": _multiview(request).draft_prompts(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-multiview", status_code=201, operation_id="submitAssetMultiView")
async def submit_asset_multiview(asset_id: str, payload: AssetMultiViewSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return _multiview(request).submit(asset_id, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-expression:preflight", operation_id="preflightAssetExpression")
async def preflight_asset_expression(asset_id: str, payload: AssetExpressionPreflightRequest, request: Request) -> dict[str, object]:
    try:
        return {"preflight": _expression(request).preflight(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-expression", status_code=201, operation_id="submitAssetExpression")
async def submit_asset_expression(asset_id: str, payload: AssetExpressionSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return _expression(request).submit(asset_id, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-detail:preflight", operation_id="preflightAssetDetail")
async def preflight_asset_detail(asset_id: str, payload: AssetDetailPreflightRequest, request: Request) -> dict[str, object]:
    try:
        return {"preflight": _detail(request).preflight(asset_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/generate-detail", status_code=201, operation_id="submitAssetDetail")
async def submit_asset_detail(asset_id: str, payload: AssetDetailSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return _detail(request).submit(asset_id, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/states", status_code=201, operation_id="createStoryAssetState")
async def create_story_asset_state(asset_id: str, payload: StoryAssetStateCreateRequest, request: Request) -> dict[str, object]:
    try:
        project_id = _asset_project(request, asset_id)
        return {"state": _run_command(request, lambda service: service.create_state(project_id, asset_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.patch("/story-asset-states/{state_id}", operation_id="updateStoryAssetState")
async def update_story_asset_state(state_id: str, payload: StoryAssetStateUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return {"state": _run_command(request, lambda service: service.update_state(state_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-asset-states/{state_id}:archive", status_code=201, operation_id="archiveStoryAssetState")
async def archive_story_asset_state(state_id: str, payload: StoryAssetStateArchiveRequest, request: Request) -> dict[str, object]:
    try:
        return {"state": _run_command(request, lambda service: service.archive_state(state_id, payload.expected_revision))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/story-assets/{asset_id}/references", operation_id="listStoryAssetReferences")
async def list_story_asset_references(asset_id: str, request: Request, asset_state_id: str | None = None) -> dict[str, object]:
    try:
        return {"items": _run_query(request, lambda service: service.list_references(asset_id, asset_state_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{asset_id}/references", status_code=201, operation_id="createStoryAssetReference")
async def create_story_asset_reference(asset_id: str, payload: StoryAssetReferenceCreateRequest, request: Request) -> dict[str, object]:
    try:
        project_id = _asset_project(request, asset_id)
        return {"reference": _run_command(request, lambda service: service.add_reference(project_id, asset_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.patch("/story-asset-references/{reference_id}", operation_id="updateStoryAssetReference")
async def update_story_asset_reference(reference_id: str, payload: StoryAssetReferenceUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return {"reference": _run_command(request, lambda service: service.update_reference(reference_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-asset-references/{reference_id}:archive", status_code=201, operation_id="archiveStoryAssetReference")
async def archive_story_asset_reference(reference_id: str, payload: StoryAssetReferenceArchiveRequest, request: Request) -> dict[str, object]:
    try:
        return {"reference": _run_command(request, lambda service: service.archive_reference(reference_id, payload.expected_revision))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/asset-state-bindings", status_code=201, operation_id="setEpisodeAssetState")
async def set_episode_asset_state(episode_id: str, payload: EpisodeAssetStateBindRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": _run_command(request, lambda service: service.set_episode_asset_state(episode_id=episode_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}/asset-state-bindings", status_code=201, operation_id="setShotAssetState")
async def set_shot_asset_state(shot_id: str, payload: ShotAssetStateBindRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": _run_command(request, lambda service: service.set_shot_asset_state(shot_id=shot_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


class CharacterVoiceCloneRequest(BaseModel):
    media_version_id: str
    title: str = ""
    transcript: str = ""
    consent: bool = False


@router.post("/story-assets/{asset_id}/voice-clone", status_code=201, operation_id="cloneCharacterVoice")
async def clone_character_voice(asset_id: str, payload: CharacterVoiceCloneRequest, request: Request) -> dict[str, object]:
    project_id = _asset_project(request, asset_id)
    try:
        result = VoiceCloneService(_database(request), request.app.state.settings).clone_character_voice(
            project_id,
            asset_id,
            media_version_id=payload.media_version_id,
            title=payload.title,
            transcript=payload.transcript,
            consent=payload.consent,
        )
        return {
            "voice": result["voice"],
            "binding": result["binding"],
            "reference_media_version_id": result["reference_media_version_id"],
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
