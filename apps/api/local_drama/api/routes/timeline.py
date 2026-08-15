from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g8 import (
    AudioBindingRequest,
    DeliveryBuildRequest,
    DeliveryWithdrawRequest,
    EnhancementPlanRequest,
    EnhancementRunRequest,
    FrameAnchorRequest,
    PostProcessRecipeRequest,
    RenderEpisodeRequest,
    SubtitleRevisionRequest,
    TimelineRevisionRequest,
    TransitionConstraintRequest,
)
from local_drama.application.errors import api_error_from_domain
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


@router.post("/episodes/{episode_id}/timeline-revisions", status_code=201, operation_id="createTimelineRevision")
async def create_timeline_revision(episode_id: str, payload: TimelineRevisionRequest, request: Request) -> dict[str, object]:
    try:
        return {"timeline": service(request).create_timeline_revision(episode_id, [item.model_dump() for item in payload.items], payload.input_snapshot, status=payload.status)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/timeline-revisions/{timeline_revision_id}", operation_id="getTimelineRevision")
async def get_timeline_revision(timeline_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"timeline": service(request).get_timeline(timeline_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/timeline-revisions/{timeline_revision_id}:export", operation_id="exportTimelineRevision")
async def export_timeline_revision(timeline_revision_id: str, request: Request) -> dict[str, object]:
    try:
        return {"export": TimelineExportService(request.app.state.database, request.app.state.settings).export_revision(timeline_revision_id)}
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


@router.post("/episodes/{episode_id}/audio-bindings", status_code=201, operation_id="bindEpisodeAudio")
async def bind_episode_audio(episode_id: str, payload: AudioBindingRequest, request: Request) -> dict[str, object]:
    try:
        return {"audio_binding": service(request).bind_audio(episode_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/audio-bindings", operation_id="listEpisodeAudioBindings")
async def list_episode_audio(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_audio_bindings(episode_id)}
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
        return {"enhancement": service(request).run_enhancement(**payload.model_dump())}
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
        return {"render": service(request).render_episode(timeline_revision_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages", status_code=201, operation_id="buildDeliveryPackage")
async def build_delivery(payload: DeliveryBuildRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).build_delivery(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/delivery-packages/{package_id}:verify", operation_id="verifyDeliveryPackage")
async def verify_delivery(package_id: str, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).verify_delivery(package_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/delivery-packages/{package_id}:withdraw", operation_id="withdrawDeliveryPackage")
async def withdraw_delivery(package_id: str, payload: DeliveryWithdrawRequest, request: Request) -> dict[str, object]:
    try:
        return {"delivery": service(request).withdraw_delivery(package_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
