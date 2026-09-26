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

import hashlib
import json
import re
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from local_drama.application.explainers.aligner_timestamps import (
    DEFAULT_ALIGNER_SAMPLE_RATE_HZ,
    declared_aligner_sample_rate,
    normalize_aligner_timestamps,
)
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
    "canonical_edition_key",
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
def canonical_edition_key(
    *, voice_locale: str, subtitle_mode: str, aspect_ratio: str
) -> str:
    """Derive an edition key from the shape the output really describes.

    A fixed key such as ``zh-clean-169`` labelled a vertical or English edition as
    if it were the Chinese clean 16:9 one, so the key stopped saying what the
    edition was (design §2.3).  The key is a function of the language, the subtitle
    treatment and the aspect ratio, which means the same shape always maps to the
    same key and a different shape can never reuse it.
    """

    language = normalize_locale(str(voice_locale or "")).split("-")[0].lower() or "und"
    mode = str(subtitle_mode or "NONE").upper()
    if mode == "NONE":
        subtitle_token = "clean"
    elif mode.startswith("BILINGUAL"):
        subtitle_token = "bilingual"
    else:
        subtitle_token = "captioned"
    return f"{language}-{subtitle_token}-{str(aspect_ratio).replace(':', '')}"


def adopt_generated_candidates(
    repo: ExplainerRepository,
    context: Mapping[str, Any],
    *,
    authority: str = "MACHINE_POLICY",
    actor: str = "explainer-worker",
    beat_ids: Sequence[str] = (),
    purpose: str = "VISUAL",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Adopt each beat's generated candidate through the single adoption entry.

    ``VISUAL_GENERATION`` used to insert the active selection itself, so a candidate
    was adopted before any check about it existed — the vision and QC stages were
    decorative (audit A06).  Adoption now happens *here*, after the checks, and only
    for a candidate whose applicable required checks all PASSED.  A beat whose
    content check has not run stays unadopted and is reported for review, which is
    the design's rule: an unmeasured check is UNKNOWN, never a pass (design §4.2,
    §6.3).

    ``authority="HUMAN"`` is the operator's batch decision: it may adopt past an
    unmeasured *content* check (the reviewer is the authority for content) but never
    past a hard technical failure, which is what ``adopt_selection`` enforces.
    ``dry_run`` returns the same plan without writing, so the operator sees the scope
    before confirming (design §2.5, "同类问题可以一批处理").
    """

    from local_drama.application.explainers.storyboard import (
        HARD_TECHNICAL_BLOCKERS,
        build_storyboard_service,
    )

    project_id = str(context.get("project_id") or "")
    video_id = str(context.get("video_id") or "")
    scoped_edition = (
        "" if str(context.get("edition_scope") or "") == "VIDEO" else str(context.get("edition_id") or "")
    )
    wanted_beats = {str(item) for item in beat_ids if str(item)}
    editions = [
        item
        for item in repo.editions(video_id)
        if not scoped_edition or str(item["id"]) == scoped_edition
    ]
    service = build_storyboard_service(repo)
    adopted: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    beats_without_candidate: list[dict[str, Any]] = []
    for beat in repo.beats(video_id):
        beat_id = str(beat["id"])
        if wanted_beats and beat_id not in wanted_beats:
            continue
        candidates = [
            item
            for item in repo.list_where(
                "explainer_media_candidates", {"beat_id": beat_id}, order_by="variant_no", descending=False
            )
            # Only the requested layer is a candidate for this adoption.  Without the
            # filter the ranking could pick the beat's *keyframe* (a picture, not a
            # clip), adopt it under its own KEYFRAME purpose, and leave the beat
            # without the VISUAL selection the composition actually needs — while
            # reporting success.
            if str(item.get("purpose") or "VISUAL") == purpose
        ]
        if not candidates:
            beats_without_candidate.append(
                {"beat_id": beat_id, "beat_code": str(beat["code"]), "reason": "NO_CANDIDATE"}
            )
            continue
        evaluation = service.evaluate_candidates(beat_id=beat_id, candidates=candidates)
        machine_candidate = str(evaluation.get("recommended_candidate_id") or "")
        if authority == "MACHINE_POLICY":
            # A machine may only take a candidate every applicable required check
            # PASSED; there is no fallback, because the fallback *is* the defect.
            candidate_id = machine_candidate
        else:
            # Under human authority a candidate is admissible when it has no hard
            # technical failure, even if a content check was never measured: the
            # reviewer is the authority for content (design §6.3).
            fallback = next(
                (
                    item
                    for item in evaluation["ranked"]
                    if not item["blocked"] and not item["unknown_checks"]
                ),
                None,
            )
            if fallback is None:
                fallback = next(
                    (
                        item
                        for item in evaluation["ranked"]
                        if not set(item["adoption_blockers"]) & HARD_TECHNICAL_BLOCKERS
                    ),
                    None,
                )
            candidate_id = machine_candidate or (str(fallback["candidate_id"]) if fallback else "")
        if not candidate_id:
            needs_review.append(
                {
                    "beat_id": beat_id,
                    "beat_code": str(beat["code"]),
                    "reason": (
                        "NO_MACHINE_ADOPTABLE_CANDIDATE"
                        if authority == "MACHINE_POLICY"
                        else "NO_ADOPTABLE_CANDIDATE"
                    ),
                    "candidates": [
                        {
                            "candidate_id": item["candidate_id"],
                            "verdict": item["adoption_tier_name"],
                            "unknown_checks": item["unknown_checks"],
                            "blockers": item["adoption_blockers"],
                        }
                        for item in evaluation["ranked"]
                    ],
                }
            )
            continue
        entry = next(
            (item for item in evaluation["ranked"] if item["candidate_id"] == candidate_id), {}
        )
        for edition in editions:
            edition_id = str(edition["id"])
            if dry_run:
                planned.append(
                    {
                        "beat_id": beat_id,
                        "beat_code": str(beat["code"]),
                        "edition_id": edition_id,
                        "candidate_id": candidate_id,
                        "verdict": str(entry.get("adoption_tier_name") or ""),
                        "unknown_checks": list(entry.get("unknown_checks") or []),
                        "would_use_machine_policy": candidate_id == machine_candidate,
                    }
                )
                continue
            try:
                result = service.adopt_selection(
                    project_id=project_id,
                    video_id=video_id,
                    beat_id=beat_id,
                    candidate_id=candidate_id,
                    edition_id=edition_id,
                    authority=authority,
                    actor=actor,
                )
            except ExplainerContractError as error:
                needs_review.append(
                    {
                        "beat_id": beat_id,
                        "beat_code": str(beat["code"]),
                        "edition_id": edition_id,
                        "candidate_id": candidate_id,
                        "reason": str(error.code),
                        "message": str(error.message),
                    }
                )
                continue
            adopted.append(
                {
                    "beat_id": beat_id,
                    "beat_code": str(beat["code"]),
                    "edition_id": edition_id,
                    "candidate_id": candidate_id,
                    "selection_id": str(result.get("selection_id") or ""),
                    "reused_existing_selection": bool(result.get("reused_existing_selection")),
                    "verdict": str(result.get("verdict") or ""),
                    "adoption_authority": str(result.get("adoption_authority") or authority),
                }
            )
    return {
        "project_id": project_id,
        "video_id": video_id,
        "editions": [str(item["id"]) for item in editions],
        "authority": authority,
        "actor": actor,
        "dry_run": bool(dry_run),
        "adopted": adopted,
        "adopted_count": len(adopted),
        "planned": planned,
        "planned_count": len(planned),
        "needs_review": needs_review,
        "needs_review_count": len(needs_review),
        "beats_without_candidate": beats_without_candidate,
        "beats_without_candidate_count": len(beats_without_candidate),
        "adoption_authority": authority,
        "unknown_is_not_a_pass": True,
        # A beat with no active selection can never be rendered, so the caller must
        # treat this as unfinished work rather than a pass.
        "every_beat_has_a_selection": (
            not needs_review
            and not beats_without_candidate
            and (bool(planned) if dry_run else True)
        ),
    }


def make_candidate_adopter() -> Callable[[ExplainerRepository, Mapping[str, Any]], dict[str, Any]]:
    """Port-shaped wrapper so the QC handler does not import this module."""

    def adopter(repo: ExplainerRepository, context: Mapping[str, Any]) -> dict[str, Any]:
        return adopt_generated_candidates(repo, context)

    return adopter


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
        aspect = str(output.get("aspect_ratio") or "16:9")
        if aspect not in _ASPECT_PIXELS:
            raise ExplainerContractError("SCHEMA_INVALID", "不支持的画幅", {"aspect_ratio": aspect})
        fps = output.get("fps") or {}
        fps_num = int(fps.get("num") or 25)
        fps_den = int(fps.get("den") or 1)
        subtitle_mode = str(output.get("subtitle_mode") or "NONE")
        subtitle_locales = [normalize_locale(str(item)) for item in (output.get("subtitle_locales") or [])]
        voice_locale = normalize_locale(str(output.get("voice_locale") or video["source_locale"]))
        # The key is derived from the shape, not trusted from the caller: a caller
        # that labels a 9:16 English edition ``zh-clean-169`` is describing something
        # other than what it asked for, and that must fail loudly.
        edition_key = canonical_edition_key(
            voice_locale=voice_locale, subtitle_mode=subtitle_mode, aspect_ratio=aspect
        )
        declared_key = str(output.get("edition_key") or "").strip()
        if declared_key and declared_key != edition_key:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "输出声明的 edition_key 与实际语言/字幕/画幅不一致",
                {"declared": declared_key, "derived": edition_key, "aspect_ratio": aspect,
                 "subtitle_mode": subtitle_mode, "voice_locale": voice_locale},
            )
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


# --------------------------------------------------------------------------- #
# port-style service factories
# --------------------------------------------------------------------------- #
# A stage handler *calls* one of these instead of constructing a service inline.
# The repository's architecture guard reports concrete cross-service construction
# inside a business method as new debt, so construction belongs in a
# ``make_*``/``build_*`` scope.  The imports stay function-local on purpose: the
# explainer modules import each other, and a module-level import here would create
# a cycle.
def make_narration_service(repo: Any) -> Any:
    from local_drama.application.explainers.narration import ExplainerNarrationService

    return ExplainerNarrationService(repo)


def make_subtitle_service(repo: Any) -> Any:
    from local_drama.application.explainers.subtitles import ExplainerSubtitleService

    return ExplainerSubtitleService(repo)


def make_delivery_service(repo: Any) -> Any:
    from local_drama.application.explainers.deliveries import ExplainerDeliveryService

    return ExplainerDeliveryService(repo)


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
    """Mark the newest take of one segment as the adopted machine take.

    Delegates to the narration service so the pipeline and the standalone TTS job
    family adopt takes the same way (audit A03).
    """

    return make_narration_service(repo).adopt_take(segment_id=segment_id, actor=actor)


# --------------------------------------------------------------------------- #
# IDENTITY_ASSETS
# --------------------------------------------------------------------------- #
def make_identity_assets_handler(
    repo_factory: Callable[[], Any],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``IDENTITY_ASSETS``: freeze the edition set and the entity roster.

    The stage is the graph's declared dependency for "reference assets and channel
    style".  It does **not** generate character reference imagery itself: the
    explainer's picture path is a real local image model driven per beat, and the
    identity inputs an operator adopts travel with the *generation command* that
    consumes them (a keyframe draw or a reference-edited redraw), not with this
    stage.  What the stage really does is

    * materialise the editions the frozen plan asked for, so every later stage has
      a clock, a canvas and a language;
    * record the entity roster the fact stage extracted, with the honest statement
      that this stage consumes no identity slot.

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
            f"已冻结 {len(editions)} 个输出版本与 {len(entities)} 个实体；"
            "本阶段不生成人物参考图，身份输入由各画面段的生成命令按需消费。",
            {
                "edition_ids": [str(item["id"]) for item in editions],
                "edition_keys": [str(item["edition_key"]) for item in editions],
                "entity_count": len(entities),
                "person_entity_codes": persons,
                "generated_reference_images": 0,
                "reference_generation_reason": "REFERENCE_GENERATION_BELONGS_TO_THE_GENERATION_COMMAND",
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
        receipts: dict[str, dict[str, Any]] = {}
        for item in execution.payload.get("items") or []:
            if str(item.get("status")) == "PASS":
                produced[str(item["id"])] = str(item["output"])
                # The per-item receipt is what actually reports whether the model
                # applied the requested speech rate natively and which sampling
                # parameters were used; it is carried to the take record instead
                # of being replaced by a hardcoded assumption.
                receipts[str(item["id"])] = dict(item)
        entry["batch_receipt"] = {
            "item_count": execution.payload.get("item_count"),
            "failed_count": execution.payload.get("failed_count"),
            "model_load_seconds": execution.payload.get("model_load_seconds"),
            "elapsed_seconds": execution.payload.get("elapsed_seconds"),
            "parameters": dict(execution.payload.get("parameters") or {}),
        }
        entry["item_receipts"] = receipts
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
                frozen = make_narration_service(repo).freeze_script(
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
                group["item_receipts"] = {}
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
                        pre_synthesized_receipt=group.get("item_receipts", {}).get(segment_id),
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

        The batch task reports aligner timestamps in the runtime's own vocabulary
        (``text``/``start_time``/``end_time`` seconds).  ``word_timings`` must use
        the pipeline's canonical vocabulary instead — ``token`` with
        ``start_sample``/``end_sample`` — because that is what
        ``_match_chunks_to_tokens`` and ``_build_display_mapping`` read.  Forwarding
        the raw dictionaries made every token unalignable while the stage still
        reported an aligned revision, so the conversion goes through the same
        normaliser the single-take adapter uses.
        """

        def __init__(self, timings: Mapping[str, Any], *, detector_version: str, model: str | None) -> None:
            self._timings = {
                str(key): normalize_aligner_timestamps(value) for key, value in timings.items()
            }
            # The rate the batch's positions are in: a runtime that declares its own
            # rate per timestamp is believed, otherwise the locked 16 kHz model is
            # assumed.  ``run_narration_align_job`` converts to the take's rate.
            self._aligner_sample_rate_hz = max(
                (declared_aligner_sample_rate(value) for value in timings.values()),
                default=DEFAULT_ALIGNER_SAMPLE_RATE_HZ,
            )
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
                "aligner_sample_rate_hz": self._aligner_sample_rate_hz,
                "network_used": False,
                "model": self._model,
            }

    class _BatchAsr:
        """ASR review port shared by the real adapter and the batch aligner.

        ``run_narration_align_job`` reads ``asr_result["text"]``.  Both the
        pipeline align stage and the standalone ``NARRATION_ALIGN`` job now inject
        a real ASR port, so "independent transcription" is a configured fact
        rather than a stage that silently recorded ``ASR_REVIEW_NOT_CONFIGURED``
        for every take and still passed.
        """

        def __init__(self, asr: Any) -> None:
            self._asr = asr

        def transcribe(self, *, media_path: Path, locale: str, timeout_seconds: int) -> Mapping[str, Any]:
            result = self._asr.transcribe(
                media_path=media_path, locale=locale, timeout_seconds=int(timeout_seconds)
            ) or {}
            text = str(result.get("text") or result.get("transcription") or "").strip()
            return {**dict(result), "text": text, "transcription": text}

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
        # Independent ASR review.  The previous handler passed ``asr=None``, so
        # every take in the production graph recorded
        # ``ASR_REVIEW_NOT_CONFIGURED`` and the stage passed without ever
        # comparing the audible narration against the script: "no misread was
        # found" was indistinguishable from "nothing was listened to".
        asr_port = _BatchAsr(asr) if asr is not None else None
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
                    asr=asr_port,
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
                # Stated explicitly so a report can never imply "independent ASR
                # found no misread" when no ASR port was wired at all.
                "asr_review_included": asr_port is not None,
                "asr_review_state": "WIRED" if asr_port is not None else "ASR_REVIEW_NOT_CONFIGURED",
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
                        "render_type": str(beat.get("render_type") or "I2V"),
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


#: Render types that represent a real, composable clip that has been produced.  A
#: retired still-motion row is deliberately absent: it is history, not a clip.
REAL_CLIP_RENDER_TYPES = frozenset({"I2V", "INFOGRAPHIC", "LICENSED_MEDIA"})


def _manifest_picture_path(clips: Sequence[Mapping[str, Any]]) -> str:
    """The honest picture path of a composition, derived from its own clips.

    A render whose every clip is an ``I2V`` model output is an AI-generated film; one
    that also carries infographic or licensed-material clips is mixed.  Reporting a
    constant here — the retired code always wrote "deterministic typeset card
    motion" — would misdescribe a delivered film, so the value is computed.
    """

    types = {str(item.get("render_type_actual") or "") for item in clips}
    types.discard("")
    if types and types == {"I2V"}:
        return "AI_I2V_GENERATED"
    if "I2V" in types:
        return "MIXED_AI_I2V_AND_OTHER"
    if types:
        return "NO_AI_I2V_" + "_".join(sorted(types))
    return "PICTURE_PATH_UNRECORDED"


def _aggregate_qc_status(statuses: Sequence[str]) -> str:
    """The worst verdict across a film's per-layer QC reports.

    QC writes one report per layer, and a bundle that names only the newest one can
    present a NOT_RUN layer as the film's whole QC state.  The aggregate is the most
    severe verdict, so a PASS on one layer can never hide a FAIL or an unchecked
    layer on another.
    """

    order = {
        "FAIL": 6,
        "BLOCKED": 5,
        "CORRUPT": 5,
        "TERMINAL_FAILED": 5,
        "PARTIAL": 4,
        "NEEDS_HITL": 3,
        "STALE_REVISION": 3,
        "NOT_RUN": 2,
        "PASS_WITH_ISSUES": 1,
        "PASS": 0,
        "SUCCEEDED": 0,
    }
    present = [str(item).upper() for item in statuses if str(item)]
    if not present:
        return "NOT_RUN"
    return max(present, key=lambda item: order.get(item, 3))


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


def _last_frames(error: BaseException, *, limit: int = 1200) -> str:
    """The tail of a traceback, for a report a human has to act on."""

    return "".join(traceback.format_exception(type(error), error, error.__traceback__))[-limit:]


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
Style: Kicker,Microsoft YaHei,{kicker_size},&H005482B7,&H005482B7,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,2,0,7,{margin},{margin},{kicker_v},1
Style: Card,Microsoft YaHei,{card_size},&H00D0DDE4,&H00D0DDE4,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,0,1,7,{margin},{margin},{card_v},1
Style: Body,Microsoft YaHei,{body_size},&H00C8D2DA,&H00C8D2DA,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,0,1,7,{margin},{margin},{body_v},1
Style: Footer,Microsoft YaHei,{footer_size},&H00A0B4C0,&H00A0B4C0,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,2,0,2,{footer_margin},{footer_margin},{footer_v},1

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


def _generated_disclosure_line() -> str:
    """The in-frame disclosure a *generated* picture must carry.

    The typeset-card wording says "非生成画面" and would be a false statement on a
    frame the image model drew, so the generated path states what actually happened:
    the picture is model-generated, not photographed.
    """

    return "AI 生成画面 · 本机图像模型（非实拍）"


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
    picture_runtime: Any | None = None,
    motion_runtime: Any | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``VISUAL_GENERATION``: one real AI clip per beat.

    An explainer's moving pictures are **real image-to-video generations**.  The
    retired path rendered a still picture and gave it a deterministic FFmpeg camera
    push; that is not a video model's output, it cannot honestly be labelled
    ``I2V``, and the product no longer offers it.  The path here is therefore: run
    the bound local image model once per beat to get the first frame, adopt that
    frame as the beat's keyframe, then run the bound local image-to-video model on
    that keyframe to get the composable clip.

    Both ``picture_runtime`` and ``motion_runtime`` run **inside this stage**: the
    worker claims one job at a time, so a stage that waited on its own child GPU job
    would deadlock, and the stage already runs under the worker's lease heartbeat.

    Nothing here falls back to a still image.  A beat whose real generation failed
    is recorded as a named failure and keeps no candidate, which is the honest
    outcome: a missing clip must stay visible, not be papered over with a picture
    that pretends to be video.
    """

    from local_drama.infrastructure.composition.ffmpeg_renderer import (
        FfmpegCommand,
        FfmpegRunner,
        escape_filter_value,
    )

    ffmpeg_path = getattr(settings, "ffmpeg_path", None)
    ffprobe_path = getattr(settings, "ffprobe_path", None)
    runner = FfmpegRunner(ffmpeg=ffmpeg_path or "ffmpeg", ffprobe=ffprobe_path or "ffprobe", timeout_seconds=1800.0)
    generated_root = Path(work_root) / "explainer_generated"

    def _fit_generated_still(
        *,
        source_image: Path,
        still_path: Path,
        ass_path: Path | None,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        """Scale a generated picture onto the edition canvas and burn its labels.

        The model is asked for the nearest grid-aligned canvas of the same aspect
        ratio (864x480 for an 854x480 edition), so the difference is a few pixels of
        padding — never a crop, because a crop would silently change the
        composition the operator's edition declares.  The kicker and the AI
        disclosure are drawn here; the narration caption is *not*, because the
        captioned edition burns the subtitle track during composition and a second
        copy would double it.
        """

        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={_CARD_PALETTE['background']}",
            # A real photograph has no design-controlled contrast, so the kicker and
            # the disclosure sit on a translucent band instead of vanishing into a
            # bright sky or a white wall.
            f"drawbox=x=0:y=0:w={width}:h={max(8, int(height * 0.12))}:"
            f"color={_CARD_PALETTE['band']}@0.55:t=fill",
            f"drawbox=x=0:y={max(8, int(height * 0.12))}:w={width}:h={max(2, int(round(height * 0.0056)))}:"
            f"color={_CARD_PALETTE['accent']}@0.85:t=fill",
            f"drawbox=x=0:y={height - max(10, int(height * 0.13))}:w={width}:h={max(10, int(height * 0.13))}:"
            f"color={_CARD_PALETTE['band']}@0.55:t=fill",
        ]
        if ass_path is not None:
            filters.append(f"subtitles=filename={escape_filter_value(str(ass_path))}")
        filters.append("format=yuv420p")
        command = FfmpegCommand(
            args=(
                "-hide_banner", "-nostats", "-y",
                "-i", str(source_image),
                "-vf", ",".join(filters),
                "-frames:v", "1",
                str(still_path),
            ),
            purpose="EXPLAINER_GENERATED_STILL",
            note="把本机生成的画面按交付画布等比缩放并补齐，不裁切构图",
        )
        return dict(runner.run(command))

    def _generated_prompt(beat: Mapping[str, Any]) -> str:
        """The prompt handed to the local image model for one beat.

        The storyboard already writes a generation-ready English ``prompt_intent``
        and it is used verbatim, so the picture matches the shot the planner
        described.  On-screen words are the subtitle layer's job — a model asked to
        draw letters produces garbage — so the suffix asks for a photographic frame
        with no rendered text.
        """

        prompt = str(beat.get("prompt_intent") or "").strip() or str(beat.get("visual_intent") or "").strip()
        if not prompt:
            prompt = "a realistic documentary illustration of the narrated subject"
        return (
            f"{prompt} Photographic documentary still, natural lighting, cinematic composition, "
            "high detail, no text, no letters, no captions, no watermark."
        )

    def _generation_seed(*, video_id: str, beat_id: str) -> int:
        """A reproducible seed per beat, so a re-run regenerates the same frame."""

        digest = hashlib.sha256(f"{video_id}:{beat_id}:image".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _motion_seed(*, video_id: str, beat_id: str) -> int:
        """A reproducible motion seed per beat, independent of the keyframe seed."""

        digest = hashlib.sha256(f"{video_id}:{beat_id}:motion".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _beat_keyframe(
        *,
        runtime: Any,
        probe: Mapping[str, Any],
        video_id: str,
        beat_id: str,
        beat: Mapping[str, Any],
        kicker: str,
        footer: str,
        duration_seconds: float,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        """One real generated first frame for one beat.

        The first frame is the *input* to the image-to-video model, so it is a real
        generated picture fitted onto the edition canvas with its kicker and AI
        disclosure burned in.  The narration caption is not drawn here: the captioned
        edition burns the subtitle track during composition and a second copy would
        double it.
        """

        prompt = _generated_prompt(beat)
        image = runtime.generate_image(
            binding=probe,
            prompt=prompt,
            width=width,
            height=height,
            seed=_generation_seed(video_id=video_id, beat_id=beat_id),
            negative_prompt=str(getattr(settings, "explainer_generation_negative_prompt", "") or ""),
            steps=int(getattr(settings, "explainer_generation_steps", 20) or 20),
            output_prefix=f"local_drama/explainer/{video_id[:8]}",
            timeout_seconds=float(getattr(settings, "explainer_generation_timeout_seconds", 600.0) or 600.0),
        )
        beat_dir = generated_root / video_id / beat_id
        beat_dir.mkdir(parents=True, exist_ok=True)
        label_ass = beat_dir / "labels.ass"
        label_ass.write_text(
            _card_ass(
                text="", kicker=kicker, footer=footer, duration_seconds=duration_seconds, width=width, height=height
            ),
            encoding="utf-8",
        )
        still_path = beat_dir / "keyframe.png"
        fit = _fit_generated_still(
            source_image=Path(image["path"]),
            still_path=still_path,
            ass_path=label_ass,
            width=width,
            height=height,
        )
        if fit.get("status") != "SUCCEEDED":
            raise RuntimeError(f"generated-still-fit:{fit.get('stage') or fit.get('status')}")
        return {
            "status": "SUCCEEDED",
            "still_path": still_path,
            "prompt": prompt,
            "image": image,
            "lineage": {
                "source": "LOCAL_GENERATED_IMAGE",
                "generated_picture_model": str(image.get("model_code") or ""),
                "generation_profile_version_id": str(image.get("profile_version_id") or ""),
                "generation_workflow_version_id": str(image.get("workflow_version_id") or ""),
                "generation_runtime_code": str(image.get("runtime_code") or ""),
                "generation_prompt_id": str(image.get("prompt_id") or ""),
                "generation_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "generation_seed": int(image.get("seed") or 0),
                "generation_width": int(image.get("generation_width") or 0),
                "generation_height": int(image.get("generation_height") or 0),
                "generation_steps": int(image.get("steps") or 0),
                "generation_seconds": float(image.get("elapsed_seconds") or 0.0),
                "image_sha256": str(image.get("sha256") or ""),
                "note": "首帧由本机图像生成模型产出，作为图生视频的输入。",
            },
            "execution_snapshot": {
                "provider": f"COMFYUI:{image.get('runtime_code') or 'local'}",
                "model_code": str(image.get("model_code") or ""),
                "network_used": False,
            },
        }

    def _beat_clip(
        *,
        project_id: str,
        runtime: Any,
        probe: Mapping[str, Any],
        video_id: str,
        beat_id: str,
        beat: Mapping[str, Any],
        keyframe_media_version_id: str,
        keyframe_sha256: str,
        frames: int,
    ) -> dict[str, Any]:
        """One real AI image-to-video clip for one beat.

        The clip is a model output, so ``render_type_actual`` is ``I2V`` — the honest
        value.  There is no still-image substitute: a model that did not run cannot
        produce a clip, and the caller records the failure instead.
        """

        motion_prompt = str(beat.get("prompt_intent") or "").strip() or str(beat.get("visual_intent") or "").strip()
        if not motion_prompt:
            motion_prompt = "a slow, natural continuation of the narrated subject"
        motion_prompt = (
            f"{motion_prompt} Animate this exact frame: one continuous natural motion of the subject "
            "and one restrained camera move, keeping the identity, wardrobe, scene and lighting "
            "unchanged. No text, no letters, no captions, no watermark."
        )
        video = runtime.generate_video(
            binding=probe,
            project_id=project_id,
            first_frame_media_version_id=keyframe_media_version_id,
            first_frame_sha256=keyframe_sha256,
            prompt=motion_prompt,
            seed=_motion_seed(video_id=video_id, beat_id=beat_id),
            frames=frames,
            output_prefix=f"local_drama/explainer/{video_id[:8]}",
            timeout_seconds=float(getattr(settings, "explainer_video_timeout_seconds", 1800.0) or 1800.0),
        )
        return {
            "status": "SUCCEEDED",
            "clip_path": Path(str(video["path"])),
            "actual_type": "I2V",
            "fallback_reason": None,
            "lineage": {
                "source": "LOCAL_GENERATED_VIDEO",
                "generated_motion_model": str(video.get("capability") or ""),
                "motion_profile_version_id": str(video.get("profile_version_id") or ""),
                "motion_workflow_version_id": str(video.get("workflow_version_id") or ""),
                "motion_prompt_sha256": hashlib.sha256(motion_prompt.encode("utf-8")).hexdigest(),
                "motion_seed": int(video.get("seed") or 0),
                "motion_prompt_id": str(video.get("prompt_id") or ""),
                "motion_seconds": float(video.get("elapsed_seconds") or 0.0),
                "clip_sha256": str(video.get("sha256") or ""),
                "first_frame_media_version_id": str(keyframe_media_version_id),
                "first_frame_sha256": str(keyframe_sha256),
                "frames_requested": int(video.get("frames_requested") or frames),
                "planned_render_type": str(beat.get("render_type") or "I2V"),
                "note": "片段由本机图生视频模型产出，是真实的模型生成动态。",
            },
            "execution_snapshot": {
                "provider": f"COMFYUI:{video.get('profile_version_id') or 'local'}",
                "capability": str(video.get("capability") or ""),
                "network_used": False,
            },
        }

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = dict(context.get("semantic_inputs") or {})
        project_id = str(payload.get("project_id") or "")
        video_id = str(payload.get("video_id") or "")
        generated: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        capability_snapshot = payload.get("capability_snapshot")
        if not isinstance(capability_snapshot, Mapping):
            capability_snapshot = None
        picture_probe: dict[str, Any] = {"available": False, "reason": "PICTURE_RUNTIME_NOT_CONFIGURED"}
        if picture_runtime is not None:
            try:
                picture_probe = dict(picture_runtime.probe(capability_snapshot))
            except Exception as error:  # a broken runtime degrades, it never aborts the stage
                picture_probe = {
                    "available": False,
                    "reason": "PICTURE_PROBE_FAILED",
                    "detail": {"error": type(error).__name__},
                }
        # The moving picture is a real image-to-video generation; without a working
        # motion runtime the beat has no clip at all, so the probe is a hard gate.
        motion_probe: dict[str, Any] = {"available": False, "reason": "MOTION_RUNTIME_NOT_CONFIGURED"}
        if motion_runtime is not None:
            try:
                motion_probe = dict(motion_runtime.probe(capability_snapshot))
            except Exception as error:
                motion_probe = {
                    "available": False,
                    "reason": "MOTION_PROBE_FAILED",
                    "detail": {"error": type(error).__name__},
                }
        with repo_factory() as repo:
            video, editions = _video_and_editions(repo, project_id=project_id, video_id=video_id)
            if not editions:
                return _blocked("该作品没有输出版本，无法生成画面", "SCHEMA_INVALID", {"video_id": video_id})
            beats = {str(item["id"]): item for item in repo.beats(video_id)}
            generated_disclosure = _generated_disclosure_line()
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
                            "render_type": placement.get("render_type") or "I2V",
                            "segment_ids": [str(placement["segment_id"])],
                            "visual_intent": str(placement.get("visual_intent") or ""),
                        }
            # A caller may narrow the stage to an explicit beat selection (a repair of
            # one画面段, or a first pass over a few beats).  An empty list means the
            # whole film, which is what the production graph always asks for.
            requested_beats = {
                str(item)
                for item in (payload.get("beat_ids") or [])
                if str(item).strip()
            }
            if requested_beats:
                unknown = sorted(requested_beats - set(beats))
                if unknown:
                    return _blocked(
                        "所选画面段不属于该作品",
                        "INVALID_REQUEST",
                        {"video_id": video_id, "unknown_beat_ids": unknown[:10]},
                    )
                needed = {key: value for key, value in needed.items() if key in requested_beats}
            # Only a READY *VISUAL* candidate that is a real produced clip means this
            # beat already has one.  Keying this by any candidate made a beat with only
            # a keyframe (or only an entity reference) look finished; counting a
            # retired still-motion row would make a film whose clips are the removed
            # still-with-a-camera-move look complete and never regenerate them.
            existing = {
                str(item["beat_id"]): item
                for item in repo.list_where("explainer_media_candidates", {"video_id": video_id})
                if str(item.get("status")) == "READY"
                and str(item.get("purpose") or "VISUAL") == "VISUAL"
                and str(item.get("render_type_actual") or "") in REAL_CLIP_RENDER_TYPES
            }
            requested_edition_id = str(payload.get("edition_id") or "").strip()
            primary_edition = next(
                (item for item in editions if str(item["id"]) == requested_edition_id),
                editions[0],
            )
            width = int(primary_edition["width"])
            height = int(primary_edition["height"])
            fps_num = int(primary_edition["fps_num"])
            fps_den = int(primary_edition["fps_den"])

        def _run_beats() -> None:
            from local_drama.application.explainers.storyboard import build_storyboard_service

            for beat_id, requirement in sorted(needed.items(), key=lambda item: int(beats[item[0]]["ordinal"])):
                if beat_id in existing:
                    generated.append({"beat_id": beat_id, "reused": True, "candidate_id": str(existing[beat_id]["id"])})
                    continue
                beat = beats[beat_id]
                frames = max(1, int(requirement["frames"]))
                duration_seconds = frames * fps_den / fps_num
                with repo_factory() as repo:
                    entities = [
                        str(code) for code in (beat.get("entity_refs_json") or []) if str(code)
                    ][:6]
                    entity_line = ("实体：" + "、".join(entities)) if entities else ""
                    # A first frame the operator already adopted and locked is the
                    # input, not a suggestion: re-drawing it would silently discard a
                    # human decision and waste a GPU run.  Only a beat without an
                    # adopted keyframe gets a fresh one.
                    adopted_keyframe = repo.resolved_beat_selection(
                        beat_id, str(primary_edition["id"]), purpose="KEYFRAME"
                    )
                kicker = f"画面 {int(beat['ordinal']) + 1:02d} · {beat['code']}"
                generated_footer = " ｜ ".join([part for part in (entity_line, generated_disclosure) if part])
                # 1) real first frame; 2) real image-to-video clip on that frame.
                # There is no still-image substitute any more: a beat that cannot be
                # really generated is reported and left without a candidate.
                keyframe: dict[str, Any] | None = None
                if adopted_keyframe is not None and str(adopted_keyframe.get("media_version_id") or ""):
                    keyframe_media_version_id = str(adopted_keyframe["media_version_id"])
                    keyframe_sha256 = str(adopted_keyframe.get("media_sha256") or "")
                    keyframe_lineage: dict[str, Any] = {
                        "source": "ADOPTED_KEYFRAME",
                        "selection_id": str(adopted_keyframe.get("id") or ""),
                        "candidate_id": str(adopted_keyframe.get("candidate_id") or ""),
                        "adopted_by": str(adopted_keyframe.get("actor") or ""),
                        "adoption_authority": str(adopted_keyframe.get("adoption_authority") or ""),
                        "note": "首帧沿用人工已采用的首帧候选，未重新生成。",
                    }
                else:
                    try:
                        keyframe = _beat_keyframe(
                            runtime=picture_runtime,
                            probe=picture_probe,
                            video_id=video_id,
                            beat_id=beat_id,
                            beat=beat,
                            kicker=kicker,
                            footer=generated_footer,
                            duration_seconds=duration_seconds,
                            width=width,
                            height=height,
                        )
                    except Exception as error:
                        code = str(getattr(error, "code", type(error).__name__))
                        # The failing frame is recorded with the beat: a bare error
                        # class name ("ValueError") told the operator nothing about
                        # which step of the picture path actually failed.
                        failures.append(
                            {
                                "beat_id": beat_id,
                                "stage": "picture-generation",
                                "reason": str(picture_probe.get("reason") or code) if not picture_probe.get("available") else code,
                                "detail": _last_frames(error),
                            }
                        )
                        continue
                    # The first frame becomes a project media version first: the I2V
                    # input bridge only accepts a verified business reference, never a
                    # path, so the file has to be registered and hashed before the
                    # motion model may consume it.
                    registered_keyframe = media_service.import_file(
                        project_id,
                        Path(str(keyframe["still_path"])),
                        purpose="EXPLAINER_BEAT_KEYFRAME",
                        owner_type="EXPLAINER_VIDEO",
                        owner_id=video_id,
                        media_kind="IMAGE",
                        stage="KEYFRAME",
                        actor="explainer-worker",
                        schedule_derivatives=True,
                    )
                    keyframe_media_version_id = str(registered_keyframe.get("media_version_id"))
                    keyframe_sha256 = str(registered_keyframe.get("sha256"))
                    keyframe_lineage = dict(keyframe["lineage"])
                try:
                    attempt = _beat_clip(
                        project_id=project_id,
                        runtime=motion_runtime,
                        probe=motion_probe,
                        video_id=video_id,
                        beat_id=beat_id,
                        beat=beat,
                        keyframe_media_version_id=keyframe_media_version_id,
                        keyframe_sha256=keyframe_sha256,
                        frames=frames,
                    )
                except Exception as error:
                    code = str(getattr(error, "code", type(error).__name__))
                    failures.append(
                        {
                            "beat_id": beat_id,
                            "stage": "motion-generation",
                            "reason": str(motion_probe.get("reason") or code) if not motion_probe.get("available") else code,
                            "detail": _last_frames(error),
                        }
                    )
                    continue
                if keyframe is not None:
                    try:
                        with repo_factory() as repo:
                            # The keyframe is registered as its own adopt-able candidate so
                            # the operator can re-draw the first frame and re-run motion from
                            # it without losing the clip they already accepted.
                            build_storyboard_service(repo).register_candidate(
                                project_id=str(video["project_id"]),
                                video_id=video_id,
                                beat_id=beat_id,
                                candidate_kind="CREATIVE",
                                media_version_id=keyframe_media_version_id,
                                render_type_actual=None,
                                fallback_reason=None,
                                purpose="KEYFRAME",
                                lineage={**keyframe_lineage, "adopted_by": "explainer-worker"},
                                execution_snapshot=dict(keyframe["execution_snapshot"]),
                                qc_summary={
                                    "file_valid": True,
                                    "measured_by": "VISUAL_GENERATION",
                                    "content_checked": False,
                                },
                            )
                    except Exception as error:
                        # One beat's registration failure must not throw away the other
                        # beats' finished generations.
                        failures.append(
                            {
                                "beat_id": beat_id,
                                "stage": "keyframe-registration",
                                "reason": str(getattr(error, "code", type(error).__name__)),
                                "detail": _last_frames(error),
                            }
                        )
                clip_path = Path(str(attempt["clip_path"]))
                registered = media_service.import_file(
                    project_id,
                    clip_path,
                    purpose="EXPLAINER_BEAT_CLIP",
                    owner_type="EXPLAINER_VIDEO",
                    owner_id=video_id,
                    media_kind="VIDEO",
                    stage="VISUAL_GENERATION",
                    actor="explainer-worker",
                    # Step 4/5 renders a card per candidate.  Every other media
                    # producer in this codebase queues the default thumbnail (and
                    # proxy) at registration time; without it the read-only
                    # thumbnail endpoint answers ``MEDIA_DERIVATIVE_NOT_READY`` and
                    # the candidate grid can only show a placeholder.
                    schedule_derivatives=True,
                )
                with repo_factory() as repo:
                    # Design §D2.1: candidate registration has exactly one entry point.
                    # This stage used to insert the row itself, which meant the worker
                    # path skipped the budget ledger and the variant numbering every
                    # other producer honours, so two producers could both claim
                    # ``variant_no = 1`` for one beat.
                    # The clip is a real model output, so its render type is ``I2V``.
                    registered_actual = str(attempt["actual_type"])
                    render_type_actual = registered_actual
                    try:
                        candidate = build_storyboard_service(repo).register_candidate(
                            project_id=str(video["project_id"]),
                            video_id=video_id,
                            beat_id=beat_id,
                            candidate_kind="CREATIVE",
                            media_version_id=str(registered.get("media_version_id")),
                            render_type_actual=render_type_actual,
                            fallback_reason=attempt.get("fallback_reason"),
                            purpose="VISUAL",
                            lineage=dict(attempt["lineage"]),
                            execution_snapshot=dict(attempt["execution_snapshot"]),
                            # Only the facts this stage really measured: the media was
                            # produced and registered with a hash, so the file exists.
                            # Content, identity and readability need a picture check and
                            # stay absent — an absent check is UNKNOWN, never a pass,
                            # which is what keeps this candidate out of machine adoption
                            # until the checks run (design §6.2).
                            qc_summary={
                                "file_valid": True,
                                "duration_ms": int(round(duration_seconds * 1000)),
                                "render_type_actual": registered_actual,
                                "measured_by": "VISUAL_GENERATION",
                                "content_checked": False,
                            },
                        )
                    except Exception as error:
                        # A finished GPU generation must never be lost to a bookkeeping
                        # refusal: the media version is already registered, so the clip
                        # is recoverable, and the remaining beats keep generating.
                        failures.append(
                            {
                                "beat_id": beat_id,
                                "stage": "clip-registration",
                                "reason": str(getattr(error, "code", type(error).__name__)),
                                "media_version_id": str(registered.get("media_version_id")),
                                "detail": _last_frames(error),
                            }
                        )
                        continue
                    # Adoption is NOT this stage's job: ``EXPLAINER_VISUAL_QC`` adopts
                    # after the checks have run.  Inserting the selection here made the
                    # candidate active before anything had examined it, and the beat
                    # was then rendered from an unverified picture (audit A06).
                    # The beat row must state what really happened, not what the
                    # planner assumed before any picture existed.
                    repo.update(
                        "explainer_visual_beats",
                        beat_id,
                        {
                            "actual_fallback_type": str(attempt["actual_type"]),
                            "fallback_reason": attempt.get("fallback_reason"),
                        },
                        actor="explainer-worker",
                    )
                generated.append(
                    {
                        "beat_id": beat_id,
                        "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or (candidate.get("candidate") or {}).get("id")),
                        "media_version_id": registered.get("media_version_id"),
                        "frames": frames,
                        "source": str(attempt["lineage"].get("source") or ""),
                        "actual_type": str(attempt["actual_type"]),
                    }
                )

        session_cm: Any = None
        if picture_probe.get("available") and picture_runtime is not None:
            try:
                session_cm = picture_runtime.session(owner_ref=f"explainer-visual-{video_id}")
            except Exception as error:
                picture_probe = {
                    **{key: value for key, value in picture_probe.items() if key != "detail"},
                    "available": False,
                    "reason": "PICTURE_GPU_LEASE_FAILED",
                    "detail": {"error": type(error).__name__},
                }
                session_cm = None
        if session_cm is None:
            _run_beats()
        else:
            with session_cm:
                _run_beats()
        if not generated:
            return _blocked(
                "没有任何画面段生成可用的 AI 图生视频片段",
                "MEDIA_CANDIDATES_MISSING",
                {
                    "failures": failures[:5],
                    "beat_count": len(needed),
                    "picture_probe": {key: value for key, value in picture_probe.items() if key != "detail"},
                    "motion_probe": {key: value for key, value in motion_probe.items() if key != "detail"},
                },
            )
        fresh = [item for item in generated if not item.get("reused")]
        clip_count = sum(1 for item in fresh if item.get("actual_type") == "I2V")
        return _passed(
            f"已为 {len(generated)} 个画面段准备 AI 图生视频片段（本次新生成 {clip_count} 段）。",
            {
                "candidates": generated,
                "failures": failures,
                "beat_count": len(needed),
                "planned_types": sorted({str(item.get("render_type")) for item in needed.values()}),
                "generation_probe": {key: value for key, value in picture_probe.items() if key != "detail"},
                "motion_probe": {key: value for key, value in motion_probe.items() if key != "detail"},
                "generated_beat_count": clip_count,
                "typeset_card_beat_count": 0,
                "disclosure": (
                    "画面首帧由本机图像生成模型产出，动态由本机图生视频模型真实生成；"
                    "生成失败或不可用的画面段不会用静图顶替，已记录为缺口。"
                ),
            },
        )

    return handler


# --------------------------------------------------------------------------- #
# SUBTITLE_BUILD
# --------------------------------------------------------------------------- #
#: Sentence enders.  A cue may always end right after one of these.
_CUE_SENTENCE_END = "。！？；!?;"
#: Clause enders.  Preferred boundaries when one sentence does not fit one cue:
#: Chinese subtitles break at a comma, never at an arbitrary character.
_CUE_CLAUSE_END = "，、：,:"
#: Punctuation that must never end a cue, and must never start one.  Ambiguous
#: quote characters are deliberately absent from both sets: a straight quote is
#: as likely to open as to close, and guessing wrongly would move a boundary.
_CUE_OPENING = "（(《〈【[“‘"
_CUE_CLOSING = "）)》〉】]”’"
#: A cue boundary never falls inside a run of ASCII letters or digits, so "50件"
#: stays readable instead of being cut into "5" and "0件".
_CUE_ASCII_WORD_CHAR = re.compile(r"[0-9A-Za-z]")
#: Shortest tail a split may leave behind.  Below this the viewer sees a two or
#: three character flicker — the delivered 1962 film had cues reading "铁网。",
#: "0件偷来的雨衣…" and "闯必死无疑。" because the splitter cut on a character
#: count alone.
MIN_CUE_FRAGMENT_CHARS = 6

_CUE_SENTENCE_RE = re.compile(rf"[^{_CUE_SENTENCE_END}]*[{_CUE_SENTENCE_END}]?")

#: A mapped alignment clock is used for cue boundaries only when it really explains
#: the take: it must reach this fraction of the narration clip, and the pace it
#: implies must be human.  The delivered 1962 film's clock covered a third of each
#: clip at ~19 characters per second, which the builder trusted.
MIN_ALIGNMENT_CLOCK_COVERAGE = 0.5
MIN_MS_PER_DISPLAY_CHARACTER = 60.0
MAX_MS_PER_DISPLAY_CHARACTER = 500.0


def _split_cue_text(text: str, *, max_chars: int) -> list[str]:
    """Deterministic sentence split, then a hard wrap for an over-long clause."""

    return [chunk for chunk, _start, _end in _split_cue_chunks(text, max_chars=max_chars)]


def _choose_cue_break(piece: str, *, cursor: int, max_chars: int) -> int:
    """The offset inside ``piece`` where the next cue should end.

    Order of preference, all inside the ``max_chars`` window: a clause boundary, a
    Latin word boundary, and only then a hard break — which still refuses to cut an
    ASCII word or to leave a closing bracket or a fragment of punctuation alone.
    The returned offset is always in ``(cursor, cursor + max_chars]``.
    """

    length = len(piece)
    upper = min(length - 1, cursor + max_chars)
    if upper <= cursor:
        return length
    # A tail shorter than ``MIN_CUE_FRAGMENT_CHARS`` is only acceptable when the
    # remaining text cannot fill it (a genuinely short last sentence).
    minimum_tail = min(MIN_CUE_FRAGMENT_CHARS, max(1, length - cursor - MIN_CUE_FRAGMENT_CHARS))
    for end in range(upper, cursor, -1):
        if piece[end - 1] in _CUE_CLAUSE_END and length - end >= minimum_tail:
            return end
    for end in range(upper, cursor, -1):
        if piece[end - 1].isspace() and length - end >= minimum_tail:
            return end
    end = upper
    # The walk-backs are bounded: a window that is entirely one ASCII run (a long
    # URL, a serial number) has no legal boundary inside it at all, and walking to
    # the window start would emit one-character cues instead of an honest mid-token
    # break.  A third of the window is the shortest cue this splitter will create.
    floor = cursor + max(1, max_chars // 3)
    while end > floor and (
        _CUE_ASCII_WORD_CHAR.match(piece[end - 1]) and _CUE_ASCII_WORD_CHAR.match(piece[end])
    ):
        end -= 1
    while end > floor and piece[end] in _CUE_CLOSING:
        end -= 1
    while end > floor and piece[end - 1] in _CUE_OPENING:
        end -= 1
    if end == floor and piece[cursor:upper] and all(
        _CUE_ASCII_WORD_CHAR.match(character) for character in piece[cursor:upper]
    ):
        # The whole window is a single unbroken token; an honest break at the window
        # edge keeps the chunk sizes even instead of leaving the tail of the run to
        # be re-split with a different floor.  The tail floor still wins, so a short
        # run is not split into a long head and a two-character tail.
        end = min(upper, max(floor, length - minimum_tail))
    if length - end < minimum_tail:
        candidate = max(floor, length - minimum_tail)
        if candidate < end and not (
            _CUE_ASCII_WORD_CHAR.match(piece[candidate - 1]) and _CUE_ASCII_WORD_CHAR.match(piece[candidate])
        ):
            end = candidate
    return max(cursor + 1, min(end, cursor + max_chars))


def _split_cue_chunks(text: str, *, max_chars: int) -> list[tuple[str, int, int]]:
    """``(text, start_offset, end_offset)`` chunks, so a chunk maps back to a span.

    The offsets are what let a cue's boundary come from the alignment instead of
    from an assumed reading speed.  The previous builder consumed only the chunk
    *strings*, so a subtitle revision recorded alignment revision ids while its
    cue times were still an even character-proportional split of the segment.

    A chunk is never a raw character slice of a clause: the boundary is chosen by
    :func:`_choose_cue_break`, which prefers punctuation and protects words, so a
    cue reads as a phrase instead of a severed fragment.
    """

    max_chars = max(1, int(max_chars))
    pieces: list[tuple[str, int, int]] = []
    for match in _CUE_SENTENCE_RE.finditer(text):
        piece = match.group(0)
        if piece:
            pieces.append((piece, match.start(), match.end()))
    if not pieces:
        pieces = [(text, 0, len(text))]
    chunks: list[tuple[str, int, int]] = []
    for piece, piece_start, _piece_end in pieces:
        stripped_start = piece_start + (len(piece) - len(piece.lstrip()))
        stripped = piece.strip()
        if not stripped:
            continue
        cursor = 0
        while len(stripped) - cursor > max_chars:
            end = _choose_cue_break(stripped, cursor=cursor, max_chars=max_chars)
            chunks.append((stripped[cursor:end], stripped_start + cursor, stripped_start + end))
            cursor = end
        if stripped[cursor:]:
            chunks.append((stripped[cursor:], stripped_start + cursor, stripped_start + len(stripped)))
    if not chunks:
        return [(text[:max_chars], 0, min(len(text), max_chars))]
    return chunks


def _display_map_span(alignment: Mapping[str, Any]) -> list[tuple[int, int, int, int]]:
    """``(display_start, display_end, start_sample, end_sample)`` from a revision.

    Mapped entries only: an unmapped display span carries no time, and inventing
    one is exactly what the declared evidence rules forbid.
    """

    raw = alignment.get("display_map_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    if not isinstance(raw, Sequence):
        return []
    spans: list[tuple[int, int, int, int]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        start_offset = entry.get("display_start")
        end_offset = entry.get("display_end")
        start_sample = entry.get("start_sample")
        end_sample = entry.get("end_sample")
        if start_offset is None or end_offset is None or start_sample is None or end_sample is None:
            continue
        try:
            spans.append(
                (
                    int(start_offset),
                    int(end_offset),
                    int(start_sample),
                    int(end_sample),
                )
            )
        except (TypeError, ValueError):
            continue
    spans.sort(key=lambda item: (item[0], item[1]))
    return spans


def _alignment_clock_verdict(
    spans: Sequence[tuple[int, int, int, int]],
    *,
    sample_rate_hz: int,
    clip_start_ms: int,
    clip_end_ms: int,
) -> dict[str, Any]:
    """Whether a mapped clock may be used for cue boundaries at all.

    An aligner clock is only a clock when it explains the take: the mapped spans
    must reach a real part of the clip, and the pace they imply must be human.  The
    1962 film's alignment declared the take's 48 kHz rate over 16 kHz positions, so
    the map covered a third of each clip and implied ~19 characters per second; the
    subtitle builder trusted it and cut each sentence into one very short cue and one
    very long one.  A clock that fails either test is refused, and every cue of that
    take is timed by the deterministic character-proportional fallback instead.
    """

    clip_ms = max(1, int(clip_end_ms) - int(clip_start_ms))
    if not spans or sample_rate_hz <= 0:
        return {
            "trusted": False,
            "reason": "NO_MAPPED_SPANS",
            "coverage": 0.0,
            "ms_per_character": None,
            "mapped_span_count": len(spans),
        }
    last_end_ms = max(int(span[3]) for span in spans) * 1000.0 / float(sample_rate_hz)
    first_start_ms = min(int(span[2]) for span in spans) * 1000.0 / float(sample_rate_hz)
    first_display = min(int(span[0]) for span in spans)
    last_display = max(int(span[1]) for span in spans)
    characters = max(1, last_display - first_display)
    coverage = last_end_ms / float(clip_ms)
    pace_ms = (last_end_ms - first_start_ms) / characters
    verdict: dict[str, Any] = {
        "trusted": True,
        "reason": None,
        "coverage": round(coverage, 4),
        "ms_per_character": round(pace_ms, 2),
        "mapped_span_count": len(spans),
        "last_mapped_end_ms": round(last_end_ms, 2),
    }
    if coverage < MIN_ALIGNMENT_CLOCK_COVERAGE:
        verdict["trusted"] = False
        verdict["reason"] = "ALIGNMENT_CLOCK_COVERS_TOO_LITTLE_OF_THE_CLIP"
    elif pace_ms < MIN_MS_PER_DISPLAY_CHARACTER:
        verdict["trusted"] = False
        verdict["reason"] = "ALIGNMENT_CLOCK_PACE_IMPLAUSIBLY_FAST"
    elif pace_ms > MAX_MS_PER_DISPLAY_CHARACTER:
        verdict["trusted"] = False
        verdict["reason"] = "ALIGNMENT_CLOCK_PACE_IMPLAUSIBLY_SLOW"
    return verdict


def _aligned_cue_times(
    chunks: Sequence[tuple[str, int, int]],
    *,
    alignment: Mapping[str, Any] | None,
    sample_rate_hz: int,
    clip_start_ms: int,
    clip_end_ms: int,
) -> list[tuple[int, int]]:
    """Per-cue ``(start_ms, end_ms)`` from the alignment clock, with a stated fallback.

    A cue that overlaps a mapped display span takes that span's time, converted
    from the take's samples to milliseconds and clipped to the narration clip it
    belongs to.  Cues the alignment could not place — and a take with no usable
    alignment at all — fall back to the deterministic character-proportional split
    inside the *remaining* clip window, in order, so the track stays monotonic and
    still ends exactly at the clip boundary.

    The whole take's clock is validated first by :func:`_alignment_clock_verdict`:
    a map that covers a fraction of the clip, or implies an impossible speaking
    pace, is not a clock, and using it produced cues of 773 ms for thirteen
    characters in the delivered film.

    The fallback is kept because a cue with no time at all would be worse than an
    estimated one, but it is never applied silently: ``_subtitle_alignment_facts``
    reports how many cues came from the aligner and how many were estimated.
    """

    if clip_end_ms <= clip_start_ms:
        clip_end_ms = clip_start_ms + 1
    spans: list[tuple[int, int, int, int]] = []
    if alignment is not None and sample_rate_hz > 0:
        mapped = _display_map_span(alignment)
        if _alignment_clock_verdict(
            mapped, sample_rate_hz=sample_rate_hz, clip_start_ms=clip_start_ms, clip_end_ms=clip_end_ms
        )["trusted"]:
            spans = mapped
    resolved: list[tuple[int, int] | None] = []
    for _chunk, start_offset, end_offset in chunks:
        best: tuple[int, int] | None = None
        for display_start, display_end, start_sample, end_sample in spans:
            if display_end <= start_offset or display_start >= end_offset:
                continue
            candidate = (
                clip_start_ms + int(round(start_sample * 1000 / sample_rate_hz)),
                clip_start_ms + int(round(end_sample * 1000 / sample_rate_hz)),
            )
            if best is None:
                best = candidate
            else:
                best = (min(best[0], candidate[0]), max(best[1], candidate[1]))
        if best is None:
            resolved.append(None)
            continue
        start_ms = max(clip_start_ms, min(best[0], clip_end_ms))
        end_ms = max(start_ms + 1, min(best[1], clip_end_ms))
        resolved.append((start_ms, end_ms))

    # Monotonicity: a cue may not start before the previous one ended.
    previous = clip_start_ms
    for index, item in enumerate(resolved):
        if item is None:
            continue
        start_ms = max(previous, item[0])
        end_ms = max(start_ms + 1, item[1])
        resolved[index] = (start_ms, end_ms)
        previous = end_ms

    # Character-proportional fallback inside each unresolved gap.
    times: list[tuple[int, int]] = []
    index = 0
    while index < len(chunks):
        if resolved[index] is not None:
            times.append(resolved[index])  # type: ignore[arg-type]
            index += 1
            continue
        gap_end = index
        while gap_end < len(chunks) and resolved[gap_end] is None:
            gap_end += 1
        gap_start_ms = clip_start_ms if index == 0 else times[-1][1]
        gap_end_ms = resolved[gap_end][0] if gap_end < len(resolved) and resolved[gap_end] else clip_end_ms
        if gap_end_ms <= gap_start_ms:
            gap_end_ms = gap_start_ms + 1
        weights = [max(1, len(chunks[position][0])) for position in range(index, gap_end)]
        total = sum(weights)
        cursor = gap_start_ms
        for position in range(index, gap_end):
            weight = weights[position - index] / total
            span = max(1, int(round((gap_end_ms - gap_start_ms) * weight)))
            cue_end = gap_end_ms if position == gap_end - 1 else min(gap_end_ms, cursor + span)
            cue_end = max(cursor + 1, cue_end)
            times.append((cursor, cue_end))
            cursor = cue_end
        index = gap_end

    # Final guard: the clock must cover the clip exactly once, in order.
    ordered: list[tuple[int, int]] = []
    cursor = clip_start_ms
    for position, (start_ms, end_ms) in enumerate(times):
        start_value = clip_start_ms if position == 0 else max(cursor, start_ms)
        end_value = clip_end_ms if position == len(times) - 1 else max(start_value + 1, min(end_ms, clip_end_ms))
        ordered.append((start_value, end_value))
        cursor = end_value
    return ordered


def _subtitle_alignment_facts(
    alignment: Mapping[str, Any] | None,
    *,
    sample_rate_hz: int = 0,
    clip_start_ms: int | None = None,
    clip_end_ms: int | None = None,
) -> dict[str, Any]:
    """What the alignment revision actually provides for cue timing.

    With the clip's own bounds the report also states whether that take's clock was
    trusted (:func:`_alignment_clock_verdict`), so a subtitle revision whose cues
    came from the character-proportional fallback says why instead of looking like an
    aligned one.
    """

    if alignment is None:
        return {
            "alignment_revision_id": None,
            "word_timing_count": 0,
            "mapped_span_count": 0,
            "clock": {"trusted": False, "reason": "NO_ALIGNMENT_REVISION", "coverage": 0.0},
        }
    raw = alignment.get("word_timings_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    spans = _display_map_span(alignment)
    clock: dict[str, Any] = {"trusted": False, "reason": "CLIP_BOUNDS_UNKNOWN", "coverage": 0.0}
    if clip_start_ms is not None and clip_end_ms is not None and sample_rate_hz > 0:
        clock = _alignment_clock_verdict(
            spans, sample_rate_hz=int(sample_rate_hz), clip_start_ms=int(clip_start_ms), clip_end_ms=int(clip_end_ms)
        )
    return {
        "alignment_revision_id": str(alignment.get("id") or ""),
        "alignment_status": str(alignment.get("alignment_status") or ""),
        "word_timing_count": len(raw) if isinstance(raw, Sequence) else 0,
        "mapped_span_count": len(spans),
        # The aligner's own grid: a cue boundary is quantised to it, so this can
        # never be presented as sample-accurate subtitle timing.
        "aligner_timestamp_grid_ms": 80,
        "clock": clock,
    }


def make_subtitle_build_handler(
    repo_factory: Callable[[], Any],
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``SUBTITLE_BUILD``: cue revisions from the alignment clock, per edition."""

    from local_drama.application.explainers.subtitles import (
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
                service = make_subtitle_service(repo)
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
                    alignment_facts: list[dict[str, Any]] = []
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
                        alignment_facts.append(
                            _subtitle_alignment_facts(
                                alignment,
                                sample_rate_hz=int(edition["audio_sample_rate_hz"] or 0),
                                clip_start_ms=start_ms,
                                clip_end_ms=end_ms,
                            )
                        )
                        chunks = _split_cue_chunks(
                            str(segment.get("display_text") or ""), max_chars=max_chars
                        )
                        cue_times = _aligned_cue_times(
                            chunks,
                            alignment=alignment,
                            sample_rate_hz=int(edition["audio_sample_rate_hz"] or 0),
                            clip_start_ms=start_ms,
                            clip_end_ms=end_ms,
                        )
                        for (chunk, _chunk_start, _chunk_end), (cue_start, cue_end) in zip(
                            chunks, cue_times, strict=True
                        ):
                            cues.append(
                                {
                                    "start_ms": cue_start,
                                    "end_ms": max(cue_start + 1, cue_end),
                                    "text": chunk,
                                    "paired_text": "",
                                    "segment_canonical_id": str(segment["canonical_segment_id"]),
                                }
                            )
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
                    # Only a *trusted* clock times a cue.  Counting a map that
                    # covered a third of the clip as "from alignment" is exactly how
                    # a revision full of estimated cues was presented as aligned.
                    mapped_cues = sum(
                        1
                        for fact in alignment_facts
                        if bool((fact.get("clock") or {}).get("trusted"))
                    )
                    built.append(
                        {
                            "edition_id": str(edition["id"]),
                            "locale": locale,
                            "subtitle_revision_id": str(revision["id"]),
                            "cue_count": len(created["cues"]),
                            "alignment_revision_count": len(alignment_ids),
                            # How much of the cue timing came from a *trusted*
                            # alignment clock: a revision whose cues are all
                            # estimated is still a legal revision, but it must be
                            # legible as estimated rather than presented as aligned.
                            "cues_from_alignment_segments": mapped_cues,
                            "narration_segment_count": len(alignment_facts),
                            "cue_clock_rejections": sorted(
                                {
                                    str((fact.get("clock") or {}).get("reason"))
                                    for fact in alignment_facts
                                    if not bool((fact.get("clock") or {}).get("trusted"))
                                }
                            ),
                            "cue_timing_source": (
                                "ALIGNMENT_CLOCK" if mapped_cues == len(alignment_facts) and mapped_cues
                                else "CHARACTER_PROPORTIONAL"
                            ),
                            "alignment_facts": alignment_facts,
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


def _render_canvas(
    *,
    edition_width: int,
    edition_height: int,
    aspect_ratio: str,
    source_heights: Sequence[int | None],
) -> tuple[int, int]:
    """Pick the render canvas: the edition's size, capped by the real sources.

    The renderer must not invent detail (upscaling a 480p source to 1080p adds no
    picture information) and must not discard it either — the motion-card clips this
    pipeline produces are already at the edition canvas.  When the sources cannot
    fill the edition canvas the platform's own proxy geometry for that aspect is
    used at the tallest real source height, so the film keeps the edition's aspect
    ratio and stays aligned with the subtitle geometry laid out for the edition.
    """

    height = max(2, int(edition_height) - (int(edition_height) % 2))
    width = max(2, int(edition_width) - (int(edition_width) % 2))
    heights = [int(item) for item in source_heights if item is not None and int(item) > 0]
    cap = max(heights) if heights else 0
    if cap and cap < height:
        from local_drama.domain.explainers.contracts import aspect_pixels_for_height

        return aspect_pixels_for_height(aspect_ratio, cap)
    return width, height


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

    from local_drama.application.composition.manifest import build_manifest, plan_chunks
    from local_drama.application.composition.validation import validate_manifest
    from local_drama.application.explainers.subtitles import default_safe_area
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
                #: Heights of the real source clips, used to pick the render canvas.
                source_heights: list[int] = []
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
                                # No adopted clip means there is no actual render type.
                                # The retired ``MOTION_STILL`` label asserted a real
                                # still-plus-camera-move clip that does not exist here.
                                "render_type_actual": None,
                                "transition": {"kind": "CUT"},
                            }
                        )
                        continue
                    media_version_id = str(selection["media_version_id"])
                    has_audio[media_version_id] = False
                    # The declared source window must be the *real media*, not the
                    # placement's span.  Declaring ``source_out == span`` made the
                    # renderer believe a short clip was long enough, so a placement
                    # that outlives its media (the declared outro hold especially)
                    # silently ran out of frames and failed the chunk frame check
                    # instead of cloning the last frame the way the design says.
                    media_row = repo.query_one(
                        "SELECT duration_ms, "
                        "json_extract(probe_json,'$.streams[0].height') AS source_height "
                        "FROM media_versions WHERE id=?",
                        (media_version_id,),
                    )
                    media_span_us = int(media_row["duration_ms"]) * 1000 if media_row and media_row["duration_ms"] else None
                    if media_row is not None and media_row["source_height"]:
                        source_heights.append(int(media_row["source_height"]))
                    if media_span_us is None or media_span_us <= 0:
                        media_span_us = int(
                            round(span * 1_000_000 * timeline["fps"].den / timeline["fps"].num)
                        )
                    source_in_us = int(selection.get("source_in_us") or 0)
                    clip = {
                        "clip_id": f"v-{len(clips):04d}",
                        "track": "VIDEO",
                        "item_kind": "VIDEO_CLIP",
                        "media_version_id": media_version_id,
                        "media_sha256": str(selection["media_sha256"]),
                        "start_frame": int(placement["start_frame"]),
                        "end_frame_exclusive": int(placement["end_frame_exclusive"]),
                        "source_in_us": source_in_us,
                        "source_out_us": source_in_us + media_span_us,
                        "sample_start": None,
                        "sample_end_exclusive": None,
                        "beat_id": beat_id,
                        "narration_segment_id": str(placement["segment_id"]),
                        "render_type_planned": placement.get("render_type"),
                        "render_type_actual": selection.get("render_type_actual"),
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
                        "source_heights": tuple(source_heights),
                    }
                )
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
            render_canvas = _render_canvas(
                edition_width=int(edition["width"]),
                edition_height=int(edition["height"]),
                aspect_ratio=str(edition["aspect_ratio"]),
                source_heights=plan.get("source_heights") or (),
            )
            if plan["subtitle_revision"] is not None:
                with repo_factory() as repo:
                    service = make_subtitle_service(repo)
                    ass_text = service.serialize(
                        cues=list(plan["subtitle_revision"].get("cues_json") or []),
                        format="ASS",
                        width=int(edition["width"]),
                        height=int(edition["height"]),
                        # The burn-in must use the same safe area the layout detector
                        # checks, otherwise the recorded geometry and the film's
                        # captions disagree about where the margin is.
                        safe_area=default_safe_area(str(edition.get("aspect_ratio") or "16:9")),
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
                # The edition is the delivery contract this render is produced for,
                # and the card clips this pipeline generates already use the edition
                # canvas — but the manifest used to omit the size and fall back to
                # ``AspectRatio.pixels``, a module-level 854x480 proxy that ignores
                # both the edition and the configured generation height.  A 1080p
                # edition therefore delivered a 480p film and relied on a "later
                # super-resolution" step that cannot run on a machine without the
                # ncnn model, so the detail the sources really carry was thrown away
                # at the very last step.  The canvas is now the edition size capped
                # by the tallest real source, so nothing is invented and nothing is
                # discarded.
                width=render_canvas[0],
                height=render_canvas[1],
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
                    # The picture path is read off the manifest's own clip records: the
                    # previous constant claimed a deterministic typeset card even when
                    # every clip was a real model-generated video.
                    "picture_path": _manifest_picture_path(plan["clips"]),
                    "generation_model_used": any(
                        str(item.get("render_type_actual") or "") == "I2V" for item in plan["clips"]
                    ),
                    "timing_authority": "MEASURED_TTS",
                    # The picture track may hold its last card to reach the declared
                    # target.  The hold is an explicit, declared ending, and the
                    # frame accounting is part of the frozen manifest.
                    "declared_silence": list(timeline.get("declared_silence") or []),
                    "measured_narration_frames": int(timeline.get("measured_frames") or 0),
                    "outro_hold_frames": int(timeline.get("outro_frames") or 0),
                },
            )
            # The frozen manifest is validated *before* a single frame is rendered.
            # ``validate_manifest`` existed but nothing called it, so a manifest with
            # an out-of-film audio placement, an undeclared silence or a source range
            # past its media went straight to FFmpeg and was silently truncated
            # (design §4.2/§7.2: the render step calls manifest, validation, then the
            # atomic render).
            # The validator needs every clip's *frozen media row*, not only the
            # narration: without the picture clips' rows it would report every
            # candidate clip as MEDIA_MISSING and refuse a healthy render.
            media_lookup: dict[str, dict[str, Any]] = {}
            with repo_factory() as repo:
                for clip in plan["clips"]:
                    media_version_id = str(clip.get("media_version_id") or "")
                    if not media_version_id or media_version_id in media_lookup:
                        continue
                    try:
                        row = repo.require_same_project_media(
                            project_id=project_id, media_version_id=media_version_id
                        )
                    except ExplainerContractError:
                        # Deliberately left out of the lookup: the validator turns an
                        # unresolvable media reference into a MEDIA_MISSING blocker,
                        # which states the real reason to refuse the render.
                        continue
                    media_lookup[media_version_id] = {
                        "sha256": row.get("sha256"),
                        "duration_ms": row.get("duration_ms"),
                        "audio_sample_rate_hz": int(edition["audio_sample_rate_hz"]),
                        "integrity_status": row.get("integrity_status"),
                    }
            validation = validate_manifest(manifest, media_lookup=media_lookup)
            blockers = [item.as_dict() for item in validation.blockers]
            if blockers:
                failures.append(
                    {
                        "edition_id": str(edition["id"]),
                        "reason": "MANIFEST_VALIDATION_BLOCKED",
                        "blockers": blockers[:5],
                    }
                )
                continue
            # The manifest is the time authority: hand the mixer each declared audio
            # media version's real path so it can trim to the declared source window
            # and place every sentence at its absolute sample position.  The earlier
            # caller-ordered path list could not express sentence pauses and made the
            # mixer's layout depend on list order (design §7.2).
            audio_sources = {
                str(item["media_version_id"]): Path(
                    _media_path(str(item["media_version_id"]), str(item["media_sha256"]))
                )
                for item in plan["narration_clips"]
            }
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
                audio_sources=audio_sources,
                has_audio_lookup=plan["has_audio"],
                # The delivered loudness must satisfy the declared -16 +/-1 LUFS
                # band; one dynamic ``loudnorm`` pass does not land there.
                loudness_normaliser=runner.normalise_loudness,
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
                lambda master_path=master_path, edition=edition: media_service.import_file(
                    project_id,
                    master_path,
                    purpose="EXPLAINER_RENDER",
                    owner_type="EXPLAINER_EDITION",
                    owner_id=str(edition["id"]),
                    media_kind="VIDEO",
                    stage="COMPOSITION_RENDER",
                    actor="explainer-worker",
                    schedule_derivatives=True,
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
                repo.insert(
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
                        "validation_json": {
                            "status": "PASS",
                            "source": "build_manifest",
                            "canvas": {"width": int(manifest.width), "height": int(manifest.height)},
                            "canvas_source": "EDITION_CAPPED_BY_SOURCE",
                        },
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
                    audio_sources=audio_sources,
                    has_audio_lookup=plan["has_audio"],
                    # The clean master is the same mix as the burned one with the
                    # captions omitted, so it must carry the same loudness pass;
                    # otherwise the two delivered masters differ by several LU.
                    loudness_normaliser=runner.normalise_loudness,
                )
                if str(clean_outcome.get("status")) == "SUCCEEDED":
                    clean_path = Path(str(clean_outcome["final_path"]))
                    clean_registered = _retry_on_locked(
                        lambda clean_path=clean_path, edition=edition: media_service.import_file(
                            project_id,
                            clean_path,
                            purpose="EXPLAINER_CLEAN_MASTER",
                            owner_type="EXPLAINER_EDITION",
                            owner_id=str(edition["id"]),
                            media_kind="VIDEO",
                            stage="COMPOSITION_RENDER",
                            actor="explainer-worker",
                            schedule_derivatives=True,
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

    import zipfile

    from local_drama.application.explainers.deliveries import (
        BURNED_MASTER,
        CHAPTERS,
        CITATIONS,
        CLEAN_MASTER,
        COVER,
        LICENSE_LIST,
        QC_REPORT,
        SUBTITLE,
        SUMMARY,
        TITLE,
        PackageItem,
    )
    from local_drama.application.explainers.subtitles import default_safe_area

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
                    package_dir: Path = package_dir,
                    items: list[Any] = items,
                    files: list[dict[str, Any]] = files,
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
                    ass = make_subtitle_service(repo).serialize(
                        cues=cues,
                        format="ASS",
                        width=int(edition["width"]),
                        height=int(edition["height"]),
                        safe_area=default_safe_area(str(edition.get("aspect_ratio") or "16:9")),
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
                                    schedule_derivatives=True,
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
                # QC is one report per layer, and the package used to ship whichever
                # layer wrote last.  After the 1962 film's semantic layer reported
                # NOT_RUN (the local multimodal call failed inside the worker) the
                # bundle's ``qc_report.json`` was that NOT_RUN row even though the
                # technical layer had PASSED the same render with 3132/3132 decoded
                # frames and a measured -16.1 LUFS.  The file now carries the newest
                # report *plus* every layer's own verdict and the worst-of aggregate,
                # so a reader can see what was really checked.
                subject_hash = str(root_render.get("sha256") or "")
                qc_report = repo.latest_qc_report(
                    subject_kind="COMPOSITION_RENDER",
                    subject_revision_id=str(root_render["id"]),
                    subject_hash=subject_hash or None,
                )
                layer_reports = [
                    {
                        "report_id": str(item["id"]),
                        "detector": str(
                            (item.get("detectors_json") or [{}])[0].get("detector")
                            if isinstance(item.get("detectors_json"), list) and item.get("detectors_json")
                            else ""
                        ),
                        "status": str(item.get("status") or ""),
                        "created_at": str(item.get("created_at") or ""),
                    }
                    for item in repo.qc_reports_for_subject(
                        subject_kind="COMPOSITION_RENDER",
                        subject_revision_id=str(root_render["id"]),
                        subject_hash=subject_hash or None,
                    )
                ]
                packaged_qc: dict[str, Any] = dict(qc_report or {"status": "NOT_RUN"})
                packaged_qc["layer_reports"] = layer_reports
                packaged_qc["aggregate_status"] = _aggregate_qc_status(
                    [str(item["status"]) for item in layer_reports]
                )
                packaged_qc["aggregate_note"] = (
                    "每一层各自出报告；aggregate_status 取最严结论（FAIL > BLOCKED > PARTIAL > NOT_RUN > PASS）。"
                )
                add_file(
                    role=QC_REPORT,
                    rel_path="qc_report.json",
                    path=None,
                    content=json.dumps(packaged_qc, ensure_ascii=False, indent=2, default=str),
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
                frozen = make_delivery_service(repo).freeze_package(
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
        # A package is BLOCKED only by an *unverified asset licence scope* (a
        # missing preset is a warning, not a blocker), so "frozen" is not the same
        # as "deliverable".  Reporting PASS whenever one package row existed is what
        # let a run project COMPLETED while every package it produced was unusable:
        # the delivery record existed, but no verifiable artefact did.
        not_ready = [item for item in packages if not item["publishable"]]
        if not_ready:
            return _blocked(
                f"{len(not_ready)}/{len(packages)} 个交付包未就绪："
                + "、".join(str(item["status"]) for item in not_ready)
                + "；解决许可范围后重试导出",
                "PUBLICATION_PACKAGE_NOT_READY",
                {"packages": packages, "failures": failures},
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
    picture_runtime: Any = None,
    motion_runtime: Any = None,
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
            repo_factory,
            settings=settings,
            media_service=media_service,
            work_root=resolved_work_root,
            picture_runtime=picture_runtime,
            motion_runtime=motion_runtime,
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
