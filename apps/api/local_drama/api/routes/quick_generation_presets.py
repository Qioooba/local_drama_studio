from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.quick_generation_presets import (
    QuickGenerationPresetCreateRequest,
    QuickGenerationPresetDeleteResponse,
    QuickGenerationPresetListResponse,
    QuickGenerationPresetResponse,
    QuickGenerationPresetUpdateRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.profiles import ProfileService
from local_drama.application.quick_generation_presets import QuickGenerationPresetService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/quick-generation-presets", tags=["quick-generations"])


def service(request: Request) -> QuickGenerationPresetService:
    database = request.app.state.database
    settings = request.app.state.settings
    return QuickGenerationPresetService(database, ProfileService(database, settings.manifest_path))


@router.get("", operation_id="listQuickGenerationPresets", response_model=QuickGenerationPresetListResponse)
async def list_presets(request: Request, capability: str | None = None) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).list, capability)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("", status_code=201, operation_id="createQuickGenerationPreset", response_model=QuickGenerationPresetResponse)
async def create_preset(payload: QuickGenerationPresetCreateRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).create, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/{preset_id}", operation_id="updateQuickGenerationPreset", response_model=QuickGenerationPresetResponse)
async def update_preset(preset_id: str, payload: QuickGenerationPresetUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).update, preset_id, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/{preset_id}", operation_id="deleteQuickGenerationPreset", response_model=QuickGenerationPresetDeleteResponse)
async def delete_preset(preset_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_in_threadpool(service(request).delete, preset_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
