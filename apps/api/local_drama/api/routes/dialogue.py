from __future__ import annotations

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.dialogue import (
    CharacterVoiceBindRequest,
    DialogueLineRequest,
    DialogueTextRevisionRequest,
    EpisodeTTSBatchRequest,
    SapiTTSProfilePublishRequest,
    TTSCandidateRequest,
    TTSJobRequest,
    VoiceProfileRequest,
)
from local_drama.application.dialogue import DialogueService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["dialogue", "tts"])


def service(request: Request) -> DialogueService:
    return DialogueService(request.app.state.database, request.app.state.settings)


@router.post("/episodes/{episode_id}/dialogue-lines", status_code=201, operation_id="createDialogueLine")
async def create_dialogue_line(episode_id: str, payload: DialogueLineRequest, request: Request) -> dict[str, object]:
    try:
        return {"dialogue": service(request).create_line(episode_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/dialogue-lines", operation_id="listDialogueLines")
async def list_dialogue_lines(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_lines(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/dialogue-lines/{line_id}", operation_id="getDialogueLine")
async def get_dialogue_line(line_id: str, request: Request) -> dict[str, object]:
    try:
        return {"dialogue": service(request).get_line(line_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/dialogue-lines/{line_id}/text-revisions", status_code=201, operation_id="createDialogueTextRevision")
async def create_dialogue_text_revision(line_id: str, payload: DialogueTextRevisionRequest, request: Request) -> dict[str, object]:
    try:
        return {"dialogue": service(request).revise_text(line_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/voice-profile-versions", status_code=201, operation_id="createVoiceProfileVersion")
async def create_voice_profile(project_id: str, payload: VoiceProfileRequest, request: Request) -> dict[str, object]:
    try:
        return {"voice_profile": service(request).create_voice_profile(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/voice-profile-versions", operation_id="listVoiceProfileVersions")
async def list_voice_profiles(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_voice_profiles(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/tts/voices:discover", operation_id="discoverLocalSapiVoices")
async def discover_local_sapi_voices(request: Request) -> dict[str, object]:
    return service(request).discover_local_sapi_voices()


@router.post("/tts/sapi-profile:publish", status_code=201, operation_id="publishLocalSapiTTSProfile")
async def publish_local_sapi_tts_profile(payload: SapiTTSProfilePublishRequest, request: Request) -> dict[str, object]:
    try:
        return {"profile": service(request).publish_local_sapi_profile(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/dialogue-text-revisions/{text_revision_id}/tts-candidates", status_code=201, operation_id="registerTTSCandidate")
async def register_tts_candidate(text_revision_id: str, payload: TTSCandidateRequest, request: Request) -> dict[str, object]:
    try:
        return {"candidate": service(request).register_candidate(text_revision_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/tts-candidates/{candidate_id}:select", status_code=201, operation_id="selectTTSCandidate")
async def select_tts_candidate(candidate_id: str, request: Request) -> dict[str, object]:
    try:
        return {"selection": service(request).select_candidate(candidate_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/dialogue-text-revisions/{text_revision_id}/tts-jobs", status_code=201, operation_id="submitTTSJob")
async def submit_tts_job(
    text_revision_id: str,
    payload: TTSJobRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return {"job": service(request).submit_tts_job(text_revision_id, idempotency_key=idempotency_key, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/tts-jobs/{job_id}:finalize", status_code=201, operation_id="finalizeTTSJob")
async def finalize_tts_job(job_id: str, request: Request) -> dict[str, object]:
    try:
        return {"result": service(request).finalize_tts_job(job_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/character-voice-bindings", status_code=201, operation_id="bindCharacterVoice")
async def bind_character_voice(project_id: str, payload: CharacterVoiceBindRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": service(request).bind_character_voice(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/character-voice-bindings", operation_id="listCharacterVoiceBindings")
async def list_character_voice_bindings(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_character_voice_bindings(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/character-voice-bindings/{binding_id}", operation_id="unbindCharacterVoice")
async def unbind_character_voice(binding_id: str, request: Request) -> dict[str, object]:
    try:
        return {"result": service(request).unbind_character_voice(binding_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/dialogue-tts:batch", status_code=201, operation_id="submitEpisodeTTSBatch")
async def submit_episode_tts_batch(episode_id: str, payload: EpisodeTTSBatchRequest, request: Request) -> dict[str, object]:
    try:
        return {"batch": service(request).submit_episode_tts_batch(episode_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
