from __future__ import annotations

from fastapi import APIRouter, Body, Header, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.g3 import BreakdownRequest
from local_drama.api.schemas.llm import LLMProbeRequest, LLMProfilePublishRequest, LLMProfileSyncRequest, VideoPromptExpandRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.local_llm import LocalLLMService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import endpoint_is_remote
from local_drama.platform.contracts import SecretRef

router = APIRouter(tags=["local-llm"])


def service(request: Request) -> LocalLLMService:
    return LocalLLMService(
        request.app.state.database,
        request.app.state.settings,
        request.app.state.platform.secret_store,
    )


@router.get("/local-llm/status", operation_id="getLocalLLMStatus")
async def status(
    request: Request,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    live_probe: bool = True,
) -> dict[str, object]:
    try:
        return {"status": service(request).status(provider=provider, base_url=base_url, model=model, live_probe=live_probe)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/local-llm/models", operation_id="discoverOllamaModels", response_model=dict[str, object])
async def discover_ollama_models(request: Request, base_url: str | None = None) -> dict[str, object]:
    try:
        return {"catalog": service(request).discover_ollama_models(base_url=base_url)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/probe", operation_id="probeLocalLLM")
async def probe_llm(
    request: Request,
    payload: LLMProbeRequest = Body(...),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        from urllib.parse import urlparse
        resolved_provider = (payload.provider or request.app.state.settings.llm_provider or "LLAMA_CPP_MANAGED").strip().upper()
        resolved_base_url = (payload.base_url or request.app.state.settings.llm_base_url).strip()
        parsed = urlparse(resolved_base_url)
        is_remote = endpoint_is_remote(resolved_base_url)
        if resolved_provider == "OPENAI_COMPAT" and is_remote and not payload.allow_remote_outbound:
            raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "数据将离开本机：测试远程 LLM 连接需要显式确认出境安全许可")

        client = service(request).client(
            model=payload.model,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
            provider_connection_id=payload.provider_connection_id,
        )
        probe = client.probe(load_test=payload.load_test)
        if payload.remember_api_key:
            if resolved_provider != "OPENAI_COMPAT" or (parsed.hostname or "").casefold() != "api.deepseek.com":
                raise DomainRuleError(
                    "LLM_CREDENTIAL_STORE_PROVIDER_UNSUPPORTED",
                    "当前安全凭据槽仅用于 DeepSeek 官方 API",
                )
            if not payload.api_key or not payload.api_key.strip():
                raise DomainRuleError("LLM_API_KEY_REQUIRED", "安全保存 DeepSeek 凭据需要输入 API Key")
            if probe.get("status") != "PASS" or int(probe.get("probe_level_passed") or 0) < 4:
                raise DomainRuleError("LLM_CREDENTIAL_NOT_VERIFIED", "只有通过四级连接验证的 DeepSeek API Key 才能安全保存")
            try:
                request.app.state.platform.secret_store.put(SecretRef("DeepSeekAPI", "default"), payload.api_key)
            except (OSError, ValueError) as error:
                raise DomainRuleError(
                    "LLM_CREDENTIAL_STORE_FAILED",
                    "DeepSeek 验证通过，但无法安全保存到操作系统凭据库",
                ) from error
            probe["secret_persisted"] = True
            probe["credential_store"] = request.app.state.platform.secret_store.name
        return {"probe": probe}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/local-llm/probe:submit", status_code=202, operation_id="submitLocalLLMProbe")
async def submit_llm_probe(
    project_id: str,
    request: Request,
    payload: LLMProbeRequest = Body(...),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        return {"job": service(request).submit_probe(
            project_id,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            load_test=payload.load_test,
            allow_remote_outbound=payload.allow_remote_outbound,
            provider_connection_id=payload.provider_connection_id,
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/local-llm/probes/{job_id}", operation_id="getLocalLLMProbeResult")
async def get_llm_probe_result(job_id: str, request: Request) -> dict[str, object]:
    try:
        return service(request).probe_job_result(job_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/profile:sync", operation_id="syncLocalLLMProfile")
async def sync_profile(
    request: Request,
    payload: LLMProfileSyncRequest = Body(default_factory=LLMProfileSyncRequest),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        return {
            "profile": service(request).sync_candidate(
                model=payload.model,
                capability=payload.capability or "LLM_STORY_PARSE",
                provider=payload.provider,
                base_url=payload.base_url,
                api_key=payload.api_key,
                allow_remote_outbound=payload.allow_remote_outbound,
                probe_job_id=payload.probe_job_id,
                provider_connection_id=payload.provider_connection_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/profile:publish", operation_id="publishLocalLLMProfile")
async def publish_profile(
    request: Request,
    payload: LLMProfilePublishRequest = Body(...),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        return {
            "profile": service(request).publish(
                payload.profile_version_id,
                api_key=payload.api_key,
                allow_remote_outbound=payload.allow_remote_outbound,
                probe_job_id=payload.probe_job_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/local-llm/video-prompt:expand", operation_id="expandVideoPrompt")
async def expand_video_prompt(
    request: Request,
    payload: VideoPromptExpandRequest = Body(...),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        return {
            "plan": await run_in_threadpool(
                service(request).expand_video_prompt,
                payload.profile_version_id,
                payload.story,
                api_key=payload.api_key,
                remember_api_key=payload.remember_api_key,
                allow_remote_outbound=payload.allow_remote_outbound,
                language=payload.language,
                output_spec=payload.output_spec,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/import-sessions/{session_id}:breakdown-local-llm", status_code=202, operation_id="breakdownWithLocalLLM")
async def breakdown(
    session_id: str,
    payload: BreakdownRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return {
            "job": service(request).enqueue_breakdown(
                session_id,
                payload.profile_version_id,
                idempotency_key or "",
                target_episode_id=payload.episode_id,
            ),
            "automatic_apply": False,
            "requires_human_action": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/script-breakdown-drafts", operation_id="listScriptBreakdownDrafts")
async def list_breakdown_drafts(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_breakdown_drafts(project_id), "automatic_apply": False, "requires_human_action": True}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
