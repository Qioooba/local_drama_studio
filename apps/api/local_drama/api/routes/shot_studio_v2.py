from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import TypeAdapter

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
    ShotMarkReadyRequest,
    ShotStudioResponse,
    ShotWorkingAdoptionResponse,
)
from local_drama.application.dialogue import DialogueService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.shot_studio import ShotStudioQueryService
from local_drama.application.shot_studio_commands import ShotStudioCommandService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import (
    SqliteShotStudioCommandRepository,
)
from local_drama.infrastructure.database.shot_studio_repository import SqliteShotStudioReadRepository

router = APIRouter(tags=["shot-studio-v2"])
shot_generation_response_adapter = TypeAdapter(ShotGenerationResponse)


def service(request: Request) -> ShotStudioQueryService:
    return ShotStudioQueryService(SqliteShotStudioReadRepository(request.app.state.database))


def command_service(request: Request) -> ShotStudioCommandService:
    return ShotStudioCommandService(SqliteShotStudioCommandRepository(request.app.state.database))


def generation_service(request: Request) -> GenerationService:
    return GenerationService(request.app.state.database, request.app.state.settings)


def frame_bridge_service(request: Request) -> FrameBridgeCommandService:
    return FrameBridgeCommandService(request.app.state.database)


def dialogue_service(request: Request) -> DialogueService:
    return DialogueService(
        request.app.state.database,
        request.app.state.settings,
        jobs=JobService(request.app.state.database, request.app.state.settings),
        media=MediaService(request.app.state.database, request.app.state.settings),
    )


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
            payload.fields.model_dump(),
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
            draft=payload.draft.model_dump() if payload.draft is not None else None,
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
