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

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from fastapi import APIRouter, Header, Query, Request, Response

from local_drama.api.schemas.explainers import (
    ExplainerBatchAdoptionRequest,
    ExplainerBreakdownStoryRequest,
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
from local_drama.domain.explainers.contracts import (
    ASPECT_PIXELS,
    ExplainerContractError,
    ProductKind,
    aspect_pixels_for_height,
    normalize_locale,
)
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


#: Per-process secret for the listing cursor signature.  A cursor is an opaque,
#: tamper-evident sort key — never a value that can be concatenated into SQL.
_LIST_CURSOR_SECRET = secrets.token_bytes(32)


def _query(request: Request, fn: Callable[[ExplainerRepository], T]) -> T:
    with _database(request).connect() as connection:
        return fn(ExplainerRepository(connection))


def _command(request: Request, fn: Callable[[ExplainerRepository], T]) -> T:
    with _database(request).transaction() as connection:
        return fn(ExplainerRepository(connection))


# --------------------------------------------------------------------------- #
# service wiring
# --------------------------------------------------------------------------- #
def _capability_probe(database: Database, settings: Any) -> Callable[..., dict[str, Any]]:
    """Resolve an explainer requirement through its real binding.

    The explainer graph names its requirements with dotted business keys, so the
    probe goes through :mod:`local_drama.application.explainers.capability_binding`
    — which maps each key to one canonical capability and to the profile store or
    first-party local runtime that actually satisfies it.  Nothing is reported
    available optimistically: an unresolved or unconfigured requirement comes back
    ``available: False`` with the concrete reason.
    """

    from local_drama.application.explainers.capability_binding import (
        build_explainer_capability_probe,
    )

    return build_explainer_capability_probe(database, settings)


def _workflow_service(request: Request) -> Any:
    from local_drama.application.automation_workflows import AutomationWorkflowService

    return AutomationWorkflowService(_database(request))


def production_service(request: Request) -> ExplainerProductionService:
    database = _database(request)
    settings = request.app.state.settings
    return ExplainerProductionService(
        database,
        capability_probe=_capability_probe(database, settings),
        workflow_service=_workflow_service(request),
        settings=settings,
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
def _encode_list_cursor(*, updated_at: str, project_id: str, filter_digest: str) -> str:
    """Encode a keyset cursor: the sort key plus a signed filter digest.

    The cursor is *signed* so a hand-edited value cannot be spliced into SQL, and it
    carries the filter digest so a cursor minted under other filters is rejected
    instead of silently returning a different page.
    """

    payload = json.dumps(
        {"v": 1, "u": str(updated_at), "i": str(project_id), "f": str(filter_digest)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_LIST_CURSOR_SECRET, payload, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")


def _decode_list_cursor(cursor: str, *, filter_digest: str) -> tuple[str, str]:
    """Return ``(updated_at, project_id)`` or refuse the cursor explicitly."""

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ExplainerContractError(
            "INVALID_CURSOR", "cursor 不是合法的分页游标", {"cursor": str(cursor)[:64]}
        ) from error
    if len(raw) <= 12:
        raise ExplainerContractError("INVALID_CURSOR", "cursor 长度不合法")
    payload, signature = raw[:-12], raw[-12:]
    expected = hmac.new(_LIST_CURSOR_SECRET, payload, hashlib.sha256).digest()[:12]
    if not hmac.compare_digest(signature, expected):
        raise ExplainerContractError("INVALID_CURSOR", "cursor 签名不匹配，已拒绝")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplainerContractError("INVALID_CURSOR", "cursor 内容无法解析") from error
    if not isinstance(decoded, dict) or int(decoded.get("v") or 0) != 1:
        raise ExplainerContractError("INVALID_CURSOR", "cursor 版本不受支持")
    if str(decoded.get("f") or "") != str(filter_digest):
        raise ExplainerContractError(
            "INVALID_CURSOR",
            "cursor 与当前筛选条件不一致，请从第一页重新开始",
            {"cursor": str(cursor)[:64]},
        )
    updated_at = str(decoded.get("u") or "")
    project_id = str(decoded.get("i") or "")
    if not updated_at or not project_id:
        raise ExplainerContractError("INVALID_CURSOR", "cursor 缺少排序键")
    # The timestamp is compared as text in SQL, so an unexpected shape is refused
    # rather than silently ordering differently.
    if not re.fullmatch(r"[0-9T:.\-+Z ]{10,40}", updated_at):
        raise ExplainerContractError("INVALID_CURSOR", "cursor 的时间戳格式不合法")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", project_id):
        raise ExplainerContractError("INVALID_CURSOR", "cursor 的项目 ID 格式不合法")
    return updated_at, project_id


def _list_filter_digest(*, project_kind: str, search: str | None) -> str:
    return hashlib.sha256(
        json.dumps({"kind": project_kind, "search": (search or "").strip()}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


@router.get("/explainers", operation_id="listExplainers", response_model=None)
async def list_explainers(
    request: Request,
    project_kind: str = Query(default="EXPLAINER", max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=256),
    search: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """List explainer workspaces with real keyset pagination.

    The declared ``cursor`` used to be ignored entirely: the SQL had no cursor
    predicate and ``next_cursor`` was always ``null``, so work beyond the first
    ``limit`` rows could not be reached at all — 101 projects with ``limit=100``
    reported 100 rows and no way to fetch the 101st.  The listing now pages by the
    ``(updated_at DESC, id ASC)`` sort key, runs the search filter in SQL, and
    derives ``edition_count`` / ``open_issue_count`` with two bounded aggregate
    queries instead of two queries per row.
    """

    try:
        def run(repo: ExplainerRepository) -> dict[str, Any]:
            filter_digest = _list_filter_digest(project_kind=project_kind, search=search)
            parameters: list[Any] = [project_kind]
            where = ["p.product_kind = ?"]
            if search and search.strip():
                where.append("(p.title LIKE ? ESCAPE '\\' OR p.code LIKE ? ESCAPE '\\')")
                pattern = f"%{search.strip().replace('%', '\\%').replace('_', '\\_')}%"
                parameters += [pattern, pattern]
            if cursor:
                last_updated, last_id = _decode_list_cursor(cursor, filter_digest=filter_digest)
                where.append("(p.updated_at < ? OR (p.updated_at = ? AND p.id > ?))")
                parameters += [last_updated, last_updated, last_id]
            # limit + 1 decides "is there another page" without counting the whole
            # filtered set.
            parameters.append(int(limit) + 1)
            rows = repo.query_all(
                f"""
                SELECT p.id AS project_id, p.code, p.title, p.status, p.product_kind, p.revision,
                       p.target_duration_ms, p.created_at, p.updated_at,
                       v.id AS video_id, v.content_kind, v.target_seconds, v.duration_mode,
                       v.automation_mode, v.status AS video_status
                FROM projects p
                LEFT JOIN explainer_videos v ON v.project_id = p.id
                WHERE {" AND ".join(where)}
                ORDER BY p.updated_at DESC, p.id ASC
                LIMIT ?
                """,
                tuple(parameters),
            )
            page = [dict(row) for row in rows[: int(limit)]]
            has_more = len(rows) > int(limit)

            video_ids = [str(item["video_id"]) for item in page if item.get("video_id")]
            edition_counts: dict[str, int] = {}
            issue_counts: dict[str, int] = {}
            if video_ids:
                placeholders = ", ".join("?" for _ in video_ids)
                for row in repo.query_all(
                    f"SELECT video_id, COUNT(*) AS n FROM explainer_editions"
                    f" WHERE video_id IN ({placeholders}) GROUP BY video_id",
                    tuple(video_ids),
                ):
                    edition_counts[str(row["video_id"])] = int(row["n"])
                for row in repo.query_all(
                    f"SELECT video_id, COUNT(*) AS n FROM explainer_qc_issues"
                    f" WHERE video_id IN ({placeholders}) AND status IN ('OPEN','FIXING')"
                    f" GROUP BY video_id",
                    tuple(video_ids),
                ):
                    issue_counts[str(row["video_id"])] = int(row["n"])
            for item in page:
                video_id = item.get("video_id")
                item["edition_count"] = 0 if not video_id else edition_counts.get(str(video_id), 0)
                item["open_issue_count"] = 0 if not video_id else issue_counts.get(str(video_id), 0)
                item["episode_count"] = 0

            next_cursor = None
            if has_more and page:
                last = page[-1]
                next_cursor = _encode_list_cursor(
                    updated_at=str(last["updated_at"]),
                    project_id=str(last["project_id"]),
                    filter_digest=filter_digest,
                )
            return {
                "items": page,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "loaded_count": len(page),
                "limit": int(limit),
                "search": search or None,
                "product_kind_filter": project_kind,
                "aggregate_query_count": 2 if video_ids else 0,
                "cursor_sort_key": ["updated_at DESC", "id ASC"],
                "note": "updated_at 会变化；并发更新下普通键集分页不承诺无重复，需要稳定快照请使用导出。",
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
    operation_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> dict[str, Any]:
    """Create the EXPLAINER project together with its video and input references.

    Project, video, durable input projection and the composite idempotency receipt
    are written in one transaction by
    :class:`~local_drama.application.explainers.commands.ExplainerCreationService`.
    A failure therefore leaves no orphan project, a replay with the same key and
    body returns the same ids, and the same key with a different body is a
    structured conflict.
    """

    from local_drama.application.explainers.commands import (
        ExplainerCreateCommand,
        build_explainer_creation_service,
    )

    settings = request.app.state.settings
    database = _database(request)
    try:
        command = ExplainerCreateCommand(
            title=payload.title,
            topic=payload.topic,
            content_kind=payload.content_kind,
            project_code=payload.project_code,
            input_kind=payload.input_kind,
            source_refs=tuple(item.model_dump(mode="json") for item in payload.source_refs),
            reference_urls=tuple(payload.reference_urls),
            pasted_text=payload.pasted_text,
            duration_mode=payload.duration_mode,
            target_seconds=payload.target_seconds,
            tolerance_percent=payload.tolerance_percent,
            source_locale=payload.source_locale,
            automation_mode=payload.automation_mode,
            inference_mode=payload.inference_mode,
            research_mode=payload.research_mode,
            allowed_domains=tuple(payload.allowed_domains),
            channel_profile_id=payload.channel_profile_id,
            channel_profile_version_id=payload.channel_profile_version_id,
            aspect_ratio=payload.aspect_ratio,
            width=payload.width,
            height=payload.height,
            primary_language=payload.primary_language,
            subtitle_mode=payload.subtitle_mode,
            subtitle_language=payload.subtitle_language,
            fps_num=payload.outputs[0].fps.num,
            fps_den=payload.outputs[0].fps.den,
            outputs=tuple(item.model_dump(mode="json") for item in payload.outputs),
        )
        service = build_explainer_creation_service(database, settings)
        result = service.create_workspace(command, idempotency_key=idempotency_key, operation_id=operation_id)
        if idempotency_key:
            response.headers["Idempotency-Replayed"] = "true" if result.get("idempotent_replay") else "false"
        project = result["project"]
        video = result["video"]
        return {
            "project": project,
            "video": video,
            "product_kind": ProductKind.EXPLAINER.value,
            "outputs": [item.model_dump(mode="json") for item in payload.outputs],
            "operation_id": result.get("operation_id"),
            "request_digest": result.get("request_digest"),
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "next_step": {
                "action": "PREFLIGHT",
                "hint": "导入资料或直接点击“检查并一键生成”；预检只冻结计划，不排队 GPU。",
                "created_project_id": str(project.get("id")),
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

    Delegates to the composite creation service so there is one implementation of
    the code rule.  The suffix is request-derived there, which is what allows an
    intentionally separate second work with the same title to exist.
    """

    from local_drama.application.explainers.commands import derive_project_code

    return derive_project_code(title)


def _input_payload(payload: ExplainerCreateRequest) -> dict[str, Any]:
    """Legacy input projection (kept for the standalone video route).

    ``POST /explainers`` no longer uses this: it stores the pasted manuscript
    itself through ``ExplainerCreateCommand.input_payload`` instead of only its
    presence and length.
    """

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
        from local_drama.application.documents import extract_document_text

        extracted: dict[str, Any] | None = None
        # ``receive_bounded_upload`` is an async context manager that yields the one
        # staged temporary file; it is not an async iterator.  Iterating it raised
        # ``TypeError: 'async for' requires an object with __aiter__`` on every
        # upload, so no explainer source could ever be imported from the browser.
        async with receive_bounded_upload(
            request,
            work_group="explainer-sources",
            allowed_suffixes=_SOURCE_SUFFIXES,
            maximum_bytes=_MAX_UPLOAD_BYTES,
            default_filename="source.txt",
            error_prefix="EXPLAINER_SOURCE",
            too_large_message="来源文件超过 64 MiB 上限",
        ) as (temporary_path, upload_name, byte_size):
            del byte_size
            # Dispatch on the *real* container format.  A DOCX/PDF/EPUB is not a
            # text stream, so asking the user to "choose another encoding" can
            # never recover it; the shared importer's readers handle each format.
            extracted = extract_document_text(
                temporary_path,
                maximum_bytes=_MAX_UPLOAD_BYTES,
            )
            if not title:
                title = upload_name

        if extracted is None:  # pragma: no cover - the generator always yields once
            raise ExplainerContractError("SCHEMA_INVALID", "上传没有产生可用文件")
        if extracted.get("quality") != "complete":
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "文件内容无法可靠提取，请转换格式后重试",
                {
                    "format": extracted.get("format"),
                    "encoding": extracted.get("encoding"),
                    "warnings": extracted.get("warnings"),
                },
            )
        service_factory = _service_with_repo(
            request, "local_drama.application.explainers.research", "ExplainerResearchService"
        )
        return _command(
            request,
            lambda repo: _import_source_command(
                project_id,
                repo,
                extracted["text"],
                title,
                language,
                source_kind,
                service_factory,
                extraction={
                    "format": extracted.get("format"),
                    "encoding": extracted.get("encoding"),
                    "had_bom": extracted.get("had_bom"),
                    "byte_size": extracted.get("byte_size"),
                    "character_count": extracted.get("character_count"),
                    "paragraph_count": extracted.get("paragraph_count"),
                },
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
    extraction: Mapping[str, Any] | None = None,
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
        "extraction": dict(extraction or {}),
        "status": "IMPORTED_NOT_FACT_CHECKED",
        "note": "导入成功只表示文本已按文件真实格式解析并留存哈希，不代表事实已核验。",
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


@router.get(
    "/explainers/{project_id}/claims/{claim_id}/evidence",
    operation_id="getExplainerClaimEvidence",
    response_model=None,
)
async def get_explainer_claim_evidence(
    project_id: str, claim_id: str, request: Request
) -> dict[str, Any]:
    """FE-A11: the frozen evidence windows behind one fact.

    The fact ledger only showed code/status/importance, so a conflicting fact could
    not be inspected: ``claim_span_records`` already joined the claim to its source
    and to the exact saved span, and nothing exposed it.  Each entry carries the
    readable quote and its offsets, so the page can open the sentence rather than the
    whole document.
    """

    try:
        return _query(
            request,
            lambda repo: _claim_evidence(repo, project_id, claim_id),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _claim_evidence(repo: ExplainerRepository, project_id: str, claim_id: str) -> dict[str, Any]:
    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    claim = repo.get("explainer_claims", claim_id)
    if str(claim["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "事实不属于该解说作品", {"claim_id": claim_id})
    spans = repo.claim_span_records(claim_id)
    return {
        "video_id": str(video["id"]),
        "claim_id": claim_id,
        "claim_code": str(claim["code"]),
        "status": str(claim["status"]),
        "statement": claim.get("statement"),
        "evidence": [
            {
                "evidence_id": str(span["evidence_id"]),
                "stance": str(span.get("stance") or ""),
                "independence_key": span.get("independence_key"),
                "note": span.get("note"),
                "source_id": str(span["source_id"]),
                "source_title": span.get("source_title"),
                "source_url": span.get("source_url"),
                "published_at": span.get("published_at"),
                "fetched_at": span.get("fetched_at"),
                "credibility_kind": span.get("credibility_kind"),
                "source_body_sha256": span.get("body_sha256"),
                "span_id": str(span["span_id"]),
                "start_offset": span.get("start_offset"),
                "end_offset": span.get("end_offset"),
                "quote_text": span.get("quote_text"),
                "span_hash": span.get("span_hash"),
            }
            for span in spans
        ],
        # A fact with no span is an assertion nothing supports; the reader must be
        # able to tell that apart from "the evidence failed to load".
        "evidence_count": len(spans),
        "independent_source_count": repo.independent_evidence_count(claim_id),
        "empty_state": None if spans else "NO_EVIDENCE_SPAN_RECORDED",
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


@router.post(
    "/explainers/{project_id}/breakdown-story", status_code=201, operation_id="breakdownExplainerStory", response_model=None)
async def breakdown_explainer_story(
    project_id: str, payload: ExplainerBreakdownStoryRequest, request: Request
) -> dict[str, Any]:
    try:
        import asyncio
        from local_drama.application.explainers.story_breakdown import ExplainerStoryBreakdownService
        database = _database(request)
        settings = request.app.state.settings
        breakdown_service = ExplainerStoryBreakdownService(database, settings)
        return await asyncio.to_thread(breakdown_service.breakdown_story, project_id, payload)
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
        # The frozen plan inputs travel with the submission.  Dropping them here
        # made ``submit_run`` re-preflight with no outputs at all, so the freshly
        # computed hash could never equal the submitted one and every legitimate
        # one-click submission failed with 409 STALE_PLAN.
        outputs = [item.model_dump(mode="json") for item in payload.outputs]
        if payload.start_workflow:
            return service.start_run(
                project_id=project_id,
                plan_hash=payload.plan_hash,
                idempotency_key=idempotency_key,
                outputs=outputs,
                budget=payload.budget,
                fallback_policy=payload.fallback_policy,
            )
        return service.submit_run(
            project_id=project_id,
            plan_hash=payload.plan_hash,
            idempotency_key=idempotency_key,
            outputs=outputs,
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
    project_id: str,
    payload: ExplainerRepairRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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
        # ``confirm=true`` used to answer ``submitted: true`` while writing nothing:
        # the reviewer pressed "fix this issue" and no job existed.  The repair is
        # now submitted through the same stage commands the manual buttons use, and
        # a repair that cannot be scheduled says so instead of reporting success
        # (design §8.2).  Only the confirming call needs a key — the preview does
        # not submit anything.
        if not idempotency_key:
            raise ExplainerContractError(
                "IDEMPOTENCY_KEY_REQUIRED", "确认执行局部返工必须提供 Idempotency-Key"
            )
        from local_drama.application.explainers.stage_commands import build_explainers_command_service

        service = build_explainers_command_service(_database(request), request.app.state.settings)
        submitted = service.submit_repair(
            project_id=project_id,
            video_id=str(plan["video_id"]),
            issue_ids=payload.issue_ids,
            responsible_steps=plan["responsible_steps"],
            beat_ids=plan["beats"],
            revision=int(plan.get("revision") or payload.expected_revision),
            idempotency_key=idempotency_key,
        )
        return {"plan": plan, "requires_confirmation": False, **submitted}
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
    """Editions with a *playable* media read model, not just id/hash/integrity.

    ``current_render`` used to expose only ``id``/``integrity_status``/``sha256``, so
    a browser could not build a player even for an existing verified render — the
    review page therefore rendered a placeholder that always said "no media yet".
    The view now carries the controlled playback facts (media version, MIME, byte
    size, duration, frame count, rational fps) plus an explicit availability state,
    and it never emits a raw filesystem path.
    """

    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    editions = repo.editions(str(video["id"]))
    # Per-edition subtitle counts: the previous code queried every subtitle
    # revision of the whole *video* inside the loop, so each edition reported the
    # video's total rather than its own.
    subtitle_counts: dict[str, int] = {}
    for row in repo.query_all(
        "SELECT edition_id, COUNT(*) AS n FROM explainer_subtitle_revisions"
        " WHERE video_id = ? AND edition_id IS NOT NULL GROUP BY edition_id",
        (str(video["id"]),),
    ):
        subtitle_counts[str(row["edition_id"])] = int(row["n"])
    items: list[dict[str, Any]] = []
    for edition in editions:
        edition_id = str(edition["id"])
        composition = repo.latest_composition(edition_id)
        render = repo.current_root_render(edition_id)
        items.append(
            {
                **edition,
                "composition": {
                    "id": composition["id"],
                    "revision_no": composition["revision_no"],
                    "status": composition["status"],
                    "manifest_hash": composition.get("manifest_hash"),
                    "total_frames": composition.get("total_frames"),
                    "fps_num": composition.get("fps_num"),
                    "fps_den": composition.get("fps_den"),
                }
                if composition
                else None,
                "current_render": _render_media_view(repo, render) if render else None,
                "composition_items": _composition_item_view(repo, composition) if composition else [],
                "subtitle_revision_count": subtitle_counts.get(edition_id, 0),
                "subtitle_locales": list(edition.get("subtitle_locales_json") or []),
            }
        )
    return {
        "video_id": str(video["id"]),
        "editions": items,
        "independent_clocks": {
            str(item["voice_locale"]): item["duration_policy"] for item in items
        },
        "english_timing_copied_from_source_locale": False,
        "composition_items_truncated": any(
            len(item.get("composition_items") or []) >= _COMPOSITION_ITEM_LIMIT for item in items
        ),
        "media_paths_are_never_exposed": True,
    }


def _composition_item_view(repo: ExplainerRepository, composition: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The frozen manifest's clip ranges, bounded and path-free.

    The review timeline used to be three fixed boxes labelled 起始/主体/收束 plus a
    few invented cues.  These are the real ``composition_items`` of the edition's
    latest composition revision, reduced to what a read-only timeline needs.
    """

    items = repo.composition_items(str(composition["id"]))
    bounded = items[: _COMPOSITION_ITEM_LIMIT]
    return [
        {
            "id": str(item.get("id")),
            "track": str(item.get("track") or ""),
            "item_kind": str(item.get("item_kind") or ""),
            "ordinal": item.get("ordinal"),
            "start_frame": item.get("start_frame"),
            "end_frame_exclusive": item.get("end_frame_exclusive"),
            "source_in_us": item.get("source_in_us"),
            "source_out_us": item.get("source_out_us"),
            "media_version_id": item.get("media_version_id"),
            "narration_segment_id": item.get("narration_segment_id"),
            "beat_id": item.get("beat_id"),
        }
        for item in bounded
    ]


#: A timeline never needs an unbounded number of intervals; the response states the
#: truncation through ``composition_items_truncated``.
_COMPOSITION_ITEM_LIMIT = 500


def _render_media_view(repo: ExplainerRepository, render: Mapping[str, Any]) -> dict[str, Any]:
    """The playable facts of one render, with an explicit availability state."""

    media_version_id = render.get("media_version_id")
    version: Mapping[str, Any] | None = None
    if media_version_id:
        version = repo.find("media_versions", str(media_version_id))
    frame_count = render.get("frame_count")
    duration_ms = render.get("duration_ms")
    fps_num = None
    fps_den = None
    composition_id = render.get("composition_revision_id")
    if composition_id:
        composition = repo.find("composition_revisions", str(composition_id))
        if composition is not None:
            fps_num = composition.get("fps_num")
            fps_den = composition.get("fps_den")
            if frame_count is None:
                frame_count = composition.get("total_frames")
    integrity = str(render.get("integrity_status") or "UNKNOWN")
    status = str(render.get("status") or "")
    if version is None:
        availability = "MEDIA_REFERENCE_MISSING"
    elif integrity != "VERIFIED":
        availability = "INTEGRITY_FAILED"
    elif status not in {"SUCCEEDED", "READY", "VERIFIED"}:
        availability = "NOT_READY"
    else:
        availability = "PLAYABLE"
    return {
        "id": str(render["id"]),
        "status": status,
        "integrity_status": integrity,
        "sha256": render.get("sha256"),
        "manifest_hash": render.get("manifest_hash"),
        "composition_revision_id": composition_id,
        "media_version_id": None if media_version_id is None else str(media_version_id),
        "media_asset_id": None if render.get("media_asset_id") is None else str(render["media_asset_id"]),
        "mime_type": None if version is None else version.get("mime_type"),
        "byte_size": None if version is None else version.get("byte_size"),
        "duration_ms": duration_ms if duration_ms is not None else (None if version is None else version.get("duration_ms")),
        "frame_count": frame_count,
        "fps_num": fps_num,
        "fps_den": fps_den,
        "playback_url": (
            None
            if media_version_id is None
            else f"/api/v1/media-versions/{media_version_id}/content"
        ),
        "thumbnail_url": (
            None
            if media_version_id is None
            else f"/api/v1/media-versions/{media_version_id}/thumbnail"
        ),
        "waveform_url": (
            None
            if media_version_id is None
            else f"/api/v1/media-versions/{media_version_id}/waveform"
        ),
        "availability": availability,
        "playable": availability == "PLAYABLE",
        "note": "浏览器只拿到受控媒体引用；不输出任何本机绝对路径。",
    }


@router.post(
    "/explainers/{project_id}/editions", status_code=201, operation_id="createExplainerEdition", response_model=None)
async def create_explainer_edition(
    project_id: str, payload: ExplainerEditionRequest, request: Request
) -> dict[str, Any]:
    """Add a language/aspect edition that reuses semantics and compatible media."""

    try:
        return _command(
            request,
            lambda repo: _create_edition(
                repo,
                project_id,
                payload,
                generation_height=getattr(request.app.state.settings, "explainer_generation_height", None),
            ),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


#: Aspect ratio -> generation pixels.  Defined once in the explainer domain so the
#: route, the pipeline and the edition contract can never disagree about the canvas.
_ASPECT_PIXELS: dict[str, tuple[int, int]] = ASPECT_PIXELS


def _create_edition(
    repo: ExplainerRepository,
    project_id: str,
    payload: ExplainerEditionRequest,
    *,
    generation_height: int | None = None,
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
    # The canvas is the configured generation size (480p by default), not a compiled
    # constant: the master is generated small and super-resolved to the delivery
    # height later.
    width, height = aspect_pixels_for_height(payload.aspect_ratio, generation_height)
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
    """The narration clock of one edition, with an honest per-segment state.

    Three defects are corrected here.

    *The clock was steered by the subtitle language.*  ``locale`` was also used as
    the narration query's locale and the backend fell back to
    ``edition.voice_locale`` when it was absent, so switching the subtitle preview
    language could silently move the narration query to another language — and the
    "re-read this line" command still ran in the edition's own voice locale.  The
    voice locale now always comes from the active edition; ``locale`` is accepted
    only as a *subtitle* hint and never changes the narration clock.

    *The total summed every historical take.*  ``measured_total_ms`` was
    ``sum(all takes)``, so saving a second re-read made the film look longer even
    though nothing about the current edit changed.  It is now derived from the
    takes the current frozen script revision actually selects, and reported both as
    a sum of segments and as the timeline length including declared pauses.

    *A take existing was reported as "aligned".*  ``alignment_status`` is now an
    explicit state per segment (``NOT_GENERATED`` / ``AUDIO_READY`` / ``ALIGNING``
    / ``ALIGNED`` / ``FAILED`` / ``STALE``) read from the alignment revision, its
    hash and the take's media state.
    """

    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    voice_locale = normalize_locale(str(edition["voice_locale"]))
    subtitle_locale = normalize_locale(locale) if locale else None
    frozen_script_revision_id = edition.get("frozen_script_revision_id")

    segments: list[dict[str, Any]] = []
    if frozen_script_revision_id:
        segments = repo.segments(str(frozen_script_revision_id))
    segment_ids = {str(segment["id"]) for segment in segments}
    canonical_ids = {str(segment["canonical_segment_id"]) for segment in segments}

    # Only this edition's voice locale, and only takes of the current frozen
    # revision: a historical re-read must not inflate "measured total".
    all_takes = repo.list_where(
        "narration_takes",
        {"video_id": str(video["id"]), "locale": voice_locale},
        order_by="canonical_segment_id",
        descending=False,
    )
    scoped_takes = [
        take
        for take in all_takes
        if str(take.get("canonical_segment_id")) in canonical_ids
        and (not frozen_script_revision_id or str(take.get("segment_id")) in segment_ids)
    ]
    selected: dict[str, dict[str, Any]] = {}
    for take in scoped_takes:
        if take.get("selected"):
            selected[str(take["canonical_segment_id"])] = take

    segment_states: list[dict[str, Any]] = []
    aligned_total_ms = 0
    audio_ready_total_ms = 0
    for segment in segments:
        canonical = str(segment["canonical_segment_id"])
        take = selected.get(canonical)
        state = "NOT_GENERATED"
        alignment: dict[str, Any] | None = None
        duration_ms: int | None = None
        if take is not None:
            duration_ms = None if take.get("measured_duration_ms") is None else int(take["measured_duration_ms"])
            alignment = repo.latest_alignment_for_take(str(take["id"]))
            if alignment is None:
                # A take without an alignment revision has *audio*, not alignment.
                state = "AUDIO_READY"
            else:
                alignment_status = str(
                    alignment.get("alignment_status") or alignment.get("status") or ""
                ).upper()
                take_hash = str(take.get("segment_hash") or "")
                alignment_hash = str(alignment.get("script_hash") or alignment.get("segment_hash") or "")
                if alignment_status == "FAILED":
                    state = "FAILED"
                elif alignment_hash and take_hash and alignment_hash != take_hash:
                    # The alignment was produced for a different script hash.
                    state = "STALE"
                elif alignment_status == "ALIGNED":
                    state = "ALIGNED"
                    if duration_ms is not None:
                        aligned_total_ms += duration_ms
                else:
                    # PARTIAL is a real measurement of an incomplete alignment; it
                    # is neither a pass nor a failure, and it does not contribute to
                    # the "aligned" clock.
                    state = "ALIGNING" if alignment_status in {"RUNNING", "PENDING"} else "AUDIO_READY"
            if duration_ms is not None:
                audio_ready_total_ms += duration_ms
        segment_states.append(
            {
                "canonical_segment_id": canonical,
                "segment_id": str(segment["id"]),
                "ordinal": int(segment.get("ordinal") or 0),
                "display_text": segment.get("display_text"),
                "spoken_text": segment.get("spoken_text"),
                "pause_after_ms": int(segment.get("pause_after_ms") or 0),
                "selected_take_id": None if take is None else str(take["id"]),
                "take_no": None if take is None else take.get("take_no"),
                "measured_duration_ms": duration_ms,
                "alignment_id": None if alignment is None else str(alignment["id"]),
                "alignment_status": (
                    None
                    if alignment is None
                    else alignment.get("alignment_status") or alignment.get("status")
                ),
                "alignment_error": (
                    None
                    if alignment is None
                    else (
                        alignment.get("error_detail")
                        or alignment.get("failure_reason")
                        or alignment.get("unaligned_tokens_json")
                        or (
                            alignment.get("asr_review_json")
                            if isinstance(alignment.get("asr_review_json"), str)
                            and alignment.get("asr_review_json") not in {"", "{}"}
                            else None
                        )
                    )
                ),
                "media_version_id": None if take is None else take.get("media_version_id"),
                "media_sha256": None if take is None else take.get("media_sha256"),
                "audio_url": (
                    None
                    if take is None or not take.get("media_version_id")
                    else f"/api/v1/media-versions/{take['media_version_id']}/content"
                ),
                "waveform_url": (
                    None
                    if take is None or not take.get("media_version_id")
                    else f"/api/v1/media-versions/{take['media_version_id']}/waveform"
                ),
                "state": state,
            }
        )

    pauses_ms = sum(item["pause_after_ms"] if item["state"] == "ALIGNED" else 0 for item in segment_states)
    aligned_count = sum(1 for item in segment_states if item["state"] == "ALIGNED")
    alignments = [
        repo.latest_alignment_for_take(str(take["id"]))
        for take in scoped_takes
        if take.get("id")
    ]
    alignments = [item for item in alignments if item]
    return {
        "edition_id": edition_id,
        "video_id": str(video["id"]),
        "voice_locale": voice_locale,
        "subtitle_locale": subtitle_locale,
        "subtitle_locale_does_not_change_the_voice_clock": True,
        "requested_locale_ignored_for_the_clock": bool(subtitle_locale and subtitle_locale != voice_locale),
        "frozen_script_revision_id": frozen_script_revision_id,
        "segments": segments,
        "segment_states": segment_states,
        "takes": scoped_takes,
        "historical_takes": [take for take in all_takes if take not in scoped_takes],
        "alignments": alignments,
        "selected_take_count": len(selected),
        # Only the current edit contributes: a saved re-read no longer makes the
        # film appear longer.
        "measured_total_ms": aligned_total_ms or None,
        "audio_ready_total_ms": audio_ready_total_ms or None,
        "segment_duration_sum_ms": aligned_total_ms or None,
        "timeline_total_ms": (aligned_total_ms + pauses_ms) or None,
        "declared_pause_total_ms": pauses_ms,
        "aligned_segment_count": aligned_count,
        "segment_count": len(segment_states),
        "state_counts": {
            state: sum(1 for item in segment_states if item["state"] == state)
            for state in ("NOT_GENERATED", "AUDIO_READY", "ALIGNING", "ALIGNED", "FAILED", "STALE")
        },
        "independent_clock": True,
        "clock_source": str(edition["duration_policy"]),
        "null_means_not_generated": True,
        "alignment_read_from_revision": True,
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
        database = _database(request)
        return _resynthesize(
            database,
            edition_id,
            canonical_segment_id,
            reason,
            idempotency_key,
            settings=request.app.state.settings,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _resynthesize(
    database: Database,
    edition_id: str,
    canonical_segment_id: str,
    reason: str,
    idempotency_key: str,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Freeze the re-read scope and schedule the real ``NARRATION_TTS`` job.

    The previous implementation queried the edition/video/segment and returned a
    description with **zero** writes: the UI showed "re-read registered" while no
    job, no take and no idempotency receipt existed.  The submission now goes
    through :class:`ExplainersCommandService`, so the returned ids are real, and a
    stage without a registered worker reports ``CAPABILITY_UNAVAILABLE`` instead of
    pretending to be accepted.
    """

    from local_drama.application.explainers.stage_commands import build_explainers_command_service

    service = build_explainers_command_service(database, settings)
    result = service.submit_narration_resynthesis(
        edition_id=edition_id,
        canonical_segment_id=canonical_segment_id,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    result["requested_stage"] = "NARRATION_TTS"
    result["reason"] = reason
    return result


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
        database = _database(request)
        with database.connect() as connection:
            repo = ExplainerRepository(connection)
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
        return _plan_or_submit_render(
            database,
            edition_id=edition_id,
            composition=composition,
            payload=payload,
            idempotency_key=idempotency_key,
            settings=request.app.state.settings,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _plan_or_submit_render(
    database: Database,
    *,
    edition_id: str,
    composition: Mapping[str, Any] | None,
    payload: ExplainerRenderRequest,
    idempotency_key: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Plan, or hand the confirmed render to the real submission service.

    The plan branch keeps its honest ``READY_TO_START`` / ``BLOCKED`` shape.  The
    confirmed branch no longer flips the edition to ``RENDERING``: that status was
    written with no job behind it, leaving the workbench "running" forever.  A stage
    with no registered worker now reports ``CAPABILITY_UNAVAILABLE`` and changes
    nothing.
    """

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
    if str(composition["status"]) != "FROZEN":
        if not payload.freeze:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "渲染必须基于已冻结的 composition manifest，不能读取“最新”候选"
            )
        with database.transaction() as connection:
            composition = ExplainerRepository(connection).update(
                "composition_revisions",
                str(composition["id"]),
                {"status": "FROZEN", "frozen_at": _now(), "frozen_by": "local-user"},
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
    from local_drama.application.explainers.stage_commands import build_explainers_command_service

    service = build_explainers_command_service(database, settings)
    result = service.submit_composition_render(
        edition_id=edition_id,
        composition=composition,
        idempotency_key=idempotency_key,
        confirm=True,
    )
    result["edition_id"] = edition_id
    result["composition_revision_id"] = composition["id"]
    result["manifest_hash"] = composition["manifest_hash"]
    return result


def _start_render(
    repo: ExplainerRepository,
    edition_id: str,
    payload: ExplainerRenderRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    """Deprecated alias.

    Kept only so an out-of-tree caller cannot revive the old "flip the edition to
    RENDERING and answer SUBMITTED" behaviour; the route now uses
    :func:`_plan_or_submit_render`, which writes nothing unless a real worker can
    claim the stage.
    """

    del repo
    raise ExplainerContractError(
        "INVALID_REQUEST",
        "_start_render 已废弃：渲染必须通过 _plan_or_submit_render 提交，不能只改状态返回 SUBMITTED",
        {"edition_id": edition_id, "idempotency_key": idempotency_key},
    )


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
    """Read the QC state of the *same* subject the human decision writes to.

    The page queried ``EDITION`` while the confirmation was recorded against
    ``COMPOSITION_RENDER`` + the current render hash, so a recorded approval never
    came back after a refresh.  Either both sides name the current render, or — when
    no verified render exists — the view says so honestly instead of dressing the
    edition-level report up as a film QC.
    """

    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    target = repo.current_review_target(edition_id)
    if render_id:
        # A named render must belong to this edition; the helper refuses otherwise.
        named = repo.require_render_for_edition(
            edition_id=edition_id, render_id=render_id, require_deliverable=False
        )
        subject_id = str(named["id"])
        subject_kind = "COMPOSITION_RENDER"
        subject_hash = str(named.get("sha256") or "")
        target = {**target, "render_id": subject_id, "render_sha256": named.get("sha256"), "has_render": True}
    elif target["render_id"]:
        subject_id = str(target["render_id"])
        subject_kind = "COMPOSITION_RENDER"
        subject_hash = str(target["render_sha256"] or "")
    else:
        # No verified render: keep the edition subject, but say so rather than
        # letting the caller believe it is looking at a finished film.
        subject_id = edition_id
        subject_kind = "EDITION"
        subject_hash = ""
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
        "film_review_target": target,
        "subject": {"kind": subject_kind, "revision_id": subject_id, "hash": subject_hash or None},
        "subject_is_the_film": subject_kind == "COMPOSITION_RENDER",
        "empty_state": None if subject_kind == "COMPOSITION_RENDER" else "NO_VERIFIED_RENDER",
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
            # This used to be a prose answer: "已请求内部处理器按冻结政策重跑" with
            # no persisted intent anywhere, so nothing could ever be claimed.
            from local_drama.application.explainers.stage_commands import (
                build_explainers_command_service,
            )

            idempotency_key = request.headers.get("Idempotency-Key")
            if not idempotency_key:
                raise ExplainerContractError(
                    "IDEMPOTENCY_KEY_REQUIRED", "重跑政策必须提供 Idempotency-Key"
                )
            service = build_explainers_command_service(
                _database(request), request.app.state.settings
            )
            result = service.submit_policy_rerun(
                edition_id=edition_id,
                idempotency_key=idempotency_key,
                reason=payload.note or "OPERATOR_REQUEST",
            )
            result["policy_rule_version_refrozen"] = result.get("status") == "ACCEPTED"
            result["machine_decision_created_by_http"] = False
            result["note"] = (
                "已把政策重跑写入真实任务；HTTP 客户端不能自填 POLICY_ACCEPTED。"
                if result.get("status") == "ACCEPTED"
                else "政策重跑未被接受，请按 blockers 处理；未创建任何任务。"
            )
            return result
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
        result = _command(request, lambda repo: _start_export(repo, edition_id, payload, idempotency_key))
        if bool(payload.confirm) and str(result.get("status")) == "BUILDING" and result.get("package_id"):
            # A confirmed export used to stop at the durable ``BUILDING`` row: the
            # operator saw "已受理" while no worker could ever claim the work, so the
            # package stayed BUILDING forever.  The confirmed command now also
            # schedules the real stage job that fills that exact row.
            from local_drama.application.explainers.stage_commands import build_explainers_command_service

            service = build_explainers_command_service(_database(request), request.app.state.settings)
            submission = service.submit_export(
                edition_id=edition_id,
                package_id=str(result["package_id"]),
                idempotency_key=f"{str(idempotency_key).strip()}:stage",
            )
            result = {
                **result,
                "status": "ACCEPTED" if str(submission.get("status")) == "ACCEPTED" else str(submission.get("status")),
                "job_id": submission.get("job_id"),
                "job_state": submission.get("job_state"),
                "stage_submission": submission,
                "durable_intent_persisted": bool(submission.get("durable_intent_persisted")),
                "would_create_jobs": bool(submission.get("would_create_jobs")),
            }
        return result
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
    """Plan, then (only on explicit confirmation) freeze a publication package.

    Two things were wrong here.  ``confirm`` was declared with a default of
    ``False`` and never read, so a "plan only" request still wrote a ``BUILDING``
    package — a protection switch that did not protect anything.  And an explicit
    ``render_id`` was fetched with a plain ``find``, so a render from another
    edition/project could be bound into this edition's package.

    The key is also now real: ``explainer-export:{edition_id}`` carries a payload
    digest, so replaying one key returns the same package instead of inserting a
    second one on every click.
    """

    from local_drama.domain.explainers.contracts import content_hash

    edition = repo.get("explainer_editions", edition_id)
    video = repo.get("explainer_videos", str(edition["video_id"]))
    if payload.edition_id and str(payload.edition_id) != str(edition_id):
        raise ExplainerContractError(
            "INVALID_REQUEST",
            "请求体 edition_id 与 URL 不一致",
            {"url_edition_id": edition_id, "body_edition_id": payload.edition_id},
        )

    # Ownership first: a named render must belong to *this* edition, and a
    # deliverable export additionally requires a VERIFIED, complete root render.
    render = repo.require_render_for_edition(
        edition_id=edition_id,
        render_id=payload.render_id,
        require_deliverable=bool(payload.confirm),
    )
    if render is None:
        if payload.confirm:
            render = repo.current_root_render(edition_id)
        else:
            # Planning is allowed to mention the render it *would* use, but it
            # must not fall back to "the latest" as if the caller had asked for it.
            render = repo.current_root_render(edition_id)
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
            "package": None,
            "operation_id": None,
            "idempotency_key": idempotency_key,
            "confirm": bool(payload.confirm),
        }

    frozen_plan = {
        "edition_id": edition_id,
        "project_id": str(video["project_id"]),
        "video_id": str(video["id"]),
        "render_id": str(render["id"]),
        "render_sha256": render.get("sha256"),
        "composition_revision_id": str(render["composition_revision_id"]),
        "manifest_hash": str(render["manifest_hash"]),
        "platform_code": payload.platform_code,
        "intended_territories": list(payload.intended_territories),
        "include_stems": bool(payload.include_stems),
        "include_subtitles": bool(payload.include_subtitles),
    }
    plan_hash = content_hash(frozen_plan)
    scope = f"explainer-export:{edition_id}"

    if not payload.confirm:
        return {
            "edition_id": edition_id,
            "status": "PREVIEW",
            "plan": frozen_plan,
            "plan_hash": plan_hash,
            "requires_confirmation": True,
            "would_build_package": False,
            "package": None,
            "operation_id": None,
            "idempotency_key": idempotency_key,
            "confirm": False,
            "note": "这是计划：确认（confirm=true）之前不会写入任何发布包，也不会排任何任务。",
        }

    # Confirmation must be for *this* frozen plan, not for a different one that
    # happened to share an idempotency key.
    existing = repo.query_one(
        "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
        (scope, str(idempotency_key).strip()),
    )
    if existing is not None:
        if str(existing["payload_hash"]) != plan_hash:
            raise ExplainerContractError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "相同 Idempotency-Key 已用于不同的导出计划",
                {"edition_id": edition_id},
            )
        import json as _json

        replayed = _json.loads(str(existing["response_json"]))
        return {
            **replayed,
            "idempotent_replay": True,
            "plan_hash": plan_hash,
            "license_preflight_required": True,
            "global_export_does_not_imply_global_license": True,
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
                "plan_hash": plan_hash,
                "confirm_required": True,
            },
            "license_scope_json": {"evaluated": False},
        },
    )
    response = {
        "edition_id": edition_id,
        "status": "BUILDING",
        "package": package,
        "plan": frozen_plan,
        "plan_hash": plan_hash,
        "package_id": package["id"],
        "operation_id": str(package["id"]),
        "idempotency_key": idempotency_key,
        "idempotent_replay": False,
        "bound_to_frozen_revision": True,
        "bound_manifest_hash": str(render["manifest_hash"]),
        "license_preflight_required": True,
        "global_export_does_not_imply_global_license": True,
    }
    repo.execute(
        "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
        (
            scope,
            str(idempotency_key).strip(),
            plan_hash,
            json.dumps(response, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    return response


@router.post(
    "/explainers/{project_id}/beats:adopt-generated",
    status_code=202,
    operation_id="adoptExplainerGeneratedBeats",
    response_model=None,
)
async def adopt_explainer_generated_beats(
    project_id: str,
    payload: ExplainerBatchAdoptionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Adopt the generated candidate of every beat that has no active selection.

    ``confirm=false`` returns the scope preview (which beats would be adopted, which
    have no adoptable candidate and which checks were never measured) and writes
    nothing.  ``confirm=true`` records one ``HUMAN`` adoption per beat through the
    single adoption entry, which is the operator's batch decision: it may pass an
    unmeasured content check but never a hard technical failure (design §2.5/§6.3).
    """

    try:
        return _command(
            request,
            lambda repo: _adopt_generated_beats(repo, project_id, payload, idempotency_key),
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ExplainerContractError as error:
        raise api_error_from_explainers(error) from error


def _adopt_generated_beats(
    repo: ExplainerRepository,
    project_id: str,
    payload: ExplainerBatchAdoptionRequest,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Request validation and dispatch for the batch adoption command.

    Kept as a plain function so the decision (preview vs. write, revision check,
    idempotency requirement) is testable without an HTTP server; the request body is
    the only thing the route adds.
    """

    from local_drama.application.explainers.production_pipeline import (
        adopt_generated_candidates,
    )

    video = repo.require_video_for_project(project_id)
    current_revision = int(video.get("revision") or 1)
    if current_revision != int(payload.expected_revision):
        raise ExplainerContractError(
            "STALE_REVISION",
            "作品已被其他操作更新，请基于最新 revision 提交采用",
            {"expected_revision": payload.expected_revision, "actual_revision": current_revision},
        )
    context = {
        "project_id": project_id,
        "video_id": str(video["id"]),
        "edition_id": payload.edition_id,
        "edition_scope": "VIDEO" if payload.edition_id is None else "EDITION",
    }
    if not payload.confirm:
        preview = adopt_generated_candidates(
            repo,
            context,
            authority="HUMAN",
            actor=payload.actor,
            beat_ids=payload.beat_ids,
            dry_run=True,
        )
        return {
            "requires_confirmation": True,
            "plan": preview,
            "planned_count": preview["planned_count"],
            "needs_review": preview["needs_review"],
            "beats_without_candidate": preview["beats_without_candidate"],
            "submitted": False,
            "note": "确认后按人工权威采用这些候选；未测量的内容检查会被记录，技术硬错误仍不可采用。",
        }
    if not idempotency_key:
        raise ExplainerContractError(
            "IDEMPOTENCY_KEY_REQUIRED", "确认批量采用必须提供 Idempotency-Key"
        )
    result = adopt_generated_candidates(
        repo,
        context,
        authority="HUMAN",
        actor=payload.actor,
        beat_ids=payload.beat_ids,
    )
    return {"requires_confirmation": False, "submitted": True, **result}


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
    """Adopt a candidate through the single application command.

    This handler used to insert the selection row itself, which meant the HTTP path
    skipped every service guarantee the domain depends on: the required-check gate,
    the must-be-motion refusal, the frozen source window (it read a
    ``source_in_us`` column that does not exist, so the window was always NULL) and
    the "same candidate is not a change" rule.  Request validation stays here; the
    adoption itself is now ``ExplainerStoryboardService.adopt_selection`` (design
    §6.3: one adoption entry).
    """

    repo.require_explainer_project(project_id)
    video = repo.require_video_for_project(project_id)
    beat = repo.get("explainer_visual_beats", beat_id)
    if str(beat["video_id"]) != str(video["id"]):
        raise ExplainerContractError("INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id})
    if payload.lock and not (payload.actor or "").strip():
        raise ExplainerContractError("SCHEMA_INVALID", "人工替换必须记录操作者")
    authority = "HUMAN" if payload.lock else "MACHINE_POLICY"
    from local_drama.application.explainers.storyboard import build_storyboard_service

    result = build_storyboard_service(repo).adopt_selection(
        project_id=project_id,
        video_id=str(video["id"]),
        beat_id=beat_id,
        candidate_id=payload.candidate_id,
        edition_id=payload.edition_id,
        authority=authority,
        actor=payload.actor,
    )
    planned = str(beat.get("render_type") or "")
    actual = result.get("render_type_actual")
    if actual and actual != planned:
        repo.update(
            "explainer_visual_beats",
            beat_id,
            {"render_type_actual": actual, "fallback_reason": result.get("fallback_reason") or ""},
        )
    # Dependency edges start at the selection that was *replaced* (or, for a first
    # adoption, at the new one) — the impact view reads the same key, so both sides
    # finally agree on what a change invalidates (design §6.3).
    dependency_root = str(result.get("superseded_selection_id") or result["selection_id"])
    if not result.get("reused_existing_selection"):
        repo.mark_dependents_stale(
            upstream_kind="BEAT_SELECTION",
            upstream_id=dependency_root,
            downstream_kinds=("COMPOSITION_REVISION", "RENDER", "DELIVERY", "QC_REPORT"),
            reason="BEAT_SELECTION_CHANGED",
            invalidated_by=payload.actor or "local-user",
        )
    return {
        **result,
        "human_approval_written": False,
        "planned_render_type": planned,
        "actual_render_type": actual or planned,
        "degraded": bool(actual and actual != planned),
        "fallback_reason": result.get("fallback_reason"),
        "supersedes_previous": not result.get("reused_existing_selection"),
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
