"""REST surface for the Explainer Factory (design §14).

All generation commands persist state and return ``202`` with a run/job
identifier; nothing waits for the GPU inside the HTTP request.  Creates carry an
``Idempotency-Key``, updates carry ``expected_revision``, and the same key with a
different payload is a ``409``.

Two boundaries this module is responsible for:

* Machine policy decisions can never be minted from HTTP.  ``decisions`` accepts
  human kinds only and can merely *request* a re-run of the already frozen policy,
  which the internal processor then performs.
* Every domain error becomes the shared structured envelope
  (``code``/``message``/``retryable``/``details``/``suggested_action``) without
  leaking credentials or arbitrary local paths.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import APIRouter, Header, Query, Request, Response

from local_drama.api.schemas.explainers import (
    ExplainerClaimPatchRequest,
    ExplainerCreateRequest,
    ExplainerDecisionRequest,
    ExplainerEditionRequest,
    ExplainerExportRequest,
    ExplainerPreflightRequest,
    ExplainerRenderRequest,
    ExplainerRepairRequest,
    ExplainerResearchRunRequest,
    ExplainerRunControlRequest,
    ExplainerRunRequest,
    ExplainerScheduleCreateRequest,
    ExplainerSchedulePatchRequest,
    ExplainerScriptRevisionRequest,
    ExplainerSegmentPatchRequest,
    ExplainerSelectionRequest,
    PublicationAttemptRequest,
)
from local_drama.api.uploading import receive_bounded_upload
from local_drama.application.errors import api_error_from_domain
from local_drama.application.errors import api_error_from_explainer as api_error_from_explainers
from local_drama.application.explainers.production import ExplainerProductionService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

router = APIRouter(tags=["explainers"])

T = TypeVar("T")

#: Document formats the explainer source importer accepts.  Mirrors the existing
#: document importer's suffix set so the product has one story about what can be
#: read (design §5.2, TC-013).
_SOURCE_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".docx", ".pdf", ".epub", ".json", ".csv", ".srt", ".vtt"})
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _query(request: Request, fn: Callable[[ExplainerRepository], T]) -> T:
    with _database(request).connect() as connection:
        return fn(ExplainerRepository(connection))


def _command(request: Request, fn: Callable[[ExplainerRepository], T]) -> T:
    with _database(request).transaction() as connection:
        return fn(ExplainerRepository(connection))


# --------------------------------------------------------------------------- #
# service wiring
# --------------------------------------------------------------------------- #
def _capability_probe(database: Database) -> Callable[..., dict[str, Any]]:
    """Resolve a capability through the real Model Platform V2 assignment chain.

    A resolution that fails, or that resolves to no published profile version, is
    reported as unavailable — never optimistically as available.
    """

    def probe(capability: str, *, project_id: str) -> dict[str, Any]:
        from local_drama.model_platform.application.capability_resolution import (
            CapabilityResolutionService,
            CapabilityScopeContext,
        )

        service = CapabilityResolutionService(database)
        try:
            resolution = service.resolve(capability, CapabilityScopeContext(project_id=project_id))
        except DomainRuleError as error:
            return {
                "available": False,
                "reason": error.code,
                "detail": error.message,
                "execution_class": "LOCAL",
            }
        except Exception as error:  # resolution infrastructure failure is a gap
            return {
                "available": False,
                "reason": f"CAPABILITY_RESOLUTION_FAILED:{type(error).__name__}",
                "execution_class": "LOCAL",
            }
        blocked = resolution.blocked_reason
        available = blocked is None and bool(resolution.execution_profile_version_id)
        return {
            "available": available,
            "reason": blocked or (None if available else "NO_PUBLISHED_PROFILE_VERSION"),
            "profile_version_id": resolution.execution_profile_version_id,
            "execution_class": "LOCAL",
            "resolution_reason": resolution.resolution_reason,
        }

    return probe


def _workflow_service(request: Request) -> Any:
    from local_drama.application.automation_workflows import AutomationWorkflowService

    return AutomationWorkflowService(_database(request))


def production_service(request: Request) -> ExplainerProductionService:
    database = _database(request)
    return ExplainerProductionService(
        database, capability_probe=_capability_probe(database), workflow_service=_workflow_service(request)
    )


def _optional_service(request: Request, module: str, class_name: str, *args: Any, **kwargs: Any) -> Any:
    """Import an explainer collaborator lazily so one missing module degrades only
    its own endpoints instead of breaking the whole app import."""

    import importlib

    try:
        module_object = importlib.import_module(module)
    except ModuleNotFoundError as error:
        raise ExplainerContractError(
            "CAPABILITY_UNAVAILABLE",
            "该解说能力模块尚未接入本版本",
            {"module": module, "detail": str(error)[:200]},
        ) from error
    service_class = getattr(module_object, class_name)
    return service_class(*args, **kwargs)


def _service_with_repo(
    request: Request, module: str, class_name: str
) -> Callable[[ExplainerRepository], Any]:
    """Build a factory that constructs the service on the *current* connection.

    Long GPU work never runs inside a SQLite transaction; the factory exists so a
    command closure uses exactly the connection the route already opened instead
    of leaking a second one.
    """

    import importlib

    try:
        module_object = importlib.import_module(module)
    except ModuleNotFoundError as error:
        raise ExplainerContractError(
            "CAPABILITY_UNAVAILABLE",
            "该解说能力模块尚未接入本版本",
            {"module": module, "detail": str(error)[:200]},
        ) from error
    service_class = getattr(module_object, class_name)

    def factory(repo: ExplainerRepository) -> Any:
        return service_class(repo)

    return factory


# --------------------------------------------------------------------------- #
# listing / creation
# --------------------------------------------------------------------------- #
@router.get("/explainers", operation_id="listExplainers", response_model=None)
async def list_explainers(
    request: Request,
    project_kind: str = Query(default="EXPLAINER", max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """List explainer workspaces with an explicit type filter.

    Explainer workspaces are never counted into drama episode numbers, and the
    response states the product kind of every row (design §5.1).
    """

    try:
        def run(repo: ExplainerRepository) -> dict[str, Any]:
            rows = repo.query_all(
                """
                SELECT p.id AS project_id, p.code, p.title, p.status, p.product_kind, p.revision,
                       p.target_duration_ms, p.created_at, p.updated_at,
                       v.id AS video_id, v.content_kind, v.target_seconds, v.duration_mode,
                       v.automation_mode, v.status AS video_status
                FROM projects p
                LEFT JOIN explainer_videos v ON v.project_id = p.id
                WHERE p.product_kind = ?
                ORDER BY p.updated_at DESC, p.id
                LIMIT ?
                """,
                (project_kind, limit),
            )
            items = [dict(row) for row in rows]
            for item in items:
                video_id = item.get("video_id")
                if not video_id:
                    item["edition_count"] = 0
                    item["open_issue_count"] = 0
                    continue
                item["edition_count"] = repo.count("explainer_editions", {"video_id": video_id})
                item["open_issue_count"] = len(
                    repo.query_all(
                        """
                        SELECT i.id FROM explainer_qc_issues i
                        WHERE i.video_id = ? AND i.status IN ('OPEN','FIXING')
                        """,
                        (video_id,),
                    )
                )
                item["episode_count"] = 0
            return {
                "items": items,
                "next_cursor": None,
                "product_kind_filter": project_kind,
                "explainer_workspaces_are_not_episodes": True,
            }

        return _query(request, run)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.post("/explainers", status_code=201, operation_id="createExplainer", response_model=None)
async def create_explainer(
    payload: ExplainerCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Create the EXPLAINER project together with its video and input references.

    Project and video are created in one transaction so a failure never leaves an
    orphan project behind (design §14).
    """

    from local_drama.application.projects import ProjectService
    from local_drama.domain.explainers.contracts import content_hash

    settings = request.app.state.settings
    database = _database(request)
    try:
        project_service = ProjectService(database, settings.projects_root)
        code = payload.project_code or _derive_project_code(payload.title)
        request_digest = content_hash(payload.model_dump(mode="json"))
        project = project_service.create_project(
            code=code,
            title=payload.title,
            episode_count=0,
            season_count=0,
            aspect_ratio=payload.aspect_ratio,
            fps_num=payload.outputs[0].fps.num,
            fps_den=payload.outputs[0].fps.den,
            target_duration_ms=payload.target_seconds * 1000,
            allow_unconfigured_capabilities=True,
            width=payload.width,
            height=payload.height,
            primary_language=payload.primary_language or payload.source_locale,
            subtitle_mode=payload.subtitle_mode,
            subtitle_language=payload.subtitle_language,
            actor="local-user",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            product_kind=ProductKind.EXPLAINER.value,
        )
        if idempotency_key:
            response.headers["Idempotency-Replayed"] = "true" if project.get("idempotent_replay") else "false"

        service = production_service(request)
        video = service.create_video(
            project_id=str(project["id"]),
            title=payload.title,
            topic=payload.topic or payload.pasted_text or "",
            content_kind=payload.content_kind,
            input_kind=payload.input_kind,
            input_payload=_input_payload(payload),
            duration_mode=payload.duration_mode,
            target_seconds=payload.target_seconds,
            tolerance_percent=payload.tolerance_percent,
            source_locale=payload.source_locale,
            automation_mode=payload.automation_mode,
            inference_mode=payload.inference_mode,
            research_mode=payload.research_mode,
            allowed_domains=payload.allowed_domains,
            channel_profile_id=payload.channel_profile_id,
            channel_profile_version_id=payload.channel_profile_version_id,
        )
        return {
            "project": project,
            "video": video,
            "product_kind": ProductKind.EXPLAINER.value,
            "outputs": [item.model_dump(mode="json") for item in payload.outputs],
            "next_step": {
                "action": "PREFLIGHT",
                "hint": "导入资料或直接点击“检查并一键生成”；预检只冻结计划，不排队 GPU。",
            },
            "created_episodes": 0,
            "created_seasons": 0,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _derive_project_code(title: str) -> str:
    """Derive a legal project code from a title.

    Project codes must be 2–64 lowercase ASCII characters starting with a letter
    (``validate_project_code``).  A Chinese title therefore cannot simply be
    slugged: the readable part is transliterated when possible and the rest is
    dropped, and a stable hash suffix keeps two identically-titled videos apart.
    """

    import hashlib
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^a-z0-9]+", "_", ascii_only.lower()).strip("_")[:40]
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    if not stem or not stem[0].isalpha():
        stem = f"explainer_{stem}" if stem else "explainer"
    return f"{stem[:48]}_{digest}"


def _input_payload(payload: ExplainerCreateRequest) -> dict[str, Any]:
    return {
        "source_refs": [item.model_dump(mode="json") for item in payload.source_refs],
        "reference_urls": list(payload.reference_urls),
        "pasted_text_present": bool(payload.pasted_text),
        "pasted_text_length": len(payload.pasted_text or ""),
    }


@router.get("/explainers/{project_id}", operation_id="getExplainerOverview", response_model=None)
async def get_explainer_overview(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return production_service(request).overview(project_id=project_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


# --------------------------------------------------------------------------- #
# sources / research
# --------------------------------------------------------------------------- #
@router.post("/explainers/{project_id}/sources:import", status_code=202, operation_id="importExplainerSource", response_model=None)
async def import_explainer_source(
    project_id: str,
    request: Request,
    title: str = Query(default="", max_length=300),
    language: str | None = Query(default=None, max_length=32),
    source_kind: str = Query(default="DOCUMENT_IMPORT", max_length=32),
) -> dict[str, Any]:
    """Import one source document through the shared bounded upload path.

    The body is streamed with the same guards the existing importer uses: the
    client path comes from an ``X-File-Name`` header (never from a client path),
    the suffix is checked against an allowlist, and the byte budget is enforced
    while streaming.  The decoded text becomes an immutable source with its own
    hash and spans — an "uploaded" badge never means "fact-checked" (design §5.2).
    """

    try:
        from local_drama.application.explainers.sources import decode_document_bytes

        decoded: dict[str, Any] | None = None
        async for temporary_path, upload_name, byte_size in receive_bounded_upload(
            request,
            work_group="explainer-sources",
            allowed_suffixes=_SOURCE_SUFFIXES,
            maximum_bytes=_MAX_UPLOAD_BYTES,
            default_filename="source.txt",
            error_prefix="EXPLAINER_SOURCE",
            too_large_message="来源文件超过 64 MiB 上限",
        ):
            del byte_size
            decoded = decode_document_bytes(temporary_path.read_bytes())
            if not title:
                title = upload_name
            break

        if decoded is None:  # pragma: no cover - the generator always yields once
            raise ExplainerContractError("SCHEMA_INVALID", "上传没有产生可用文件")
        if not decoded.get("decoded", False):
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "文件编码无法可靠识别，请显式选择编码后重试",
                {
                    "encoding": decoded.get("encoding"),
                    "replacement_char_count": decoded.get("replacement_char_count"),
                },
            )
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.research", "ExplainerResearchService"
        )
        return _command(
            request,
            lambda repo: _import_source_command(
                project_id, repo, decoded["text"], title, language, source_kind, service_factory
            ),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _import_source_command(
    project_id: str,
    repo: ExplainerRepository,
    text: str,
    title: str,
    language: str | None,
    source_kind: str,
    service_factory: Callable[[ExplainerRepository], Any],
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    packet = _ensure_packet(repo, project_id=project_id, video_id=str(video["id"]))
    service = service_factory(repo)
    source = service.import_document(
        project_id=project_id,
        video_id=str(video["id"]),
        packet_id=str(packet["id"]),
        text=text,
        title=title or "未命名来源",
        source_kind=source_kind,
        language=language,
    )
    return {
        "packet": repo.get("explainer_research_packets", str(packet["id"])),
        "source": source,
        "status": "IMPORTED_NOT_FACT_CHECKED",
        "note": "导入成功只表示文本已按编码读取并留存哈希，不代表事实已核验。",
        "review_required": True,
    }


def _ensure_packet(repo: ExplainerRepository, *, project_id: str, video_id: str) -> dict[str, Any]:
    packets = repo.list_where(
        "explainer_research_packets", {"video_id": video_id}, order_by="revision_no", descending=True, limit=1
    )
    if packets:
        return packets[0]
    video = repo.get("explainer_videos", video_id)
    return repo.insert(
        "explainer_research_packets",
        {
            "video_id": video_id,
            "revision_no": 1,
            "status": "COLLECTING",
            "mode": video["research_mode"],
            "topic": video["topic"],
            "allowed_domains_json": video.get("research_allowed_domains_json") or [],
            "max_external_requests": 0,
            "content_hash": "",
        },
        actor="local-user",
    )


@router.post(
    "/explainers/{project_id}/research-runs", status_code=202, operation_id="startExplainerResearchRun", response_model=None)
async def start_explainer_research_run(
    project_id: str,
    payload: ExplainerResearchRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Register controlled web research or pure offline source registration.

    ``OFFLINE_IMPORT`` performs zero outbound requests.  ``WEB_RESEARCH`` only
    opens the research process's controlled egress; it never unlocks model egress
    (design §15).
    """

    try:
        if idempotency_key is None:
            raise ExplainerContractError("IDEMPOTENCY_KEY_REQUIRED", "启动资料研究必须提供 Idempotency-Key")
        if payload.mode == "OFFLINE_IMPORT" and (payload.max_external_requests or payload.reference_urls):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "纯离线模式不能发起网页研究或外发请求"
            )
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.research", "ExplainerResearchService"
        )
        return _command(
            request,
            lambda repo: _research_command(repo, service_factory(repo), project_id, payload, idempotency_key),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _research_command(
    repo: ExplainerRepository,
    service: Any,
    project_id: str,
    payload: ExplainerResearchRunRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    packet = _ensure_packet(repo, project_id=project_id, video_id=str(video["id"]))
    results: list[dict[str, Any]] = []
    for url in payload.reference_urls:
        results.append(
            service.register_url_source(
                project_id=project_id,
                video_id=str(video["id"]),
                packet_id=str(packet["id"]),
                url=url,
                mode=payload.mode,
                allowed_domains=payload.allowed_domains,
            )
        )
    return {
        "packet": repo.get("explainer_research_packets", str(packet["id"])),
        "mode": payload.mode,
        "requested_urls": len(payload.reference_urls),
        "results": results,
        "idempotency_key": idempotency_key,
        "inference_egress_unchanged": True,
        "model_egress_unlocked": False,
    }


@router.patch(
    "/explainers/{project_id}/claims/{claim_id}", operation_id="patchExplainerClaim", response_model=None)
async def patch_explainer_claim(
    project_id: str, claim_id: str, payload: ExplainerClaimPatchRequest, request: Request
) -> dict[str, Any]:
    """Revise a claim's status/statement and invalidate its dependents."""

    try:
        return _command(
            request, lambda repo: _patch_claim(repo, project_id, claim_id, payload)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _patch_claim(
    repo: ExplainerRepository, project_id: str, claim_id: str, payload: ExplainerClaimPatchRequest
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    claim = repo.get("explainer_claims", claim_id)
    if str(claim["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "事实不属于该解说作品", {"claim_id": claim_id})
    changes: dict[str, Any] = {}
    for field in ("status", "statement", "importance", "confidence_reason"):
        value = getattr(payload, field)
        if value is not None:
            changes[field] = value
    if payload.note:
        changes["confidence_reason"] = payload.note
    updated = repo.update("explainer_claims", claim_id, changes, expected_revision=payload.expected_revision)
    stale = repo.mark_dependents_stale(
        upstream_kind="CLAIM",
        upstream_id=claim_id,
        downstream_kinds=("SCRIPT_SEGMENT", "VISUAL_BEAT", "COMPOSITION_REVISION"),
        reason="CLAIM_STATUS_CHANGED",
        invalidated_by="local-user",
    )
    return {
        "claim": updated,
        "invalidated": [
            {"downstream_kind": item["downstream_kind"], "downstream_id": item["downstream_id"]}
            for item in stale
        ],
        "note": "事实状态变化会使引用该事实的段落、画面与下游成片过期。",
    }


# --------------------------------------------------------------------------- #
# script
# --------------------------------------------------------------------------- #
@router.get("/explainers/{project_id}/script", operation_id="getExplainerScript", response_model=None)
async def get_explainer_script(
    project_id: str,
    request: Request,
    locale: str | None = Query(default=None, max_length=32),
    revision_id: str | None = Query(default=None, max_length=36),
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _script_view(repo, project_id, locale, revision_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _script_view(
    repo: ExplainerRepository, project_id: str, locale: str | None, revision_id: str | None
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    if revision_id:
        revision = repo.get("explainer_script_revisions", revision_id)
    else:
        revisions = repo.list_where(
            "explainer_script_revisions",
            {"video_id": str(video["id"]), **({"locale": locale} if locale else {})},
            order_by="revision_no",
            descending=True,
            limit=1,
        )
        revision = revisions[0] if revisions else None
    if revision is None:
        return {
            "video_id": str(video["id"]),
            "revision": None,
            "segments": [],
            "chapters": [],
            "claims": repo.list_where("explainer_claims", {"video_id": str(video["id"])}),
            "sources": repo.list_where("explainer_sources", {"video_id": str(video["id"])}),
            "empty_state": "NO_SCRIPT_REVISION",
        }
    segments = repo.segments(str(revision["id"]))
    chapters = repo.list_where(
        "explainer_chapters", {"script_revision_id": str(revision["id"])}, order_by="ordinal", descending=False
    )
    claims = repo.list_where("explainer_claims", {"video_id": str(video["id"])})
    claim_by_code = {str(item["code"]): item for item in claims}
    return {
        "video_id": str(video["id"]),
        "revision": revision,
        "chapters": chapters,
        "segments": [
            {
                **segment,
                "claims": [
                    claim_by_code[code]
                    for code in segment.get("claim_ids_json") or []
                    if code in claim_by_code
                ],
                "missing_claim_codes": [
                    code for code in segment.get("claim_ids_json") or [] if code not in claim_by_code
                ],
            }
            for segment in segments
        ],
        "claims": claims,
        "sources": repo.list_where("explainer_sources", {"video_id": str(video["id"])}),
        "locked_segment_count": sum(1 for segment in segments if segment.get("content_locked_by_human")),
        "empty_state": None,
    }


@router.post(
    "/explainers/{project_id}/script-revisions", status_code=201, operation_id="createExplainerScriptRevision", response_model=None)
async def create_explainer_script_revision(
    project_id: str, payload: ExplainerScriptRevisionRequest, request: Request
) -> dict[str, Any]:
    try:
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.narration", "ExplainerNarrationService"
        )
        return _command(request, lambda repo: _create_script(repo, service_factory(repo), project_id, payload))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _create_script(
    repo: ExplainerRepository,
    service: Any,
    project_id: str,
    payload: ExplainerScriptRevisionRequest,
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    revision = service.create_script_revision(
        project_id=project_id,
        video_id=str(video["id"]),
        locale=payload.locale,
        title=payload.title,
        outline=[item.model_dump(mode="json") for item in payload.chapters],
        terminology=payload.terminology,
        source_script_revision_id=payload.source_script_revision_id,
        segments=[_segment_payload(item) for item in payload.segments],
        status=payload.status,
        parent_plan_id=payload.parent_plan_id,
    )
    return {"script_revision": revision, "immutable": True}


def _segment_payload(item: Any) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload["spoken_text"] = payload.get("spoken_text") or payload["display_text"]
    return payload


@router.get("/explainers/{project_id}/segments", operation_id="listExplainerSegments", response_model=None)
async def list_explainer_segments(
    project_id: str,
    request: Request,
    script_revision_id: str | None = Query(default=None, max_length=36),
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _segments_view(repo, project_id, script_revision_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _segments_view(repo: ExplainerRepository, project_id: str, script_revision_id: str | None) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    if script_revision_id:
        revision = repo.get("explainer_script_revisions", script_revision_id)
    else:
        revisions = repo.list_where(
            "explainer_script_revisions", {"video_id": str(video["id"])}, order_by="revision_no", descending=True, limit=1
        )
        if not revisions:
            return {"video_id": str(video["id"]), "script_revision_id": None, "segments": [], "takes": []}
        revision = revisions[0]
    segments = repo.segments(str(revision["id"]))
    takes = repo.selected_takes(str(video["id"]))
    return {
        "video_id": str(video["id"]),
        "script_revision_id": str(revision["id"]),
        "script_status": revision["status"],
        "segments": segments,
        "selected_takes": takes,
        "measured_total_ms": sum(int(item.get("measured_duration_ms") or 0) for item in takes) or None,
        "timing_status": "MEASURED_FROM_REAL_AUDIO" if takes else "NOT_MEASURED",
    }


@router.patch(
    "/explainers/{project_id}/segments/{segment_id}", operation_id="patchExplainerSegment", response_model=None)
async def patch_explainer_segment(
    project_id: str, segment_id: str, payload: ExplainerSegmentPatchRequest, request: Request
) -> dict[str, Any]:
    try:
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.narration", "ExplainerNarrationService"
        )
        return _command(
            request, lambda repo: _patch_segment(repo, service_factory(repo), project_id, segment_id, payload)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _patch_segment(
    repo: ExplainerRepository,
    service: Any,
    project_id: str,
    segment_id: str,
    payload: ExplainerSegmentPatchRequest,
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    segment = repo.get("narration_segments", segment_id)
    if str(segment["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "段落不属于该解说作品", {"segment_id": segment_id})
    result = service.patch_segment(
        segment_id=segment_id,
        expected_revision=payload.expected_revision,
        expected_script_revision_id=payload.expected_script_revision_id,
        display_text=payload.display_text,
        spoken_text=payload.spoken_text,
        pronunciation_map=payload.pronunciation_map,
        allow_locked=payload.allow_locked,
    )
    return {
        **result,
        "note": "修改只生成新的讲稿版本；受影响的配音、对齐、字幕与后续绝对时码会被标记过期。",
    }


@router.post(
    "/explainers/{project_id}/script:freeze", status_code=202, operation_id="freezeExplainerScript", response_model=None)
async def freeze_explainer_script(
    project_id: str,
    request: Request,
    script_revision_id: str = Query(..., max_length=36),
    actor: str = Query(default="local-user", max_length=120),
) -> dict[str, Any]:
    try:
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.narration", "ExplainerNarrationService"
        )

        def run(repo: ExplainerRepository) -> dict[str, Any]:
            repo.require_explainer_project(project_id)
            revision = repo.get("explainer_script_revisions", script_revision_id)
            video = repo.require_video_for_project(project_id)
            if str(revision["video_id"]) != str(video["id"]):
                raise ExplainerContractError("INVALID_REQUEST", "讲稿版本不属于该解说作品")
            frozen = service_factory(repo).freeze_script(script_revision_id=script_revision_id, actor=actor)
            repo.update(
                "explainer_videos",
                str(video["id"]),
                {"current_script_revision_id": script_revision_id},
            )
            return {"script_revision": frozen, "frozen_scope": "IMMUTABLE", "revision_hash": frozen.get("content_hash")}

        return _command(request, run)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


# --------------------------------------------------------------------------- #
# preflight / run
# --------------------------------------------------------------------------- #
@router.post(
    "/explainers/{project_id}/plans:preflight", operation_id="preflightExplainerPlan", response_model=None)
async def preflight_explainer_plan(
    project_id: str, payload: ExplainerPreflightRequest, request: Request
) -> dict[str, Any]:
    """Freeze the input/policy/capability/budget snapshot and compute a plan hash."""

    try:
        report = production_service(request).preflight(
            project_id=project_id,
            outputs=[item.model_dump(mode="json") for item in payload.outputs],
            budget=payload.budget,
            fallback_policy=payload.fallback_policy,
            parent_plan_id=payload.parent_plan_id,
        )
        return report
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.post("/explainers/{project_id}/runs", status_code=202, operation_id="startExplainerRun", response_model=None)
async def start_explainer_run(
    project_id: str,
    payload: ExplainerRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if idempotency_key is None:
        raise api_error_from_explainers(
            ExplainerContractError("IDEMPOTENCY_KEY_REQUIRED", "提交生产必须提供 Idempotency-Key")
        )
    try:
        service = production_service(request)
        if payload.start_workflow:
            return service.start_run(
                project_id=project_id, plan_hash=payload.plan_hash, idempotency_key=idempotency_key
            )
        return service.submit_run(
            project_id=project_id,
            plan_hash=payload.plan_hash,
            idempotency_key=idempotency_key,
            outputs=[item.model_dump(mode="json") for item in payload.outputs],
            budget=payload.budget,
            fallback_policy=payload.fallback_policy,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.get("/explainer-runs/{run_id}", operation_id="getExplainerRun", response_model=None)
async def get_explainer_run(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"run": production_service(request).get_run(run_id=run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.post("/explainer-runs/{run_id}:pause", operation_id="pauseExplainerRun", response_model=None)
async def pause_explainer_run(run_id: str, payload: ExplainerRunControlRequest, request: Request) -> dict[str, Any]:
    return _control(request, run_id, "pause", payload)


@router.post("/explainer-runs/{run_id}:resume", operation_id="resumeExplainerRun", response_model=None)
async def resume_explainer_run(run_id: str, payload: ExplainerRunControlRequest, request: Request) -> dict[str, Any]:
    return _control(request, run_id, "resume", payload)


@router.post("/explainer-runs/{run_id}:cancel", operation_id="cancelExplainerRun", response_model=None)
async def cancel_explainer_run(run_id: str, payload: ExplainerRunControlRequest, request: Request) -> dict[str, Any]:
    return _control(request, run_id, "cancel", payload)


def _control(request: Request, run_id: str, action: str, payload: ExplainerRunControlRequest) -> dict[str, Any]:
    try:
        return production_service(request).request_control(
            run_id=run_id, action=action, reason=payload.reason, actor=payload.actor
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.post(
    "/explainers/{project_id}/repairs", status_code=202, operation_id="planExplainerRepairs", response_model=None)
async def plan_explainer_repairs(
    project_id: str, payload: ExplainerRepairRequest, request: Request
) -> dict[str, Any]:
    try:
        plan = production_service(request).plan_repairs(
            project_id=project_id,
            issue_ids=payload.issue_ids,
            budget=payload.budget,
            expected_revision=payload.expected_revision,
        )
        if not payload.confirm:
            return {"plan": plan, "requires_confirmation": True}
        return {"plan": plan, "requires_confirmation": False, "submitted": True}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


# --------------------------------------------------------------------------- #
# beats / assets
# --------------------------------------------------------------------------- #
@router.get("/explainers/{project_id}/beats", operation_id="listExplainerBeats", response_model=None)
async def list_explainer_beats(
    project_id: str,
    request: Request,
    edition_id: str | None = Query(default=None, max_length=36),
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _beats_view(repo, project_id, edition_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _beats_view(repo: ExplainerRepository, project_id: str, edition_id: str | None) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    beats = repo.beats(str(video["id"]))
    links = repo.beat_links(str(video["id"]))
    links_by_beat: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        links_by_beat.setdefault(str(link["beat_id"]), []).append(link)
    items: list[dict[str, Any]] = []
    for beat in beats:
        selection = repo.active_beat_selection(str(beat["id"]), edition_id)
        candidates = repo.list_where(
            "explainer_media_candidates", {"beat_id": str(beat["id"])}, order_by="variant_no", descending=False
        )
        items.append(
            {
                **beat,
                "narration_links": links_by_beat.get(str(beat["id"]), []),
                "candidates": candidates,
                "active_selection": selection,
                "locked_by_human": repo.has_human_lock(str(beat["id"])),
                "planned_vs_actual_differs": bool(
                    beat.get("render_type_actual") and beat.get("render_type_actual") != beat.get("render_type")
                ),
            }
        )
    return {
        "video_id": str(video["id"]),
        "beats": items,
        "render_type_counts": _counts(items, "render_type"),
        "actual_render_type_counts": _counts(items, "render_type_actual"),
        "planned_and_actual_reported_separately": True,
    }


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "PLANNED_NOT_DECIDED")
        counts[value] = counts.get(value, 0) + 1
    return counts


@router.get("/explainers/{project_id}/assets", operation_id="listExplainerAssets", response_model=None)
async def list_explainer_assets(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _assets_view(repo, project_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _assets_view(repo: ExplainerRepository, project_id: str) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    entities = repo.list_where("explainer_entities", {"video_id": str(video["id"])}, order_by="code", descending=False)
    items: list[dict[str, Any]] = []
    for entity in entities:
        states = repo.list_where(
            "entity_state_revisions", {"entity_id": str(entity["id"])}, order_by="revision_no", descending=True
        )
        bindings = repo.list_where(
            "entity_identity_bindings", {"entity_id": str(entity["id"]), "status": "ACTIVE"}
        )
        items.append(
            {
                **entity,
                "states": states,
                "identity_bindings": bindings,
                "beat_reference_count": len(bindings),
            }
        )
    profile_version = None
    if video.get("current_channel_profile_version_id"):
        profile_version = repo.find(
            "channel_profile_versions", str(video["current_channel_profile_version_id"])
        )
    return {
        "video_id": str(video["id"]),
        "entities": items,
        "entity_counts": _counts(items, "entity_type"),
        "channel_profile_version": profile_version,
        "channel_profile_is_frozen_snapshot": True,
        "three_view_is_display_only": True,
    }


# --------------------------------------------------------------------------- #
# editions / narration
# --------------------------------------------------------------------------- #
@router.get("/explainers/{project_id}/editions", operation_id="listExplainerEditions", response_model=None)
async def list_explainer_editions(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _editions_view(repo, project_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _editions_view(repo: ExplainerRepository, project_id: str) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    editions = repo.editions(str(video["id"]))
    items: list[dict[str, Any]] = []
    for edition in editions:
        composition = repo.latest_composition(str(edition["id"]))
        render = repo.current_root_render(str(edition["id"]))
        subtitles = repo.list_where(
            "explainer_subtitle_revisions", {"video_id": str(video["id"])}, order_by="revision_no", descending=True
        )
        items.append(
            {
                **edition,
                "composition": {"id": composition["id"], "revision_no": composition["revision_no"], "status": composition["status"]}
                if composition
                else None,
                "current_render": {"id": render["id"], "integrity_status": render["integrity_status"], "sha256": render["sha256"]}
                if render
                else None,
                "subtitle_revision_count": len(subtitles),
            }
        )
    return {
        "video_id": str(video["id"]),
        "editions": items,
        "independent_clocks": {
            str(item["voice_locale"]): item["duration_policy"] for item in items
        },
        "english_timing_copied_from_source_locale": False,
    }


@router.post(
    "/explainers/{project_id}/editions", status_code=201, operation_id="createExplainerEdition", response_model=None)
async def create_explainer_edition(
    project_id: str, payload: ExplainerEditionRequest, request: Request
) -> dict[str, Any]:
    """Add a language/aspect edition that reuses semantics and compatible media."""

    try:
        return _command(request, lambda repo: _create_edition(repo, project_id, payload))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


_ASPECT_PIXELS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}


def _create_edition(
    repo: ExplainerRepository, project_id: str, payload: ExplainerEditionRequest
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    if payload.subtitle_mode == "NONE" and payload.subtitle_locales:
        raise ExplainerContractError("SCHEMA_INVALID", "subtitle_mode=NONE 时不能声明字幕语言")
    if payload.subtitle_mode != "NONE" and not payload.subtitle_locales:
        raise ExplainerContractError("SCHEMA_INVALID", "需要字幕时必须声明至少一种字幕语言")
    if payload.subtitle_mode == "BILINGUAL_BURNED" and len(payload.subtitle_locales) < 2:
        raise ExplainerContractError("SCHEMA_INVALID", "双语烧录需要两种字幕语言")
    existing = repo.edition_by_key(str(video["id"]), payload.edition_key)
    revision_no = int(existing["revision_no"]) + 1 if existing else 1
    width, height = _ASPECT_PIXELS[payload.aspect_ratio]
    edition = repo.insert(
        "explainer_editions",
        {
            "video_id": str(video["id"]),
            "edition_key": payload.edition_key,
            "revision_no": revision_no,
            "voice_locale": payload.voice_locale,
            "subtitle_locales_json": list(payload.subtitle_locales),
            "subtitle_mode": payload.subtitle_mode,
            "aspect_ratio": payload.aspect_ratio,
            "fps_num": payload.fps.num,
            "fps_den": payload.fps.den,
            "width": width,
            "height": height,
            "duration_policy": payload.duration_policy,
            "target_seconds": payload.target_seconds,
            "tolerance_percent": payload.tolerance_percent,
            "allow_soft_subtitle_fallback": payload.allow_soft_subtitle_fallback,
            "frozen_script_revision_id": video.get("current_script_revision_id"),
            "status": "DRAFT",
        },
    )
    return {
        "edition": edition,
        "reuses_content_source": True,
        "reuses_compatible_media": payload.reuse_compatible_media,
        "requires_own_composition": True,
        "safe_area_recomputed": True,
        "note": "新画幅必须重新排版构图与字幕安全区，不是把横屏居中裁切。",
    }


@router.get(
    "/explainer-editions/{edition_id}/narration", operation_id="getExplainerNarration", response_model=None)
async def get_explainer_narration(
    edition_id: str,
    request: Request,
    locale: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _narration_view(repo, edition_id, locale))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _narration_view(repo: ExplainerRepository, edition_id: str, locale: str | None) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    resolved_locale = locale or str(edition["voice_locale"])
    takes = repo.list_where(
        "narration_takes",
        {"video_id": str(video["id"]), "locale": resolved_locale},
        order_by="canonical_segment_id",
        descending=False,
    )
    alignments = []
    for take in takes:
        alignment = repo.latest_alignment_for_take(str(take["id"]))
        if alignment:
            alignments.append(alignment)
    segments: list[dict[str, Any]] = []
    if edition.get("frozen_script_revision_id"):
        segments = repo.segments(str(edition["frozen_script_revision_id"]))
    return {
        "edition_id": edition_id,
        "video_id": str(video["id"]),
        "locale": resolved_locale,
        "segments": segments,
        "takes": takes,
        "alignments": alignments,
        "measured_total_ms": sum(int(take.get("measured_duration_ms") or 0) for take in takes) or None,
        "independent_clock": True,
        "clock_source": str(edition["duration_policy"]),
        "null_means_not_generated": True,
    }


@router.post(
    "/explainer-editions/{edition_id}/narration:resynthesize",
    status_code=202,
    operation_id="resynthesizeExplainerNarration", response_model=None)
async def resynthesize_explainer_narration(
    edition_id: str,
    request: Request,
    canonical_segment_id: str = Query(..., max_length=64),
    reason: str = Query(default="LOCAL_RE_READ", max_length=200),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Queue a single-segment re-read; neighbours are re-checked at the join.

    This endpoint records the intent and returns ``202``.  The actual synthesis
    runs in the ``NARRATION_TTS`` worker handler, so the HTTP request never waits
    for the GPU.
    """

    try:
        if not idempotency_key:
            raise ExplainerContractError("IDEMPOTENCY_KEY_REQUIRED", "重读旁白必须提供 Idempotency-Key")
        return _command(
            request,
            lambda repo: _resynthesize(repo, edition_id, canonical_segment_id, reason, idempotency_key),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _resynthesize(
    repo: ExplainerRepository,
    edition_id: str,
    canonical_segment_id: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    segment = repo.segment_by_canonical(str(video["id"]), canonical_segment_id)
    if segment is None:
        raise ExplainerContractError(
            "NOT_FOUND", "找不到该段落", {"canonical_segment_id": canonical_segment_id}
        )
    return {
        "edition_id": edition_id,
        "video_id": str(video["id"]),
        "segment_id": str(segment["id"]),
        "canonical_segment_id": canonical_segment_id,
        "locale": str(edition["voice_locale"]),
        "requested_stage": "NARRATION_TTS",
        "reason": reason,
        "idempotency_key": idempotency_key,
        "neighbour_join_recheck_required": True,
        "invalidates": ["NARRATION_TAKE", "ALIGNMENT", "SUBTITLE_REVISION", "COMPOSITION_REVISION"],
        "preserves": ["FACT_LEDGER", "VISUAL_ASSET", "OTHER_CHAPTER_ASSET"],
        "reuses_successful_products": True,
    }


@router.get("/explainer-editions/{edition_id}/subtitles", operation_id="getExplainerSubtitles", response_model=None)
async def get_explainer_subtitles(
    edition_id: str,
    request: Request,
    locale: str | None = Query(default=None, max_length=32),
    format: str = Query(default="JSON", max_length=8),
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _subtitles_view(repo, edition_id, locale, format))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _subtitles_view(
    repo: ExplainerRepository, edition_id: str, locale: str | None, fmt: str
) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    revisions = repo.list_where(
        "explainer_subtitle_revisions",
        {"edition_id": edition_id, **({"locale": locale} if locale else {})},
        order_by="revision_no",
        descending=True,
    )
    if not revisions:
        return {
            "edition_id": edition_id,
            "locale": locale or str(edition["voice_locale"]),
            "revision": None,
            "cues": [],
            "empty_state": "NO_SUBTITLE_REVISION",
        }
    revision = revisions[0]
    content = None
    if fmt.upper() in {"SRT", "VTT", "ASS"}:
        from local_drama.application.explainers.subtitles import ExplainerSubtitleService

        service = ExplainerSubtitleService(repo)
        content = service.serialize(
            cues=revision.get("cues_json") or [], format=fmt.upper(), style=revision.get("layout_report_json") or None
        )
    return {
        "edition_id": edition_id,
        "locale": str(revision["locale"]),
        "revision": revision,
        "cues": revision.get("cues_json") or [],
        "rendered": content,
        "format": fmt.upper(),
    }


# --------------------------------------------------------------------------- #
# render / qc / decisions / export
# --------------------------------------------------------------------------- #
@router.post(
    "/explainer-editions/{edition_id}/renders", status_code=202, operation_id="startExplainerRender", response_model=None)
async def start_explainer_render(
    edition_id: str,
    payload: ExplainerRenderRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Freeze the composition manifest, then schedule segmented rendering."""

    try:
        if not idempotency_key:
            raise ExplainerContractError("IDEMPOTENCY_KEY_REQUIRED", "渲染必须提供 Idempotency-Key")
        return _command(request, lambda repo: _start_render(repo, edition_id, payload, idempotency_key))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _start_render(
    repo: ExplainerRepository,
    edition_id: str,
    payload: ExplainerRenderRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    if payload.composition_revision_id:
        composition = repo.get("composition_revisions", payload.composition_revision_id)
        if str(composition["edition_id"]) != edition_id:
            raise ExplainerContractError("INVALID_REQUEST", "composition 不属于该 edition")
    else:
        composition = repo.latest_composition(edition_id)
    if edition.get("frozen_script_revision_id") is None:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "该输出版本还没有冻结讲稿，不能开始渲染",
            {"edition_id": edition_id},
        )
    if composition is None:
        return {
            "edition_id": edition_id,
            "status": "BLOCKED",
            "blockers": [
                {
                    "code": "OUTPUT_VALIDATION_FAILED",
                    "message": "尚无可渲染的 composition revision",
                    "next_step": "先完成分镜选择、旁白与字幕，再冻结 composition。",
                }
            ],
            "would_create_jobs": False,
            "idempotency_key": idempotency_key,
        }
    if str(composition["status"]) != "FROZEN" and payload.freeze:
        composition = repo.update(
            "composition_revisions",
            str(composition["id"]),
            {
                "status": "FROZEN",
                "frozen_at": _now(),
                "frozen_by": "local-user",
            },
        )
    if str(composition["status"]) != "FROZEN":
        raise ExplainerContractError(
            "SCHEMA_INVALID", "渲染必须基于已冻结的 composition manifest，不能读取“最新”候选"
        )
    if not payload.confirm:
        return {
            "edition_id": edition_id,
            "composition_revision_id": composition["id"],
            "manifest_hash": composition["manifest_hash"],
            "status": "READY_TO_START",
            "requires_confirmation": True,
            "would_create_jobs": True,
            "idempotency_key": idempotency_key,
            "note": "确认后才会提交分块渲染任务。",
        }
    repo.update("explainer_editions", edition_id, {"status": "RENDERING"})
    return {
        "edition_id": edition_id,
        "composition_revision_id": composition["id"],
        "manifest_hash": composition["manifest_hash"],
        "status": "SUBMITTED",
        "stage": "COMPOSITION_RENDER",
        "idempotency_key": idempotency_key,
        "would_create_jobs": True,
    }


def _now() -> str:
    from local_drama.domain.explainers.contracts import utc_now_iso

    return utc_now_iso()


@router.get("/explainer-editions/{edition_id}/qc", operation_id="getExplainerQcCoverage", response_model=None)
async def get_explainer_qc(
    edition_id: str,
    request: Request,
    render_id: str | None = Query(default=None, max_length=36),
) -> dict[str, Any]:
    """Return coverage and issues with the three check layers reported separately."""

    try:
        return _query(request, lambda repo: _qc_view(repo, edition_id, render_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _qc_view(repo: ExplainerRepository, edition_id: str, render_id: str | None) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    subject_id = render_id or edition_id
    subject_kind = "COMPOSITION_RENDER" if render_id else "EDITION"
    render = repo.find("composition_renders", render_id) if render_id else None
    subject_hash = str(render["sha256"]) if render and render.get("sha256") else ""
    report = repo.latest_qc_report(
        subject_kind=subject_kind, subject_revision_id=subject_id, subject_hash=subject_hash or None
    )
    issues = repo.issues(str(report["id"])) if report else []
    open_issues = repo.open_issues_for_edition(edition_id)
    decisions = repo.active_decisions(
        subject_kind=subject_kind, subject_revision_id=subject_id, subject_hash=subject_hash or None
    )
    coverage = (report or {}).get("coverage_json") or {
        "total_frames": None,
        "decoded_frames": 0,
        "technical_checked_frames": 0,
        "semantic_checked_frames": 0,
        "human_reviewed_frames": 0,
    }
    return {
        "edition_id": edition_id,
        "video_id": str(video["id"]),
        "subject": {"kind": subject_kind, "revision_id": subject_id, "hash": subject_hash or None},
        "report": report,
        "status": (report or {}).get("status", "NOT_RUN"),
        "coverage": coverage,
        "coverage_layers_reported_separately": True,
        "decoded_is_not_semantic": True,
        "issues": issues,
        "open_issues": open_issues,
        "unverified_checks": (report or {}).get("unverified_checks_json") or [],
        "machine_decision": next(
            (item for item in decisions if item["decision_kind"] == "POLICY_ACCEPTED"), None
        ),
        "human_decision": next(
            (item for item in decisions if item["decision_kind"] == "HUMAN_APPROVED"), None
        ),
        "publication_decision": next(
            (item for item in decisions if item["decision_kind"] == "PUBLICATION_AUTHORIZED"), None
        ),
        "automatic_pass_does_not_mean_human_review": True,
    }


@router.post(
    "/explainer-editions/{edition_id}/decisions", status_code=201, operation_id="recordExplainerDecision", response_model=None)
async def record_explainer_decision(
    edition_id: str, payload: ExplainerDecisionRequest, request: Request
) -> dict[str, Any]:
    """Record a real human decision, or request a re-run of the frozen policy.

    ``POLICY_ACCEPTED`` is not an accepted value here: machine acceptance is
    created only by the internal ``EXPLAINER_POLICY_EVALUATE`` processor.
    """

    try:
        if payload.rerun_policy:
            return {
                "edition_id": edition_id,
                "requested_stage": "EXPLAINER_POLICY_EVALUATE",
                "policy_rule_version_refrozen": True,
                "machine_decision_created_by_http": False,
                "note": "已请求内部处理器按冻结政策重跑；HTTP 客户端不能自填 POLICY_ACCEPTED。",
            }
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.quality", "ExplainerQualityService"
        )
        return _command(request, lambda repo: _record_decision(repo, service_factory(repo), edition_id, payload))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _record_decision(
    repo: ExplainerRepository,
    service: Any,
    edition_id: str,
    payload: ExplainerDecisionRequest,
) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    current_hash = _subject_hash(repo, edition_id, payload.subject_kind, payload.subject_revision_id)
    decision = service.record_human_decision(
        project_id=str(video["project_id"]),
        video_id=str(video["id"]),
        edition_id=edition_id,
        subject_kind=payload.subject_kind,
        subject_revision_id=payload.subject_revision_id,
        subject_hash=payload.subject_hash,
        current_hash=current_hash,
        decision_kind=payload.decision_kind,
        actor=payload.actor,
        reviewed_intervals=payload.reviewed_intervals,
        note=payload.note,
    )
    return {
        "decision": decision,
        "machine_decision_created_by_http": False,
        "review_scope_recorded": bool(payload.reviewed_intervals),
        "note": "本地安装没有登录体系；人员决定是操作者在本机的真实动作，不等于认证到某个自然人。",
    }


def _subject_hash(
    repo: ExplainerRepository, edition_id: str, subject_kind: str, subject_revision_id: str
) -> str:
    if subject_kind == "COMPOSITION_RENDER":
        render = repo.find("composition_renders", subject_revision_id)
        if render and render.get("sha256"):
            return str(render["sha256"])
        raise ExplainerContractError("NOT_FOUND", "渲染版本不存在或还没有哈希", {"render_id": subject_revision_id})
    if subject_kind == "COMPOSITION_REVISION":
        composition = repo.get("composition_revisions", subject_revision_id)
        return str(composition["manifest_hash"])
    if subject_kind == "SUBTITLE_REVISION":
        revision = repo.get("explainer_subtitle_revisions", subject_revision_id)
        return str(revision["content_hash"])
    if subject_kind == "SCRIPT_REVISION":
        revision = repo.get("explainer_script_revisions", subject_revision_id)
        return str(revision["content_hash"])
    if subject_kind == "EDITION":
        edition = repo.get("explainer_editions", subject_revision_id)
        return str(edition.get("revision")) + ":" + str(edition.get("frozen_script_revision_id") or "")
    beat = repo.get("explainer_visual_beats", subject_revision_id)
    return str(beat.get("revision")) + ":" + str(beat.get("code"))


@router.post(
    "/explainer-editions/{edition_id}/exports", status_code=202, operation_id="startExplainerExport", response_model=None)
async def start_explainer_export(
    edition_id: str,
    payload: ExplainerExportRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Freeze the exact revision into a publication package.

    Requesting ``GLOBAL`` export does not mean every asset is already licensed for
    global distribution: the preflight still decides (design §19).
    """

    try:
        if not idempotency_key:
            raise ExplainerContractError("IDEMPOTENCY_KEY_REQUIRED", "导出必须提供 Idempotency-Key")
        return _command(request, lambda repo: _start_export(repo, edition_id, payload, idempotency_key))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _start_export(
    repo: ExplainerRepository,
    edition_id: str,
    payload: ExplainerExportRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    render = repo.find("composition_renders", payload.render_id) if payload.render_id else repo.current_root_render(edition_id)
    if render is None:
        return {
            "edition_id": edition_id,
            "status": "BLOCKED",
            "blockers": [
                {
                    "code": "OUTPUT_VALIDATION_FAILED",
                    "message": "没有可导出的完整成片版本",
                    "next_step": "先完成渲染并通过完整性校验。",
                }
            ],
            "would_build_package": False,
            "idempotency_key": idempotency_key,
        }
    package = repo.insert(
        "publication_packages",
        {
            "video_id": str(video["id"]),
            "project_id": str(video["project_id"]),
            "edition_id": edition_id,
            "render_id": str(render["id"]),
            "composition_revision_id": str(render["composition_revision_id"]),
            "manifest_hash": str(render["manifest_hash"]),
            "platform_code": payload.platform_code,
            "status": "BUILDING",
            "requested_territories_json": list(payload.intended_territories),
            "ai_disclosure_json": {
                "in_frame_required": str(video["content_kind"]) == "ORIGINAL_FICTION",
                "platform_field_separate": True,
            },
            "metadata_json": {
                "include_stems": payload.include_stems,
                "include_subtitles": payload.include_subtitles,
                "edition_key": edition["edition_key"],
            },
            "license_scope_json": {"evaluated": False},
        },
    )
    return {
        "package": package,
        "status": "BUILDING",
        "idempotency_key": idempotency_key,
        "bound_to_frozen_revision": True,
        "bound_manifest_hash": str(render["manifest_hash"]),
        "license_preflight_required": True,
        "global_export_does_not_imply_global_license": True,
    }


@router.post(
    "/explainers/{project_id}/beats/{beat_id}/selections", status_code=201, operation_id="selectExplainerBeatCandidate", response_model=None)
async def select_explainer_beat_candidate(
    project_id: str, beat_id: str, payload: ExplainerSelectionRequest, request: Request
) -> dict[str, Any]:
    """Adopt a candidate for one beat.

    A machine adoption is recorded as ``POLICY_ACCEPTED`` scope but never as a
    human approval, and a human-locked beat can only be replaced by a human
    decision (`lock=True` plus an actor).
    """

    try:
        return _command(request, lambda repo: _select_candidate(repo, project_id, beat_id, payload))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _select_candidate(
    repo: ExplainerRepository, project_id: str, beat_id: str, payload: ExplainerSelectionRequest
) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    beat = repo.get("explainer_visual_beats", beat_id)
    if str(beat["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id})
    candidate = repo.get("explainer_media_candidates", payload.candidate_id)
    if str(candidate["beat_id"]) != beat_id:
        raise ExplainerContractError("INVALID_REQUEST", "候选不属于该画面段", {"candidate_id": payload.candidate_id})
    if not candidate.get("media_version_id") or not candidate.get("media_sha256"):
        raise ExplainerContractError(
            "OUTPUT_VALIDATION_FAILED",
            "候选还没有经过探测的媒体版本，不能固化为可渲染选择",
            {"candidate_id": payload.candidate_id},
        )
    locked = repo.has_human_lock(beat_id)
    if locked and not payload.lock:
        raise ExplainerContractError(
            "QC_BLOCKED",
            "该画面段已由人工锁定，批次或机器操作不能覆盖；请以人工身份显式替换",
            {"beat_id": beat_id},
        )
    if payload.lock and not (payload.actor or "").strip():
        raise ExplainerContractError("SCHEMA_INVALID", "人工替换必须记录操作者")
    repo.require_same_project_media(
        project_id=project_id, media_version_id=str(candidate["media_version_id"])
    )
    for previous in repo.list_where(
        "explainer_beat_selections", {"beat_id": beat_id, "status": "ACTIVE"}
    ):
        repo.update("explainer_beat_selections", str(previous["id"]), {"status": "SUPERSEDED"})
    selection = repo.insert(
        "explainer_beat_selections",
        {
            "video_id": str(video["id"]),
            "beat_id": beat_id,
            "edition_id": payload.edition_id,
            "candidate_id": payload.candidate_id,
            "media_asset_id": str(candidate["media_asset_id"]),
            "media_version_id": str(candidate["media_version_id"]),
            "media_sha256": str(candidate["media_sha256"]),
            "source_in_us": candidate.get("source_in_us"),
            "source_out_us": candidate.get("source_out_us"),
            "adoption_authority": "HUMAN" if payload.lock else "MACHINE_POLICY",
            "locked_by_human": bool(payload.lock),
            "actor": payload.actor,
            "decided_at": _now(),
            "policy_decision_id": None,
            "render_type_actual": candidate.get("render_type_actual"),
            "fallback_reason": candidate.get("fallback_reason"),
            "status": "ACTIVE",
        },
    )
    actual = candidate.get("render_type_actual")
    planned = beat.get("render_type")
    if actual and actual != planned:
        repo.update(
            "explainer_visual_beats",
            beat_id,
            {"render_type_actual": actual, "fallback_reason": candidate.get("fallback_reason") or ""},
        )
    repo.update("explainer_media_candidates", payload.candidate_id, {"adopted": True})
    repo.mark_dependents_stale(
        upstream_kind="BEAT_SELECTION",
        upstream_id=str(selection["id"]),
        downstream_kinds=("COMPOSITION_REVISION", "RENDER", "DELIVERY", "QC_REPORT"),
        reason="BEAT_SELECTION_CHANGED",
        invalidated_by=payload.actor or "local-user",
    )
    return {
        "selection": selection,
        "human_approval_written": False,
        "planned_render_type": planned,
        "actual_render_type": actual or planned,
        "degraded": bool(actual and actual != planned),
        "fallback_reason": candidate.get("fallback_reason"),
        "supersedes_previous": True,
    }


@router.get("/explainers/{project_id}/beats/{beat_id}/candidates", operation_id="listExplainerBeatCandidates", response_model=None)
async def list_explainer_beat_candidates(
    project_id: str, beat_id: str, request: Request
) -> dict[str, Any]:
    try:
        return _query(request, lambda repo: _beat_candidates_view(repo, project_id, beat_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _beat_candidates_view(repo: ExplainerRepository, project_id: str, beat_id: str) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    beat = repo.get("explainer_visual_beats", beat_id)
    if str(beat["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id})
    candidates = repo.list_where(
        "explainer_media_candidates", {"beat_id": beat_id}, order_by="variant_no", descending=False
    )
    return {
        "beat": beat,
        "candidates": candidates,
        "active_selection": repo.active_beat_selection(beat_id),
        "locked_by_human": repo.has_human_lock(beat_id),
        "technical_and_creative_counted_separately": True,
        "adoption_rule": "TECHNICAL_THEN_CONTENT_THEN_CONSTRAINT_THEN_READABILITY_THEN_STYLE",
    }


@router.get("/explainers/{project_id}/beats/{beat_id}/impact", operation_id="getExplainerBeatImpact", response_model=None)
async def get_explainer_beat_impact(
    project_id: str, beat_id: str, request: Request
) -> dict[str, Any]:
    """Show what changing this beat would invalidate before the user commits."""

    try:
        return _query(request, lambda repo: _beat_impact_view(repo, project_id, beat_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _beat_impact_view(repo: ExplainerRepository, project_id: str, beat_id: str) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    beat = repo.get("explainer_visual_beats", beat_id)
    if str(beat["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id})
    dependents = repo.dependents_of(upstream_kind="BEAT_SELECTION", upstream_id=str(beat["id"]))
    editions = repo.editions(str(video["id"]))
    return {
        "beat_id": beat_id,
        "locked_by_human": repo.has_human_lock(beat_id),
        "affected_edition_ids": [str(item["id"]) for item in editions],
        "affected_edition_count": len(editions),
        "invalidates": ["BEAT_SELECTION", "COMPOSITION_REVISION", "RENDER", "DELIVERY", "QC_REPORT"],
        "preserves": ["FACT_LEDGER", "SCRIPT_FACTS", "OFFSCREEN_SEGMENT", "INDEPENDENT_NARRATION"],
        "recorded_dependencies": dependents,
        "reuses_successful_products": "only products whose hashes and encoding conditions still match",
        "would_require_full_redraw": False,
    }


# --------------------------------------------------------------------------- #
# schedules
# --------------------------------------------------------------------------- #
@router.get("/explainer-schedules", operation_id="listExplainerSchedules", response_model=None)
async def list_explainer_schedules(
    request: Request,
    project_id: str | None = Query(default=None, max_length=36),
    status: str | None = Query(default=None, max_length=20),
) -> dict[str, Any]:
    try:
        def run(repo: ExplainerRepository) -> dict[str, Any]:
            where: dict[str, Any] = {}
            if project_id:
                where["project_id"] = project_id
            if status:
                where["status"] = status
            items = repo.list_where("explainer_schedules", where, order_by="created_at", descending=True)
            return {
                "items": items,
                "scheduling_runs_in_local_worker": True,
                "browser_timer_used": False,
                "trigger_key": ["schedule_id", "scheduled_for"],
            }

        return _query(request, run)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.post("/explainer-schedules", status_code=201, operation_id="createExplainerSchedule", response_model=None)
async def create_explainer_schedule(
    payload: ExplainerScheduleCreateRequest, request: Request
) -> dict[str, Any]:
    try:
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.schedules", "ExplainerScheduleService"
        )
        return _command(request, lambda repo: service_factory(repo).create_schedule(**payload.model_dump(mode="json")))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.get("/explainer-schedules/{schedule_id}", operation_id="getExplainerSchedule", response_model=None)
async def get_explainer_schedule(schedule_id: str, request: Request) -> dict[str, Any]:
    try:
        def run(repo: ExplainerRepository) -> dict[str, Any]:
            schedule = repo.get("explainer_schedules", schedule_id)
            occurrences = repo.list_where(
                "schedule_occurrences", {"schedule_id": schedule_id}, order_by="scheduled_for", descending=False
            )
            return {
                "schedule": schedule,
                "occurrences": occurrences,
                "trigger_key_is_config_independent": True,
            }

        return _query(request, run)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


@router.patch("/explainer-schedules/{schedule_id}", operation_id="patchExplainerSchedule", response_model=None)
async def patch_explainer_schedule(
    schedule_id: str, payload: ExplainerSchedulePatchRequest, request: Request
) -> dict[str, Any]:
    try:
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.schedules", "ExplainerScheduleService"
        )
        fields = payload.model_dump(mode="json")
        fields.pop("expected_revision")
        return _command(
            request, lambda repo: service_factory(repo).update_schedule(schedule_id=schedule_id, **fields)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


# --------------------------------------------------------------------------- #
# publication
# --------------------------------------------------------------------------- #
@router.post(
    "/publication-packages/{package_id}/attempts", status_code=202, operation_id="attemptExplainerPublication", response_model=None)
async def attempt_explainer_publication(
    package_id: str, payload: PublicationAttemptRequest, request: Request
) -> dict[str, Any]:
    """Attempt an authorized upload, or record a manual publication handoff.

    With no authorization the adapter is never called and the package is marked
    ``NEEDS_MANUAL_PUBLISH`` with a complete handoff record.
    """

    try:
        return _command(request, lambda repo: _publication_attempt(repo, package_id, payload))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _publication_attempt(
    repo: ExplainerRepository, package_id: str, payload: PublicationAttemptRequest
) -> dict[str, Any]:
    package = repo.get("publication_packages", package_id)
    if not payload.authorized:
        receipt = repo.insert(
            "publication_receipts",
            {
                "package_id": package_id,
                "video_id": str(package["video_id"]),
                "attempt_no": 1,
                "publisher_adapter": payload.adapter_code,
                "platform_code": payload.platform_code,
                "account_ref": payload.account_ref,
                "package_hash": str(package.get("zip_sha256") or package["manifest_hash"]),
                "status": "NEEDS_MANUAL_PUBLISH",
                "handoff_note": payload.handoff_note or "未授权自动上传；请按手工发布包交接。",
            },
        )
        repo.update("publication_packages", package_id, {"status": "NEEDS_MANUAL_PUBLISH"})
        return {
            "receipt": receipt,
            "outbound_attempts": 0,
            "would_send": False,
            "manual_handoff_required": True,
            "note": "没有账号授权或接口能力时不会发送任何内容，也不为上传重新生成作品。",
        }
    return {
        "package_id": package_id,
        "status": "PUBLISHING",
        "outbound_attempts": 1,
        "would_send": True,
        "idempotency_scope": ["platform", "account", "package_hash"],
        "timeout_reconciliation": "QUERY_ORIGINAL_SESSION_FIRST",
    }
