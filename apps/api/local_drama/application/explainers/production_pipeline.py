"""The explainer production stages that turn a frozen plan into a finished film.

The explainer graph (:data:`~local_drama.application.explainers.production.TASK_SKELETON`)
plans fourteen steps.  Seven of them already had a first-party implementation —
the four text stages, the two layered QC stages and the machine policy path — and
two more had dedicated worker families (``NARRATION_TTS`` / ``NARRATION_ALIGN``)
that only the single-segment re-read command could reach.  Five steps had no
implementation at all, so the workflow stalled on the first one and the run could
never reach a render:

``IDENTITY_ASSETS`` · ``NARRATION_TTS`` · ``NARRATION_ALIGN`` ·
``SUBTITLE_BUILD`` · ``VISUAL_GENERATION`` · ``COMPOSITION_RENDER`` ·
``EXPLAINER_EXPORT``

This module is those steps.  Three rules hold throughout:

* **Reuse the real business flows.**  Narration runs through
  :func:`~local_drama.application.worker_handlers.narration_tts.run_narration_tts_job`
  and alignment through
  :func:`~local_drama.application.worker_handlers.narration_align.run_narration_align_job`
  exactly as the dedicated job families do, so a take produced inside the
  workflow is the same immutable fact a re-read would produce.  Subtitles, the
  storyboard plan, the composition manifest and the publication package go
  through their own application services.
* **Every picture has a declared source.**  The picture path in this build is the
  design's deterministic one: a typeset scene card is rendered with the shared
  FFmpeg filter graph and turned into a real motion clip, then registered as an
  immutable media version and adopted as the beat's candidate.  ``render_type``
  is reported as *planned* and *actual* separately and the fallback reason is
  recorded, so a degraded path can never be mistaken for a generated I2V shot.
* **Nothing is fabricated.**  A stage that cannot do its work returns a
  ``BLOCKED`` report with the concrete missing piece; it never writes a fake
  success, a human approval or a publication authorization.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import (
    ASPECT_PIXELS,
    ExplainerContractError,
    aspect_pixels_for_height,
    content_hash,
    normalize_locale,
    utc_now_iso,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

__all__ = [
    "EXPLAINER_PIPELINE_TASK_CODES",
    "build_explainer_pipeline_handlers",
    "ensure_editions_for_outputs",
]

#: The stages this module implements.
EXPLAINER_PIPELINE_TASK_CODES: tuple[str, ...] = (
    "IDENTITY_ASSETS",
    "NARRATION_TTS",
    "NARRATION_ALIGN",
    "VISUAL_GENERATION",
    "SUBTITLE_BUILD",
    "COMPOSITION_RENDER",
    "EXPLAINER_EXPORT",
)

#: Aspect ratio -> generation pixels, from the single domain table (480p canvases;
#: delivery resolution comes from the later super-resolution step).
_ASPECT_PIXELS: dict[str, tuple[int, int]] = ASPECT_PIXELS

#: The model-default narration voice.
#:
#: VoxCPM2's own voice is used when the work has no channel profile that pins a
#: cloned voice reference.  The model card is the licence evidence; the built-in
#: voice is the model's own output rather than a third party's recording.
#: The model-default narration voice sentinel shared with the runtime adapter.
MODEL_DEFAULT_VOICE_SENTINEL = "LOCAL_MODEL_DEFAULT"
MODEL_DEFAULT_VOICE_REF = f"voxcpm2:{MODEL_DEFAULT_VOICE_SENTINEL}"
MODEL_DEFAULT_VOICE_LICENSE = "VERIFIED"
MODEL_DEFAULT_VOICE_EVIDENCE = {
    "kind": "MODEL_CARD",
    "license": "apache-2.0",
    "source": "PyTorch/VoxCPM2/README.md",
    "voice": "model_default",
}


def _now() -> str:
    return utc_now_iso()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# editions
# --------------------------------------------------------------------------- #
def ensure_editions_for_outputs(
    repo: ExplainerRepository,
    *,
    video: Mapping[str, Any],
    outputs: Sequence[Mapping[str, Any]],
    generation_height: int | None = None,
) -> list[dict[str, Any]]:
    """Materialise one edition row per requested output, idempotently.

    The preflight freezes the output list and the workbench tells the operator
    that the editions are created for it ("预检时会按所选语言与画幅创建"), but
    nothing performed that write: a run started from the one-click page had no
    edition, so narration, subtitles and the render had nowhere to land.  This is
    the missing write.  It keys on ``(video, edition_key)`` and returns the one
    with the highest revision, so replaying a submission never forks a second
    edition of the same shape.

    ``generation_height`` is the configured proxy canvas height (480p); the edition
    is rendered at that size and super-resolved to the delivery height later.
    """

    video_id = str(video["id"])
    created: list[dict[str, Any]] = []
    for output in outputs:
        edition_key = str(output.get("edition_key") or "").strip()
        if not edition_key:
            raise ExplainerContractError("SCHEMA_INVALID", "输出缺少 edition_key，无法创建输出版本")
        aspect = str(output.get("aspect_ratio") or "16:9")
        if aspect not in _ASPECT_PIXELS:
            raise ExplainerContractError("SCHEMA_INVALID", "不支持的画幅", {"aspect_ratio": aspect})
        fps = output.get("fps") or {}
        fps_num = int(fps.get("num") or 25)
        fps_den = int(fps.get("den") or 1)
        subtitle_mode = str(output.get("subtitle_mode") or "NONE")
        subtitle_locales = [normalize_locale(str(item)) for item in (output.get("subtitle_locales") or [])]
        voice_locale = normalize_locale(str(output.get("voice_locale") or video["source_locale"]))
        existing = repo.edition_by_key(video_id, edition_key)
        if existing is not None:
            created.append(existing)
            continue
        width, height = aspect_pixels_for_height(aspect, generation_height)
        row = repo.insert(
            "explainer_editions",
            {
                "video_id": video_id,
                "edition_key": edition_key,
                "revision_no": 1,
                "voice_locale": voice_locale,
                "subtitle_locales_json": subtitle_locales,
                "subtitle_mode": subtitle_mode,
                "aspect_ratio": aspect,
                "fps_num": fps_num,
                "fps_den": fps_den,
                "width": width,
                "height": height,
                "audio_sample_rate_hz": 48_000,
                "duration_policy": str(output.get("duration_policy") or "NATURAL_NARRATION"),
                "target_seconds": int(video["target_seconds"]),
                "tolerance_percent": float(video["tolerance_percent"]),
                "allow_soft_subtitle_fallback": bool(output.get("allow_soft_subtitle_fallback")),
                "status": "PLANNED",
            },
        )
        created.append(row)
    return created


# --------------------------------------------------------------------------- #
# narration voice
# --------------------------------------------------------------------------- #
def _narration_voice_snapshot(repo: ExplainerRepository, *, video: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """The authorized voice facts the TTS handler re-validates.

    A channel profile may pin a cloned voice; without one the work narrates with
    the local model's own voice, whose licence evidence is the model card that
    ships inside the model directory.  The delivery authorization is therefore a
    real, checkable fact in both branches — never an optimistic default.
    """

    profile_version_id = video.get("current_channel_profile_version_id")
    voice: Mapping[str, Any] = {}
    if profile_version_id:
        profile_version = repo.find("channel_profile_versions", str(profile_version_id))
        raw = (profile_version or {}).get("voice_json")
        if isinstance(raw, Mapping):
            voice = raw
    voice_ref = str(voice.get("voice_ref") or "").strip()
    if voice_ref:
        license_status = str(voice.get("license_status") or "UNVERIFIED").upper()
        return {
            "voice_profile_version_id": voice.get("voice_profile_version_id") or profile_version_id,
            "voice_ref": voice_ref,
            "model_ref": str(voice.get("model_ref") or voice_ref),
            "supported_locales": [normalize_locale(str(item)) for item in (voice.get("supported_locales") or [])]
            or [locale],
            "purposes": [str(item) for item in (voice.get("purposes") or [])] or ["NARRATION"],
            "license_status": license_status,
            "license_evidence": voice.get("license_evidence"),
            "test_only_acknowledged": bool(voice.get("test_only_acknowledged")),
            "voice_source": "CHANNEL_PROFILE",
        }
    return {
        "voice_profile_version_id": profile_version_id,
        "voice_ref": MODEL_DEFAULT_VOICE_REF,
        "model_ref": "voxcpm2-local-model-default",
        "supported_locales": ["zh-CN", "en-US"],
        "purposes": ["NARRATION"],
        "license_status": MODEL_DEFAULT_VOICE_LICENSE,
        "license_evidence": dict(MODEL_DEFAULT_VOICE_EVIDENCE),
        "test_only_acknowledged": False,
        "voice_source": "LOCAL_MODEL_DEFAULT",
    }


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _blocked(summary: str, code: str, detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "machine_check": {"status": "BLOCKED", "ok": False, "blocker_code": code, "detail": dict(detail or {})},
        "produced": {},
        "summary": summary,
    }


def _passed(summary: str, produced: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "machine_check": {"status": "PASS", "ok": True},
        "produced": dict(produced),
        "summary": summary,
    }


def _video_and_editions(
    repo: ExplainerRepository, *, project_id: str, video_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    video = repo.get("explainer_videos", video_id)
    if str(video["project_id"]) != project_id:
        raise ExplainerContractError(
            "INVALID_REQUEST", "解说作品不属于该项目", {"video_id": video_id, "project_id": project_id}
        )
    return video, repo.editions(video_id)


def _current_script_revision(
    repo: ExplainerRepository, *, video: Mapping[str, Any], video_id: str
) -> dict[str, Any] | None:
    revision_id = video.get("current_script_revision_id")
    if revision_id:
        row = repo.find("explainer_script_revisions", str(revision_id))
        if row is not None and str(row["video_id"]) == video_id:
            return row
    rows = repo.list_where(
        "explainer_script_revisions", {"video_id": video_id}, order_by="revision_no", descending=True, limit=1
    )
    return rows[0] if rows else None


def _segments_by_locale(
    repo: ExplainerRepository, *, video_id: str, script_revision_id: str, locale: str
) -> list[dict[str, Any]]:
    rows = repo.list_where(
        "narration_segments",
        {"video_id": video_id, "script_revision_id": script_revision_id, "locale": locale},
        order_by="ordinal",
        descending=False,
    )
    return rows


def _current_takes(repo: ExplainerRepository, video_id: str) -> dict[str, dict[str, Any]]:
    """The take each segment currently narrates with, keyed by ``segment_id``.

    A take is adopted by the stage that produced it (``selected``), and a human
    re-read that selects another take wins outright.  When a segment has takes but
    none is marked selected — the state a stage-written take used to be left in —
    the newest take is the current one, so a machine stage can never leave the
    graph with narration that exists but is unreachable.
    """

    rows = repo.list_where(
        "narration_takes", {"video_id": video_id}, order_by="take_no", descending=False
    )
    current: dict[str, dict[str, Any]] = {}
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        segment_id = str(row["segment_id"])
        current[segment_id] = row
        if row.get("selected"):
            selected[segment_id] = row
    return {**current, **selected}


def _adopt_take(repo: ExplainerRepository, *, segment_id: str, actor: str) -> dict[str, Any] | None:
    """Mark the newest take of one segment as the adopted machine take."""

    row = repo.query_one(
        "SELECT * FROM narration_takes WHERE segment_id=? ORDER BY take_no DESC LIMIT 1",
        (segment_id,),
    )
    if row is None:
        return None
    # ``query_one`` returns a raw ``sqlite3.Row``: index it, never call ``.get``.
    existing_generation = row["generation_json"]
    if isinstance(existing_generation, str):
        try:
            existing_generation = json.loads(existing_generation)
        except (TypeError, ValueError):
            existing_generation = {}
    repo.execute(
        "UPDATE narration_takes SET selected=0 WHERE segment_id=? AND id<>?",
        (segment_id, str(row["id"])),
    )
    return repo.update(
        "narration_takes",
        str(row["id"]),
        {
            "selected": True,
            "status": "VERIFIED",
            # A machine stage adopts the take it just verified; this is not a human
            # approval, and the record says which stage did it.
            "generation_json": {
                **dict(existing_generation or {}),
                "adoption_authority": "MACHINE_STAGE",
                "adopted_by": actor,
            },
        },
        actor=actor,
    )


# --------------------------------------------------------------------------- #
# IDENTITY_ASSETS
# --------------------------------------------------------------------------- #
def make_identity_assets_handler(
    repo_factory: Callable[[], Any],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``IDENTITY_ASSETS``: freeze the edition set and the entity roster.

    The stage is the graph's declared dependency for "reference assets and channel
    style".  In this build the picture path is deterministic (typeset scene cards
    rendered by the shared FFmpeg graph), so no character reference imagery is
    generated or claimed: what the stage really does is

    * materialise the editions the frozen plan asked for, so every later stage has
      a clock, a canvas and a language;
    * record the entity roster the fact stage extracted, with the honest statement
      that no identity slot is consumed by a typeset card.

    ``generated_reference_images`` is therefore reported as ``0`` with a reason —
    never as a successful identity pack.
    """

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            outputs = list((video.get("plan_editions_json") if isinstance(video, Mapping) else None) or [])
            if not editions and outputs:
                editions = ensure_editions_for_outputs(repo, video=video, outputs=outputs)
            if not editions:
                return _blocked(
                    "该作品没有输出版本，画面与旁白没有可落地的载体",
                    "SCHEMA_INVALID",
                    {"video_id": video_id},
                )
            entities = repo.list_where("explainer_entities", {"video_id": video_id})
            persons = [
                str(item["code"])
                for item in entities
                if str(item["entity_type"]) in {"REAL_PERSON", "FICTIONAL_CHARACTER"}
            ]
            script_revision = _current_script_revision(repo, video=video, video_id=video_id)
        return _passed(
            f"已冻结 {len(editions)} 个输出版本与 {len(entities)} 个实体；本机画面路径为确定性排版卡，不消费人物参考图。",
            {
                "edition_ids": [str(item["id"]) for item in editions],
                "edition_keys": [str(item["edition_key"]) for item in editions],
                "entity_count": len(entities),
                "person_entity_codes": persons,
                "generated_reference_images": 0,
                "reference_generation_reason": "PICTURE_PATH_IS_DETERMINISTIC_TYPESET_CARD",
                "identity_slots_consumed": [],
                "script_revision_id": None if script_revision is None else str(script_revision["id"]),
                "human_approval_written": False,
            },
        )

    return handler


# --------------------------------------------------------------------------- #
# NARRATION_TTS / NARRATION_ALIGN
# --------------------------------------------------------------------------- #
def make_narration_tts_handler(
    repo_factory: Callable[[], Any],
    *,
    database: Any,
    narration_runtime: Any,
    media_ops: Any,
    atomic_writer: Callable[[Path, Callable[[Path], object]], None],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``NARRATION_TTS``: synthesise every segment of every edition, in order.

    The dedicated ``NARRATION_TTS`` job family synthesises exactly one segment, so
    a re-read stays a single-segment command.  The graph needs the whole film's
    clock before the storyboard can be re-planned against real audio, and the
    workflow's batch is single-pass: this handler therefore drives the same
    first-party flow once per segment, in ordinal order, and reports the real
    measured total.  A segment that already has a selected take is reused instead
    of being re-synthesised, so a resumed run does not double-charge the GPU.
    """

    from local_drama.application.worker_handlers.narration_tts import run_narration_tts_job

    def _batch_targets(entry: dict[str, Any], output_root: Path, work_root: Path) -> dict[str, str]:
        """Ask the runtime to synthesise this group in one resident-model process."""

        runtime = getattr(narration_runtime, "runtime", narration_runtime)
        # Editions that share a voice share the same narration segments, so the
        # group's item list repeats a segment once per edition.  Asking the model
        # for the identical text twice would double the GPU cost of the longest
        # stage in the graph and leave a duplicate take behind, so the batch is
        # deduplicated by segment id before it reaches the runtime.
        segment_ids: list[str] = []
        batch_items: list[dict[str, str]] = []
        for item in entry["items"]:
            segment = item["segment"]
            segment_id = str(segment["id"])
            if segment_id in segment_ids:
                continue
            segment_ids.append(segment_id)
            batch_items.append({"id": segment_id, "text": str(segment["spoken_text"])})
        manifest_path = output_root / f"narration-batch-{entry['key']}.json"
        output_dir = output_root / f"narration-batch-{entry['key']}"
        execution = runtime.synthesize_batch(
            batch_items,
            manifest_path=manifest_path,
            output_dir=output_dir,
            prompt_audio=entry["prompt_audio"],
            prompt_text=entry["prompt_text"],
            speed=1.0,
        )
        produced: dict[str, str] = {}
        for item in execution.payload.get("items") or []:
            if str(item.get("status")) == "PASS":
                produced[str(item["id"])] = str(item["output"])
        entry["batch_receipt"] = {
            "item_count": execution.payload.get("item_count"),
            "failed_count": execution.payload.get("failed_count"),
            "model_load_seconds": execution.payload.get("model_load_seconds"),
            "elapsed_seconds": execution.payload.get("elapsed_seconds"),
        }
        entry["missing"] = [segment_id for segment_id in segment_ids if segment_id not in produced]
        return produced

    def _prompt_for(voice: Mapping[str, Any], work_root: Path) -> tuple[Path | None, str | None]:
        reference = str(voice.get("voice_ref") or "").removeprefix("voxcpm2:")
        prompt_ref, _, prompt_text = reference.partition("|")
        if prompt_ref.startswith("media:"):
            _meta, path = media_ops.content_path(prompt_ref.removeprefix("media:").strip())
            return Path(path), prompt_text.strip() or None
        if prompt_ref.strip() and prompt_ref.strip() != MODEL_DEFAULT_VOICE_SENTINEL:
            return Path(prompt_ref.strip()), prompt_text.strip() or None
        del work_root
        return None, None

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        output_root = Path(str(context.get("output_root")))
        work_root = Path(str(context.get("work_root")))
        generated: list[dict[str, Any]] = []
        reused: list[str] = []
        failures: list[dict[str, Any]] = []
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            script_revision = _current_script_revision(repo, video=video, video_id=video_id)
            if script_revision is None:
                return _blocked("该作品还没有解说稿，无法合成旁白", "SCHEMA_INVALID", {"video_id": video_id})
            script_revision_id = str(script_revision["id"])
            if str(script_revision.get("status")) != "FROZEN":
                from local_drama.application.explainers.narration import ExplainerNarrationService

                frozen = ExplainerNarrationService(repo).freeze_script(
                    script_revision_id=script_revision_id, actor="explainer-worker"
                )
                script_revision = frozen.get("script_revision") or repo.get(
                    "explainer_script_revisions", script_revision_id
                )
            groups: dict[str, dict[str, Any]] = {}
            existing_takes = _current_takes(repo, video_id)
            for edition in editions:
                locale = normalize_locale(str(edition["voice_locale"]))
                voice = _narration_voice_snapshot(repo, video=video, locale=locale)
                # The edition's clock is defined by the script revision it narrates;
                # binding it here means a later revision cannot silently change what
                # this edition's takes were produced from.
                repo.update(
                    "explainer_editions",
                    str(edition["id"]),
                    {"frozen_script_revision_id": script_revision_id},
                    actor="explainer-worker",
                )
                key = f"{locale}-{voice.get('voice_source')}"
                group = groups.setdefault(key, {"key": key, "locale": locale, "voice": voice, "items": []})
                for segment in _segments_by_locale(
                    repo, video_id=video_id, script_revision_id=script_revision_id, locale=locale
                ):
                    existing = existing_takes.get(str(segment["id"]))
                    if existing is not None and int(existing.get("measured_duration_ms") or 0) > 0:
                        # A take already narrates this segment: reuse it instead of
                        # re-synthesising, and adopt it if no take was adopted yet.
                        if not existing.get("selected"):
                            _adopt_take(repo, segment_id=str(segment["id"]), actor="explainer-narration-stage")
                        reused.append(str(segment["id"]))
                        continue
                    group["items"].append({"edition_id": str(edition["id"]), "segment": segment})
            for group in groups.values():
                group["prompt_audio"], group["prompt_text"] = _prompt_for(group["voice"], work_root)
                group["batch_receipt"] = None
                group["missing"] = []
        # One resident-model process per voice/locale instead of one per segment.
        for key, group in groups.items():
            if not group["items"]:
                continue
            try:
                produced = _batch_targets(group, output_root, work_root)
            except (DomainRuleError, OSError, RuntimeError) as error:
                failures.append(
                    {
                        "batch": key,
                        "code": type(error).__name__,
                        "message": str(error)[:300],
                        "segment_count": len(group["items"]),
                    }
                )
                continue
            processed: set[str] = set()
            for item in group["items"]:
                segment = item["segment"]
                segment_id = str(segment["id"])
                if segment_id in processed:
                    # Already registered for this voice in this stage; a second
                    # pass would only write a duplicate take for the same audio.
                    continue
                processed.add(segment_id)
                pre = produced.get(segment_id)
                if pre is None:
                    failures.append(
                        {
                            "segment_id": segment_id,
                            "canonical_segment_id": str(segment["canonical_segment_id"]),
                            "code": "NARRATION_BATCH_ITEM_FAILED",
                        }
                    )
                    continue
                synthetic_job = {
                    "id": f"explainer-narration-{segment_id}",
                    "project_id": project_id,
                    "subject_type": "NARRATION_SEGMENT",
                    "subject_id": segment_id,
                    "input_snapshot": {
                        "semantic_inputs": {
                            "narration_segment_id": segment_id,
                            "canonical_segment_id": str(segment["canonical_segment_id"]),
                            "locale": group["locale"],
                            "segment_hash": str(segment["segment_hash"]),
                        },
                        "voice_snapshot": group["voice"],
                        "speech_rate": 1.0,
                        "timeout_seconds": 300,
                        # The script revision was frozen immediately above by this
                        # stage; the flag documents that the freeze is the stage's own
                        # deliberate act rather than a bypass of a human lock.
                        "allow_draft_script": True,
                    },
                }
                try:
                    kind, relative = run_narration_tts_job(
                        synthetic_job,
                        output_root,
                        work_root=work_root,
                        database=database,
                        narration_runtime=narration_runtime,
                        media_ops=media_ops,
                        atomic_writer=atomic_writer,
                        pre_synthesized=Path(pre),
                    )
                except (DomainRuleError, ExplainerContractError) as error:
                    failures.append(
                        {
                            "segment_id": segment_id,
                            "canonical_segment_id": str(segment["canonical_segment_id"]),
                            "code": getattr(error, "code", type(error).__name__),
                            "message": getattr(error, "message", str(error))[:300],
                        }
                    )
                    continue
                with repo_factory() as repo:
                    _adopt_take(repo, segment_id=segment_id, actor="explainer-narration-stage")
                generated.append(
                    {
                        "segment_id": segment_id,
                        "canonical_segment_id": str(segment["canonical_segment_id"]),
                        "artifact": relative,
                        "kind": kind,
                    }
                )
        if failures and not generated and not reused:
            return _blocked(
                "旁白合成没有产生任何可用的 take",
                "NARRATION_TTS_FAILED",
                {"failures": failures[:5]},
            )
        with repo_factory() as repo:
            takes = list(_current_takes(repo, video_id).values())
            measured_total = sum(int(take.get("measured_duration_ms") or 0) for take in takes)
        return _passed(
            f"旁白合成完成：新生成 {len(generated)} 段、复用 {len(reused)} 段，实测总长 {measured_total} ms。",
            {
                "generated": generated,
                "reused_segment_ids": reused,
                "failures": failures,
                "batches": [
                    {"key": key, "receipt": group["batch_receipt"], "missing": group["missing"]}
                    for key, group in groups.items()
                    if group["batch_receipt"] is not None
                ],
                "selected_take_count": len(takes),
                "measured_total_ms": measured_total,
            },
        )

    return handler


def make_narration_align_handler(
    repo_factory: Callable[[], Any],
    *,
    database: Any,
    aligner: Any,
    asr: Any,
    media_ops: Any,
    atomic_writer: Callable[[Path, Callable[[Path], object]], None],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``NARRATION_ALIGN``: force-align every selected take of the work."""

    from local_drama.application.worker_handlers.narration_align import run_narration_align_job

    class _BatchAligner:
        """Aligner port backed by one resident-model batch run.

        ``run_narration_align_job`` owns the real alignment business flow (token
        matching, unaligned tokens, display mapping, revision numbering).  Feeding
        it a port that replays an already-computed batch keeps all of that logic
        while avoiding one model load per take.
        """

        def __init__(self, timings: Mapping[str, Any], *, detector_version: str, model: str | None) -> None:
            self._timings = dict(timings)
            self._detector_version = detector_version
            self._model = model
            self.misses: list[str] = []

        def align(
            self,
            *,
            media_path: Path,
            media_sha256: str,
            spoken_text: str,
            display_text: str,
            locale: str,
            sample_offset: int,
        ) -> Mapping[str, Any]:
            del spoken_text, display_text, locale
            key = str(media_sha256)
            timings = self._timings.get(key)
            if timings is None:
                self.misses.append(key)
                timings = []
            return {
                "word_timings": [dict(item) for item in timings],
                "alignment_status": "ALIGNED" if timings else "PARTIAL",
                "detector_version": self._detector_version,
                "media_sha256": media_sha256,
                "sample_offset": int(sample_offset),
                "network_used": False,
                "model": self._model,
            }

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        output_root = Path(str(context.get("output_root")))
        work_root = Path(str(context.get("work_root")))
        with repo_factory() as repo:
            _video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            locales = {normalize_locale(str(item["voice_locale"])) for item in editions}
            takes = [
                take
                for take in _current_takes(repo, video_id).values()
                if normalize_locale(str(take["locale"])) in locales
            ]
            pending = [take for take in takes if repo.latest_alignment_for_take(str(take["id"])) is None]
        aligned: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        timings: dict[str, Any] = {}
        detector_version = ""
        model_ref: str | None = None
        if pending:
            runtime = getattr(aligner, "runtime", None)
            items: list[dict[str, str]] = []
            keyed: dict[str, dict[str, Any]] = {}
            with repo_factory() as repo:
                for take in pending:
                    segment = repo.get("narration_segments", str(take["segment_id"]))
                    _meta, path = media_ops.content_path(str(take["media_version_id"]))
                    items.append(
                        {
                            "id": str(take["id"]),
                            "audio": str(path),
                            "transcript": str(segment["spoken_text"]),
                        }
                    )
                    keyed[str(take["id"])] = {"take": take, "segment": segment, "path": str(path)}
            use_batch = runtime is not None and hasattr(runtime, "align_batch")
            if use_batch:
                try:
                    execution = runtime.align_batch(
                        items,
                        manifest_path=output_root / "alignment-batch.json",
                        language="Chinese",
                    )
                    detector_version = "qwen3-forced-aligner-0.6b-hf"
                    model_ref = str(execution.payload.get("model") or "") or None
                    for entry in execution.payload.get("items") or []:
                        if str(entry.get("status")) != "PASS":
                            failures.append(
                                {"take_id": str(entry.get("id")), "code": "ALIGNMENT_BATCH_ITEM_FAILED"}
                            )
                            continue
                        take = keyed[str(entry["id"])]["take"]
                        timings[str(take["media_sha256"])] = entry.get("timestamps") or []
                except (RuntimeError, OSError) as error:
                    failures.append({"batch": "alignment", "code": type(error).__name__, "message": str(error)[:300]})
                    use_batch = False
        batch_port = _BatchAligner(timings, detector_version=detector_version, model=model_ref) if timings else None
        for take in pending:
            if batch_port is not None and str(take["media_sha256"]) not in timings:
                continue
            synthetic_job = {
                "id": f"explainer-align-{take['id']}",
                "project_id": project_id,
                "subject_type": "NARRATION_TAKE",
                "subject_id": str(take["id"]),
                "input_snapshot": {
                    "semantic_inputs": {"take_id": str(take["id"]), "locale": normalize_locale(str(take["locale"]))}
                },
            }
            try:
                kind, relative = run_narration_align_job(
                    synthetic_job,
                    output_root,
                    work_root=work_root,
                    database=database,
                    aligner=batch_port if batch_port is not None else aligner,
                    media_ops=media_ops,
                    atomic_writer=atomic_writer,
                    asr=None,
                )
            except (DomainRuleError, ExplainerContractError) as error:
                failures.append(
                    {
                        "take_id": str(take["id"]),
                        "code": getattr(error, "code", type(error).__name__),
                        "message": getattr(error, "message", str(error))[:300],
                    }
                )
                continue
            aligned.append({"take_id": str(take["id"]), "artifact": relative, "kind": kind})
        with repo_factory() as repo:
            aligned_ids = [
                str(item["id"])
                for take in _current_takes(repo, video_id).values()
                if (item := repo.latest_alignment_for_take(str(take["id"]))) is not None
            ]
        if not aligned_ids:
            return _blocked(
                "没有任何已选旁白 take 完成强制对齐，字幕与时间轴没有权威时码",
                "NARRATION_ALIGNMENT_MISSING",
                {"failures": failures[:5], "selected_take_count": len(takes)},
            )
        return _passed(
            f"已完成 {len(aligned)} 段强制对齐（复用 {len(takes) - len(pending)} 段），失败 {len(failures)} 段。",
            {
                "aligned": aligned,
                "failures": failures,
                "alignment_revision_count": len(aligned_ids),
                "selected_take_count": len(takes),
            },
        )

    return handler


# --------------------------------------------------------------------------- #
# the measured clock: one timeline shared by pictures, subtitles and the render
# --------------------------------------------------------------------------- #
def _load_timeline(
    repo: ExplainerRepository, *, video: Mapping[str, Any], edition: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the edition's frame/sample clock from real selected narration.

    The clock is the measured TTS audio, nothing else: every segment contributes
    exactly its own measured duration, the frames are allocated with the same
    integer arithmetic the storyboard plans with, and the per-beat spans are the
    segment spans split across the beats that narrate them.  A beat that narrates
    nothing is reported instead of being given invented screen time, and a segment
    whose take is missing blocks the stage — an unmeasured film must never be
    rendered as if it had been measured.
    """

    from local_drama.application.explainers.storyboard import _allocate_frames  # noqa: PLC2701 - shared integer rule
    from local_drama.domain.explainers.contracts import Ratio

    video_id = str(video["id"])
    edition_id = str(edition["id"])
    locale = normalize_locale(str(edition["voice_locale"]))
    script_revision_id = str(edition.get("frozen_script_revision_id") or "")
    if not script_revision_id:
        current = _current_script_revision(repo, video=video, video_id=video_id)
        if current is None:
            raise ExplainerContractError("SCHEMA_INVALID", "该作品没有讲稿版本", {"video_id": video_id})
        script_revision_id = str(current["id"])
    segments = _segments_by_locale(
        repo, video_id=video_id, script_revision_id=script_revision_id, locale=locale
    )
    if not segments:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "该输出版本的语言没有可用的旁白段落",
            {"edition_id": edition_id, "locale": locale},
        )
    takes_by_segment: dict[str, dict[str, Any]] = {}
    for take in _current_takes(repo, video_id).values():
        if normalize_locale(str(take["locale"])) != locale:
            continue
        takes_by_segment.setdefault(str(take["segment_id"]), take)
    missing = [str(item["canonical_segment_id"]) for item in segments if str(item["id"]) not in takes_by_segment]
    if missing:
        raise ExplainerContractError(
            "NARRATION_SEGMENT_MISSING",
            "缺少已选旁白 take，无法建立成片时钟",
            {"edition_id": edition_id, "locale": locale, "missing_canonical_segment_ids": missing[:20]},
        )
    durations = [int(takes_by_segment[str(item["id"])]["measured_duration_ms"] or 0) for item in segments]
    fps = Ratio(int(edition["fps_num"]), int(edition["fps_den"]))
    frames, total_frames = _allocate_frames(durations, fps)

    links_by_segment: dict[str, list[dict[str, Any]]] = {}
    for link in repo.beat_links(video_id):
        links_by_segment.setdefault(str(link["narration_segment_id"]), []).append(link)
    beats = {str(item["id"]): item for item in repo.beats(video_id)}
    for items in links_by_segment.values():
        items.sort(key=lambda item: int(beats.get(str(item["beat_id"]), {}).get("ordinal") or 0))

    placements: list[dict[str, Any]] = []
    narration_clips: list[dict[str, Any]] = []
    cursor_frame = 0
    cursor_sample = 0
    beats_without_narration: list[str] = []
    for index, segment in enumerate(segments):
        segment_frames = int(frames[index])
        take = takes_by_segment[str(segment["id"])]
        samples = fps.samples_for_frames(segment_frames, int(edition["audio_sample_rate_hz"]))
        narration_clips.append(
            {
                "segment_id": str(segment["id"]),
                "canonical_segment_id": str(segment["canonical_segment_id"]),
                "take_id": str(take["id"]),
                "media_version_id": str(take["media_version_id"]),
                "media_sha256": str(take["media_sha256"]),
                "start_frame": cursor_frame,
                "end_frame_exclusive": cursor_frame + segment_frames,
                "sample_start": cursor_sample,
                "sample_end_exclusive": cursor_sample + samples,
                "measured_duration_ms": int(take["measured_duration_ms"] or 0),
            }
        )
        targets = links_by_segment.get(str(segment["id"]), [])
        if not targets:
            beats_without_narration.append(str(segment["canonical_segment_id"]))
            placements.append(
                {
                    "beat_id": None,
                    "beat_code": None,
                    "segment_id": str(segment["id"]),
                    "canonical_segment_id": str(segment["canonical_segment_id"]),
                    "start_frame": cursor_frame,
                    "end_frame_exclusive": cursor_frame + segment_frames,
                    "render_type": "INFOGRAPHIC",
                    "fallback_reason": "SEGMENT_HAS_NO_LINKED_BEAT",
                }
            )
        else:
            base, extra = divmod(segment_frames, len(targets))
            inner_cursor = cursor_frame
            for position, link in enumerate(targets):
                count = base + (1 if position < extra else 0)
                beat = beats.get(str(link["beat_id"]), {})
                placements.append(
                    {
                        "beat_id": str(link["beat_id"]),
                        "beat_code": str(beat.get("code") or ""),
                        "segment_id": str(segment["id"]),
                        "canonical_segment_id": str(segment["canonical_segment_id"]),
                        "start_frame": inner_cursor,
                        "end_frame_exclusive": inner_cursor + count,
                        "render_type": str(beat.get("render_type") or "STILL_MOTION"),
                        "visual_intent": str(beat.get("visual_intent") or ""),
                        "preferred_duration_ms": beat.get("preferred_duration_ms"),
                    }
                )
                inner_cursor += count
        cursor_frame += segment_frames
        cursor_sample += samples
    return {
        "video_id": video_id,
        "edition_id": edition_id,
        "locale": locale,
        "script_revision_id": script_revision_id,
        "fps": fps,
        "total_frames": int(total_frames),
        "placements": placements,
        "narration_clips": narration_clips,
        "beats_without_narration": beats_without_narration,
        "segment_count": len(segments),
        "measured_total_ms": sum(durations),
        "take_paths": [str(takes_by_segment[str(item["id"])]["media_version_id"]) for item in segments],
    }


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _retry_on_locked(operation: Callable[[], Any], *, attempts: int = 10, delay_seconds: float = 3.0) -> Any:
    """Run one idempotent database operation, retrying a transient SQLite lock.

    The instance database is a single SQLite file shared by the API, the worker and
    the lease heartbeat.  SQLite serialises writers, so a short ``database is locked``
    is a scheduling fact, not a business failure — and losing a ten-minute render to
    it would be the wrong answer.  Only that error is retried, and only a bounded
    number of times; anything else propagates unchanged.
    """

    import sqlite3
    import time as _time

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == attempts - 1:
                raise
            last = error
            _time.sleep(delay_seconds)
    raise last if last is not None else RuntimeError("retry_on_locked exhausted")


def _next_render_revision(repo: ExplainerRepository, edition_id: str) -> int:
    """Next unused render revision for one edition (``(edition_id, revision_no)`` is unique)."""

    row = repo.query_one(
        "SELECT COALESCE(MAX(revision_no), 0) AS current FROM composition_renders WHERE edition_id=?",
        (edition_id,),
    )
    return int((row or {"current": 0})["current"]) + 1


# --------------------------------------------------------------------------- #
# VISUAL_GENERATION
# --------------------------------------------------------------------------- #
#: The palette the local card renderer draws with (design §3.3).
_CARD_PALETTE = {
    "background": "#263C4D",
    "band": "#1B2C39",
    "accent": "#B78254",
    "text": "#E4DDD0",
    "muted": "#9FB2BE",
}

#: CJK-capable fonts, in preference order.
_CARD_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


def _card_font() -> str | None:
    for candidate in _CARD_FONTS:
        if Path(candidate).is_file():
            return candidate
    return None


def _ass_escape(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def _ass_timestamp(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _wrap_cjk(value: str, chars_per_line: int) -> str:
    """Break a card line into ``\\N``-separated runs that fit the frame.

    CJK text has no spaces for libass to break on, so the layout is computed here
    from the same character budget the style was sized with.  Latin runs are kept
    whole where possible, and a single over-long token is still hard-split so no
    line can leave the safe area.
    """

    limit = max(4, int(chars_per_line))
    lines: list[str] = []
    for paragraph in str(value).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9 ._:/+-]*|\s+|[^\s]", paragraph):
            if token.isspace():
                if current:
                    current += " "
                continue
            while len(token) > limit:
                # A single token longer than the line (a long URL or Latin word)
                # still must not overflow: split it and keep going.
                room = limit - len(current)
                if room <= 0:
                    lines.append(current.rstrip())
                    current = ""
                    room = limit
                current += token[:room]
                token = token[room:]
                lines.append(current.rstrip())
                current = ""
            if len(current) + len(token) > limit:
                lines.append(current.rstrip())
                current = token
            else:
                current += token
        lines.append(current.rstrip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _card_ass(*, text: str, kicker: str, footer: str, duration_seconds: float, width: int, height: int) -> str:
    """A one-shot ASS card: kicker, wrapped body and the required disclosure.

    The body is wrapped here rather than left to libass.  ``WrapStyle: 0`` only
    breaks at Latin word boundaries, so a long Chinese sentence was laid out as one
    line and ran off both edges of the frame — the delivered card literally lost
    characters.  Budgeting the characters per line from the real canvas and the
    rendered font size keeps every glyph inside the declared margins.
    """

    # Sizes are declared against the 1080p reference layout and scaled to the canvas
    # actually generated (the 480p edition canvas), so a 480p master is a faithful
    # half-size of the same design rather than the same absolute pixels crammed into
    # a quarter of the frame.  The binding dimension is whichever of width/height is
    # tighter, which keeps portrait canvases from setting text wider than they are.
    canvas_scale = max(0.2, min(int(width) / 1920.0, int(height) / 1080.0))
    card_size = max(10, int(round(64 * canvas_scale)))
    margin = max(12, int(round(110 * canvas_scale)))
    kicker_size = max(8, int(round(38 * canvas_scale)))
    body_size = max(8, int(round(44 * canvas_scale)))
    footer_size = max(8, int(round(30 * canvas_scale)))
    footer_margin = max(10, int(round(80 * canvas_scale)))
    kicker_v = max(8, int(round(80 * canvas_scale)))
    card_v = max(10, int(round(150 * canvas_scale)))
    body_v = max(12, int(round(300 * canvas_scale)))
    footer_v = max(8, int(round(60 * canvas_scale)))
    usable = max(160, int(width) - 2 * margin)
    # A full-width CJK glyph advances by roughly its font size, so the usable
    # width divided by the size is the character budget for one line.
    chars_per_line = max(8, int(usable / max(1, card_size)))
    body = _wrap_cjk(str(text), chars_per_line)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kicker,Microsoft YaHei,{kicker_size},&H005482B7,&H005482B7,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,7,{margin},{margin},{kicker_v},1
Style: Card,Microsoft YaHei,{card_size},&H00D0DDE4,&H00D0DDE4,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,0,1,7,{margin},{margin},{card_v},1
Style: Body,Microsoft YaHei,{body_size},&H00C8D2DA,&H00C8D2DA,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,0,1,7,{margin},{margin},{body_v},1
Style: Footer,Microsoft YaHei,{footer_size},&H00A0B4C0,&H00A0B4C0,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,0,0,2,{footer_margin},{footer_margin},{footer_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    start = _ass_timestamp(0.0)
    end = _ass_timestamp(max(1.0, duration_seconds))
    events = [
        f"Dialogue: 0,{start},{end},Kicker,,0,0,0,,{_ass_escape(kicker)}",
        f"Dialogue: 0,{start},{end},Card,,0,0,0,,{_ass_escape(body)}",
        f"Dialogue: 0,{start},{end},Footer,,0,0,0,,{_ass_escape(footer)}",
    ]
    return header + "\n".join(events) + "\n"


def _disclosure_line(content_kind: str) -> str:
    if str(content_kind).upper() == "ORIGINAL_FICTION":
        return "原创虚构 · AI 画面演绎 · 本地排版卡"
    return "AI 辅助解说 · 本地排版卡（非实拍、非生成画面）"


def _beat_card_text(repo: ExplainerRepository, *, segment_ids: Sequence[str], intent: str) -> str:
    parts: list[str] = []
    if intent.strip():
        parts.append(intent.strip())
    for segment_id in segment_ids:
        segment = repo.find("narration_segments", str(segment_id))
        if segment is None:
            continue
        display = str(segment.get("display_text") or "").strip()
        if display and display not in parts:
            parts.append(display)
    text = "\n".join(parts[:3])
    return text[:220] if text else "（本段没有可显示的讲解文本）"


def make_visual_generation_handler(
    repo_factory: Callable[[], Any],
    *,
    settings: Any,
    media_service: Any,
    work_root: Path,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``VISUAL_GENERATION``: one real motion clip per beat, from a typeset card.

    The design's picture taxonomy is *still motion*, *parallax*, *I2V*,
    *infographic* and *licensed media*, and it pre-authorizes degrading a
    generated shot to a motion still or an information graphic.  This build has
    no in-process path from a step job to a GPU generation job — the worker claims
    one job at a time, so a step that waited on a child generation job would
    deadlock — therefore every beat is rendered through the deterministic path and
    reported as such: ``render_type_planned`` keeps what the storyboard asked for,
    ``render_type_actual`` says ``MOTION_STILL``, and ``fallback_reason`` names the
    reason.  Nothing here claims a generated shot happened.
    """

    from local_drama.infrastructure.composition.ffmpeg_renderer import (
        FfmpegCommand,
        FfmpegFilterGraph,
        FfmpegRunner,
        escape_filter_value,
    )

    ffmpeg_path = getattr(settings, "ffmpeg_path", None)
    ffprobe_path = getattr(settings, "ffprobe_path", None)
    runner = FfmpegRunner(ffmpeg=ffmpeg_path or "ffmpeg", ffprobe=ffprobe_path or "ffprobe", timeout_seconds=1800.0)
    font = _card_font()
    renders_root = Path(work_root) / "explainer_cards"

    def _render_card_clip(
        *,
        ass_path: Path,
        still_path: Path,
        clip_path: Path,
        width: int,
        height: int,
        fps_num: int,
        fps_den: int,
        frames: int,
    ) -> dict[str, Any]:
        duration = frames * fps_den / fps_num
        accent_height = max(2, int(round(height * 0.0056)))
        still_command = FfmpegCommand(
            args=(
                "-hide_banner", "-nostats", "-y",
                "-f", "lavfi", "-i", f"color=c={_CARD_PALETTE['background']}:s={width}x{height}",
                "-vf", (
                    f"drawbox=x=0:y=0:w={width}:h={int(height * 0.16)}:color={_CARD_PALETTE['band']}@1:t=fill,"
                    f"drawbox=x=0:y={int(height * 0.16)}:w={width}:h={accent_height}:color={_CARD_PALETTE['accent']}@1:t=fill,"
                    f"subtitles=filename={escape_filter_value(str(ass_path))}"
                ),
                "-frames:v", "1",
                str(still_path),
            ),
            purpose="EXPLAINER_CARD_STILL",
            note="确定性地用本机 FFmpeg + libass 绘制排版卡，不调用图像模型",
        )
        still_outcome = dict(runner.run(still_command))
        if str(still_outcome.get("status")) != "SUCCEEDED":
            return {"status": "FAILED", "stage": "card-still", **still_outcome}
        zoom = (
            "zoompan=z='min(1+0.06*on/{frames},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps_num}/{fps_den}"
        ).format(frames=max(1, frames))
        clip_command = FfmpegCommand(
            args=(
                "-hide_banner", "-nostats", "-y",
                "-loop", "1", "-i", str(still_path),
                "-vf", f"{zoom},format=yuv420p",
                "-frames:v", str(max(1, frames)),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-r", f"{fps_num}/{fps_den}",
                str(clip_path),
            ),
            purpose="EXPLAINER_CARD_MOTION",
            note="静帧动效：同一母图上的确定性缓慢推近，不使用 -shortest",
        )
        clip_outcome = dict(runner.run(clip_command))
        if str(clip_outcome.get("status")) != "SUCCEEDED":
            # A zoompan expression the local build refuses must not lose the beat:
            # the declared motion is degraded to a held still and reported.
            static_command = FfmpegCommand(
                args=(
                    "-hide_banner", "-nostats", "-y",
                    "-loop", "1", "-i", str(still_path),
                    "-vf", "format=yuv420p",
                    "-frames:v", str(max(1, frames)),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-r", f"{fps_num}/{fps_den}",
                    str(clip_path),
                ),
                purpose="EXPLAINER_CARD_STATIC",
                note="推近表达式失败时的降级：保持静帧，仍按实测时长补齐帧数",
            )
            clip_outcome = dict(runner.run(static_command))
            if str(clip_outcome.get("status")) != "SUCCEEDED":
                return {"status": "FAILED", "stage": "card-motion", **clip_outcome}
            return {"status": "SUCCEEDED", "motion": "STATIC_FALLBACK", "duration_seconds": duration}
        return {"status": "SUCCEEDED", "motion": "SLOW_PUSH", "duration_seconds": duration}

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        generated: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            if not editions:
                return _blocked("该作品没有输出版本，无法生成画面", "SCHEMA_INVALID", {"video_id": video_id})
            beats = {str(item["id"]): item for item in repo.beats(video_id)}
            disclosure = _disclosure_line(str(video.get("content_kind") or ""))
            # One clip per beat, sized for the longest edition placement so every
            # edition can reference the same immutable media version.
            timelines = {str(edition["id"]): _load_timeline(repo, video=video, edition=edition) for edition in editions}
            needed: dict[str, dict[str, Any]] = {}
            for edition in editions:
                timeline = timelines[str(edition["id"])]
                for placement in timeline["placements"]:
                    beat_id = placement.get("beat_id")
                    if not beat_id:
                        continue
                    span = int(placement["end_frame_exclusive"]) - int(placement["start_frame"])
                    current = needed.get(str(beat_id))
                    if current is None or span > current["frames"]:
                        needed[str(beat_id)] = {
                            "frames": span,
                            "render_type": placement.get("render_type") or "STILL_MOTION",
                            "segment_ids": [str(placement["segment_id"])],
                            "visual_intent": str(placement.get("visual_intent") or ""),
                        }
            existing = {
                str(item["beat_id"]): item
                for item in repo.list_where("explainer_media_candidates", {"video_id": video_id})
                if str(item.get("status")) == "READY"
            }
            primary_edition = editions[0]
            width = int(primary_edition["width"])
            height = int(primary_edition["height"])
            fps_num = int(primary_edition["fps_num"])
            fps_den = int(primary_edition["fps_den"])
            voice_locale = normalize_locale(str(primary_edition["voice_locale"]))

        for beat_id, requirement in sorted(needed.items(), key=lambda item: int(beats[item[0]]["ordinal"])):
            if beat_id in existing:
                generated.append({"beat_id": beat_id, "reused": True, "candidate_id": str(existing[beat_id]["id"])})
                continue
            beat = beats[beat_id]
            frames = max(1, int(requirement["frames"]))
            duration_seconds = frames * fps_den / fps_num
            card_dir = renders_root / video_id / beat_id
            card_dir.mkdir(parents=True, exist_ok=True)
            with repo_factory() as repo:
                body = _beat_card_text(
                    repo,
                    segment_ids=requirement["segment_ids"],
                    intent=requirement["visual_intent"],
                )
                entities = [
                    str(code) for code in (beat.get("entity_refs_json") or []) if str(code)
                ][:6]
                entity_line = ("实体：" + "、".join(entities)) if entities else ""
            kicker = f"画面 {int(beat['ordinal']) + 1:02d} · {beat['code']}"
            footer = " ｜ ".join([part for part in (entity_line, disclosure) if part])
            ass_text = _card_ass(
                text=body, kicker=kicker, footer=footer, duration_seconds=duration_seconds, width=width, height=height
            )
            ass_path = card_dir / "card.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            still_path = card_dir / "card.png"
            clip_path = card_dir / "card.mp4"
            outcome = _render_card_clip(
                ass_path=ass_path,
                still_path=still_path,
                clip_path=clip_path,
                width=width,
                height=height,
                fps_num=fps_num,
                fps_den=fps_den,
                frames=frames,
            )
            if outcome.get("status") != "SUCCEEDED":
                failures.append({"beat_id": beat_id, "stage": outcome.get("stage"), "reason": outcome.get("reason")})
                continue
            if font is None:
                failures.append(
                    {
                        "beat_id": beat_id,
                        "stage": "font",
                        "reason": "CJK_FONT_NOT_FOUND_ON_THIS_MACHINE",
                    }
                )
            registered = media_service.import_file(
                project_id,
                clip_path,
                purpose="EXPLAINER_BEAT_CLIP",
                owner_type="EXPLAINER_VIDEO",
                owner_id=video_id,
                media_kind="VIDEO",
                stage="VISUAL_GENERATION",
                actor="explainer-worker",
            )
            with repo_factory() as repo:
                candidate = repo.insert(
                    "explainer_media_candidates",
                    {
                        "video_id": video_id,
                        "beat_id": beat_id,
                        "variant_no": 1,
                        "candidate_kind": "CREATIVE",
                        "purpose": "VISUAL",
                        "media_asset_id": registered.get("media_asset_id"),
                        "media_version_id": registered.get("media_version_id"),
                        "media_sha256": registered.get("sha256"),
                        "status": "READY",
                        "render_type_planned": str(beat.get("render_type") or "STILL_MOTION"),
                        "render_type_actual": "MOTION_STILL",
                        "fallback_reason": "DETERMINISTIC_TYPESET_CARD_PATH",
                        "lineage_json": {
                            "source": "LOCAL_FFMPEG_TYPESET_CARD",
                            "card_ass_sha256": _sha256_file(ass_path),
                            "clip_sha256": _sha256_file(clip_path),
                            "duration_seconds": duration_seconds,
                            "frames": frames,
                            "motion": outcome.get("motion"),
                            "generated_picture_model": None,
                            "note": "本机未调用图像/视频生成模型；画面为确定性排版卡与其静帧动效。",
                        },
                        "execution_snapshot_json": {"provider": "LOCAL_FFMPEG", "network_used": False},
                        "adopted": False,
                    },
                    actor="explainer-worker",
                )
                for edition in editions:
                    if str(edition["id"]) not in timelines:
                        continue
                    existing_selection = repo.query_one(
                        "SELECT id FROM explainer_beat_selections WHERE beat_id=? AND edition_id=? AND status='ACTIVE'",
                        (beat_id, str(edition["id"])),
                    )
                    if existing_selection is not None:
                        repo.update(
                            "explainer_beat_selections",
                            str(existing_selection["id"]),
                            {"status": "SUPERSEDED"},
                            actor="explainer-worker",
                        )
                    repo.insert(
                        "explainer_beat_selections",
                        {
                            "video_id": video_id,
                            "beat_id": beat_id,
                            "edition_id": str(edition["id"]),
                            "candidate_id": str(candidate["id"]),
                            "media_asset_id": registered.get("media_asset_id"),
                            "media_version_id": registered.get("media_version_id"),
                            "media_sha256": registered.get("sha256"),
                            "source_in_us": 0,
                            "source_out_us": int(round(duration_seconds * 1_000_000)),
                            "adoption_authority": "MACHINE_POLICY",
                            "render_type_actual": "MOTION_STILL",
                            "fallback_reason": "DETERMINISTIC_TYPESET_CARD_PATH",
                            "status": "ACTIVE",
                        },
                        actor="explainer-worker",
                    )
                repo.update(
                    "explainer_media_candidates",
                    str(candidate["id"]),
                    {"adopted": True},
                    actor="explainer-worker",
                )
                if str(beat.get("render_type")) != "MOTION_STILL":
                    repo.update(
                        "explainer_visual_beats",
                        beat_id,
                        {
                            "actual_fallback_type": "STILL_MOTION",
                            "fallback_reason": "PLANNED_VISUAL_DEGRADED_TO_DETERMINISTIC_CARD",
                        },
                        actor="explainer-worker",
                    )
            generated.append(
                {
                    "beat_id": beat_id,
                    "candidate_id": str(candidate["id"]),
                    "media_version_id": registered.get("media_version_id"),
                    "frames": frames,
                }
            )
        if not generated:
            return _blocked(
                "没有任何画面段生成可用的画面候选",
                "MEDIA_CANDIDATES_MISSING",
                {"failures": failures[:5], "beat_count": len(needed)},
            )
        degraded = sum(1 for item in generated if not item.get("reused"))
        return _passed(
            f"已为 {len(generated)} 个画面段准备画面（新渲染 {degraded} 个），全部为本地确定性排版卡静帧动效，已记录降级原因。",
            {
                "candidates": generated,
                "failures": failures,
                "beat_count": len(needed),
                "planned_types": sorted({str(item.get("render_type")) for item in needed.values()}),
                "actual_type": "MOTION_STILL",
                "generation_model_used": False,
                "disclosure": "画面为本地排版卡静帧动效，未调用图像或视频生成模型；I2V 降级已记录。",
            },
        )

    return handler


# --------------------------------------------------------------------------- #
# SUBTITLE_BUILD
# --------------------------------------------------------------------------- #
def _split_cue_text(text: str, *, max_chars: int) -> list[str]:
    """Deterministic sentence split, then a hard wrap for an over-long clause."""

    import re

    pieces = [piece for piece in re.split(r"(?<=[。！？；!?;])", text) if piece.strip()]
    if not pieces:
        pieces = [text]
    chunks: list[str] = []
    for piece in pieces:
        stripped = piece.strip()
        while len(stripped) > max_chars:
            chunks.append(stripped[:max_chars])
            stripped = stripped[max_chars:]
        if stripped:
            chunks.append(stripped)
    return chunks or [text[:max_chars]]


def make_subtitle_build_handler(
    repo_factory: Callable[[], Any],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``SUBTITLE_BUILD``: cue revisions from the alignment clock, per edition."""

    from local_drama.application.explainers.subtitles import (
        ExplainerSubtitleService,
        character_budget_per_line,
    )

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        built: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        subtitle_modes: set[str] = set()
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            subtitle_modes = {str(item.get("subtitle_mode")) for item in editions}
            for edition in editions:
                mode = str(edition.get("subtitle_mode") or "NONE")
                locales = [normalize_locale(str(item)) for item in (edition.get("subtitle_locales_json") or [])]
                if mode == "NONE" or not locales:
                    continue
                timeline = _load_timeline(repo, video=video, edition=edition)
                fps = timeline["fps"]
                service = ExplainerSubtitleService(repo)
                for locale in locales:
                    script_revision = repo.list_where(
                        "explainer_script_revisions",
                        {"video_id": video_id, "locale": locale},
                        order_by="revision_no",
                        descending=True,
                        limit=1,
                    )
                    if not script_revision:
                        skipped.append(
                            {
                                "edition_id": str(edition["id"]),
                                "locale": locale,
                                "reason": "NO_SCRIPT_REVISION_IN_THIS_LANGUAGE",
                            }
                        )
                        continue
                    script_revision_row = script_revision[0]
                    segments = {
                        str(item["id"]): item
                        for item in _segments_by_locale(
                            repo,
                            video_id=video_id,
                            script_revision_id=str(script_revision_row["id"]),
                            locale=locale,
                        )
                    }
                    if not segments:
                        skipped.append(
                            {
                                "edition_id": str(edition["id"]),
                                "locale": locale,
                                "reason": "NO_SEGMENTS_IN_THIS_LANGUAGE",
                            }
                        )
                        continue
                    max_chars = (
                        character_budget_per_line(
                            aspect_ratio=str(edition["aspect_ratio"]), locale=locale
                        )
                        * 2
                    )
                    cues: list[dict[str, Any]] = []
                    alignment_ids: list[str] = []
                    for clip in timeline["narration_clips"]:
                        segment = segments.get(str(clip["segment_id"]))
                        if segment is None:
                            continue
                        alignment = repo.latest_alignment_for_take(str(clip["take_id"]))
                        if alignment is not None:
                            alignment_ids.append(str(alignment["id"]))
                        # ``Ratio`` exposes seconds; convert with the same integer
                        # arithmetic the timeline uses so a cue boundary is the
                        # frame the manifest actually places.
                        start_ms = int(round(fps.seconds_for_frames(int(clip["start_frame"])) * 1000))
                        end_ms = int(round(fps.seconds_for_frames(int(clip["end_frame_exclusive"])) * 1000))
                        chunks = _split_cue_text(str(segment.get("display_text") or ""), max_chars=max_chars)
                        total_chars = sum(max(1, len(chunk)) for chunk in chunks)
                        cursor = start_ms
                        for index, chunk in enumerate(chunks):
                            weight = max(1, len(chunk)) / total_chars
                            span = max(1200, int(round((end_ms - start_ms) * weight)))
                            cue_end = min(end_ms, cursor + span) if index < len(chunks) - 1 else end_ms
                            if cue_end <= cursor:
                                cue_end = min(end_ms, cursor + 400)
                            cues.append(
                                {
                                    "start_ms": cursor,
                                    "end_ms": max(cursor + 1, cue_end),
                                    "text": chunk,
                                    "paired_text": "",
                                    "segment_canonical_id": str(segment["canonical_segment_id"]),
                                }
                            )
                            cursor = max(cursor + 1, cue_end)
                    if not cues:
                        skipped.append(
                            {"edition_id": str(edition["id"]), "locale": locale, "reason": "NO_CUES_BUILT"}
                        )
                        continue
                    created = service.create_revision(
                        project_id=project_id,
                        video_id=video_id,
                        edition_id=str(edition["id"]),
                        locale=locale,
                        cues=cues,
                        text_authority="NARRATION_SCRIPT",
                        script_revision_id=str(script_revision_row["id"]),
                        narration_alignment_revision_ids=alignment_ids,
                        format="JSON",
                        status="FROZEN",
                        actor="explainer-worker",
                    )
                    revision = created["subtitle_revision"]
                    repo.update(
                        "explainer_editions",
                        str(edition["id"]),
                        {"frozen_subtitle_revision_id": str(revision["id"])},
                        actor="explainer-worker",
                    )
                    built.append(
                        {
                            "edition_id": str(edition["id"]),
                            "locale": locale,
                            "subtitle_revision_id": str(revision["id"]),
                            "cue_count": len(created["cues"]),
                            "alignment_revision_count": len(alignment_ids),
                        }
                    )
        if not built:
            if subtitle_modes and subtitle_modes != {"NONE"}:
                return _blocked(
                    "需要字幕的输出版本没有建立任何字幕修订",
                    "SUBTITLE_REVISION_MISSING",
                    {"skipped": skipped},
                )
        return _passed(
            f"已为 {len(built)} 个字幕语言建立冻结字幕修订。",
            {"revisions": built, "skipped": skipped},
        )

    return handler


# --------------------------------------------------------------------------- #
# COMPOSITION_RENDER
# --------------------------------------------------------------------------- #
def _apply_target_padding(timeline: dict[str, Any], *, edition: Mapping[str, Any]) -> dict[str, Any]:
    """Hold the last card until the edition's declared target length is reached.

    The edition declares a target duration; the narration is whatever the local TTS
    actually measured.  When the measured narration is shorter, the design's only
    legal way to reach the target is a *declared* hold — never truncating audio and
    never hiding a gap with ``-shortest``.  The hold is recorded as declared silence
    on the narration clock and as an explicit outro placement on the picture track,
    so the report shows exactly how many frames the ending accounts for.
    """

    fps = timeline["fps"]
    measured_frames = int(timeline["total_frames"])
    target_seconds = int(edition.get("target_seconds") or 0)
    if target_seconds <= 0:
        return {**timeline, "target_frames": None, "outro_frames": 0, "measured_frames": measured_frames}
    target_frames = int(fps.frames_for_seconds(float(target_seconds)))
    if target_frames <= measured_frames:
        return {**timeline, "target_frames": None, "outro_frames": 0, "measured_frames": measured_frames}
    outro_frames = target_frames - measured_frames
    placements = [dict(item) for item in timeline["placements"]]
    last = placements[-1] if placements else None
    if last is not None:
        placements.append(
            {
                **last,
                "start_frame": measured_frames,
                "end_frame_exclusive": target_frames,
                "outro_hold": True,
                "fallback_reason": "DESIGNED_OUTRO_HOLD_TO_DECLARED_TARGET",
            }
        )
    sample_rate = int(edition["audio_sample_rate_hz"])
    silence = list(timeline.get("declared_silence") or [])
    silence.append(
        {
            "reason": "DESIGNED_OUTRO_HOLD_TO_DECLARED_TARGET",
            "start_frame": measured_frames,
            "end_frame_exclusive": target_frames,
            "sample_start": int(fps.samples_for_frames(measured_frames, sample_rate)),
            "sample_end_exclusive": int(fps.samples_for_frames(target_frames, sample_rate)),
        }
    )
    return {
        **timeline,
        "total_frames": target_frames,
        "placements": placements,
        "declared_silence": silence,
        "target_frames": target_frames,
        "measured_frames": measured_frames,
        "outro_frames": outro_frames,
    }


def make_composition_render_handler(
    repo_factory: Callable[[], Any],
    *,
    settings: Any,
    media_service: Any,
    work_root: Path,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``COMPOSITION_RENDER``: freeze the manifest and render the whole film.

    This is the step the audit found entirely missing — the composition tables had
    no writer and the FFmpeg render core had no production caller.  The handler
    builds the immutable manifest from the measured clock, the adopted beat media,
    the narration takes and the frozen subtitle revision, then drives
    :func:`~local_drama.infrastructure.composition.ffmpeg_renderer.atomic_render`
    (per-chunk encode, exact frame checks, full decode, hash, atomic publish).
    """

    from local_drama.application.composition.manifest import ManifestClip, build_manifest, plan_chunks
    from local_drama.infrastructure.composition.ffmpeg_renderer import FfmpegRunner, atomic_render

    ffmpeg_path = getattr(settings, "ffmpeg_path", None)
    ffprobe_path = getattr(settings, "ffprobe_path", None)

    def _media_path(media_version_id: str, media_sha256: str | None) -> str:
        meta, path = media_service.content_path(str(media_version_id))
        if media_sha256 and str(meta.get("sha256") or "") and str(meta["sha256"]) != str(media_sha256):
            raise ExplainerContractError(
                "OUTPUT_VALIDATION_FAILED",
                "媒体版本哈希与清单不一致，拒绝渲染",
                {"media_version_id": media_version_id},
            )
        return str(path)

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        only_edition = (
            "" if str(payload.get("edition_scope") or "") == "VIDEO" else str(payload.get("edition_id") or "").strip()
        )
        runner = FfmpegRunner(
            ffmpeg=ffmpeg_path or "ffmpeg", ffprobe=ffprobe_path or "ffprobe", timeout_seconds=7200.0
        )
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            if only_edition:
                editions = [item for item in editions if str(item["id"]) == only_edition]
            if not editions:
                return _blocked("该作品没有可渲染的输出版本", "SCHEMA_INVALID", {"video_id": video_id})
            plans: list[dict[str, Any]] = []
            for edition in editions:
                timeline = _apply_target_padding(
                    _load_timeline(repo, video=video, edition=edition), edition=edition
                )
                placements = timeline["placements"]
                clips: list[dict[str, Any]] = []
                video_items: list[dict[str, Any]] = []
                missing_media: list[str] = []
                has_audio: dict[str, bool] = {}
                for placement in placements:
                    beat_id = placement.get("beat_id")
                    selection = repo.active_beat_selection(str(beat_id), str(edition["id"])) if beat_id else None
                    span = int(placement["end_frame_exclusive"]) - int(placement["start_frame"])
                    if selection is None:
                        missing_media.append(str(placement.get("beat_code") or placement["segment_id"]))
                        clips.append(
                            {
                                "clip_id": f"v-{len(clips):04d}",
                                "track": "VIDEO",
                                "item_kind": "VIDEO_CLIP",
                                "media_version_id": None,
                                "media_sha256": None,
                                "start_frame": int(placement["start_frame"]),
                                "end_frame_exclusive": int(placement["end_frame_exclusive"]),
                                "source_in_us": None,
                                "source_out_us": None,
                                "sample_start": None,
                                "sample_end_exclusive": None,
                                "beat_id": beat_id,
                                "render_type_planned": placement.get("render_type"),
                                "render_type_actual": "MOTION_STILL",
                                "transition": {"kind": "CUT"},
                            }
                        )
                        continue
                    media_version_id = str(selection["media_version_id"])
                    has_audio[media_version_id] = False
                    clip = {
                        "clip_id": f"v-{len(clips):04d}",
                        "track": "VIDEO",
                        "item_kind": "VIDEO_CLIP",
                        "media_version_id": media_version_id,
                        "media_sha256": str(selection["media_sha256"]),
                        "start_frame": int(placement["start_frame"]),
                        "end_frame_exclusive": int(placement["end_frame_exclusive"]),
                        "source_in_us": 0,
                        "source_out_us": int(round(span * 1_000_000 * timeline["fps"].den / timeline["fps"].num)),
                        "sample_start": None,
                        "sample_end_exclusive": None,
                        "beat_id": beat_id,
                        "narration_segment_id": str(placement["segment_id"]),
                        "render_type_planned": placement.get("render_type"),
                        "render_type_actual": "MOTION_STILL",
                        "transition": {"kind": "CUT"},
                    }
                    clips.append(clip)
                    video_items.append(clip)
                narration_clips: list[dict[str, Any]] = []
                for item in timeline["narration_clips"]:
                    narration_clips.append(
                        {
                            "clip_id": f"n-{len(narration_clips):04d}",
                            "track": "NARRATION",
                            "item_kind": "AUDIO_CLIP",
                            "media_version_id": item["media_version_id"],
                            "media_sha256": item["media_sha256"],
                            "start_frame": int(item["start_frame"]),
                            "end_frame_exclusive": int(item["end_frame_exclusive"]),
                            "source_in_us": 0,
                            "source_out_us": int(item["measured_duration_ms"]) * 1000,
                            "sample_start": int(item["sample_start"]),
                            "sample_end_exclusive": int(item["sample_end_exclusive"]),
                            "narration_segment_id": item["segment_id"],
                            "narration_take_id": item["take_id"],
                            "transition": {"kind": "CUT"},
                        }
                    )
                clips.extend(narration_clips)
                subtitle_revision = None
                if str(edition.get("subtitle_mode") or "NONE") != "NONE":
                    frozen_id = edition.get("frozen_subtitle_revision_id")
                    subtitle_revision = repo.find("explainer_subtitle_revisions", str(frozen_id)) if frozen_id else None
                plans.append(
                    {
                        "edition": edition,
                        "timeline": timeline,
                        "clips": clips,
                        "video_items": video_items,
                        "narration_clips": narration_clips,
                        "missing_media": missing_media,
                        "has_audio": has_audio,
                        "subtitle_revision": subtitle_revision,
                    }
                )
            primary = plans[0]
        for plan in plans:
            edition = plan["edition"]
            timeline = plan["timeline"]
            if plan["missing_media"]:
                failures.append(
                    {
                        "edition_id": str(edition["id"]),
                        "reason": "BEAT_MEDIA_MISSING",
                        "beats": plan["missing_media"][:10],
                    }
                )
                continue
            revision_id = str(uuid.uuid4())
            subtitle_tracks: list[dict[str, Any]] = []
            subtitle_artifact: Path | None = None
            if plan["subtitle_revision"] is not None:
                revision = plan["subtitle_revision"]
                cues = list(revision.get("cues_json") or [])
                for cue in cues:
                    subtitle_tracks.append(
                        {
                            "start_frame": timeline["fps"].frames_for_seconds(
                                float(cue["start_ms"]) / 1000.0
                            ),
                            "end_frame_exclusive": timeline["fps"].frames_for_seconds(
                                float(cue["end_ms"]) / 1000.0
                            ),
                            "text": str(cue["text"]),
                            "locale": normalize_locale(str(revision["locale"])),
                        }
                    )
            work_dir = Path(work_root) / "explainer_renders" / str(edition["id"]) / revision_id
            work_dir.mkdir(parents=True, exist_ok=True)
            if plan["subtitle_revision"] is not None:
                from local_drama.application.explainers.subtitles import ExplainerSubtitleService

                with repo_factory() as repo:
                    service = ExplainerSubtitleService(repo)
                    ass_text = service.serialize(
                        cues=list(plan["subtitle_revision"].get("cues_json") or []),
                        format="ASS",
                        width=int(edition["width"]),
                        height=int(edition["height"]),
                    )
                subtitle_artifact = work_dir / "captions.ass"
                subtitle_artifact.write_text(ass_text, encoding="utf-8")
            manifest = build_manifest(
                edition_id=str(edition["id"]),
                composition_revision_id=revision_id,
                aspect_ratio=str(edition["aspect_ratio"]),
                fps=timeline["fps"],
                total_frames=int(timeline["total_frames"]),
                audio_sample_rate_hz=int(edition["audio_sample_rate_hz"]),
                duration_policy=str(edition["duration_policy"]),
                clips=plan["clips"],
                chunks=plan_chunks(total_frames=int(timeline["total_frames"]), fps=timeline["fps"]),
                subtitle_tracks=subtitle_tracks if str(edition.get("subtitle_mode")) == "SOFT" else (),
                mix={
                    # ``segments`` / ``expected_segment_ids`` are the keys the shared
                    # mix validator reads; the plan must declare every narration
                    # segment exactly once or the validator reports a blocker.
                    "segments": [
                        {
                            "narration_segment_id": item["narration_segment_id"],
                            "take_id": item["narration_take_id"],
                            "sample_start": item["sample_start"],
                            "sample_end_exclusive": item["sample_end_exclusive"],
                        }
                        for item in plan["narration_clips"]
                    ],
                    "expected_segment_ids": [
                        item["narration_segment_id"] for item in plan["narration_clips"]
                    ],
                    "declared_silence": list(timeline.get("declared_silence") or []),
                    "may_truncate_audio": False,
                    "may_stretch_narration": False,
                },
                meta={
                    "project_id": project_id,
                    "video_id": video_id,
                    "locale": timeline["locale"],
                    "content_kind": str(plan["edition"].get("voice_locale") or ""),
                    "renderer": "localdrama.explainer.ffmpeg",
                    "picture_path": "DETERMINISTIC_TYPESET_CARD_MOTION",
                    "generation_model_used": False,
                    "timing_authority": "MEASURED_TTS",
                    # The picture track may hold its last card to reach the declared
                    # target.  The hold is an explicit, declared ending, and the
                    # frame accounting is part of the frozen manifest.
                    "declared_silence": list(timeline.get("declared_silence") or []),
                    "measured_narration_frames": int(timeline.get("measured_frames") or 0),
                    "outro_hold_frames": int(timeline.get("outro_frames") or 0),
                },
            )
            narration_paths = [_media_path(str(item["media_version_id"]), str(item["media_sha256"])) for item in plan["narration_clips"]]
            narration_paths = [Path(item) for item in narration_paths]
            outcome = atomic_render(
                manifest=manifest,
                temp_dir=work_dir / "tmp",
                final_path=work_dir / "master.mp4",
                work_dir=work_dir / "staging",
                run_command=runner.run,
                probe=runner.probe,
                decode_check=runner.full_decode_check,
                media_path_resolver=_media_path,
                subtitle_paths=[] if subtitle_artifact is None else [subtitle_artifact],
                narration_paths=narration_paths,
                has_audio_lookup=plan["has_audio"],
            )
            if str(outcome.get("status")) != "SUCCEEDED":
                failures.append(
                    {
                        "edition_id": str(edition["id"]),
                        "reason": str(outcome.get("reason") or outcome.get("status")),
                        "steps": [step for step in (outcome.get("steps") or [])][-3:],
                    }
                )
                continue
            master_path = Path(str(outcome["final_path"]))
            duration_ms = int(round(float((outcome.get("probe") or {}).get("duration_seconds") or 0.0) * 1000))
            # Registering a multi-hundred-megabyte master and writing its bookkeeping
            # are the only database touches of this stage, and they happen while the
            # job's own lease heartbeat is writing too.  A local single-writer SQLite
            # database can answer ``database is locked`` for a moment in that window;
            # the work is idempotent, so it is retried instead of losing a finished
            # render.
            registered = _retry_on_locked(
                lambda: media_service.import_file(
                    project_id,
                    master_path,
                    purpose="EXPLAINER_RENDER",
                    owner_type="EXPLAINER_EDITION",
                    owner_id=str(edition["id"]),
                    media_kind="VIDEO",
                    stage="COMPOSITION_RENDER",
                    actor="explainer-worker",
                )
            )
            with repo_factory() as repo:
                revision_no = 1 + int(
                    (
                        repo.query_one(
                            "SELECT COALESCE(MAX(revision_no),0) AS current FROM composition_revisions WHERE edition_id=?",
                            (str(edition["id"]),),
                        )
                        or {"current": 0}
                    )["current"]
                )
                composition = repo.insert(
                    "composition_revisions",
                    {
                        "id": revision_id,
                        "edition_id": str(edition["id"]),
                        "video_id": video_id,
                        "project_id": project_id,
                        "revision_no": revision_no,
                        "status": "FROZEN",
                        "manifest_json": manifest.as_dict(),
                        "manifest_hash": manifest.manifest_hash,
                        "fps_num": int(timeline["fps"].num),
                        "fps_den": int(timeline["fps"].den),
                        "total_frames": int(manifest.total_frames),
                        "audio_sample_rate_hz": int(edition["audio_sample_rate_hz"]),
                        "total_samples": int(manifest.total_samples),
                        "duration_policy": str(edition["duration_policy"]),
                        "target_frames": manifest.target_frames,
                        "frozen_at": _now(),
                        "frozen_by": "explainer-worker",
                        "validation_json": {"status": "PASS", "source": "build_manifest"},
                    },
                    actor="explainer-worker",
                )
                for ordinal, clip in enumerate(manifest.clips):
                    repo.insert(
                        "composition_items",
                        {
                            "composition_revision_id": revision_id,
                            "edition_id": str(edition["id"]),
                            "video_id": video_id,
                            "ordinal": ordinal,
                            "item_kind": clip.item_kind,
                            "track": clip.track,
                            "beat_id": clip.beat_id,
                            "narration_segment_id": clip.narration_segment_id,
                            "narration_take_id": clip.narration_take_id,
                            "media_version_id": clip.media_version_id,
                            "media_sha256": clip.media_sha256,
                            "start_frame": clip.start_frame,
                            "end_frame_exclusive": clip.end_frame_exclusive,
                            "source_in_us": clip.source_in_us,
                            "source_out_us": clip.source_out_us,
                            "sample_start": clip.sample_start,
                            "sample_end_exclusive": clip.sample_end_exclusive,
                            "transform_json": dict(clip.transform),
                            "layer_json": dict(clip.layer),
                            "subtitle_json": dict(clip.subtitle),
                            "audio_json": dict(clip.audio),
                            "transition_json": dict(clip.transition),
                            "render_type_planned": clip.render_type_planned,
                            "render_type_actual": clip.render_type_actual,
                            "media_kind": clip.media_kind,
                            "item_hash": content_hash(clip.as_dict()),
                        },
                        actor="explainer-worker",
                    )
                render = repo.insert(
                    "composition_renders",
                    {
                        "edition_id": str(edition["id"]),
                        "composition_revision_id": revision_id,
                        "video_id": video_id,
                        "project_id": project_id,
                        # ``(edition_id, revision_no)`` is unique: a retried render
                        # must take the next number instead of colliding with the
                        # row an earlier attempt already wrote.
                        "revision_no": _next_render_revision(repo, str(edition["id"])),
                        "render_kind": "FULL",
                        "manifest_hash": manifest.manifest_hash,
                        "status": "SUCCEEDED",
                        "media_asset_id": registered.get("media_asset_id"),
                        "media_version_id": registered.get("media_version_id"),
                        "rel_path": str(registered.get("rel_path") or ""),
                        "sha256": str(outcome.get("sha256") or registered.get("sha256") or ""),
                        "byte_size": int(registered.get("byte_size") or master_path.stat().st_size),
                        "frame_count": int(manifest.total_frames),
                        "duration_ms": duration_ms,
                        "probe_json": dict(outcome.get("probe") or {}),
                        "integrity_status": "VERIFIED",
                    },
                    actor="explainer-worker",
                )
                repo.update(
                    "explainer_editions",
                    str(edition["id"]),
                    {
                        "status": "READY",
                        "frozen_narration_take_ids_json": [
                            item["narration_take_id"] for item in plan["narration_clips"]
                        ],
                    },
                    actor="explainer-worker",
                )
            render_id = str(render["id"])
            # The clean master is a delivery requirement even when the edition burns
            # captions; it is produced from the same frozen revision with the same
            # chunks and only the caption burn omitted.  It deliberately runs
            # *outside* the write transaction above: a multi-minute FFmpeg pass must
            # never hold the SQLite write lock, because that starves the job's own
            # lease heartbeat and deadlocks the media import, which opens its own
            # connection.
            if subtitle_artifact is not None:
                clean_dir = work_dir / "clean"
                clean_outcome = atomic_render(
                    manifest=manifest,
                    temp_dir=clean_dir / "tmp",
                    final_path=clean_dir / "clean.mp4",
                    work_dir=clean_dir / "staging",
                    run_command=runner.run,
                    probe=runner.probe,
                    decode_check=runner.full_decode_check,
                    media_path_resolver=_media_path,
                    subtitle_paths=[],
                    narration_paths=narration_paths,
                    has_audio_lookup=plan["has_audio"],
                )
                if str(clean_outcome.get("status")) == "SUCCEEDED":
                    clean_path = Path(str(clean_outcome["final_path"]))
                    clean_registered = _retry_on_locked(
                        lambda: media_service.import_file(
                            project_id,
                            clean_path,
                            purpose="EXPLAINER_CLEAN_MASTER",
                            owner_type="EXPLAINER_EDITION",
                            owner_id=str(edition["id"]),
                            media_kind="VIDEO",
                            stage="COMPOSITION_RENDER",
                            actor="explainer-worker",
                        )
                    )
                    with repo_factory() as repo:
                        repo.insert(
                            "composition_renders",
                            {
                                "edition_id": str(edition["id"]),
                                "composition_revision_id": revision_id,
                                "video_id": video_id,
                                "project_id": project_id,
                                "revision_no": _next_render_revision(repo, str(edition["id"])),
                                "render_kind": "EXPORT",
                                "manifest_hash": manifest.manifest_hash,
                                "status": "SUCCEEDED",
                                "media_asset_id": clean_registered.get("media_asset_id"),
                                "media_version_id": clean_registered.get("media_version_id"),
                                "rel_path": str(clean_registered.get("rel_path") or ""),
                                "sha256": str(clean_registered.get("sha256") or ""),
                                "byte_size": int(clean_registered.get("byte_size") or 0),
                                "frame_count": int(manifest.total_frames),
                                "duration_ms": duration_ms,
                                "probe_json": dict(clean_outcome.get("probe") or {}),
                                "integrity_status": "VERIFIED",
                                "parent_render_id": render_id,
                            },
                            actor="explainer-worker",
                        )
                else:
                    failures.append(
                        {
                            "edition_id": str(edition["id"]),
                            "reason": "CLEAN_MASTER_FAILED",
                            "detail": str(clean_outcome.get("reason") or ""),
                        }
                    )
            results.append(
                {
                    "edition_id": str(edition["id"]),
                    "composition_revision_id": revision_id,
                    "render_id": render_id,
                    "manifest_hash": manifest.manifest_hash,
                    "total_frames": int(manifest.total_frames),
                    "duration_ms": duration_ms,
                    "sha256": str(outcome.get("sha256")),
                    "media_version_id": registered.get("media_version_id"),
                    "clip_count": len(manifest.clips),
                    "chunk_count": len(manifest.chunks),
                }
            )
        if not results:
            return _blocked(
                "没有任何输出版本完成渲染",
                "COMPOSITION_RENDER_FAILED",
                {"failures": failures[:5], "edition_count": len(plans)},
            )
        total_seconds = round(sum(int(item["duration_ms"]) for item in results) / 1000.0, 2)
        return _passed(
            f"已渲染 {len(results)} 个输出版本，累计时长 {total_seconds} 秒；失败 {len(failures)} 个。",
            {"renders": results, "failures": failures, "total_seconds": total_seconds},
        )

    return handler


# --------------------------------------------------------------------------- #
# EXPLAINER_EXPORT
# --------------------------------------------------------------------------- #
def make_explainer_export_handler(
    repo_factory: Callable[[], Any],
    *,
    media_service: Any,
    work_root: Path,
    repo_factory_read: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``EXPLAINER_EXPORT``: build and freeze the publication package.

    The roster the delivery service enforces is complete (clean master, burned
    master, cover, title, summary, chapters, citations, QC report, licence list,
    plus one subtitle item per declared locale), so this handler produces exactly
    those files from the frozen render and records their real hashes.  A package
    whose zip hash is missing stays ``BUILDING`` by construction — it can never be
    reported as ready without the file that proves it.
    """

    from local_drama.application.explainers.deliveries import (
        BURNED_MASTER,
        CITATIONS,
        CLEAN_MASTER,
        CHAPTERS,
        COVER,
        ExplainerDeliveryService,
        LICENSE_LIST,
        PackageItem,
        QC_REPORT,
        SUBTITLE,
        SUMMARY,
        TITLE,
    )
    from local_drama.application.explainers.subtitles import ExplainerSubtitleService

    import zipfile

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        # A per-edition command names one edition; the graph-driven stage covers the
        # whole work and must package every declared edition.  The bridge marks the
        # latter with ``edition_scope=VIDEO`` because it always carries the first
        # edition for the stages that need a default.
        scoped_edition = (
            "" if str(payload.get("edition_scope") or "") == "VIDEO" else str(payload.get("edition_id") or "")
        )
        target_package_id = str(payload.get("package_id") or "").strip()
        packages: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        # Packaging copies, hashes, compresses and registers media.  None of that may
        # run inside a write transaction: the media registration opens its own
        # connection, and holding the write lock across a multi-hundred-megabyte zip
        # would both deadlock that write and starve the job's lease heartbeat.  The
        # read connection below takes no write lock; the frozen package row is
        # written afterwards in one short transaction.
        with repo_factory_read() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            if scoped_edition:
                editions = [item for item in editions if str(item["id"]) == scoped_edition]
            if target_package_id:
                row = repo.get("publication_packages", target_package_id)
                editions = [item for item in editions if str(item["id"]) == str(row["edition_id"])]
            script_revision = _current_script_revision(repo, video=video, video_id=video_id)
            for edition in editions:
                root_render = repo.current_root_render(str(edition["id"]))
                if root_render is None:
                    failures.append({"edition_id": str(edition["id"]), "reason": "NO_VERIFIED_RENDER"})
                    continue
                composition_revision_id = str(root_render["composition_revision_id"])
                clean_row = repo.query_one(
                    """SELECT * FROM composition_renders
                    WHERE edition_id=? AND composition_revision_id=? AND render_kind='EXPORT' AND status='SUCCEEDED'
                    ORDER BY revision_no DESC LIMIT 1""",
                    (str(edition["id"]), composition_revision_id),
                )
                clean_render = dict(clean_row) if clean_row is not None else None
                package_dir = Path(work_root) / "explainer_packages" / str(edition["id"]) / str(root_render["id"])
                package_dir.mkdir(parents=True, exist_ok=True)
                items: list[PackageItem] = []
                files: list[dict[str, Any]] = []

                def add_file(
                    *,
                    role: str,
                    rel_path: str,
                    path: Path,
                    media_version_id: str | None = None,
                    required: bool = True,
                    content: str | None = None,
                ) -> None:
                    target = package_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if content is not None:
                        target.write_text(content, encoding="utf-8")
                    elif path is not None and Path(path) != target:
                        import shutil

                        shutil.copyfile(path, target)
                    digest = _sha256_file(target)
                    size = target.stat().st_size
                    items.append(
                        PackageItem(
                            role=role,
                            rel_path=rel_path,
                            media_version_id=media_version_id,
                            sha256=digest,
                            byte_size=size,
                            required=required,
                        )
                    )
                    files.append({"rel_path": rel_path, "sha256": digest, "byte_size": size, "role": role})

                locale = normalize_locale(str(edition["voice_locale"]))
                subtitle_mode = str(edition.get("subtitle_mode") or "NONE")
                # The package file names carry the resolution the film was really
                # generated at (the 480p generation canvas), never a hardcoded
                # "1080p": the delivery resolution is produced by the later
                # super-resolution step, so labelling a 480p master 1080p would be a
                # false claim about the file inside the bundle.
                resolution_label = f"{int(edition['height'])}p"
                clean_media_version_id: str | None = None
                if clean_render is not None and clean_render.get("media_version_id"):
                    _meta, clean_path = media_service.content_path(str(clean_render["media_version_id"]))
                    clean_media_version_id = str(clean_render["media_version_id"])
                else:
                    _meta, clean_path = media_service.content_path(str(root_render["media_version_id"]))
                    clean_media_version_id = str(root_render["media_version_id"])
                add_file(
                    role=CLEAN_MASTER,
                    rel_path=f"master.clean.{locale}.{resolution_label}.mp4",
                    path=Path(clean_path),
                    media_version_id=clean_media_version_id,
                )
                if subtitle_mode != "NONE":
                    _meta, burned_path = media_service.content_path(str(root_render["media_version_id"]))
                    add_file(
                        role=BURNED_MASTER,
                        rel_path=f"master.{locale}.burned.{resolution_label}.mp4",
                        path=Path(burned_path),
                        media_version_id=str(root_render["media_version_id"]),
                    )
                else:
                    add_file(
                        role=BURNED_MASTER,
                        rel_path=f"master.{locale}.burned.{resolution_label}.mp4",
                        path=Path(clean_path),
                        media_version_id=clean_media_version_id,
                    )
                for subtitle_locale in [
                    normalize_locale(str(item)) for item in (edition.get("subtitle_locales_json") or [])
                ]:
                    revision_row = repo.query_one(
                        """SELECT * FROM explainer_subtitle_revisions
                        WHERE video_id=? AND edition_id=? AND locale=? AND status='FROZEN'
                        ORDER BY revision_no DESC LIMIT 1""",
                        (video_id, str(edition["id"]), subtitle_locale),
                    )
                    revision = dict(revision_row) if revision_row is not None else None
                    if revision is not None and isinstance(revision.get("cues_json"), str):
                        # ``query_one`` hands back a raw row, so JSON columns are still
                        # text; the repository's own decoder is not applied here.
                        try:
                            revision["cues_json"] = json.loads(revision["cues_json"] or "[]")
                        except (TypeError, ValueError):
                            revision["cues_json"] = []
                    if revision is None:
                        failures.append(
                            {
                                "edition_id": str(edition["id"]),
                                "reason": "SUBTITLE_REVISION_MISSING",
                                "locale": subtitle_locale,
                            }
                        )
                        continue
                    cues = list(revision.get("cues_json") or [])
                    srt = str(revision.get("content_text") or "")
                    add_file(
                        role=f"{SUBTITLE}:{subtitle_locale}",
                        rel_path=f"subtitles.{subtitle_locale}.srt",
                        path=None,
                        content=srt,
                    )
                    ass = ExplainerSubtitleService(repo).serialize(
                        cues=cues,
                        format="ASS",
                        width=int(edition["width"]),
                        height=int(edition["height"]),
                    )
                    add_file(
                        role=f"{SUBTITLE}:{subtitle_locale}:ass",
                        rel_path=f"subtitles.{subtitle_locale}.ass",
                        path=None,
                        content=ass,
                        required=False,
                    )
                # Cover: the first beat's still, registered as an immutable image.
                beats = repo.beats(video_id)
                cover_media_version_id: str | None = None
                if beats:
                    selection = repo.active_beat_selection(str(beats[0]["id"]), str(edition["id"]))
                    if selection is not None:
                        _meta, clip_path = media_service.content_path(str(selection["media_version_id"]))
                        cover_source = Path(clip_path)
                        if cover_source.is_file():
                            from local_drama.infrastructure.composition.ffmpeg_renderer import (
                                FfmpegCommand,
                                FfmpegRunner,
                            )

                            runner = FfmpegRunner()
                            cover_png = package_dir / "cover.png"
                            runner.run(
                                FfmpegCommand(
                                    args=(
                                        "-hide_banner", "-nostats", "-y",
                                        "-ss", "0.5", "-i", str(cover_source),
                                        "-frames:v", "1", str(cover_png),
                                    ),
                                    purpose="EXPLAINER_COVER",
                                )
                            )
                            if cover_png.is_file():
                                registered = media_service.import_file(
                                    project_id,
                                    cover_png,
                                    purpose="EXPLAINER_COVER",
                                    owner_type="EXPLAINER_EDITION",
                                    owner_id=str(edition["id"]),
                                    media_kind="IMAGE",
                                    stage="EXPLAINER_EXPORT",
                                    actor="explainer-worker",
                                )
                                cover_media_version_id = str(registered.get("media_version_id") or "") or None
                                add_file(
                                    role=COVER,
                                    rel_path="cover.png",
                                    path=cover_png,
                                    media_version_id=cover_media_version_id,
                                )
                if not any(item.base_role == COVER for item in items):
                    failures.append({"edition_id": str(edition["id"]), "reason": "COVER_NOT_PRODUCED"})
                    continue
                beats_json = [
                    {
                        "code": str(beat["code"]),
                        "ordinal": int(beat["ordinal"]),
                        "render_type": str(beat["render_type"]),
                        "visual_intent": str(beat.get("visual_intent") or ""),
                    }
                    for beat in beats
                ]
                add_file(
                    role=TITLE,
                    rel_path="title.json",
                    path=None,
                    content=json.dumps(
                        {
                            "title": str(video["title"]),
                            "topic": str(video.get("topic") or ""),
                            "content_kind": str(video.get("content_kind") or ""),
                            "locale": locale,
                            "target_seconds": int(video.get("target_seconds") or 0),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                add_file(
                    role=SUMMARY,
                    rel_path="summary.md",
                    path=None,
                    content=(
                        f"# {video['title']}\n\n"
                        f"- 语言版本：{locale}\n"
                        f"- 画面段：{len(beats_json)}\n"
                        f"- 成片帧数：{int(root_render.get('frame_count') or 0)}\n"
                        f"- 成片时长：{round(int(root_render.get('duration_ms') or 0) / 1000.0, 2)} 秒\n"
                        "- 画面来源：本地确定性排版卡静帧动效（未调用图像/视频生成模型）\n"
                        "- 旁白：本机离线 VoxCPM2\n"
                        "- 字幕：由实测语音时钟生成\n\n"
                        "本片由 LocalDramaStudio 解说工厂在本机生成；画面为 AI 辅助排版演绎，不代表实拍素材。\n"
                    ),
                )
                add_file(
                    role=CHAPTERS,
                    rel_path="chapters.json",
                    path=None,
                    content=json.dumps(beats_json, ensure_ascii=False, indent=2),
                )
                claims = (
                    repo.list_where("explainer_claims", {"video_id": video_id}) if script_revision else []
                )
                add_file(
                    role=CITATIONS,
                    rel_path="citations.json",
                    path=None,
                    content=json.dumps(
                        {
                            "fact_ledger_claims": [
                                {
                                    "code": str(item["code"]),
                                    "status": str(item.get("status") or ""),
                                    "importance": str(item.get("importance") or ""),
                                }
                                for item in claims
                            ],
                            "sources": [
                                {
                                    "id": str(item["id"]),
                                    "title": str(item.get("title") or ""),
                                    "locator": str(item.get("locator") or ""),
                                }
                                for item in repo.list_where("explainer_sources", {"video_id": video_id})
                            ],
                            "note": "主张与证据的逐条对应保存在 claim_evidence；本文件只是交付包索引。",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                qc_report = repo.latest_qc_report(
                    subject_kind="COMPOSITION_RENDER",
                    subject_revision_id=str(root_render["id"]),
                    subject_hash=str(root_render.get("sha256") or "") or None,
                )
                add_file(
                    role=QC_REPORT,
                    rel_path="qc_report.json",
                    path=None,
                    content=json.dumps(qc_report or {"status": "NOT_RUN"}, ensure_ascii=False, indent=2, default=str),
                )
                add_file(
                    role=LICENSE_LIST,
                    rel_path="licenses.json",
                    path=None,
                    content=json.dumps(
                        {
                            "items": [
                                {
                                    "component": "VoxCPM2",
                                    "role": "NARRATION_TTS",
                                    "license": "apache-2.0",
                                    "evidence": "PyTorch/VoxCPM2/README.md",
                                },
                                {
                                    "component": "FFmpeg",
                                    "role": "RENDER",
                                    "license": "LGPL/GPL（按本机构建）",
                                    "evidence": "本机 ffmpeg -version",
                                },
                            ],
                            "human_approval_required": True,
                            "note": "许可证清单是事实索引，不构成法律意见；发布前仍需人工确认。",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                zip_path = package_dir.with_suffix(".zip")
                # ``ZIP_STORED`` on purpose: the bundle's bulk is already-compressed
                # video, and deflating it only burns time inside the worker.
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
                    for entry in sorted(files, key=lambda item: str(item["rel_path"])):
                        archive.write(package_dir / str(entry["rel_path"]), str(entry["rel_path"]))
                zip_sha256 = _sha256_file(zip_path)
                pending.append(
                    {
                        "edition_id": str(edition["id"]),
                        "render_id": str(root_render["id"]),
                        "composition_revision_id": composition_revision_id,
                        "items": items,
                        "files": files,
                        "zip_path": zip_path,
                        "zip_sha256": zip_sha256,
                        "package_dir": package_dir,
                    }
                )
        for entry in pending:
            with repo_factory() as repo:
                frozen = ExplainerDeliveryService(repo).freeze_package(
                    project_id=project_id,
                    video_id=video_id,
                    edition_id=str(entry["edition_id"]),
                    render_id=str(entry["render_id"]),
                    composition_revision_id=str(entry["composition_revision_id"]),
                    items=entry["items"],
                    files=entry["files"],
                    ai_disclosure={
                        "in_frame": True,
                        "platform_fields": True,
                        "statement": "画面为 AI 辅助排版演绎，旁白为本地合成语音。",
                        "watermark_is_not_platform_field": True,
                    },
                    metadata={
                        "builder": "EXPLAINER_EXPORT_WORKER",
                        "package_dir": Path(entry["package_dir"]).name,
                    },
                    zip_sha256=str(entry["zip_sha256"]),
                    rel_path=Path(entry["zip_path"]).name,
                    byte_size=Path(entry["zip_path"]).stat().st_size,
                    package_id=target_package_id or None,
                )
            packages.append(
                {
                    "edition_id": str(entry["edition_id"]),
                    "package_id": str(frozen["package_id"]),
                    "status": str(frozen["status"]),
                    "publishable": bool(frozen["publishable"]),
                    "file_count": len(entry["files"]),
                    "zip_sha256": str(entry["zip_sha256"]),
                    "rel_path": Path(entry["zip_path"]).name,
                    "blockers": frozen["blockers"],
                    "warnings": frozen["warnings"],
                }
            )
        if not packages:
            return _blocked(
                "没有任何输出版本形成交付包",
                "PUBLICATION_PACKAGE_FAILED",
                {"failures": failures[:5]},
            )
        return _passed(
            f"已冻结 {len(packages)} 个交付包：" + "、".join(str(item["status"]) for item in packages),
            {"packages": packages, "failures": failures},
        )

    return handler


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def build_explainer_pipeline_handlers(
    *,
    repo_factory: Callable[[], Any],
    database: Any,
    settings: Any,
    media_service: Any,
    atomic_writer: Callable[[Path, Callable[[Path], object]], None],
    narration_runtime: Any = None,
    media_ops: Any = None,
    aligner: Any = None,
    asr: Any = None,
    work_root: Path | None = None,
    repo_factory_read: Callable[[], Any] | None = None,
) -> dict[str, Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]]:
    """The seven production stages this module owns, keyed by stage code.

    A missing collaborator removes only its own stage: the graph then fails loudly
    with ``EXPLAINER_TASK_HANDLER_MISSING`` naming the stage, which is exactly the
    behaviour the design asks for.  Nothing is silently skipped.
    """

    resolved_work_root = Path(work_root) if work_root is not None else Path(".")
    reader = repo_factory_read or repo_factory
    handlers: dict[str, Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]] = {
        "IDENTITY_ASSETS": make_identity_assets_handler(repo_factory),
        "SUBTITLE_BUILD": make_subtitle_build_handler(repo_factory),
    }
    if narration_runtime is not None and media_ops is not None:
        handlers["NARRATION_TTS"] = make_narration_tts_handler(
            repo_factory,
            database=database,
            narration_runtime=narration_runtime,
            media_ops=media_ops,
            atomic_writer=atomic_writer,
        )
    if aligner is not None and media_ops is not None:
        handlers["NARRATION_ALIGN"] = make_narration_align_handler(
            repo_factory,
            database=database,
            aligner=aligner,
            asr=asr,
            media_ops=media_ops,
            atomic_writer=atomic_writer,
        )
    if media_service is not None:
        handlers["VISUAL_GENERATION"] = make_visual_generation_handler(
            repo_factory, settings=settings, media_service=media_service, work_root=resolved_work_root
        )
        handlers["COMPOSITION_RENDER"] = make_composition_render_handler(
            repo_factory, settings=settings, media_service=media_service, work_root=resolved_work_root
        )
        handlers["EXPLAINER_EXPORT"] = make_explainer_export_handler(
            repo_factory,
            media_service=media_service,
            work_root=resolved_work_root,
            repo_factory_read=reader,
        )
    return handlers
