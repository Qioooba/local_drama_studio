from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, TypeAdapter

from local_drama.api.schemas.shot_studio import (
    AudioWorkingAdoptionCommand,
    AudioWorkingAdoptionResponse,
    DialogueTtsGenerationCommand,
    DialogueTtsGenerationResponse,
    FrameBridgeCurrentFrameCommand,
    FrameBridgeInheritCommand,
    FrameBridgeLockCommand,
    FrameBridgeSourceFrameCommand,
    FrameBridgeWriteResponse,
    ShotBaseGenerationPreflightRequest,
    ShotBaseGenerationSubmitRequest,
    ShotContinuityContextResponse,
    ShotDialogueDraftCommand,
    ShotDialogueDraftResponse,
    ShotDraftRequest,
    ShotDraftResponse,
    ShotGenerationIntentRequest,
    ShotGenerationIntentResponse,
    ShotGenerationPreflightResponse,
    ShotGenerationRequest,
    ShotGenerationResponse,
    ShotKeyframeBatchListResponse,
    ShotKeyframeBatchPlanRequest,
    ShotKeyframeBatchPlanResponse,
    ShotKeyframeBatchSubmitRequest,
    ShotKeyframeBatchSubmitResponse,
    ShotLipsyncFinalizeResponse,
    ShotLipsyncJobListResponse,
    ShotLipsyncJobResponse,
    ShotMarkReadyRequest,
    ShotStudioResponse,
    ShotWorkingAdoptionResponse,
    StoryboardGenerationBatchPlanRequest,
    StoryboardGenerationBatchPlanResponse,
    StoryboardGenerationBatchSubmitRequest,
    StoryboardGenerationBatchSubmitResponse,
)
from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.dialogue import DialogueService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.lipsync import LipsyncService
from local_drama.application.media import MediaService
from local_drama.application.shot_keyframe_generation import ShotKeyframeGenerationBatchService
from local_drama.application.shot_studio import ShotStudioQueryService
from local_drama.application.shot_studio_commands import ShotStudioCommandService
from local_drama.application.storyboard_generation_batches import StoryboardGenerationBatchService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import (
    SqliteShotStudioCommandRepository,
)
from local_drama.infrastructure.database.shot_studio_repository import SqliteShotStudioReadRepository
from local_drama.infrastructure.service_composition import build_shot_keyframe_batch

router = APIRouter(tags=["shot-studio-v2"])
shot_generation_response_adapter: TypeAdapter[ShotGenerationResponse] = TypeAdapter(ShotGenerationResponse)


class ShotLipsyncJobRequest(BaseModel):
    video_media_version_id: str
    audio_media_version_id: str
    idempotency_key: str



def service(request: Request) -> ShotStudioQueryService:
    return ShotStudioQueryService(SqliteShotStudioReadRepository(request.app.state.database))


def command_service(request: Request) -> ShotStudioCommandService:
    return ShotStudioCommandService(SqliteShotStudioCommandRepository(request.app.state.database))


def generation_service(request: Request) -> GenerationService:
    return GenerationService(request.app.state.database, request.app.state.settings)


def frame_bridge_service(request: Request) -> FrameBridgeCommandService:
    return FrameBridgeCommandService(request.app.state.database)


def lipsync_service(request: Request) -> LipsyncService:
    return LipsyncService(
        request.app.state.database,
        request.app.state.settings,
        jobs=JobService(request.app.state.database, request.app.state.settings),
        media=MediaService(request.app.state.database, request.app.state.settings),
    )


def dialogue_service(request: Request) -> DialogueService:
    return DialogueService(
        request.app.state.database,
        request.app.state.settings,
        jobs=JobService(request.app.state.database, request.app.state.settings),
        media=MediaService(request.app.state.database, request.app.state.settings),
    )


def storyboard_generation_batch_service(request: Request) -> StoryboardGenerationBatchService:
    return StoryboardGenerationBatchService(
        EpisodeWorkerActionService(request.app.state.database, request.app.state.settings),
        AutomationWorkflowService(request.app.state.database),
    )


def shot_keyframe_batch_service(request: Request) -> ShotKeyframeGenerationBatchService:
    return build_shot_keyframe_batch(request.app.state.database, request.app.state.settings)


@router.post(
    "/episodes/{episode_id}/shot-keyframe-batches:plan",
    operation_id="planShotKeyframeBatchV2",
    response_model=ShotKeyframeBatchPlanResponse,
)
async def plan_shot_keyframe_batch(episode_id: str, payload: ShotKeyframeBatchPlanRequest, request: Request) -> ShotKeyframeBatchPlanResponse:
    try:
        return ShotKeyframeBatchPlanResponse.model_validate({"plan": shot_keyframe_batch_service(request).plan(
            episode_id,
            targets=[item.model_dump() for item in payload.targets],
            frame_strategy=payload.frame_strategy,
            candidate_count=payload.candidate_count,
            profile_version_id=payload.profile_version_id,
            prompt_bundle=payload.prompt_bundle.model_dump(exclude_none=True) if payload.prompt_bundle is not None else None,
        )})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/shot-keyframe-batches:submit",
    status_code=201,
    operation_id="submitShotKeyframeBatchV2",
    response_model=ShotKeyframeBatchSubmitResponse,
)
async def submit_shot_keyframe_batch(episode_id: str, payload: ShotKeyframeBatchSubmitRequest, request: Request) -> ShotKeyframeBatchSubmitResponse:
    try:
        return ShotKeyframeBatchSubmitResponse.model_validate({"batch": shot_keyframe_batch_service(request).submit(
            episode_id,
            targets=[item.model_dump() for item in payload.targets],
            frame_strategy=payload.frame_strategy,
            candidate_count=payload.candidate_count,
            profile_version_id=payload.profile_version_id,
            prompt_bundle=payload.prompt_bundle.model_dump(exclude_none=True) if payload.prompt_bundle is not None else None,
            expected_plan_hash=payload.expected_plan_hash,
            idempotency_key=payload.idempotency_key,
        )})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/shot-keyframe-batches",
    operation_id="listShotKeyframeBatchesV2",
    response_model=ShotKeyframeBatchListResponse,
)
async def list_shot_keyframe_batches(episode_id: str, request: Request, limit: int = Query(default=10, ge=1, le=20)) -> ShotKeyframeBatchListResponse:
    try:
        return ShotKeyframeBatchListResponse.model_validate({"items": shot_keyframe_batch_service(request).list_batches(episode_id, limit=limit)})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/storyboard-generation-batches:plan",
    operation_id="planStoryboardGenerationBatchV2",
    response_model=StoryboardGenerationBatchPlanResponse,
)
async def plan_storyboard_generation_batch(
    episode_id: str,
    payload: StoryboardGenerationBatchPlanRequest,
    request: Request,
) -> StoryboardGenerationBatchPlanResponse:
    try:
        return StoryboardGenerationBatchPlanResponse.model_validate(
            {"plan": storyboard_generation_batch_service(request).plan(episode_id, [item.model_dump() for item in payload.targets])}
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/storyboard-generation-batches:submit",
    operation_id="submitStoryboardGenerationBatchV2",
    response_model=StoryboardGenerationBatchSubmitResponse,
    status_code=201,
)
async def submit_storyboard_generation_batch(
    episode_id: str,
    payload: StoryboardGenerationBatchSubmitRequest,
    request: Request,
) -> StoryboardGenerationBatchSubmitResponse:
    try:
        return StoryboardGenerationBatchSubmitResponse.model_validate(
            {"batch": storyboard_generation_batch_service(request).submit(
                episode_id,
                [item.model_dump() for item in payload.targets],
                expected_plan_hash=payload.expected_plan_hash,
                idempotency_key=payload.idempotency_key,
            )}
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/shots/{shot_id}/studio",
    operation_id="getShotStudioV2",
    response_model=ShotStudioResponse,
)
async def get_shot_studio(
    episode_id: str,
    shot_id: str,
    request: Request,
    nav_radius: int = Query(default=12, ge=2, le=25),
) -> ShotStudioResponse:
    try:
        return ShotStudioResponse.model_validate(service(request).studio(episode_id, shot_id, nav_radius))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/shots/{shot_id}/continuity-context",
    operation_id="getShotContinuityContextV2",
    response_model=ShotContinuityContextResponse,
)
async def get_shot_continuity_context_v2(
    shot_id: str,
    request: Request,
) -> ShotContinuityContextResponse:
    try:
        return ShotContinuityContextResponse.model_validate(
            {"continuity": service(request).continuity(shot_id)}
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/frame-bridges/{transition_id}:inherit",
    operation_id="inheritFrameBridgeV2",
    response_model=FrameBridgeWriteResponse,
)
async def inherit_frame_bridge_v2(
    transition_id: str,
    payload: FrameBridgeInheritCommand,
    request: Request,
) -> FrameBridgeWriteResponse:
    try:
        result = frame_bridge_service(request).inherit(transition_id, **payload.model_dump())
        return FrameBridgeWriteResponse.model_validate({"frame_bridge": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/frame-bridges/{transition_id}:set-current-frame",
    operation_id="setFrameBridgeCurrentFrameV2",
    response_model=FrameBridgeWriteResponse,
)
async def set_frame_bridge_current_frame_v2(
    transition_id: str,
    payload: FrameBridgeCurrentFrameCommand,
    request: Request,
) -> FrameBridgeWriteResponse:
    try:
        result = frame_bridge_service(request).set_current_frame(transition_id, **payload.model_dump())
        return FrameBridgeWriteResponse.model_validate({"frame_bridge": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/frame-bridges/{transition_id}:set-source-frame",
    operation_id="setFrameBridgeSourceFrameV2",
    response_model=FrameBridgeWriteResponse,
)
async def set_frame_bridge_source_frame_v2(
    transition_id: str,
    payload: FrameBridgeSourceFrameCommand,
    request: Request,
) -> FrameBridgeWriteResponse:
    try:
        result = frame_bridge_service(request).set_source_frame(transition_id, **payload.model_dump())
        return FrameBridgeWriteResponse.model_validate({"frame_bridge": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/frame-bridges/{transition_id}:set-lock",
    operation_id="setFrameBridgeLockV2",
    response_model=FrameBridgeWriteResponse,
)
async def set_frame_bridge_lock_v2(
    transition_id: str,
    payload: FrameBridgeLockCommand,
    request: Request,
) -> FrameBridgeWriteResponse:
    try:
        result = frame_bridge_service(request).set_locked(transition_id, **payload.model_dump())
        return FrameBridgeWriteResponse.model_validate({"frame_bridge": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put(
    "/shots/{shot_id}/dialogue-draft",
    operation_id="putShotDialogueDraftV2",
    response_model=ShotDialogueDraftResponse,
)
async def put_shot_dialogue_draft_v2(
    shot_id: str,
    payload: ShotDialogueDraftCommand,
    request: Request,
) -> ShotDialogueDraftResponse:
    try:
        result = dialogue_service(request).save_shot_dialogue_draft(shot_id, **payload.model_dump())
        return ShotDialogueDraftResponse.model_validate({"dialogue": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/dialogue-lines/{line_id}/tts-generations",
    operation_id="submitDialogueTtsGenerationV2",
    response_model=DialogueTtsGenerationResponse,
)
async def submit_dialogue_tts_generation_v2(
    line_id: str,
    payload: DialogueTtsGenerationCommand,
    request: Request,
) -> DialogueTtsGenerationResponse:
    try:
        result = dialogue_service(request).submit_line_tts_generation(line_id, **payload.model_dump())
        return DialogueTtsGenerationResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/audio-versions/{media_version_id}:adopt-working",
    operation_id="adoptDialogueWorkingAudioV2",
    response_model=AudioWorkingAdoptionResponse,
)
async def adopt_dialogue_working_audio_v2(
    media_version_id: str,
    payload: AudioWorkingAdoptionCommand,
    request: Request,
) -> AudioWorkingAdoptionResponse:
    try:
        result = dialogue_service(request).adopt_working_audio(media_version_id, **payload.model_dump())
        return AudioWorkingAdoptionResponse.model_validate({"adoption": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put(
    "/shots/{shot_id}/draft",
    operation_id="putShotDraftV2",
    response_model=ShotDraftResponse,
)
async def put_shot_draft(shot_id: str, payload: ShotDraftRequest, request: Request) -> ShotDraftResponse:
    try:
        result = command_service(request).save_draft(
            shot_id,
            payload.fields.model_dump(exclude_unset=True),
            freeze=payload.freeze,
            expected_revision_no=payload.expected_revision_no,
        )
        return ShotDraftResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/shots/{shot_id}:mark-ready",
    operation_id="markShotReadyV2",
    response_model=ShotDraftResponse,
)
async def mark_shot_ready(shot_id: str, payload: ShotMarkReadyRequest, request: Request) -> ShotDraftResponse:
    try:
        result = command_service(request).mark_ready(
            shot_id,
            draft=payload.draft.model_dump(exclude_unset=True) if payload.draft is not None else None,
            freeze=payload.freeze,
            expected_revision_no=payload.expected_revision_no,
        )
        return ShotDraftResponse.model_validate(result)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/media-versions/{media_version_id}:adopt",
    operation_id="adoptShotWorkingVersionV2",
    response_model=ShotWorkingAdoptionResponse,
)
async def adopt_shot_working_version(media_version_id: str, request: Request) -> ShotWorkingAdoptionResponse:
    try:
        return ShotWorkingAdoptionResponse.model_validate({"adoption": command_service(request).adopt_working_version(media_version_id)})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/shots/{shot_id}/generation-intents",
    operation_id="createShotGenerationIntentV2",
    response_model=ShotGenerationIntentResponse,
    status_code=201,
)
async def create_shot_generation_intent(
    shot_id: str,
    payload: ShotGenerationIntentRequest,
    request: Request,
) -> ShotGenerationIntentResponse:
    try:
        result = generation_service(request).create_shot_intent(
            shot_id,
            purpose=payload.purpose,
            creative_goal=payload.creative_goal,
            idempotency_key=payload.idempotency_key,
        )
        return ShotGenerationIntentResponse.model_validate({"intent": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/shots/{shot_id}/lipsync-jobs",
    operation_id="createShotLipsyncJob",
    response_model=ShotLipsyncJobResponse,
    status_code=201,
)
async def create_shot_lipsync_job(shot_id: str, payload: ShotLipsyncJobRequest, request: Request) -> ShotLipsyncJobResponse:
    try:
        job = lipsync_service(request).create_job(
            shot_id,
            video_media_version_id=payload.video_media_version_id,
            audio_media_version_id=payload.audio_media_version_id,
            idempotency_key=payload.idempotency_key,
        )
        return ShotLipsyncJobResponse.model_validate({"job": {"id": str(job["id"]), "state": str(job["state"])}})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/shots/{shot_id}/lipsync-jobs",
    operation_id="listShotLipsyncJobs",
    response_model=ShotLipsyncJobListResponse,
)
async def list_shot_lipsync_jobs(shot_id: str, request: Request, limit: int = Query(default=20, ge=1, le=50)) -> ShotLipsyncJobListResponse:
    try:
        return ShotLipsyncJobListResponse.model_validate(lipsync_service(request).list_shot_jobs(shot_id, limit=limit))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/lipsync-jobs/{job_id}:finalize",
    operation_id="finalizeLipsyncJob",
    response_model=ShotLipsyncFinalizeResponse,
)
async def finalize_lipsync_job(job_id: str, request: Request) -> ShotLipsyncFinalizeResponse:
    try:
        result = lipsync_service(request).finalize_job(job_id)
        return ShotLipsyncFinalizeResponse.model_validate({"media": result["media"], "idempotent_replay": result["idempotent_replay"]})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error



@router.post(
    "/shots/{shot_id}/generations:preflight",
    operation_id="preflightShotGenerationV2",
    response_model=ShotGenerationPreflightResponse,
)
async def preflight_shot_generation(
    shot_id: str,
    payload: ShotBaseGenerationPreflightRequest,
    request: Request,
) -> ShotGenerationPreflightResponse:
    try:
        result = generation_service(request).preflight_shot_base_variant(
            shot_id,
            payload.intent_id,
            payload.to_domain(),
            payload.expected_shot_revision,
            payload.stage_code,
        )
        return ShotGenerationPreflightResponse.model_validate({"preflight": result})
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/shots/{shot_id}/generations",
    operation_id="submitShotGenerationV2",
    response_model=ShotGenerationResponse,
    status_code=201,
)
async def submit_shot_generation(shot_id: str, payload: ShotGenerationRequest, request: Request) -> ShotGenerationResponse:
    try:
        if isinstance(payload, ShotBaseGenerationSubmitRequest):
            result = generation_service(request).submit_shot_base_variant(
                shot_id,
                payload.intent_id,
                payload.to_domain(),
                expected_shot_revision=payload.expected_shot_revision,
                stage_code=payload.stage_code,
                plan_hash=payload.plan_hash,
                idempotency_key=payload.idempotency_key,
            )
            return shot_generation_response_adapter.validate_python(
                {
                    "operation": "BASE",
                    "variant": {
                        "id": result["variant"]["id"],
                        "intent_id": result["variant"]["intent_id"],
                        "variant_no": result["variant"]["variant_no"],
                        "status": result["variant"]["status"],
                    },
                    "job": {"id": result["job"]["id"], "state": result["job"]["state"]},
                    "idempotent_replay": result["idempotent_replay"],
                }
            )
        result = generation_service(request).reroll_shot_variant(
            shot_id,
            payload.parent_variant_id,
            reason_code=payload.reason_code,
            reason_note=payload.reason_note,
            explicit_seed=payload.explicit_seed,
            profile_version_id=payload.profile_version_id,
            idempotency_key=payload.idempotency_key,
            stage_code=payload.stage_code,
        )
        return shot_generation_response_adapter.validate_python(
            {
                "operation": "REROLL",
                "variant": {
                    "id": result["variant"]["id"],
                    "intent_id": result["variant"]["intent_id"],
                    "variant_no": result["variant"]["variant_no"],
                    "status": result["variant"]["status"],
                },
                "job": {"id": result["job"]["id"], "state": result["job"]["state"]},
                "reroll": result["reroll"],
            }
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
