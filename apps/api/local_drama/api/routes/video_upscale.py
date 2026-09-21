from __future__ import annotations

import json
import mimetypes
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse

from local_drama.api.schemas.video_upscale import (
    DeliveryBuildBatchPlanRequest,
    DeliveryBuildBatchSubmitRequest,
    EpisodeDeliverySelectionCommitRequest,
    EpisodeDeliverySelectionPlanRequest,
    ProjectUpscaleSettingsUpdateRequest,
    UpscaleModelOptions,
    UpscalePipelineOptions,
    VideoUpscaleBatchControlRequest,
    VideoUpscaleBatchCreateRequest,
    VideoUpscaleCleanupCommitRequest,
    VideoUpscaleCleanupPlanRequest,
    VideoUpscalePlanCreateRequest,
    VideoUpscalePresetCreateRequest,
    VideoUpscalePresetVersionCreateRequest,
    VideoUpscalePreviewCreateRequest,
    VideoUpscaleResponse,
    VideoUpscaleSelectionResolveRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.video_upscale.batches import VideoUpscaleBatchService
from local_drama.application.video_upscale.cleanup import VideoUpscaleCleanupService
from local_drama.application.video_upscale.delivery_batches import VideoUpscaleDeliveryBatchService
from local_drama.application.video_upscale.delivery_versions import EpisodeDeliveryVersionService
from local_drama.application.video_upscale.plans import VideoUpscalePlanService
from local_drama.application.video_upscale.presets import VideoUpscalePresetService
from local_drama.application.video_upscale.sources import EpisodeDeliverySourceService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["video-upscale"])


def _presets(request: Request) -> VideoUpscalePresetService:
    return VideoUpscalePresetService(request.app.state.database)


def _sources(request: Request) -> EpisodeDeliverySourceService:
    return EpisodeDeliverySourceService(request.app.state.database)


def _plans(request: Request) -> VideoUpscalePlanService:
    return VideoUpscalePlanService(request.app.state.database, request.app.state.settings)


def _batches(request: Request) -> VideoUpscaleBatchService:
    return VideoUpscaleBatchService(request.app.state.database, request.app.state.settings)


def _delivery_versions(request: Request) -> EpisodeDeliveryVersionService:
    return EpisodeDeliveryVersionService(request.app.state.database)


def _delivery_batches(request: Request) -> VideoUpscaleDeliveryBatchService:
    return VideoUpscaleDeliveryBatchService(request.app.state.database, request.app.state.settings)


def _cleanup(request: Request) -> VideoUpscaleCleanupService:
    return VideoUpscaleCleanupService(request.app.state.database, request.app.state.settings)


@router.get(
    "/projects/{project_id}/delivery-episodes",
    operation_id="listProjectDeliveryEpisodes",
    response_model=VideoUpscaleResponse,
)
async def list_project_delivery_episodes(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=200),
    season_id: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    try:
        return _sources(request).list_episodes(
            project_id,
            limit=limit,
            cursor=cursor,
            search=search,
            season_id=season_id,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-selections:resolve",
    operation_id="resolveVideoUpscaleSelection",
    response_model=VideoUpscaleResponse,
)
async def resolve_video_upscale_selection(
    project_id: str,
    payload: VideoUpscaleSelectionResolveRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "selection": _sources(request).resolve_selection(
                project_id,
                mode=payload.mode,
                episode_ids=payload.episode_ids,
                search=payload.search,
                season_id=payload.season_id,
                source_policy=payload.source_policy,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-plans",
    operation_id="createVideoUpscalePlan",
    status_code=202,
    response_model=VideoUpscaleResponse,
)
async def create_video_upscale_plan(
    project_id: str,
    payload: VideoUpscalePlanCreateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return _plans(request).create(
            project_id,
            selection_hash=payload.selection_hash,
            episode_ids=payload.episode_ids,
            preset_version_id=payload.preset_version_id,
            execution_profile_version_id=payload.execution_profile_version_id,
            batch_pipeline_overrides=payload.batch_pipeline_overrides,
            batch_model_overrides=payload.batch_model_overrides,
            item_overrides=payload.item_overrides,
            existing_result_policy=payload.existing_result_policy,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/video-upscale-plans/{plan_id}",
    operation_id="getVideoUpscalePlan",
    response_model=VideoUpscaleResponse,
)
async def get_video_upscale_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"plan": _plans(request).get(plan_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-batches",
    operation_id="createVideoUpscaleBatch",
    status_code=202,
    response_model=VideoUpscaleResponse,
)
async def create_video_upscale_batch(
    project_id: str,
    payload: VideoUpscaleBatchCreateRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, Any]:
    try:
        return _batches(request).create(
            project_id,
            plan_id=payload.plan_id,
            plan_hash=payload.plan_hash,
            title=payload.title,
            acknowledged_warning_ids=payload.acknowledged_warning_ids,
            idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/video-upscale-batches",
    operation_id="listVideoUpscaleBatches",
    response_model=VideoUpscaleResponse,
)
async def list_video_upscale_batches(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return _batches(request).list(project_id, limit=limit, cursor=cursor)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/video-upscale-batches/{batch_id}",
    operation_id="getVideoUpscaleBatch",
    response_model=VideoUpscaleResponse,
)
async def get_video_upscale_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"batch": _batches(request).get(batch_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/video-upscale-batches/{batch_id}:control",
    operation_id="controlVideoUpscaleBatch",
    response_model=VideoUpscaleResponse,
)
async def control_video_upscale_batch(
    batch_id: str,
    payload: VideoUpscaleBatchControlRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return _batches(request).control(
            batch_id,
            action=payload.action,
            expected_revision=payload.expected_revision,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-cleanup:plan",
    operation_id="planVideoUpscaleCleanup",
    response_model=VideoUpscaleResponse,
)
async def plan_video_upscale_cleanup(
    project_id: str,
    payload: VideoUpscaleCleanupPlanRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {"plan": _cleanup(request).plan(project_id, retention_days=payload.retention_days)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-cleanup:commit",
    operation_id="commitVideoUpscaleCleanup",
    response_model=VideoUpscaleResponse,
)
async def commit_video_upscale_cleanup(
    project_id: str,
    payload: VideoUpscaleCleanupCommitRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "result": _cleanup(request).commit(
                project_id,
                retention_days=payload.retention_days,
                eligible_before=payload.eligible_before,
                plan_hash=payload.plan_hash,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/video-upscale-runs/{run_id}",
    operation_id="getVideoUpscaleRun",
    response_model=VideoUpscaleResponse,
)
async def get_video_upscale_run(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"run": _batches(request).get_run(run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/video-upscale-previews",
    operation_id="createVideoUpscalePreview",
    status_code=202,
    response_model=VideoUpscaleResponse,
)
async def create_video_upscale_preview(
    project_id: str,
    payload: VideoUpscalePreviewCreateRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, Any]:
    try:
        return _batches(request).create_preview(
            project_id,
            plan_id=payload.plan_id,
            plan_hash=payload.plan_hash,
            episode_id=payload.episode_id,
            start_ms=payload.start_ms,
            duration_ms=payload.duration_ms,
            acknowledged_warning_ids=payload.acknowledged_warning_ids,
            idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/video-upscale-previews/{run_id}/content", operation_id="getVideoUpscalePreviewContent")
async def get_video_upscale_preview_content(run_id: str, request: Request) -> FileResponse:
    try:
        path, sha256 = _batches(request).preview_content_path(run_id)
        headers = {"Cache-Control": "private, max-age=31536000, immutable"}
        if sha256:
            headers["ETag"] = f'"{sha256}"'
        return FileResponse(path, media_type="video/mp4", filename=path.name, headers=headers, content_disposition_type="inline")
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/video-upscale-previews/{run_id}/source-content", operation_id="getVideoUpscalePreviewSourceContent")
async def get_video_upscale_preview_source_content(run_id: str, request: Request) -> FileResponse:
    try:
        path, sha256 = _batches(request).preview_source_content_path(run_id)
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            filename=path.name,
            headers={"Cache-Control": "private, max-age=3600", "ETag": f'"{sha256}"'},
            content_disposition_type="inline",
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/video-upscale-presets",
    operation_id="listVideoUpscalePresets",
    response_model=VideoUpscaleResponse,
)
async def list_video_upscale_presets(request: Request, project_id: str | None = None) -> dict[str, Any]:
    try:
        return {"items": _presets(request).list_presets(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/video-upscale-presets",
    operation_id="createVideoUpscalePreset",
    status_code=201,
    response_model=VideoUpscaleResponse,
)
async def create_video_upscale_preset(
    payload: VideoUpscalePresetCreateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "preset": _presets(request).create_preset(
                payload.project_id,
                code=payload.code,
                title=payload.title,
                profile_version_id=payload.profile_version_id,
                pipeline_options=payload.pipeline_options.model_dump(mode="json"),
                model_options=payload.model_options.model_dump(mode="json"),
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/video-upscale-presets/{preset_id}/versions",
    operation_id="createVideoUpscalePresetVersion",
    status_code=201,
    response_model=VideoUpscaleResponse,
)
async def create_video_upscale_preset_version(
    preset_id: str,
    payload: VideoUpscalePresetVersionCreateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "preset": _presets(request).create_preset_version(
                preset_id,
                title=payload.title,
                profile_version_id=payload.profile_version_id,
                pipeline_options=payload.pipeline_options.model_dump(mode="json"),
                model_options=payload.model_options.model_dump(mode="json"),
                expected_current_version_id=payload.expected_current_version_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/delivery-versions",
    operation_id="listEpisodeDeliveryVersions",
    response_model=VideoUpscaleResponse,
)
async def list_episode_delivery_versions(episode_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"versions": _delivery_versions(request).list_versions(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/delivery-selections:plan",
    operation_id="planEpisodeDeliverySelections",
    response_model=VideoUpscaleResponse,
)
async def plan_episode_delivery_selections(
    project_id: str,
    payload: EpisodeDeliverySelectionPlanRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "plan": _delivery_versions(request).plan(
                project_id,
                [item.model_dump(mode="json") for item in payload.items],
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/delivery-selections:commit",
    operation_id="commitEpisodeDeliverySelections",
    response_model=VideoUpscaleResponse,
)
async def commit_episode_delivery_selections(
    project_id: str,
    payload: EpisodeDeliverySelectionCommitRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "commit": _delivery_versions(request).commit(
                project_id,
                [item.model_dump(mode="json") for item in payload.items],
                plan_hash=payload.plan_hash,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/delivery-build-batches:plan",
    operation_id="planVideoUpscaleDeliveryBuildBatch",
    response_model=VideoUpscaleResponse,
)
async def plan_video_upscale_delivery_build_batch(
    project_id: str,
    payload: DeliveryBuildBatchPlanRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "plan": _delivery_batches(request).plan(
                project_id,
                [item.model_dump(mode="json") for item in payload.items],
                brand_kit_id=payload.brand_kit_id,
                watermark_profile_id=payload.watermark_profile_id,
                compliance_policy_id=payload.compliance_policy_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/delivery-build-batches:submit",
    operation_id="submitVideoUpscaleDeliveryBuildBatch",
    status_code=202,
    response_model=VideoUpscaleResponse,
)
async def submit_video_upscale_delivery_build_batch(
    project_id: str,
    payload: DeliveryBuildBatchSubmitRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, Any]:
    try:
        return _delivery_batches(request).submit(
            project_id,
            [item.model_dump(mode="json") for item in payload.items],
            brand_kit_id=payload.brand_kit_id,
            watermark_profile_id=payload.watermark_profile_id,
            compliance_policy_id=payload.compliance_policy_id,
            plan_hash=payload.plan_hash,
            title=payload.title,
            idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/delivery-build-batches",
    operation_id="listVideoUpscaleDeliveryBuildBatches",
    response_model=VideoUpscaleResponse,
)
async def list_video_upscale_delivery_build_batches(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return _delivery_batches(request).list(project_id, limit=limit, cursor=cursor)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/delivery-build-batches/{batch_id}",
    operation_id="getVideoUpscaleDeliveryBuildBatch",
    response_model=VideoUpscaleResponse,
)
async def get_video_upscale_delivery_build_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"batch": _delivery_batches(request).get(batch_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/delivery-build-batches/{batch_id}:retry-failed",
    operation_id="retryFailedVideoUpscaleDeliveryBuildBatch",
    response_model=VideoUpscaleResponse,
)
async def retry_failed_video_upscale_delivery_build_batch(
    batch_id: str,
    payload: VideoUpscaleBatchControlRequest,
    request: Request,
) -> dict[str, Any]:
    if payload.action != "RETRY_FAILED":
        raise api_error_from_domain(DomainRuleError("DELIVERY_BATCH_CONTROL_INVALID", "批量交付只支持重试失败项"))
    try:
        return _delivery_batches(request).retry_failed(batch_id, expected_revision=payload.expected_revision)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/projects/{project_id}/video-upscale-settings",
    operation_id="getProjectVideoUpscaleSettings",
    response_model=VideoUpscaleResponse,
)
async def get_project_video_upscale_settings(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"settings": _presets(request).get_settings(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put(
    "/projects/{project_id}/video-upscale-settings",
    operation_id="updateProjectVideoUpscaleSettings",
    response_model=VideoUpscaleResponse,
)
async def update_project_video_upscale_settings(
    project_id: str,
    payload: ProjectUpscaleSettingsUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return {
            "settings": _presets(request).update_settings(
                project_id,
                preset_version_id=payload.preset_version_id,
                pipeline_overrides=payload.pipeline_overrides,
                model_overrides=payload.model_overrides,
                expected_revision=payload.expected_revision,
                actor="local-user",
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/video-upscale-options",
    operation_id="getVideoUpscaleOptions",
    response_model=VideoUpscaleResponse,
)
async def get_video_upscale_options(request: Request, project_id: str) -> dict[str, Any]:
    try:
        presets = _presets(request).list_presets(project_id)
        settings = _presets(request).get_settings(project_id)
        with request.app.state.database.connect() as connection:
            profiles = connection.execute(
                """SELECT version.id AS profile_version_id,profile.code,profile.title,version.version_no,
                publication.status AS publication_status,runtime.adapter_code,runtime.adapter_version,
                runtime.status AS runtime_status,version.payload_json,version.payload_hash
                FROM mp_execution_profile_versions version
                JOIN mp_execution_profiles profile ON profile.id=version.profile_id
                JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
                JOIN mp_runtime_installation_versions runtime ON runtime.id=version.runtime_installation_version_id
                WHERE capability.code='UPSCALE_VIDEO' AND publication.status='PUBLISHED'
                ORDER BY profile.title,version.version_no DESC"""
            ).fetchall()
        profile_items = []
        for row in profiles:
            item = dict(row)
            item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
            item["ready"] = item["runtime_status"] == "ACTIVE" and item["adapter_code"] == "ncnn.realesrgan.video.v1"
            if not item["ready"]:
                item["blocker"] = "UPSCALE_RUNTIME_NOT_READY"
            profile_items.append(item)
        return {
            "project_id": project_id,
            "settings": settings,
            "presets": presets,
            "profiles": profile_items,
            "pipeline_contract": UpscalePipelineOptions.model_json_schema(),
            "model_contract": UpscaleModelOptions.model_json_schema(),
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
