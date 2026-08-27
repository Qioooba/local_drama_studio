from __future__ import annotations

from collections.abc import Iterator
from email.utils import formatdate
from pathlib import Path

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from local_drama.api.routes.media import _range_headers
from local_drama.api.schemas.g8 import (
    ComposeSubmitRequest,
    DeliveryBuildRequest,
    DeliveryReviewRequest,
    DeliveryWithdrawRequest,
    EnhancementPlanRequest,
    EnhancementRunRequest,
    FrameAnchorRequest,
    PostProcessRecipeRequest,
    RenderEpisodeRequest,
    RenderSegmentedEpisodeRequest,
    SubtitleRevisionRequest,
    SubtitleStyleTemplateRequest,
    TimelineRefreshCommitRequest,
    TimelineRevisionRequest,
    TransitionConstraintRequest,
)
from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.compose import ComposeService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.subtitle_styles import SubtitleStyleTemplateService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_exports import TimelineExportService
from local_drama.application.timeline_status import TimelineStatusService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["timeline", "delivery"])


@router.get("/episodes/{episode_id}/timeline-status", operation_id="getEpisodeTimelineStatus")
async def get_episode_timeline_status(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"status": TimelineStatusService(request.app.state.database).inspect(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def service(request: Request) -> TimelineService:
    return TimelineService(request.app.state.database, request.app.state.settings)


@router.get("/background-operations/{job_id}", operation_id="getBackgroundOperation")
async def get_background_operation(job_id: str, request: Request) -> dict[str, object]:
    try:
        return BackgroundOperationService(request.app.state.database, request.app.state.settings).result(job_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _stream(path: Path, start: int, end: int) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


async def _render_content(render_id: str, request: Request, head: bool = False) -> Response:
    try:
        item, path = service(request).render_content_path(render_id)
        selected = _range_headers(request, path, etag=f'"{item["sha256"]}"')
        if isinstance(selected, Response):
            return selected
        start, end, status = selected
        mime = str(item["mime_type"] or "video/mp4")
        stat = path.stat()
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Type": mime,
            "ETag": f'"{item["sha256"]}"',
            "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{stat.st_size}"
        if head:
            return Response(status_code=status, headers=headers)
        return StreamingResponse(_stream(path, start, end), status_code=status, headers=headers, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episode-renders/{render_id}/content", operation_id="getEpisodeRenderContent")
async def get_episode_render_content(render_id: str, request: Request) -> Response:
    return await _render_content(render_id, request)


@router.head("/episode-renders/{render_id}/content", operation_id="headEpisodeRenderContent")
async def head_episode_render_content(render_id: str, request: Request) -> Response:
    return await _render_content(render_id, request, head=True)


@router.get("/episode-renders/{render_id}/thumbnail", operation_id="getEpisodeRenderThumbnail")
async def get_episode_render_thumbnail(render_id: str, request: Request, size: str = "medium", frame: str = "poster") -> FileResponse:
    try:
        path, mime = service(request).render_thumbnail(render_id, size=size, frame=frame)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/timeline-revisions", status_code=201, operation_id="createTimelineRevision")
async def create_timeline_revision(episode_id: str, payload: TimelineRevisionRequest, request: Request) -> dict[str, object]:
    try:
        return {"timeline": service(request).create_timeline_revision(episode_id, [item.model_dump() for item in payload.items], payload.input_snapshot, status=payload.status)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/timeline-refresh:plan", operation_id="planEpisodeTimelineRefresh")
async def plan_episode_timeline_refresh(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).plan_stale_timeline_refresh(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/timeline-refresh:commit", status_code=201, operation_id="commitEpisodeTimelineRefresh")
async def commit_episode_timeline_refresh(
    episode_id: str,
    payload: TimelineRefreshCommitRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return service(request).commit_stale_timeline_refresh(episode_id, payload.expected_plan_hash)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/timeline-revisions/{timeline_revision_id}", operation_id="getTimelineRevision")
async def get_timeline_revision(timeline_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"timeline": service(request).get_timeline(timeline_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}:export", operation_id="exportTimelineRevision")
async def export_timeline_revision(
    timeline_revision_id: str,
    request: Request,
    format: str = "standard",
    subtitle_revision_id: str | None = None,
) -> dict[str, object]:
    try:
        return {
            "export": TimelineExportService(request.app.state.database, request.app.state.settings).export_revision(
                timeline_revision_id, format=format, subtitle_revision_id=subtitle_revision_id
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/timeline-revisions/{timeline_revision_id}/export:download", operation_id="downloadTimelineExport")
async def download_timeline_export(timeline_revision_id: str, rel_path: str, request: Request) -> FileResponse:
    try:
        path = TimelineExportService(request.app.state.database, request.app.state.settings).download_archive(
            timeline_revision_id, rel_path
        )
        return FileResponse(path, media_type="application/zip", filename=path.name)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/subtitle-revisions", status_code=201, operation_id="createSubtitleRevision")
async def create_subtitle_revision(episode_id: str, payload: SubtitleRevisionRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "subtitle": service(request).create_subtitle_revision(
                episode_id,
                [cue.model_dump() for cue in payload.cues],
                format=payload.format,
                authority=payload.authority.model_dump(),
                style=payload.style,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/subtitle-draft-plan", operation_id="getEpisodeTTSSubtitleDraftPlan")
async def get_episode_tts_subtitle_draft_plan(
    episode_id: str,
    request: Request,
    source_document_version_id: str | None = None,
) -> dict[str, object]:
    try:
        return {
            "plan": service(request).plan_tts_subtitle_draft(
                episode_id,
                source_document_version_id=source_document_version_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/subtitle-revisions/{subtitle_revision_id}", operation_id="getSubtitleRevision")
async def get_subtitle_revision(subtitle_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"subtitle": service(request).get_subtitles(subtitle_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-versions/{media_version_id}:create-frame-anchor", status_code=201, operation_id="createFrameAnchor")
async def create_frame_anchor(media_version_id: str, payload: FrameAnchorRequest, request: Request) -> dict[str, object]:
    try:
        return {"frame_anchor": service(request).create_frame_anchor(media_version_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/frame-anchors/{anchor_id}", operation_id="getFrameAnchor")
async def get_frame_anchor(anchor_id: str, request: Request) -> dict[str, object]:
    try:
        return {"frame_anchor": service(request).get_frame_anchor(anchor_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shot-transitions", status_code=201, operation_id="createShotTransitionConstraint")
async def create_shot_transition(payload: TransitionConstraintRequest, request: Request) -> dict[str, object]:
    try:
        return {"constraint": service(request).create_transition_constraint(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shot-transitions/{constraint_id}:validate", operation_id="validateShotTransitionConstraint")
async def validate_shot_transition(constraint_id: str, request: Request) -> dict[str, object]:
    try:
        return {"validation": service(request).validate_transition_constraint(constraint_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/post-process-recipes", status_code=201, operation_id="createPostProcessRecipe")
async def create_post_process_recipe(payload: PostProcessRecipeRequest, request: Request) -> dict[str, object]:
    try:
        return {"recipe": service(request).create_recipe(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/post-process-recipes", operation_id="listPostProcessRecipes")
async def list_post_process_recipes(request: Request) -> dict[str, object]:
    return {"items": service(request).list_recipes()}


@router.get("/post-process-recipes/{recipe_id}", operation_id="getPostProcessRecipe")
async def get_post_process_recipe(recipe_id: str, request: Request) -> dict[str, object]:
    try:
        return {"recipe": service(request).get_recipe(recipe_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/post-process-recipes/{recipe_id}:publish", operation_id="publishPostProcessRecipe")
async def publish_post_process_recipe(recipe_id: str, request: Request) -> dict[str, object]:
    try:
        return {"recipe": service(request).publish_recipe(recipe_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/enhancement-runs:plan", operation_id="planEnhancementRun")
async def plan_enhancement(payload: EnhancementPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).plan_enhancement(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/enhancement-runs", status_code=201, operation_id="runEnhancement")
async def run_enhancement(payload: EnhancementRunRequest, request: Request) -> dict[str, object]:
    try:
        return {"enhancement": await run_in_threadpool(service(request).run_enhancement, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/enhancement-runs:submit", status_code=202, operation_id="submitEnhancementRun")
async def submit_enhancement(
    payload: EnhancementRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return BackgroundOperationService(request.app.state.database, request.app.state.settings).submit_enhancement(
            **payload.model_dump(), idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/enhancement-runs/{run_id}", operation_id="getEnhancementRun")
async def get_enhancement_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        return {"enhancement": service(request).get_enhancement_run(run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}:render", status_code=201, operation_id="renderEpisode")
async def render_episode(timeline_revision_id: str, request: Request, payload: RenderEpisodeRequest | None = None) -> dict[str, object]:
    try:
        revision_id = payload.timeline_revision_id if payload else timeline_revision_id
        if revision_id != timeline_revision_id:
            raise DomainRuleError("TIMELINE_REVISION_MISMATCH", "路径和请求体的时间线 revision 不一致")
        return {"render": await run_in_threadpool(service(request).render_episode, timeline_revision_id, force_rerender=payload.force_rerender if payload else False)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/timeline-revisions/{timeline_revision_id}/compose:preflight", operation_id="preflightEpisodeCompose")
async def preflight_episode_compose(timeline_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"preflight": ComposeService(request.app.state.database, request.app.state.settings).preflight(timeline_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}/compose:submit", status_code=202, operation_id="submitEpisodeCompose")
async def submit_episode_compose(
    timeline_revision_id: str, payload: ComposeSubmitRequest, request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return ComposeService(request.app.state.database, request.app.state.settings).submit(
            timeline_revision_id, force_rerender=payload.force_rerender, idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}:render-segmented", status_code=201, operation_id="renderSegmentedEpisode")
async def render_segmented_episode(timeline_revision_id: str, payload: RenderSegmentedEpisodeRequest, request: Request) -> dict[str, object]:
    try:
        return {"render": await run_in_threadpool(service(request).render_segmented_episode,
            timeline_revision_id, [segment.model_dump() for segment in payload.segments], force_rerender=payload.force_rerender,
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}/segmented-compose:submit", status_code=202, operation_id="submitSegmentedEpisodeCompose")
async def submit_segmented_episode_compose(
    timeline_revision_id: str,
    payload: RenderSegmentedEpisodeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return BackgroundOperationService(request.app.state.database, request.app.state.settings).submit_segmented_compose(
            timeline_revision_id,
            [segment.model_dump() for segment in payload.segments],
            force_rerender=payload.force_rerender,
            idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _style_templates(request: Request) -> SubtitleStyleTemplateService:
    return SubtitleStyleTemplateService(request.app.state.database)


@router.get("/projects/{project_id}/subtitle-style-templates", operation_id="listSubtitleStyleTemplates")
async def list_subtitle_style_templates(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": _style_templates(request).list_templates(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/subtitle-style-templates", status_code=201, operation_id="saveSubtitleStyleTemplate")
async def save_subtitle_style_template(project_id: str, payload: SubtitleStyleTemplateRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "template": _style_templates(request).save_template(
                project_id,
                payload.code,
                payload.title,
                payload.style,
                payload.change_note,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/subtitle-style-templates/{entry_id}", operation_id="getSubtitleStyleTemplate")
async def get_subtitle_style_template(entry_id: str, request: Request) -> dict[str, object]:
    try:
        return {"template": _style_templates(request).get_template(entry_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/subtitle-style-templates/{entry_id}", operation_id="deleteSubtitleStyleTemplate")
async def delete_subtitle_style_template(entry_id: str, request: Request) -> dict[str, object]:
    try:
        return {"deleted": _style_templates(request).delete_template(entry_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages", status_code=201, operation_id="buildDeliveryPackage")
async def build_delivery(payload: DeliveryBuildRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": await run_in_threadpool(service(request).build_delivery, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages:submit", status_code=202, operation_id="submitDeliveryPackageBuild")
async def submit_delivery(
    payload: DeliveryBuildRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return BackgroundOperationService(request.app.state.database, request.app.state.settings).submit_delivery(
            **payload.model_dump(), idempotency_key=idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/delivery-packages", operation_id="listEpisodeDeliveryPackages")
async def list_episode_delivery_packages(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_episode_deliveries(episode_id), "runtime_contacted": False, "network_contacted": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/delivery-packages/{package_id}:verify", operation_id="verifyDeliveryPackage")
async def verify_delivery(package_id: str, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).verify_delivery(package_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages/{package_id}:verify", operation_id="verifyDeliveryPackagePost")
async def verify_delivery_post(package_id: str, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).verify_delivery(package_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/delivery-packages/{package_id}/download", operation_id="downloadDeliveryPackage")
async def download_delivery(package_id: str, request: Request) -> FileResponse:
    try:
        path, filename = service(request).delivery_download_path(package_id)
        return FileResponse(path, media_type="application/octet-stream", filename=filename)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/delivery-packages/{package_id}/files", operation_id="listDeliveryPackageFiles")
async def list_delivery_package_files(package_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_delivery_files(package_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages/{package_id}:withdraw", operation_id="withdrawDeliveryPackage")
async def withdraw_delivery(package_id: str, payload: DeliveryWithdrawRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).withdraw_delivery(package_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages/{package_id}:review", operation_id="reviewDeliveryPackage")
async def review_delivery(package_id: str, payload: DeliveryReviewRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).review_delivery(package_id, payload.reviewer_type, payload.decision, payload.note)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/delivery-packages/{package_id}", operation_id="getDeliveryPackage")
async def get_delivery_package(package_id: str, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).get_delivery_package(package_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
