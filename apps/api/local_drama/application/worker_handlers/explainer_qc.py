"""Explainer QC stages: ``EXPLAINER_VISUAL_QC`` and ``COMPOSITION_QC``.

This module is the job-side entry point for the quality layers the design keeps
separate on purpose:

* **technical** — full-decode coverage over the whole film;
* **subtitle** — frozen-revision text, safe area, reading rate and overlap;
* **fact** — deterministic resolution of every claim against recorded evidence;
* **semantic** — sampled frames read by a real image-reading provider;
* **depth** — a full per-frame pass over one named shot, resumable.

The coverage rules are the point of the module:

* a technical report never claims semantic coverage and a semantic report never
  claims decode coverage, so the two are reported and consumed separately;
* a layer whose real measurement port is absent is reported ``NOT_RUN`` and
  recorded as an unverified check — never as a pass;
* no handler here mints a human approval or a publication authorization.

No model is constructed in this file.  The provider, the measurement readers and
the repository/transaction ports are injected, so a unit test drives every branch
with a fake while a real run binds the runtime adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from local_drama.domain.explainers.contracts import ExplainerContractError, content_hash, is_sha256
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: The job stage codes this module serves.
QC_STAGE_CODES: tuple[str, ...] = ("EXPLAINER_VISUAL_QC", "COMPOSITION_QC")

#: The check layers a plan may request, in execution order.
QC_LAYERS: tuple[str, ...] = ("TECHNICAL", "SUBTITLE", "FACT", "SEMANTIC", "DEPTH")

#: Layer -> the port that supplies its real measurements.  A missing port means the
#: layer needs a decoder or extracted frames this build does not provide, so it is
#: reported as unverified rather than silently skipped.
LAYER_PORTS: dict[str, str] = {
    "TECHNICAL": "technical_reader",
    "SUBTITLE": "subtitle_reader",
    "FACT": "fact_reader",
    "SEMANTIC": "sampling_reader",
    "DEPTH": "sampling_reader",
}

#: The honest machine-readable status of each layer in this build.
QC_LAYER_STATUS: dict[str, str] = {
    "TECHNICAL": "WIRED_WHEN_DECODER_MEASUREMENT_PORT_IS_SUPPLIED",
    "SUBTITLE": "WIRED",
    "FACT": "WIRED",
    "SEMANTIC": "WIRED_WHEN_FRAME_SAMPLER_AND_VISUAL_PROVIDER_ARE_SUPPLIED",
    "DEPTH": "WIRED_WHEN_FRAME_SAMPLER_AND_VISUAL_PROVIDER_ARE_SUPPLIED",
}

#: Reason codes recorded when a layer cannot run.
LAYER_NOT_RUN_PREFIX = "LAYER_NOT_RUN"
PROVIDER_ABSENT_REASON = "VISUAL_QC_PROVIDER_NOT_CONFIGURED"


def qc_layer_status() -> dict[str, str]:
    """Machine-readable statement of which QC layers can run with real inputs."""

    return dict(QC_LAYER_STATUS)


def unbound_layers() -> Sequence[str]:
    """Layers that need an external port (decoder results, sampled frames)."""

    return tuple(layer for layer, status in QC_LAYER_STATUS.items() if status != "WIRED")


def _payload(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("semantic_inputs")
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "质检阶段缺少 semantic_inputs")
    return value


def _require(payload: Mapping[str, Any], key: str, *, stage: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{stage} 缺少必需输入 {key}", {"semantic_inputs": sorted(payload)}
        )
    return value


def _as_frame_range(value: Any, *, default: Sequence[int] = (0, 0)) -> tuple[int, int]:
    if value is None:
        first, second = default
        return int(first), int(second)
    if isinstance(value, Mapping):
        return int(value.get("start_frame") or 0), int(value.get("end_frame_exclusive") or 0)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    raise ExplainerContractError("SCHEMA_INVALID", "depth_frame_range 必须是 [start, end)", {"value": value})


def _normalise_layers(payload: Mapping[str, Any], *, stage: str) -> list[str]:
    raw = payload.get("layers")
    layers = [str(item).upper() for item in (raw or QC_LAYERS)]
    unknown = sorted(set(layers) - set(QC_LAYERS))
    if unknown:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{stage} 请求了不支持的质检层",
            {"unknown_layers": unknown, "known_layers": list(QC_LAYERS)},
        )
    if not layers:
        raise ExplainerContractError("SCHEMA_INVALID", f"{stage} 未请求任何质检层", {})
    return [layer for layer in QC_LAYERS if layer in set(layers)]


def _stored_hash(value: Any, *, subject_kind: str, field: str) -> str:
    """Return a stored hash, or fail loudly when the artifact has none.

    A QC subject hash must be a real sha256: the report's staleness rules compare it
    with the live artifact, so a placeholder like ``edition:1`` would be rejected by
    the quality service anyway — better to say the artifact is not hashable yet.
    """

    text = str(value or "").strip()
    if not is_sha256(text):
        raise ExplainerContractError(
            "NOT_RUN",
            "质检对象还没有可用的内容哈希，不能建立可判定新鲜度的报告",
            {"subject_kind": subject_kind, "field": field, "value": text or None},
        )
    return text


def resolve_subject_hash(repo: ExplainerRepository, subject_kind: str, subject_revision_id: str) -> str:
    """The live content hash of a QC subject.

    A report is only usable for the exact revision it examined, so the hash comes
    from the stored artifact rather than from the request.  An unknown subject kind
    raises instead of returning a placeholder, because a placeholder hash would let
    a stale report look current.
    """

    kind = str(subject_kind or "").upper()
    if kind == "COMPOSITION_RENDER":
        render = repo.get("composition_renders", subject_revision_id)
        return _stored_hash(render.get("sha256") or render.get("manifest_hash"), subject_kind=kind, field="sha256")
    if kind == "COMPOSITION_REVISION":
        composition = repo.get("composition_revisions", subject_revision_id)
        return _stored_hash(composition.get("manifest_hash"), subject_kind=kind, field="manifest_hash")
    if kind == "SUBTITLE_REVISION":
        revision = repo.get("explainer_subtitle_revisions", subject_revision_id)
        return _stored_hash(revision.get("content_hash"), subject_kind=kind, field="content_hash")
    if kind == "SCRIPT_REVISION":
        revision = repo.get("explainer_script_revisions", subject_revision_id)
        return _stored_hash(revision.get("content_hash"), subject_kind=kind, field="content_hash")
    if kind == "EDITION":
        edition = repo.get("explainer_editions", subject_revision_id)
        # An edition has no stored content hash of its own, so it is derived from
        # the frozen bindings that define it.  The derivation is a real sha256, and
        # a later re-freeze changes it, which is exactly the staleness signal.
        return content_hash(
            {
                "edition_id": str(edition.get("id") or subject_revision_id),
                "revision": edition.get("revision"),
                "revision_no": edition.get("revision_no"),
                "frozen_script_revision_id": edition.get("frozen_script_revision_id"),
                "frozen_subtitle_revision_id": edition.get("frozen_subtitle_revision_id"),
                "frozen_narration_take_ids": edition.get("frozen_narration_take_ids_json") or [],
                "voice_locale": edition.get("voice_locale"),
                "subtitle_locales": edition.get("subtitle_locales_json") or [],
                "aspect_ratio": edition.get("aspect_ratio"),
                "fps_num": edition.get("fps_num"),
                "fps_den": edition.get("fps_den"),
            }
        )
    if kind == "VISUAL_BEAT":
        beat = repo.get("explainer_visual_beats", subject_revision_id)
        return _stored_hash(beat.get("segment_hash"), subject_kind=kind, field="segment_hash")
    raise ExplainerContractError("SCHEMA_INVALID", "未知的质检对象类型", {"subject_kind": subject_kind})


def _subject(payload: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    kind = str(payload.get("subject_kind") or "EDITION").upper()
    revision_id = str(
        payload.get("subject_revision_id") or payload.get("render_id") or payload.get("edition_id") or ""
    ).strip()
    if not revision_id:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{stage} 必须指定 subject_revision_id 或 render_id",
            {"semantic_inputs": sorted(payload)},
        )
    return {
        "subject_kind": kind,
        "subject_revision_id": revision_id,
        "declared_subject_hash": str(payload.get("subject_hash") or ""),
    }


def _provider_capability(service: Any) -> dict[str, Any]:
    provider = getattr(service, "visual_provider", None)
    if provider is None:
        return {
            "available": False,
            "provider_id": None,
            "model_revision": None,
            "reads_images": False,
            "reads_video": False,
            "reason": PROVIDER_ABSENT_REASON,
        }
    capability = getattr(provider, "capability", None)
    if not callable(capability):
        return {
            "available": False,
            "provider_id": type(provider).__name__,
            "model_revision": None,
            "reads_images": False,
            "reads_video": False,
            "reason": "VISUAL_QC_PROVIDER_MISSING_CAPABILITY",
        }
    value = capability()
    return dict(value) if isinstance(value, Mapping) else {"available": False, "reason": "INVALID_CAPABILITY"}


def build_qc_handlers(
    *,
    quality_factory: Callable[[ExplainerRepository], Any],
    repo_factory: Callable[[], Any],
    technical_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    subtitle_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    fact_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    sampling_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]]:
    """The QC stage handlers, keyed by stage code, in the worker-handler shape.

    Each stage code gets its own closure so the report names the stage that really
    ran: a ``COMPOSITION_QC`` job must not write a report claiming it was the
    visual QC stage.
    """

    def make(stage: str) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
        def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
            del job
            payload = _payload(context)
            return run_qc_layers(
                payload,
                stage=stage,
                quality_factory=quality_factory,
                repo_factory=repo_factory,
                technical_reader=technical_reader,
                subtitle_reader=subtitle_reader,
                fact_reader=fact_reader,
                sampling_reader=sampling_reader,
            )

        return handler

    return {stage: make(stage) for stage in QC_STAGE_CODES}


def run_qc_layers(
    payload: Mapping[str, Any],
    *,
    quality_factory: Callable[[ExplainerRepository], Any],
    repo_factory: Callable[[], Any],
    stage: str = "EXPLAINER_VISUAL_QC",
    technical_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    subtitle_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    fact_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    sampling_reader: Callable[[ExplainerRepository, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the requested QC layers for one subject and persist every report.

    The ``*_reader`` ports supply the real measurement inputs (decode counts,
    detected anomalies, cues, claims with their evidence rows, sampled frame ids
    and their file paths).  They are injected because those numbers must come from
    real decoders and real media: a handler that invented them would be worse than
    a layer that honestly did not run.
    """

    project_id = _require(payload, "project_id", stage=stage)
    video_id = _require(payload, "video_id", stage=stage)
    edition_id = str(payload.get("edition_id") or "").strip() or None
    layers = _normalise_layers(payload, stage=stage)
    subject = _subject(payload, stage=stage)
    ports = {
        "technical_reader": technical_reader,
        "subtitle_reader": subtitle_reader,
        "fact_reader": fact_reader,
        "sampling_reader": sampling_reader,
    }

    reports: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    provider_capability: dict[str, Any] | None = None
    with repo_factory() as repo:
        service = quality_factory(repo)
        subject_hash = subject["declared_subject_hash"] or resolve_subject_hash(
            repo, subject["subject_kind"], subject["subject_revision_id"]
        )
        context: dict[str, Any] = {
            **dict(payload),
            "project_id": project_id,
            "video_id": video_id,
            "edition_id": edition_id,
            "subject_kind": subject["subject_kind"],
            "subject_revision_id": subject["subject_revision_id"],
            "subject_hash": subject_hash,
        }

        def port(name: str, layer: str) -> Any:
            reader = ports[name]
            if reader is None:
                skipped.append({"layer": layer, "reason": f"{name.upper()}_NOT_INJECTED"})
            return reader

        if "TECHNICAL" in layers:
            reader = port("technical_reader", "TECHNICAL")
            if reader is not None:
                measurements = dict(reader(repo, context))
                measurement_hash = str(measurements.pop("subject_hash", "") or "")
                reports.append(
                    service.run_technical_check(
                        project_id=project_id,
                        video_id=video_id,
                        edition_id=edition_id,
                        subject_kind=subject["subject_kind"],
                        subject_revision_id=subject["subject_revision_id"],
                        subject_hash=measurement_hash or subject_hash,
                        technical=measurements,
                    )
                )

        if "SUBTITLE" in layers:
            reader = port("subtitle_reader", "SUBTITLE")
            if reader is not None:
                measured = dict(reader(repo, context))
                reports.append(
                    service.run_subtitle_check(
                        project_id=project_id,
                        video_id=video_id,
                        edition_id=edition_id,
                        subject_hash=str(measured.pop("subject_hash", "") or subject_hash),
                        cues=measured.get("cues") or (),
                        safe_area=measured.get("safe_area") or {},
                        fps_num=int(measured.get("fps_num") or 25),
                        fps_den=int(measured.get("fps_den") or 1),
                        total_frames=int(measured.get("total_frames") or 0),
                        current_revision_id=str(measured.get("current_revision_id") or "") or None,
                    )
                )

        if "FACT" in layers:
            reader = port("fact_reader", "FACT")
            if reader is not None:
                measured = dict(reader(repo, context))
                reports.append(
                    service.run_fact_check(
                        project_id=project_id,
                        video_id=video_id,
                        subject_hash=str(measured.pop("subject_hash", "") or subject_hash),
                        claims=measured.get("claims") or (),
                        segment_claims=measured.get("segment_claims") or (),
                    )
                )

        wants_semantic = "SEMANTIC" in layers
        wants_depth = "DEPTH" in layers
        if wants_semantic or wants_depth:
            reader = port("sampling_reader", "SEMANTIC" if wants_semantic else "DEPTH")
            if reader is not None:
                sampling = dict(reader(repo, context))
                sampled_hash = str(sampling.get("subject_hash") or subject_hash)
                provider_capability = _provider_capability(service)
                plan = sampling.get("plan") or {}
                if wants_semantic:
                    reports.append(
                        service.run_semantic_check(
                            project_id=project_id,
                            video_id=video_id,
                            edition_id=edition_id,
                            subject_kind=subject["subject_kind"],
                            subject_revision_id=subject["subject_revision_id"],
                            subject_hash=sampled_hash,
                            plan=plan,
                            reference_frames=sampling.get("reference_frames") or (),
                            questions=sampling.get("questions") or (),
                        )
                    )
                if wants_depth:
                    provider = getattr(service, "visual_provider", None)
                    if provider is None:
                        skipped.append({"layer": "DEPTH", "reason": PROVIDER_ABSENT_REASON})
                    else:
                        start, end = _as_frame_range(sampling.get("depth_frame_range"))
                        reports.append(
                            service.run_depth_check(
                                project_id=project_id,
                                video_id=video_id,
                                edition_id=edition_id,
                                subject_revision_id=subject["subject_revision_id"],
                                subject_hash=sampled_hash,
                                frame_range=(start, end),
                                provider=provider,
                                already_processed=sampling.get("already_processed") or (),
                                questions=sampling.get("questions") or (),
                            )
                        )
            elif wants_depth:
                # ``port`` already recorded the sampling layer as not run.
                pass

    completed = [str(item.get("detector") or item.get("subject_kind") or "") for item in reports]
    # The stage status is derived from the layer reports, not from their count: a
    # layer that reported NOT_RUN or FAIL must not be summarised as PASS just
    # because a report row exists.  ``PASS_WITH_ISSUES`` still counts as a
    # completed layer (the issues are recorded separately), while NOT_RUN does not.
    layer_statuses = [
        str((item.get("machine_check") or {}).get("status") or item.get("status") or "").upper()
        for item in reports
    ]
    passing = {"PASS", "PASS_WITH_ISSUES", "SUCCEEDED"}
    failing = {"FAIL", "BLOCKED", "CORRUPT", "TERMINAL_FAILED"}
    unresolved = {"NOT_RUN", "PARTIAL", "NEEDS_HITL", "STALE_REVISION", "CANCELLED"}
    if not reports:
        status = "NOT_RUN"
    elif any(item in failing for item in layer_statuses):
        status = "FAIL"
    elif any(item in unresolved for item in layer_statuses):
        status = "NOT_RUN" if not any(item in passing for item in layer_statuses) else "PARTIAL"
    elif skipped:
        status = "PARTIAL"
    else:
        status = "PASS"
    report: dict[str, Any] = {
        "schema_version": "localdrama.explainer-qc-run.v1",
        "stage_code": stage,
        "project_id": project_id,
        "video_id": video_id,
        "edition_id": edition_id,
        "subject": subject,
        "subject_hash": subject_hash,
        "layers_requested": layers,
        "layers_completed": completed,
        "layer_statuses": layer_statuses,
        "layers_skipped": skipped,
        "reports": reports,
        "report_count": len(reports),
        "visual_provider": provider_capability,
        "coverage_layers_reported_separately": True,
        "decoded_is_not_semantic": True,
        "human_approval_written": False,
        "publication_authorized": False,
        "human_reviewed_claimed": False,
        "status": status,
        "machine_check": {"status": status, "ok": status == "PASS"},
        "summary": (
            f"已按层分别记录 {len(reports)} 份质检报告"
            + (f"，另有 {len(skipped)} 层因缺少真实测量端口而未运行（不计为通过）" if skipped else "")
        ),
    }
    if skipped:
        # A layer that did not run is UNCHECKED, never a pass.
        report["unverified_checks"] = [f"{LAYER_NOT_RUN_PREFIX}:{item['layer']}" for item in skipped]
    return report


# --------------------------------------------------------------------- readers
def build_qc_readers(*, database: Any = None, settings: Any = None) -> dict[str, Callable[..., Any]]:
    """Bind the measurement readers whose inputs are fully content-derived.

    Only the subtitle and fact readers qualify: their inputs are the frozen
    revision artefacts already stored in the database, read through the repository
    the caller already opened.  The technical and sampling readers need real
    decoder output and real extracted frames, so they stay unbound and the
    corresponding layer is reported as ``LAYER_NOT_RUN`` until such a port is
    supplied by the media pipeline.  ``database``/``settings`` are accepted so the
    signature matches the other runtime-adaptor binders; nothing here opens a
    second connection.
    """

    del database, settings

    def subtitle_reader(repo: ExplainerRepository, context: Mapping[str, Any]) -> dict[str, Any]:
        edition_id = str(context.get("edition_id") or "")
        edition = repo.get("explainer_editions", edition_id) if edition_id else {}
        revisions = (
            repo.list_where(
                "explainer_subtitle_revisions",
                {"edition_id": edition_id},
                order_by="revision_no",
                descending=True,
                limit=1,
            )
            if edition_id
            else []
        )
        revision = revisions[0] if revisions else {}
        layout = revision.get("layout_report_json") or {}
        if isinstance(layout, str):
            import json

            layout = json.loads(layout) if layout else {}
        cues = revision.get("cues_json") or []
        if isinstance(cues, str):
            import json

            cues = json.loads(cues) if cues else []
        # Cues stored inside a revision *are* that revision's cues, so the frozen
        # binding is filled in rather than left to each cue.  Nothing is invented:
        # the revision that owns these rows is already fixed in the database.
        revision_id = str(revision.get("id") or "")
        locale = str(revision.get("locale") or "")
        normalised_cues = []
        for cue in cues if isinstance(cues, list) else []:
            if not isinstance(cue, dict):
                normalised_cues.append(cue)
                continue
            entry = dict(cue)
            if not entry.get("subtitle_revision_id"):
                entry["subtitle_revision_id"] = revision_id
            if not entry.get("locale"):
                entry["locale"] = locale
            normalised_cues.append(entry)
        safe_area = layout.get("safe_area") or edition.get("subtitle_safe_area_json") or {}
        if isinstance(safe_area, str):
            import json

            safe_area = json.loads(safe_area) if safe_area else {}
        return {
            "cues": normalised_cues,
            "safe_area": safe_area,
            "fps_num": int(edition.get("fps_num") or 25),
            "fps_den": int(edition.get("fps_den") or 1),
            "total_frames": int(context.get("total_frames") or edition.get("total_frames") or 0),
            "current_revision_id": revision_id,
            "subject_hash": str(revision.get("content_hash") or ""),
        }

    def fact_reader(repo: ExplainerRepository, context: Mapping[str, Any]) -> dict[str, Any]:
        import json

        video_id = str(context.get("video_id") or "")
        claims = repo.list_where("explainer_claims", {"video_id": video_id}, order_by="code", descending=False)
        video = repo.get("explainer_videos", video_id)
        revision_id = str(video.get("current_script_revision_id") or context.get("script_revision_id") or "")
        segments = repo.segments(revision_id) if revision_id else []
        claim_code_by_id = {str(item["id"]): str(item["code"]) for item in claims}
        segment_claims: list[dict[str, Any]] = []
        for item in segments:
            raw_claim_ids = item.get("claim_ids_json") or []
            if isinstance(raw_claim_ids, str):
                raw_claim_ids = json.loads(raw_claim_ids) if raw_claim_ids else []
            segment_claims.append(
                {
                    "segment_id": str(item["id"]),
                    "script_revision_id": str(item["script_revision_id"]),
                    "canonical_segment_id": str(item.get("canonical_segment_id") or ""),
                    "display_text": str(item.get("display_text") or ""),
                    "statement_type": str(item.get("statement_type") or ""),
                    # ``run_fact_check`` reads ``claim_ids``/``claim_refs``; it treats a
                    # segment with none of them as an uncited FACT statement, so the
                    # field name has to be the one the detector reads.
                    "claim_ids": [
                        claim_code_by_id[str(claim_id)]
                        for claim_id in raw_claim_ids
                        if str(claim_id) in claim_code_by_id
                    ],
                }
            )
        return {
            # The fact gate resolves each claim against its recorded source spans, so
            # the evidence rows travel with the claim.  A bare count would silently
            # downgrade every claim to unsupported, which is a wrong answer rather
            # than a conservative one.
            "claims": [
                {
                    "code": str(item["code"]),
                    "claim_id": str(item["code"]),
                    "statement": str(item.get("statement") or ""),
                    "status": str(item.get("status") or ""),
                    "importance": str(item.get("importance") or ""),
                    "key_terms": item.get("key_terms_json") or [],
                    "aliases": item.get("aliases_json") or [],
                    "evidence": repo.claim_span_records(str(item["id"])),
                }
                for item in claims
            ],
            "segment_claims": segment_claims,
        }

    return {"subtitle_reader": subtitle_reader, "fact_reader": fact_reader}


def qc_handler_availability() -> dict[str, dict[str, str]]:
    """The runtime-readiness summary for the QC stages."""

    return {
        stage: {
            "handler": "WIRED",
            "technical_reader": QC_LAYER_STATUS["TECHNICAL"],
            "subtitle_reader": QC_LAYER_STATUS["SUBTITLE"],
            "fact_reader": QC_LAYER_STATUS["FACT"],
            "visual_provider": "WIRED_WHEN_FRAME_SAMPLER_AND_LOCAL_VLM_ARE_SUPPLIED",
        }
        for stage in QC_STAGE_CODES
    }
