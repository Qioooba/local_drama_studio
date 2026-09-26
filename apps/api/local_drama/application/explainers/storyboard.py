"""Explainer storyboard service: beats, measured timing, candidates, adoption.

Business rules of the 解说工厂 (Explainer Factory) storyboard stage, expressed as
deterministic operations over :class:`ExplainerRepository`.

The rules this module encodes (and must never silently relax):

* A narration segment and a visual beat are **many-to-many**: one sentence may
  span two shots and one shot may carry adjacent sentences.  Nothing here
  enforces a 1:1 mapping and nothing cuts "every N seconds".
* Beat timing follows **measured TTS audio**.  ``preferred_duration_ms`` is a
  hint only; a text-length estimate is never used for frame allocation.
* First/end frame pairing is used only when the action has a terminal state, the
  two shots connect, the declared capability supports it and the two beats are
  in the same chapter.  A chapter change never reuses the previous end frame and
  conflicting end-frame geometry demands replanning instead of interpolation.
* Recurring characters periodically return to the canonical reference (chain
  limit, default 4) so identity drift cannot snowball.
* Technical retries and creative repairs are accounted **separately**; when the
  budget is exhausted the system stops instead of continuing to draw.
* Adoption order is technical -> content -> constraint -> readability -> style.
  An aesthetic score never outranks a factual/technical failure.
* A ``must_be_motion`` beat is never degraded to a still image and a
  human-locked beat is never replaced by a batch operation.  Replacements are
  new ACTIVE selections; the superseded one stays visible.
* ``render_type_planned`` and ``render_type_actual`` stay separate fields, so a
  degraded beat is never counted as a successful I2V.
* All readable text (numbers, dates, citations, map labels, chart labels) is
  produced by a deterministic text layer; an image model never guesses text.

Deliberately NOT done here
--------------------------
* No model/GPU calls, no FFmpeg, no downloads, no HTTP and no image inspection:
  this service only turns already-measured facts into explainer rows.
* No narration rewriting: the script is read, never edited.
* No deletion of anything: superseding writes a new row and marks the old one
  ``SUPERSEDED``.
* No human approval is minted.  ``adopt_selection(authority="HUMAN")`` records
  the operator the caller passed in; it never invents an approval id, and it
  never upgrades a machine decision into a human one.
* No second claim queue and no long wait inside a transaction.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    Budget,
    ExplainerContractError,
    Ratio,
    RenderType,
    VisualFactuality,
    VisualFallback,
    content_hash,
    default_budget,
    ensure_monotonic_frames,
    estimate_shot_budget,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import (
    BudgetLedger,
    FallbackDecision,
    plan_initial_candidates,
    resolve_visual_fallback,
    staleness_plan,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Starting channel mix guideline.  A 解说 film is an AI 图生视频 product, so the
#: guideline is overwhelmingly I2V; the graphic and licensed-material families stay
#: their own categories and are never merged into a still-picture substitute for a
#: moving shot.  It is a preset that the channel may adjust: it is never a template
#: lock, and LICENSED_MEDIA is reported outside the guideline.
DEFAULT_CHANNEL_MIX: dict[str, float] = {"i2v": 0.9, "infographic": 0.1}

#: Preset family -> render types that satisfy it.  The retired ``still_motion``
#: family (``STILL_MOTION`` / ``PARALLAX``) is gone with the deterministic
#: push/pull picture path it described.
MIX_FAMILIES: dict[str, tuple[str, ...]] = {
    "i2v": (RenderType.I2V.value,),
    "infographic": (RenderType.INFOGRAPHIC.value,),
    "licensed_media": (RenderType.LICENSED_MEDIA.value,),
}

#: Render types that cannot carry motion at all.  A beat that must really move may
#: never be planned or degraded onto one of these.
STILL_RENDER_TYPES: frozenset[str] = frozenset({RenderType.INFOGRAPHIC.value})
#: Render types that can produce a distinct end frame.
MOTION_CAPABLE_RENDER_TYPES: frozenset[str] = frozenset(
    {RenderType.I2V.value, RenderType.LICENSED_MEDIA.value}
)
#: Render types that represent a produced, composable clip.  A retired still-motion
#: row is deliberately absent: it is history, not a clip, so it must not make a beat
#: look as though its picture work were already delivered.
PRODUCED_CLIP_RENDER_TYPES: frozenset[str] = frozenset(
    {RenderType.I2V.value, RenderType.INFOGRAPHIC.value, RenderType.LICENSED_MEDIA.value}
)

#: Reference policies a beat may declare.  There is no DB/domain enum for this
#: column (``explainer_visual_beats.reference_policy`` is a free string with a
#: default), so the vocabulary lives here and unknown values are rejected.
REFERENCE_POLICIES: frozenset[str] = frozenset(
    {
        "LOCKED_IDENTITY_AND_SCENE",
        "CANONICAL_REFERENCE_PERIODIC",
        "SCENE_ONLY",
        "FREE_COMPOSITION",
        "LICENSED_ASSET_ONLY",
    }
)

CANDIDATE_KINDS: frozenset[str] = frozenset({"CREATIVE", "TECHNICAL_RETRY"})
#: ``KEYFRAME`` separates the adopted first frame from the final composable clip
#: (design §D2.2 item 6): candidates already carried a ``purpose`` string, and this
#: is the one value the application whitelist was missing.  Existing ``VISUAL`` rows
#: are unchanged.
CANDIDATE_PURPOSES: frozenset[str] = frozenset(
    {"VISUAL", "KEYFRAME", "REFERENCE", "COMPOSITION", "INFOGRAPHIC_LAYER", "LICENSED_MEDIA"}
)

#: The five revisions every beat is expected to reference (design: identity pack,
#: costume, scene, prop, style).  They travel inside ``shot_grammar_json``
#: because the beat table has no dedicated columns; a beat that does not declare
#: them is reported as a visible gap instead of a fabricated value.
REVISION_REF_KEYS: tuple[str, ...] = (
    "identity_pack_revision",
    "costume_revision",
    "scene_revision",
    "prop_revision",
    "style_revision",
)

ADOPTION_RULE = "TECHNICAL_THEN_CONTENT_THEN_CONSTRAINT_THEN_READABILITY_THEN_STYLE"
ADOPTION_STAGES: tuple[str, ...] = (
    "FILE_DECODE",
    "CONTENT_RELEVANCE",
    "IDENTITY_CONSTRAINTS",
    "READABILITY",
    "STYLE_AESTHETICS",
)

#: Default number of consecutive end-frame-chained beats before the plan must
#: re-anchor on the canonical reference.
DEFAULT_CHAIN_LIMIT = 4
DEFAULT_STORYBOARD_STEP_CODE = "EXPLAINER_STORYBOARD"

#: Candidate statuses that may not be adopted.
NOT_ADOPTABLE_CANDIDATE_STATUSES: frozenset[str] = frozenset({"REJECTED", "FAILED", "SUPERSEDED"})

#: Tri-state verdicts for one adoption check (design §6.2).  A check whose fields
#: were never measured is ``UNKNOWN``, never an implicit pass: the audit's A06
#: defect was that every test read ``is False``, so a candidate with no checks at
#: all produced no blocker and was reported as ``PASSED_ALL_STAGES``.
CHECK_PASS = "PASS"
CHECK_FAIL = "FAIL"
CHECK_UNKNOWN = "UNKNOWN"
CHECK_NOT_RUN = "NOT_RUN"
CHECK_STATES: tuple[str, ...] = (CHECK_PASS, CHECK_FAIL, CHECK_UNKNOWN, CHECK_NOT_RUN)

#: Blockers that even an explicit human adoption may not skip: a file that does not
#: decode, or a candidate whose recorded status is not adoptable, is a hard
#: technical failure rather than a content judgement (design §6.2/§6.3).
HARD_TECHNICAL_BLOCKERS: frozenset[str] = frozenset(
    {"FILE_NOT_DECODED", "CANDIDATE_STATUS_NOT_ADOPTABLE"}
)

#: Checks whose verdict a *machine* adoption must have verified.  ``STYLE`` is
#: absent on purpose: the design makes it a tie-break that only applies after every
#: required check has passed.
REQUIRED_ADOPTION_CHECKS: tuple[str, ...] = (
    "FILE_DECODE",
    "CONTENT_RELEVANCE",
    "IDENTITY_CONSTRAINTS",
    "READABILITY",
)

#: A beat only needs its identity/state checked when it actually binds references,
#: and its text layer checked when it draws readable text.  An inapplicable check is
#: ``NOT_RUN`` with a recorded reason rather than a silent pass.
IDENTITY_BEAT_FIELDS: tuple[str, ...] = ("entity_refs", "entity_refs_json")
TEXT_LAYER_RENDER_TYPES: frozenset[str] = frozenset({"INFOGRAPHIC"})


def _declared_false_is_failure(candidate: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, list[str]]:
    """``PASS`` when a field is explicitly true, ``FAIL`` when explicitly false.

    Absent or ``None`` means the check was never reported, which is ``UNKNOWN``.
    Checks are only as good as their declaration: the ``*_ok``/``valid``/``readable``
    fields are written by the QC layers, and a missing one is a gap to report rather
    than a pass to assume.
    """

    declared = 0
    for field in fields:
        value = candidate.get(field)
        if value is None:
            continue
        if value is False:
            return CHECK_FAIL, [field]
        declared += 1
    return (CHECK_PASS if declared else CHECK_UNKNOWN), []


def _declared_true_is_failure(candidate: Mapping[str, Any], field: str) -> tuple[str, list[str]]:
    """``FAIL`` when the field is explicitly true (a violation flag)."""

    value = candidate.get(field)
    if value is None:
        return CHECK_UNKNOWN, []
    if value is True:
        return CHECK_FAIL, [field]
    return CHECK_PASS, []


def _beat_binds_identity(beat: Mapping[str, Any]) -> bool:
    for field in IDENTITY_BEAT_FIELDS:
        value = beat.get(field)
        if isinstance(value, str):
            value = value.strip("[] ")
        if value:
            return True
    return False


TEXT_LAYER_KINDS: frozenset[str] = frozenset({"TEXT", "TEXT_LAYER", "TYPOGRAPHY", "LABEL"})
VECTOR_LAYER_KINDS: frozenset[str] = frozenset(
    {"VECTOR", "BASEMAP", "SHAPE", "PATH", "CHART_VECTOR", "RELATION_LINE"}
)
RASTER_LAYER_KINDS: frozenset[str] = frozenset(
    {"RASTER", "IMAGE", "PHOTO", "GENERATED", "MODEL_OUTPUT", "AI_IMAGE", "RENDER"}
)
#: Keys that mean "this layer paints readable characters".
READABLE_TEXT_KEYS: tuple[str, ...] = (
    "text",
    "readable_text",
    "label",
    "labels",
    "caption",
    "annotation",
    "numbers",
    "dates",
    "citations",
    "legend",
    "title",
)
GEOMETRY_GRAMMAR_KEYS: tuple[str, ...] = ("frame_geometry", "aspect_family", "render_aspect", "resolution")
_BUDGET_FIELDS: frozenset[str] = frozenset(default_budget().as_dict())


# --------------------------------------------------------------------------- #
# small deterministic helpers
# --------------------------------------------------------------------------- #
def _require_member(value: str, allowed: Iterable[str], *, field: str, **details: Any) -> str:
    allowed_set = set(allowed)
    if value not in allowed_set:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{field} 取值不合法：{value!r}",
            {**details, "field": field, "value": value, "allowed": sorted(allowed_set)},
        )
    return value


def _require_positive_int(value: Any, *, field: str, **details: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field} 必须是正整数", {**details, "field": field, "value": value}
        ) from error
    if number <= 0:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field} 必须是正整数", {**details, "field": field, "value": number}
        )
    return number


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_budget(value: Budget | Mapping[str, Any] | None, *, fallback: Budget | None = None) -> Budget:
    """Coerce an authorized budget, merging partial mappings over the defaults."""

    if isinstance(value, Budget):
        return value
    base = (fallback or default_budget()).as_dict()
    if value:
        for key, item in dict(value).items():
            if key in _BUDGET_FIELDS and item is not None:
                base[key] = item
    return Budget(**base)


def _as_family(render_type: str) -> str:
    for family, members in MIX_FAMILIES.items():
        if render_type in members:
            return family
    return "other"


def _frames_for_ms(duration_ms: int, fps: Ratio) -> int:
    """Half-up integer conversion from measured milliseconds to frames."""

    if duration_ms < 0:
        raise ExplainerContractError("SCHEMA_INVALID", "实测时长不能为负", {"duration_ms": duration_ms})
    numerator = duration_ms * fps.num
    denominator = 1000 * fps.den
    return (numerator + denominator // 2) // denominator


def _allocate_frames(durations_ms: Sequence[int], fps: Ratio) -> tuple[list[int], int]:
    """Allocate contiguous frames that follow measured audio and sum exactly.

    Each beat receives its measured span rounded to frames; rounding residue is
    moved so that ``sum(frames) == frames_for_ms(sum(durations))``.  The caller
    can therefore make the last beat's exclusive end equal the edition total by
    construction, which is what the design requires.
    """

    if not durations_ms:
        raise ExplainerContractError("SCHEMA_INVALID", "没有可分配的画面段")
    total_ms = sum(int(value) for value in durations_ms)
    total_frames = _frames_for_ms(total_ms, fps)
    if total_frames < 1:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "实测音频总时长不足一帧，不能分配画面段时长",
            {"total_measured_ms": total_ms, "fps": fps.as_dict()},
        )
    if total_frames < len(durations_ms):
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "实测音频总时长不足以分配给全部画面段",
            {"total_frames": total_frames, "beat_count": len(durations_ms)},
        )
    frames = [max(1, _frames_for_ms(int(value), fps)) for value in durations_ms]
    difference = total_frames - sum(frames)
    if difference > 0:
        remainders = sorted(
            range(len(frames)),
            key=lambda index: (
                -((int(durations_ms[index]) * fps.num) % (1000 * fps.den)),
                index,
            ),
        )
        for step in range(difference):
            frames[remainders[step % len(remainders)]] += 1
    while difference < 0:
        reducible = [index for index, value in enumerate(frames) if value > 1]
        if not reducible:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "无法在保证每个画面段至少一帧的前提下压缩到实测总帧数",
                {"total_frames": total_frames, "beat_count": len(durations_ms)},
            )
        index = max(reducible, key=lambda item: (frames[item], -item))
        frames[index] -= 1
        difference += 1
    return frames, total_frames


def _geometry_conflict(from_beat: Mapping[str, Any], to_beat: Mapping[str, Any]) -> bool:
    from_grammar = from_beat.get("shot_grammar_json") or {}
    to_grammar = to_beat.get("shot_grammar_json") or {}
    if not isinstance(from_grammar, Mapping) or not isinstance(to_grammar, Mapping):
        return False
    if from_grammar.get("end_frame_geometry_conflict") or to_grammar.get("end_frame_geometry_conflict"):
        return True
    for key in GEOMETRY_GRAMMAR_KEYS:
        left = from_grammar.get(key)
        right = to_grammar.get(key)
        if left and right and str(left) != str(right):
            return True
    return False


def _has_terminal_state(beat: Mapping[str, Any]) -> bool:
    grammar = beat.get("shot_grammar_json") or {}
    if not isinstance(grammar, Mapping):
        return False
    if grammar.get("terminal_state") is True:
        return True
    return str(grammar.get("action_phase") or "").upper() in {"TERMINAL", "RESOLVED", "COMPLETE"}


def _provides_end_frame(beat: Mapping[str, Any]) -> bool:
    grammar = beat.get("shot_grammar_json") or {}
    if not isinstance(grammar, Mapping):
        return True
    return grammar.get("provides_end_frame", True) is not False


def _normalize_capability(capability: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(capability, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "capability 必须是对象", {})
    required = ("supports_end_frame", "native_frames", "native_fps_num", "native_fps_den", "supports_reference")
    missing = [key for key in required if key not in capability]
    if missing:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "capability 缺少字段", {"missing": missing, "required": list(required)}
        )
    return {
        "supports_end_frame": bool(capability["supports_end_frame"]),
        "supports_reference": bool(capability["supports_reference"]),
        "native_frames": _require_positive_int(capability["native_frames"], field="native_frames"),
        "native_fps_num": _require_positive_int(capability["native_fps_num"], field="native_fps_num"),
        "native_fps_den": _require_positive_int(capability["native_fps_den"], field="native_fps_den"),
    }


def _candidate_identifier(candidate: Mapping[str, Any], *, index: int) -> str:
    identifier = candidate.get("candidate_id") or candidate.get("id")
    if not identifier:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "候选缺少 candidate_id", {"candidate_index": index}
        )
    return str(identifier)


#: Candidate fields that carry a *check verdict* rather than an identity.  The QC
#: layers persist them in the candidate's ``qc_summary``; a caller that already has
#: them flat (a projection, a repair preview, a test) passes them directly.
CANDIDATE_CHECK_FIELDS: tuple[str, ...] = (
    "file_valid",
    "decoded",
    "content_relevant",
    "content_match",
    "identity_ok",
    "constraints_ok",
    "text_readable",
    "readable",
    "audible",
    "must_be_motion_violated",
    "constraint_violations",
    "aesthetic_score",
    "style_score",
)

#: Identity fields that stay on the candidate row itself.
CANDIDATE_IDENTITY_FIELDS: tuple[str, ...] = (
    "status",
    "render_type",
    "render_type_actual",
    "candidate_kind",
    "variant_no",
    "purpose",
)


def _candidate_check_view(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Merge the check verdicts a candidate carries, wherever they were recorded.

    ``explainer_media_candidates`` has no column per check: the QC layers write
    them into ``qc_summary_json`` (the repository JSON-decodes the column but keeps
    its name).  Reading only the flat row made every real candidate look
    unmeasured, so this merges the nested summary and then lets an explicit
    top-level field win.
    """

    view: dict[str, Any] = {}
    for key in ("qc_summary_json", "qc_summary", "execution_snapshot_json", "execution_snapshot"):
        source = candidate.get(key)
        if isinstance(source, Mapping):
            view.update(source)
    for field in CANDIDATE_CHECK_FIELDS:
        value = candidate.get(field)
        if value is not None:
            view[field] = value
    for field in CANDIDATE_IDENTITY_FIELDS:
        value = candidate.get(field)
        if value is not None:
            view[field] = value
    return view


def build_storyboard_service(repo: ExplainerRepository) -> "ExplainerStoryboardService":
    """Port-style factory so callers never construct the service inline.

    Route handlers and worker code reach the adoption command through this
    function; constructing the concrete service inside a handler is reported as new
    cross-service debt by the repository's architecture guard.
    """

    return ExplainerStoryboardService(repo)


class ExplainerStoryboardService:
    """Storyboard-stage application service over one :class:`ExplainerRepository`."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ plan
    def create_plan(
        self,
        *,
        project_id: str,
        video_id: str,
        beats: Sequence[Mapping[str, Any]],
        actor: str = "local-user",
        plan_step_binding_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a beat plan and return it with its many-to-many mapping summary.

        Every unknown narration/entity/claim code is a hard ``SCHEMA_INVALID``:
        a plan may not silently drop a reference it could not resolve.

        ``plan_step_binding_id`` attributes every beat to the plan that produced
        it, so a second plan for the same video no longer collides with the first
        plan's codes and readers can ask for one plan instead of the union of all
        of them (audit A11).
        """

        self.repo.require_explainer_project(project_id)
        video = self.repo.get("explainer_videos", video_id)
        if str(video["project_id"]) != project_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "解说作品不属于该项目",
                {"video_id": video_id, "project_id": project_id, "video_project_id": video["project_id"]},
            )
        specs = [dict(item) for item in (beats or ())]
        if not specs:
            raise ExplainerContractError("SCHEMA_INVALID", "分镜计划至少需要一个画面段", {"video_id": video_id})

        codes: list[str] = []
        for spec in specs:
            code = str(spec.get("code") or "").strip()
            if not code:
                raise ExplainerContractError("SCHEMA_INVALID", "画面段必须提供非空 code", {"video_id": video_id})
            if code in codes:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", f"画面段 code 重复：{code}", {"beat_code": code}
                )
            codes.append(code)

        script_revision = self._current_script_revision(video)
        script_revision_id = str(script_revision["id"])
        segments = self.repo.segments(script_revision_id)
        segment_by_canonical = {str(item["canonical_segment_id"]): item for item in segments}
        if not segment_by_canonical:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "当前讲稿版本还没有解说句段，无法建立分镜",
                {"video_id": video_id, "script_revision_id": script_revision_id},
            )
        chapters = {
            str(item["id"]): item
            for item in self.repo.list_where(
                "explainer_chapters", {"script_revision_id": script_revision_id}, order_by="ordinal", descending=False
            )
        }
        entity_by_code = {
            str(item["code"]): item
            for item in self.repo.list_where(
                "explainer_entities", {"video_id": video_id}, order_by="code", descending=False
            )
        }

        created: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for ordinal, spec in enumerate(specs):
            code = codes[ordinal]
            render_type = _require_member(
                str(spec.get("render_type") or ""),
                (item.value for item in RenderType),
                field="render_type",
                beat_code=code,
            )
            visual_factuality = _require_member(
                str(spec.get("visual_factuality") or VisualFactuality.RECONSTRUCTION.value),
                (item.value for item in VisualFactuality),
                field="visual_factuality",
                beat_code=code,
            )
            must_be_motion = bool(spec.get("must_be_motion", False))
            if must_be_motion and render_type not in MOTION_CAPABLE_RENDER_TYPES:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "must_be_motion 的画面段必须使用可产生运动的渲染类型",
                    {"beat_code": code, "render_type": render_type, "allowed": sorted(MOTION_CAPABLE_RENDER_TYPES)},
                )
            reference_policy = str(spec.get("reference_policy") or "LOCKED_IDENTITY_AND_SCENE")
            _require_member(reference_policy, REFERENCE_POLICIES, field="reference_policy", beat_code=code)
            allowed_fallbacks = [
                _require_member(
                    str(item),
                    (fallback.value for fallback in VisualFallback),
                    field="allowed_fallbacks",
                    beat_code=code,
                )
                for item in (spec.get("allowed_fallbacks") or ())
            ]
            preferred_duration_ms = spec.get("preferred_duration_ms")
            if preferred_duration_ms is not None:
                preferred_duration_ms = _require_positive_int(
                    preferred_duration_ms, field="preferred_duration_ms", beat_code=code
                )

            segment_ids: list[str] = []
            canonical_ids: list[str] = []
            for canonical in spec.get("segment_canonical_ids") or ():
                canonical_id = str(canonical)
                segment = segment_by_canonical.get(canonical_id)
                if segment is None:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        f"画面段引用了不存在的解说句段：{canonical_id}",
                        {
                            "beat_code": code,
                            "canonical_segment_id": canonical_id,
                            "script_revision_id": script_revision_id,
                        },
                    )
                if str(segment["id"]) in segment_ids:
                    continue
                segment_ids.append(str(segment["id"]))
                canonical_ids.append(canonical_id)

            entity_ids: list[str] = []
            for entity_code in spec.get("entity_codes") or ():
                entity = entity_by_code.get(str(entity_code))
                if entity is None:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        f"画面段引用了不存在的实体：{entity_code}",
                        {"beat_code": code, "entity_code": str(entity_code), "video_id": video_id},
                    )
                if str(entity["id"]) not in entity_ids:
                    entity_ids.append(str(entity["id"]))

            claim_ids: list[str] = []
            for claim_code in spec.get("claim_codes") or ():
                claim = self.repo.claim_by_code(video_id, str(claim_code))
                if claim is None:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        f"画面段引用了不存在的事实断言：{claim_code}",
                        {"beat_code": code, "claim_code": str(claim_code), "video_id": video_id},
                    )
                if str(claim["id"]) not in claim_ids:
                    claim_ids.append(str(claim["id"]))

            shot_grammar = spec.get("shot_grammar") or {}
            if not isinstance(shot_grammar, Mapping):
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "shot_grammar 必须是对象", {"beat_code": code}
                )
            shot_grammar = dict(shot_grammar)
            revision_refs: Mapping[str, Any] | None = shot_grammar.get("revision_refs")
            revision_refs_declared = revision_refs is not None
            if revision_refs_declared:
                if not isinstance(revision_refs, Mapping):
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "shot_grammar.revision_refs 必须是对象",
                        {"beat_code": code},
                    )
                missing_refs = [
                    key for key in REVISION_REF_KEYS if not str(revision_refs.get(key) or "").strip()
                ]
                if missing_refs:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "每个画面段都必须引用身份包/服装/场景/道具/风格五个版本",
                        {"beat_code": code, "missing_revision_refs": missing_refs},
                    )

            row = self.repo.insert(
                "explainer_visual_beats",
                {
                    "video_id": video_id,
                    "code": code,
                    "ordinal": ordinal,
                    "render_type": render_type,
                    "visual_intent": str(spec.get("visual_intent") or ""),
                    "must_be_motion": must_be_motion,
                    "reference_policy": reference_policy,
                    "allowed_fallbacks_json": allowed_fallbacks,
                    "preferred_duration_ms": preferred_duration_ms,
                    "entity_refs_json": entity_ids,
                    "claim_refs_json": claim_ids,
                    "visual_factuality": visual_factuality,
                    "shot_grammar_json": shot_grammar,
                    "prompt_intent": str(spec.get("prompt_intent") or ""),
                    "status": "PLANNED",
                    "origin": "PLANNED",
                    "plan_step_binding_id": plan_step_binding_id or None,
                },
                actor=actor,
            )
            for link_ordinal, segment_id in enumerate(segment_ids):
                link = self.repo.insert(
                    "beat_narration_links",
                    {
                        "beat_id": str(row["id"]),
                        "narration_segment_id": segment_id,
                        "video_id": video_id,
                        "ordinal": link_ordinal,
                    },
                    actor=actor,
                )
                links.append(
                    {
                        "link_id": str(link["id"]),
                        "beat_id": str(row["id"]),
                        "beat_code": code,
                        "narration_segment_id": segment_id,
                        "canonical_segment_id": canonical_ids[link_ordinal],
                        "ordinal": link_ordinal,
                    }
                )
            chapter_codes = []
            for segment_id in segment_ids:
                segment = next(item for item in segments if str(item["id"]) == segment_id)
                chapter_id = segment.get("chapter_id")
                chapter = chapters.get(str(chapter_id)) if chapter_id else None
                chapter_code = str(chapter["code"]) if chapter else None
                if chapter_code and chapter_code not in chapter_codes:
                    chapter_codes.append(chapter_code)
            created.append(
                {
                    "beat_id": str(row["id"]),
                    "code": code,
                    "ordinal": ordinal,
                    "render_type": render_type,
                    "visual_factuality": visual_factuality,
                    "must_be_motion": must_be_motion,
                    "reference_policy": reference_policy,
                    "allowed_fallbacks": allowed_fallbacks,
                    "preferred_duration_ms": preferred_duration_ms,
                    "entity_codes": [str(item) for item in (spec.get("entity_codes") or ())],
                    "entity_ids": entity_ids,
                    "claim_codes": [str(item) for item in (spec.get("claim_codes") or ())],
                    "claim_ids": claim_ids,
                    "segment_canonical_ids": canonical_ids,
                    "segment_ids": segment_ids,
                    "chapter_codes": chapter_codes,
                    "revision_refs_declared": revision_refs_declared,
                    "revision_refs": dict(revision_refs) if revision_refs is not None else None,
                    "prompt_intent": str(spec.get("prompt_intent") or ""),
                }
            )

        segment_to_beats: dict[str, list[str]] = {}
        beat_to_segments: dict[str, list[str]] = {}
        for link in links:
            segment_to_beats.setdefault(link["canonical_segment_id"], [])
            if link["beat_code"] not in segment_to_beats[link["canonical_segment_id"]]:
                segment_to_beats[link["canonical_segment_id"]].append(link["beat_code"])
            beat_to_segments.setdefault(link["beat_code"], []).append(link["canonical_segment_id"])
        multi_segment_beats = sorted(code for code, items in beat_to_segments.items() if len(items) > 1)
        multi_beat_segments = sorted(
            canonical for canonical, items in segment_to_beats.items() if len(items) > 1
        )
        plan_body = {
            "project_id": project_id,
            "video_id": video_id,
            "script_revision_id": script_revision_id,
            "beats": created,
            "links": links,
        }
        return {
            "project_id": project_id,
            "video_id": video_id,
            "script_revision_id": script_revision_id,
            "script_revision_no": int(script_revision["revision_no"]),
            "locale": str(script_revision["locale"]),
            "actor": actor,
            "created_at": utc_now_iso(),
            "beats": created,
            "links": links,
            "mapping": {
                "beat_count": len(created),
                "link_count": len(links),
                "linked_segment_count": len(segment_to_beats),
                "segment_to_beats": segment_to_beats,
                "beat_to_segments": beat_to_segments,
                "multi_segment_beats": multi_segment_beats,
                "multi_beat_segments": multi_beat_segments,
                "beats_without_segments": sorted(
                    code for code, items in beat_to_segments.items() if not items
                ),
                "one_to_one_mapping_enforced": False,
                "mechanical_fixed_interval_cut": False,
                "mapping_rule": "NARRATION_SEGMENT_AND_VISUAL_BEAT_ARE_MANY_TO_MANY",
            },
            "beats_missing_revision_refs": [
                item["code"] for item in created if not item["revision_refs_declared"]
            ],
            "plan_hash": content_hash(plan_body),
            "generation_started": False,
        }

    def _current_script_revision(self, video: Mapping[str, Any]) -> dict[str, Any]:
        revision_id = video.get("current_script_revision_id")
        if revision_id:
            revision = self.repo.get("explainer_script_revisions", str(revision_id))
            if str(revision["video_id"]) != str(video["id"]):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "当前讲稿版本不属于该解说作品",
                    {"video_id": str(video["id"]), "script_revision_id": str(revision_id)},
                )
            return revision
        rows = self.repo.list_where(
            "explainer_script_revisions",
            {"video_id": str(video["id"])},
            order_by="revision_no",
            descending=True,
            limit=1,
        )
        if not rows:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "该解说作品还没有讲稿版本，无法建立分镜", {"video_id": str(video["id"])}
            )
        return rows[0]

    # ------------------------------------------------------------------ mix
    def channel_mix(
        self, *, video_id: str, preset: Mapping[str, float] | None = None
    ) -> dict[str, Any]:
        """Report planned vs actual render-type counts against an adjustable mix.

        The returned ``is_template_lock`` is always ``False``: the mix is a
        starting guideline the channel may override, never a locked template.
        """

        video = self.repo.get("explainer_videos", video_id)
        beats = self.repo.beats(video_id)
        resolved_preset, preset_source = self._resolve_mix_preset(preset)

        planned_counts = {family: 0 for family in MIX_FAMILIES}
        for beat in beats:
            family = _as_family(str(beat["render_type"]))
            planned_counts[family] = planned_counts.get(family, 0) + 1

        selections = self.repo.list_where(
            "explainer_beat_selections",
            {"video_id": video_id, "status": "ACTIVE"},
            order_by="created_at",
            descending=True,
        )
        latest_by_beat: dict[str, dict[str, Any]] = {}
        for selection in selections:
            latest_by_beat.setdefault(str(selection["beat_id"]), selection)
        actual_counts = {family: 0 for family in MIX_FAMILIES}
        for beat in beats:
            active_selection: Mapping[str, Any] | None = latest_by_beat.get(str(beat["id"]))
            render_type = str(
                (active_selection or {}).get("render_type_actual") or beat["render_type"]
            )
            family = _as_family(render_type)
            actual_counts[family] = actual_counts.get(family, 0) + 1

        total = len(beats)
        planned_ratios = {
            family: (round(count / total, 6) if total else 0.0) for family, count in planned_counts.items()
        }
        actual_ratios = {
            family: (round(count / total, 6) if total else 0.0) for family, count in actual_counts.items()
        }
        deviation = {
            family: round(planned_ratios[family] - resolved_preset.get(family, 0.0), 6)
            for family in resolved_preset
        }
        target_seconds = int(video.get("target_seconds") or 300)
        return {
            "video_id": video_id,
            "preset": resolved_preset,
            "preset_source": preset_source,
            "default_preset": dict(DEFAULT_CHANNEL_MIX),
            "recommended_mix": dict(DEFAULT_CHANNEL_MIX),
            "is_template_lock": False,
            "adjustable": True,
            "guideline_note": "渠道起手比例指引，可按频道调整；不是模板锁定",
            "planned": {
                "total_beats": total,
                "counts": planned_counts,
                "ratios": planned_ratios,
            },
            "actual": {
                "adopted_beats": len(latest_by_beat),
                "counts": actual_counts,
                "ratios": actual_ratios,
                "degraded_beats": sum(
                    1
                    for selection in latest_by_beat.values()
                    if str(selection.get("fallback_reason") or "").strip()
                ),
            },
            "deviation_from_preset": deviation,
            "licensed_media_reported_separately": True,
            "arithmetic_shot_estimate": estimate_shot_budget(
                target_seconds=target_seconds,
                motion_ratio=float(resolved_preset.get("i2v", 0.0)),
            ),
        }

    def _resolve_mix_preset(
        self, preset: Mapping[str, float] | None
    ) -> tuple[dict[str, float], str]:
        if preset is None:
            return dict(DEFAULT_CHANNEL_MIX), "DEFAULT_GUIDELINE"
        if not isinstance(preset, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "preset 必须是对象", {})
        resolved = dict(DEFAULT_CHANNEL_MIX)
        for key, value in preset.items():
            family = str(key)
            if family not in MIX_FAMILIES:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    f"未知的比例分组：{family}",
                    {"family": family, "allowed": sorted(MIX_FAMILIES)},
                )
            try:
                ratio = float(value)
            except (TypeError, ValueError) as error:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", f"比例必须是数字：{family}", {"family": family, "value": value}
                ) from error
            if ratio < 0 or ratio > 1:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", f"比例必须在 0–1 之间：{family}", {"family": family, "value": ratio}
                )
            resolved[family] = ratio
        guided = {family: resolved[family] for family in DEFAULT_CHANNEL_MIX}
        total = sum(guided.values())
        if abs(total - 1.0) > 1e-6:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "i2v / infographic 两个比例之和必须为 1",
                {"preset": guided, "sum": round(total, 9)},
            )
        return resolved, "CALLER_PRESET"

    # ------------------------------------------------------------------ timing
    def plan_durations_from_real_audio(
        self,
        *,
        video_id: str,
        edition_id: str,
        segment_durations_ms: Mapping[str, int],
    ) -> dict[str, Any]:
        """Allocate beat frame ranges from measured TTS audio for one edition.

        ``preferred_duration_ms`` is reported but never used to override measured
        audio.  A segment linked by several beats has its measured span split
        across them, so the plan total is the measured audio counted exactly
        once.  Frame math uses integer :class:`Ratio` arithmetic, and the last
        beat's exclusive end equals the edition total by construction.
        """

        edition = self.repo.get("explainer_editions", edition_id)
        if str(edition["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "输出 edition 不属于该解说作品",
                {"edition_id": edition_id, "video_id": video_id, "edition_video_id": edition["video_id"]},
            )
        fps = Ratio(int(edition["fps_num"]), int(edition["fps_den"]))
        measured: dict[str, int] = {}
        for key, value in dict(segment_durations_ms or {}).items():
            measured[str(key)] = _require_positive_int(
                value, field="segment_durations_ms", segment=key, edition_id=edition_id
            )

        # Design §5.2/§5.3: timing works on the *edition's* frozen script revision
        # and its own language, and every one of those segments must have a measured
        # take.  Resolving that expected set here is what makes a measured sentence
        # that no beat linked *visible*: previously such a segment contributed
        # nothing to the total, so a plan could claim a complete film while the
        # narration clock was longer than the picture clock.
        revision_id = str(edition.get("frozen_script_revision_id") or "")
        if revision_id:
            expected_source = "EDITION_FROZEN_SCRIPT_REVISION"
        else:
            current = self.repo.get("explainer_videos", video_id)
            revision_id = str(current.get("current_script_revision_id") or "")
            expected_source = "VIDEO_CURRENT_SCRIPT_REVISION" if revision_id else "UNRESOLVED"
        expected_ordinals: dict[str, int] = {}
        canonical_of_key: dict[str, str] = {}
        pause_by_canonical: dict[str, int] = {}
        edition_locale = str(edition.get("voice_locale") or "")
        if revision_id:
            for row in self.repo.segments(revision_id):
                locale = str(row.get("locale") or edition_locale)
                if edition_locale and locale != edition_locale:
                    continue
                canonical = str(row["canonical_segment_id"])
                expected_ordinals[canonical] = int(row.get("ordinal") or 0)
                pause_by_canonical[canonical] = max(0, int(row.get("pause_after_ms") or 0))
                canonical_of_key[canonical] = canonical
                # The caller may key measured audio by either identity; both are
                # accepted, and both are resolved to the canonical segment so the
                # total can never count one sentence twice.
                canonical_of_key[str(row["id"])] = canonical
        measured_canonical: dict[str, int] = {}
        for key, value in measured.items():
            canonical = canonical_of_key.get(key, key)
            measured_canonical.setdefault(canonical, value)

        beats = self.repo.beats(video_id)
        if not beats:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "该解说作品还没有画面段计划", {"video_id": video_id}
            )
        segments = {
            str(item["id"]): item
            for item in self.repo.list_where(
                "narration_segments", {"video_id": video_id}, order_by="ordinal", descending=False
            )
        }
        links_by_beat: dict[str, list[dict[str, Any]]] = {}
        link_targets: dict[str, list[tuple[str, str]]] = {}
        for link in self.repo.beat_links(video_id):
            beat_id = str(link["beat_id"])
            links_by_beat.setdefault(beat_id, []).append(link)
            link_targets.setdefault(str(link["narration_segment_id"]), []).append(
                (beat_id, str(link["canonical_segment_id"]))
            )
        ordinal_of = {str(beat["id"]): int(beat["ordinal"]) for beat in beats}
        for items in links_by_beat.values():
            items.sort(key=lambda item: int(item.get("ordinal") or 0))
        for targets in link_targets.values():
            targets.sort(key=lambda item: ordinal_of.get(item[0], 0))

        # A narration segment linked by several beats is *shared*: its measured
        # span is split across those beats (integer split, remainder to the
        # earlier beats by ordinal).  Measured audio therefore still governs the
        # plan, the total is counted exactly once, and the last beat's exclusive
        # end can equal the total.
        shares: dict[tuple[str, str], dict[str, Any]] = {}
        shared_segments: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for segment_id, targets in link_targets.items():
            canonical_label = targets[0][1]
            measured_value: int | None = None
            for _beat_id, canonical in targets:
                if canonical in measured:
                    measured_value = measured[canonical]
                    canonical_label = canonical
                    break
                if segment_id in measured:
                    measured_value = measured[segment_id]
                    break
            if measured_value is None:
                missing.append(canonical_label)
                continue
            pause_total = max(0, int((segments.get(segment_id) or {}).get("pause_after_ms") or 0))
            count = len(targets)
            base_ms, extra_ms = divmod(int(measured_value), count)
            base_pause, extra_pause = divmod(pause_total, count)
            for position, (beat_id, canonical) in enumerate(targets):
                shares[(beat_id, segment_id)] = {
                    "canonical_segment_id": canonical,
                    "measured_duration_ms": base_ms + (1 if position < extra_ms else 0),
                    "pause_after_ms": base_pause + (1 if position < extra_pause else 0),
                    "shared_with_other_beats": count > 1,
                }
            if count > 1:
                shared_segments[canonical_label] = {
                    "canonical_segment_id": canonical_label,
                    "narration_segment_id": segment_id,
                    "measured_duration_ms": int(measured_value),
                    "linked_beat_count": count,
                    "split_per_beat_ms": base_ms,
                    "shared_between_beat_ids": [beat_id for beat_id, _canonical in targets],
                    "split_rule": "MEASURED_AUDIO_SPLIT_EQUALLY_ACROSS_LINKED_BEATS",
                }

        durations: list[int] = []
        beat_inputs: list[dict[str, Any]] = []
        for beat in beats:
            beat_id = str(beat["id"])
            beat_links = links_by_beat.get(beat_id, [])
            measured_ms = 0
            pause_ms = 0
            used_segments: list[dict[str, Any]] = []
            for link in beat_links:
                share = shares.get((beat_id, str(link["narration_segment_id"])))
                if share is None:
                    continue
                measured_ms += int(share["measured_duration_ms"])
                pause_ms += int(share["pause_after_ms"])
                used_segments.append(
                    {
                        "canonical_segment_id": share["canonical_segment_id"],
                        "narration_segment_id": str(link["narration_segment_id"]),
                        "measured_duration_ms": share["measured_duration_ms"],
                        "pause_after_ms": share["pause_after_ms"],
                        "shared_with_other_beats": share["shared_with_other_beats"],
                    }
                )
            if not beat_links:
                # A beat without narration has no measured audio to follow; its
                # plan hint is the only available input and is reported as such.
                # It is deliberately *not* added to ``total_measured_ms``.
                measured_ms = int(beat.get("preferred_duration_ms") or 0)
            durations.append(measured_ms + pause_ms)
            beat_inputs.append(
                {
                    "beat_id": beat_id,
                    "beat_code": str(beat["code"]),
                    "ordinal": int(beat["ordinal"]),
                    "render_type": str(beat["render_type"]),
                    "preferred_duration_ms": beat.get("preferred_duration_ms"),
                    "measured_duration_ms": measured_ms,
                    "pause_after_ms": pause_ms,
                    "span_duration_ms": measured_ms + pause_ms,
                    "segments": used_segments,
                    "duration_authority": (
                        "MEASURED_TTS" if used_segments else "BEAT_HINT_NO_LINKED_SEGMENT"
                    ),
                }
            )
        if missing:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "缺少真实 TTS 实测时长，不能按时长分配画面段",
                {
                    "edition_id": edition_id,
                    "video_id": video_id,
                    "missing_canonical_segment_ids": sorted(set(missing)),
                },
            )

        # The expected set is only checked when the edition's script revision could
        # be resolved; an unresolvable edition keeps the old per-beat behaviour and
        # says so, rather than inventing a coverage verdict it cannot support.
        scoped_measured = (
            {key: value for key, value in measured_canonical.items() if key in expected_ordinals}
            if expected_ordinals
            else dict(measured_canonical)
        )
        covered_canonical = {
            canonical for targets in link_targets.values() for _beat_id, canonical in targets
        }
        unlinked_measured = sorted(key for key in scoped_measured if key not in covered_canonical)
        if unlinked_measured:
            # This sentence has measured narration but no picture covers it: the
            # picture clock would be shorter than the narration clock, which is
            # exactly the "short film reported as complete" failure.
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "有实测配音的段落没有被任何画面段覆盖，已拒绝该时长计划",
                {
                    "edition_id": edition_id,
                    "video_id": video_id,
                    "unlinked_measured_segment_ids": unlinked_measured,
                },
            )
        unmeasured = sorted(set(expected_ordinals) - set(measured_canonical))
        if expected_ordinals and unmeasured:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "当前稿件仍有段落没有实测配音，不能按时长分配画面段",
                {
                    "edition_id": edition_id,
                    "video_id": video_id,
                    "unmeasured_canonical_segment_ids": unmeasured,
                    "expected_segment_source": expected_source,
                },
            )
        # Measured audio is counted once, over every measured segment of *this*
        # edition — never over the subset some beat happened to link, and never by
        # substituting a plan hint for missing audio.
        total_measured_ms = sum(scoped_measured.values())
        total_pause_ms = sum(pause_by_canonical.get(key, 0) for key in scoped_measured)

        frames, total_frames = _allocate_frames(durations, fps)
        placements: list[dict[str, Any]] = []
        cursor = 0
        for index, beat_input in enumerate(beat_inputs):
            start = cursor
            end = start + frames[index]
            cursor = end
            placements.append(
                {
                    "beat_code": beat_input["beat_code"],
                    "start_frame": start,
                    "end_frame_exclusive": end,
                    "duration_frames": frames[index],
                }
            )
        if not placements or placements[-1]["end_frame_exclusive"] != total_frames:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "画面段时长分配未覆盖实测音频总长",
                {"total_frames": total_frames, "placements": placements},
            )
        ensure_monotonic_frames(
            (item["start_frame"], item["end_frame_exclusive"]) for item in placements
        )
        for index, placement in enumerate(placements):
            placement["beat_id"] = beat_inputs[index]["beat_id"]
            placement["measured_duration_ms"] = beat_inputs[index]["measured_duration_ms"]
            placement["pause_after_ms"] = beat_inputs[index]["pause_after_ms"]
            placement["preferred_duration_ms"] = beat_inputs[index]["preferred_duration_ms"]
            placement["duration_authority"] = beat_inputs[index]["duration_authority"]
        return {
            "video_id": video_id,
            "edition_id": edition_id,
            "edition_key": str(edition["edition_key"]),
            "fps": fps.as_dict(),
            "total_frames": total_frames,
            "total_measured_ms": total_measured_ms,
            "total_pause_ms": total_pause_ms,
            "total_span_ms": sum(item["span_duration_ms"] for item in beat_inputs),
            "expected_segment_source": expected_source,
            "expected_segment_count": len(expected_ordinals),
            "measured_segment_count": len(scoped_measured),
            "measured_take_covers_every_segment": (
                not expected_ordinals or not unmeasured
            ),
            "unlinked_measured_segment_ids": unlinked_measured,
            "placements": placements,
            "beats": beat_inputs,
            "shared_segments": shared_segments,
            "timing_authority": "MEASURED_TTS",
            "preferred_duration_used": False,
            "text_length_estimate_used": False,
            "last_end_equals_total": placements[-1]["end_frame_exclusive"] == total_frames,
        }

    # ------------------------------------------------------------------ bridging
    def frame_bridge_policy(
        self,
        *,
        video_id: str,
        from_beat_code: str,
        to_beat_code: str,
        capability: Mapping[str, Any],
        chain_limit: int = DEFAULT_CHAIN_LIMIT,
    ) -> dict[str, Any]:
        """Decide whether ``to_beat`` may start from ``from_beat``'s end frame.

        Returns ``use_end_frame=False`` with a concrete reason whenever the
        chapter changed, the shots do not connect, the action has no terminal
        state, the capability does not support an end frame, the previous beat
        produced no end frame, or the consecutive chain reached ``chain_limit``.
        Conflicting end-frame geometry demands replanning, never interpolation.
        """

        if chain_limit < 1:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "chain_limit 必须为正整数", {"chain_limit": chain_limit}
            )
        caps = _normalize_capability(capability)
        beats = self.repo.beats(video_id)
        by_code = {str(beat["code"]): beat for beat in beats}
        from_beat = by_code.get(str(from_beat_code))
        to_beat = by_code.get(str(to_beat_code))
        missing = [
            code for code, beat in ((from_beat_code, from_beat), (to_beat_code, to_beat)) if beat is None
        ]
        if missing:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"画面段不存在：{'、'.join(str(item) for item in missing)}",
                {"video_id": video_id, "missing_beat_codes": [str(item) for item in missing]},
            )
        assert from_beat is not None and to_beat is not None

        chapter_by_segment = {
            str(item["id"]): (str(item["chapter_id"]) if item.get("chapter_id") else None)
            for item in self.repo.list_where(
                "narration_segments", {"video_id": video_id}, order_by="ordinal", descending=False
            )
        }
        segments_of_beat: dict[str, list[str]] = {}
        for link in self.repo.beat_links(video_id):
            segments_of_beat.setdefault(str(link["beat_id"]), []).append(
                str(link["narration_segment_id"])
            )

        def chapters_of(beat: Mapping[str, Any]) -> frozenset[str | None]:
            items = segments_of_beat.get(str(beat["id"]), [])
            if not items:
                return frozenset({None})
            return frozenset(chapter_by_segment.get(item) for item in items)

        reason, use_end_frame, requires_replan = self._bridge_facts(
            from_beat, to_beat, caps, chapters_of
        )

        ordered = sorted(beats, key=lambda item: int(item["ordinal"]))
        target_index = ordered.index(to_beat)
        chain_length = 1
        cursor = target_index
        while cursor - 1 >= 0:
            previous, current = ordered[cursor - 1], ordered[cursor]
            pair_reason, pair_use, _pair_replan = self._bridge_facts(previous, current, caps, chapters_of)
            if not pair_use:
                break
            chain_length += 1
            cursor -= 1
        if use_end_frame and chain_length >= chain_limit:
            use_end_frame = False
            reason = "CHAIN_LIMIT_REACHED_RETURN_TO_CANONICAL_REFERENCE"

        return {
            "video_id": video_id,
            "from_beat_code": str(from_beat["code"]),
            "to_beat_code": str(to_beat["code"]),
            "use_end_frame": use_end_frame,
            "reason": reason,
            "requires_replan": requires_replan,
            "return_to_canonical_reference": not use_end_frame,
            "chapter_change": reason == "CHAPTER_CHANGE_NO_END_FRAME_REUSE",
            "chapter_keys": {
                "from": sorted(str(item) for item in chapters_of(from_beat)),
                "to": sorted(str(item) for item in chapters_of(to_beat)),
            },
            "consecutive_chained_beats": chain_length,
            "chain_limit": chain_limit,
            "capability": caps,
            "declared_capability_supports_end_frame": caps["supports_end_frame"],
            "forced_interpolation": False,
        }

    @staticmethod
    def _bridge_facts(
        from_beat: Mapping[str, Any],
        to_beat: Mapping[str, Any],
        capability: Mapping[str, Any],
        chapters_of: Any,
    ) -> tuple[str, bool, bool]:
        if chapters_of(from_beat) != chapters_of(to_beat):
            return "CHAPTER_CHANGE_NO_END_FRAME_REUSE", False, False
        if int(to_beat["ordinal"]) != int(from_beat["ordinal"]) + 1:
            return "BEATS_NOT_CONNECTED", False, False
        if _geometry_conflict(from_beat, to_beat):
            return "END_FRAME_GEOMETRY_CONFLICT_REPLAN_REQUIRED", False, True
        if not capability["supports_end_frame"]:
            return "CAPABILITY_DOES_NOT_SUPPORT_END_FRAME", False, False
        if str(from_beat["render_type"]) not in MOTION_CAPABLE_RENDER_TYPES or not _provides_end_frame(
            from_beat
        ):
            return "PREVIOUS_BEAT_HAS_NO_END_FRAME", False, False
        if not _has_terminal_state(from_beat):
            return "ACTION_HAS_NO_TERMINAL_STATE", False, False
        return "END_FRAME_PAIRING_ALLOWED", True, False

    # ------------------------------------------------------------------ candidates
    def initial_candidate_plan(
        self,
        *,
        video_id: str,
        budget: Budget | Mapping[str, Any],
        key_identity_beat_codes: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Initial candidate counts: 1 for ordinary beats, 2 for key identities."""

        resolved = _as_budget(budget)
        key_codes = {str(item) for item in (key_identity_beat_codes or ())}
        beats = self.repo.beats(video_id)
        known = {str(beat["code"]) for beat in beats}
        unknown = sorted(key_codes - known)
        if unknown:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"关键人物画面段不存在：{'、'.join(unknown)}",
                {"video_id": video_id, "unknown_beat_codes": unknown},
            )
        plan: list[dict[str, Any]] = []
        total = 0
        for beat in beats:
            is_key_identity = str(beat["code"]) in key_codes
            count = plan_initial_candidates(is_key_identity=is_key_identity, budget=resolved)
            total += count
            plan.append(
                {
                    "beat_id": str(beat["id"]),
                    "beat_code": str(beat["code"]),
                    "render_type": str(beat["render_type"]),
                    "must_be_motion": bool(beat["must_be_motion"]),
                    "is_key_identity": is_key_identity,
                    "initial_candidate_count": count,
                }
            )
        return {
            "video_id": video_id,
            "budget": resolved.as_dict(),
            "beats": plan,
            "total_initial_candidates": total,
            "key_identity_beat_codes": sorted(key_codes),
            "technical_retries_counted_separately": True,
            "creative_repairs_counted_separately": True,
            "budget_exhaustion_behaviour": "STOP_AND_REPORT",
        }

    def register_candidate(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_id: str,
        candidate_kind: str,
        media_version_id: str,
        execution_snapshot: Mapping[str, Any],
        lineage: Mapping[str, Any],
        render_type_actual: str | None = None,
        fallback_reason: str | None = None,
        purpose: str = "VISUAL",
        budget: Budget | Mapping[str, Any] | None = None,
        step_code: str = DEFAULT_STORYBOARD_STEP_CODE,
        key_identity: bool | None = None,
        qc_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register one candidate variant and enforce the separate budgets.

        Technical retries and creative repairs each get their own counter and
        their own authorization; when either is exhausted this raises
        ``BUDGET_EXCEEDED`` so the caller stops instead of drawing again.

        ``qc_summary`` lets a producer add the facts it *actually measured* at
        registration time (a worker that produced a real file knows the file exists
        and its duration).  It is merged **under** the fields this method measures
        itself, so a caller can never claim a check it did not run and can never
        overwrite the motion/integrity facts computed here.
        """

        kind = _require_member(str(candidate_kind), CANDIDATE_KINDS, field="candidate_kind")
        _require_member(str(purpose), CANDIDATE_PURPOSES, field="purpose")
        if not isinstance(execution_snapshot, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "execution_snapshot 必须是对象", {"beat_id": beat_id})
        if not isinstance(lineage, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "lineage 必须是对象", {"beat_id": beat_id})
        media = self.repo.require_same_project_media(
            project_id=project_id, media_version_id=str(media_version_id)
        )
        beat = self.repo.get("explainer_visual_beats", beat_id)
        if str(beat["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "画面段不属于该解说作品",
                {"beat_id": beat_id, "video_id": video_id, "beat_video_id": beat["video_id"]},
            )
        render_type_planned = str(beat["render_type"])
        # A KEYFRAME / REFERENCE candidate is a *picture*, not a rendered clip, so it
        # declares no actual render type at all: only a real composable clip has one.
        # The old fallback copied the beat's planned type onto every candidate, which
        # made a reference or a first frame claim the plan's I2V as if a video model
        # had produced it.
        if render_type_actual:
            actual: str | None = str(render_type_actual)
        elif str(purpose) == "VISUAL":
            actual = render_type_planned
        else:
            actual = None
        if actual is not None:
            _require_member(actual, (item.value for item in RenderType), field="render_type_actual", beat_id=beat_id)
        degraded = actual is not None and actual != render_type_planned
        if degraded and not str(fallback_reason or "").strip():
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "实际渲染类型与计划不一致时必须记录 fallback_reason",
                {
                    "beat_id": beat_id,
                    "render_type_planned": render_type_planned,
                    "render_type_actual": actual,
                },
            )

        resolved_budget, run_id = self._budget_for_video(video_id, budget)
        is_key_identity = bool(beat.get("entity_refs_json")) if key_identity is None else bool(key_identity)
        initial_allowance = plan_initial_candidates(is_key_identity=is_key_identity, budget=resolved_budget)
        creative_rows = self.repo.list_where(
            "explainer_media_candidates", {"beat_id": beat_id, "candidate_kind": "CREATIVE"}
        )
        technical_rows = self.repo.list_where(
            "explainer_media_candidates", {"beat_id": beat_id, "candidate_kind": "TECHNICAL_RETRY"}
        )
        creative_repairs_before = max(0, len(creative_rows) - initial_allowance)
        # The *first* candidate of a given purpose is a delivery, not a repair.  A beat
        # needs both a first frame (``KEYFRAME``) and a composable clip (``VISUAL``),
        # and they are produced by different models in two separate steps; counting the
        # second one as a redraw of the first made a legitimately generated clip
        # unregistrable — the GPU work was finished and then discarded with
        # ``BUDGET_EXCEEDED`` because three candidate rows already existed for that
        # beat.  Only a *second* candidate for the same purpose is a creative repair.
        existing_same_purpose = [
            item
            for item in creative_rows
            if str(item.get("purpose") or "VISUAL") == str(purpose)
            and (
                str(purpose) != "VISUAL"
                # A picture candidate is a delivered first frame whatever its render
                # type; a VISUAL candidate only counts once it is a real clip.
                or str(item.get("render_type_actual") or "") in PRODUCED_CLIP_RENDER_TYPES
            )
        ]
        is_first_delivery = kind == "CREATIVE" and not existing_same_purpose
        # Technical retries are counted per (step, beat): one busy beat may not
        # consume the whole step's retry budget, but it also may not exceed it.
        retry_ledger_key = f"{step_code}:{beat_id}"
        ledger = BudgetLedger(
            budget=resolved_budget,
            creative_repairs_by_beat={beat_id: creative_repairs_before},
            technical_retries_by_step={retry_ledger_key: len(technical_rows)},
        )
        if kind == "TECHNICAL_RETRY":
            ledger.check_technical_retry(retry_ledger_key)
        elif not is_first_delivery:
            ledger.check_creative_repair(beat_id)

        variant_row = self.repo.query_one(
            """
            SELECT MAX(variant_no) AS max_variant FROM explainer_media_candidates
            WHERE beat_id = ? AND candidate_kind = ?
            """,
            (beat_id, kind),
        )
        variant_no = int((variant_row["max_variant"] if variant_row else 0) or 0) + 1
        technical_retry_count = len(technical_rows) + 1 if kind == "TECHNICAL_RETRY" else 0
        creative_repair_count = creative_repairs_before + 1 if kind == "CREATIVE" else 0
        if kind == "CREATIVE" and (len(creative_rows) < initial_allowance or is_first_delivery):
            # Initial candidates, and the first delivery of any purpose, are not repairs.
            creative_repair_count = 0

        must_be_motion_violated = bool(beat["must_be_motion"]) and actual in STILL_RENDER_TYPES
        if must_be_motion_violated and str(purpose) == "VISUAL":
            # A still is a perfectly good KEYFRAME (it is the input an I2V job needs),
            # but it can never be the final composable clip of a beat the plan marked as
            # requiring motion (design §B5.2/§B6.2).  Refusing here keeps every producer
            # — the worker stage, an upload, a re-draw — under the same rule instead of
            # only the HTTP registration route.
            raise ExplainerContractError(
                "QC_BLOCKED",
                "必须运动的画面段不能登记静图作为最终片段；请登记为首帧候选并生成 AI 动态片段",
                {
                    "beat_id": beat_id,
                    "purpose": str(purpose),
                    "render_type_actual": actual,
                    "must_be_motion": True,
                },
            )
        row = self.repo.insert(
            "explainer_media_candidates",
            {
                "video_id": video_id,
                "beat_id": beat_id,
                "variant_no": variant_no,
                "candidate_kind": kind,
                "purpose": str(purpose),
                "media_asset_id": str(media["media_asset_id"]),
                "media_version_id": str(media["media_version_id"]),
                "media_sha256": str(media["sha256"]),
                "status": "READY",
                "execution_snapshot_json": dict(execution_snapshot),
                "lineage_json": dict(lineage),
                "render_type_planned": render_type_planned,
                "render_type_actual": actual,
                "fallback_reason": str(fallback_reason) if fallback_reason else None,
                "technical_retry_count": technical_retry_count,
                "creative_repair_count": creative_repair_count,
                "qc_summary_json": {
                    # Measured facts first, caller-supplied facts second: this method's
                    # own measurements always win.
                    **(dict(qc_summary) if isinstance(qc_summary, Mapping) else {}),
                    "must_be_motion_violated": must_be_motion_violated,
                    "media_integrity_status": str(media.get("integrity_status") or ""),
                    "checked_here": "REGISTRATION_ONLY",
                },
                "adopted": False,
            },
        )
        return {
            "candidate": row,
            "candidate_id": str(row["id"]),
            "video_id": video_id,
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "candidate_kind": kind,
            "variant_no": variant_no,
            "render_type_planned": render_type_planned,
            "render_type_actual": actual,
            "fallback_reason": row.get("fallback_reason"),
            "degraded": degraded,
            "technical_retry_count": technical_retry_count,
            "creative_repair_count": creative_repair_count,
            "initial_candidate_allowance": initial_allowance,
            "is_key_identity": is_key_identity,
            "must_be_motion_violated": must_be_motion_violated,
            "budget": ledger.as_dict(),
            "budget_source_run_id": run_id,
            "technical_retries_counted_separately": True,
            "counted_as_successful_planned_type": not degraded,
        }

    def _budget_for_video(
        self, video_id: str, budget: Budget | Mapping[str, Any] | None
    ) -> tuple[Budget, str | None]:
        if budget is not None:
            return _as_budget(budget), None
        runs = self.repo.list_where(
            "explainer_runs", {"video_id": video_id}, order_by="created_at", descending=True, limit=1
        )
        if not runs:
            return default_budget(), None
        raw = runs[0].get("budget_json")
        return _as_budget(raw if isinstance(raw, Mapping) else None), str(runs[0]["id"])

    def evaluate_candidates(
        self, *, beat_id: str, candidates: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Rank candidates in the fixed adoption order, with honest verdicts.

        Stage order is technical -> content -> constraint -> readability ->
        style.  A candidate with any blocking finding is ranked strictly after
        every eligible candidate, so an aesthetic score can never promote a
        factual or technical failure.

        Each required check is reported as ``PASS``/``FAIL``/``UNKNOWN``/``NOT_RUN``.
        A candidate is only *machine*-adoptable when every applicable required check
        is ``PASS``; a check that was never measured is ``UNKNOWN`` and keeps the
        candidate out of the recommended set, which is what stops an unverified
        candidate from being promoted on aesthetics alone (design §6.2).
        """

        beat = self.repo.get("explainer_visual_beats", beat_id)
        must_be_motion = bool(beat["must_be_motion"])
        identity_applicable = _beat_binds_identity(beat)
        ranked: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates or ()):
            identifier = _candidate_identifier(candidate, index=index)
            # Verdicts may be nested in the candidate's qc_summary or supplied flat.
            checks = _candidate_check_view(candidate)
            stages: list[dict[str, Any]] = []
            blockers: list[str] = []
            unknown_checks: list[str] = []
            not_run_checks: list[str] = []
            reasons: list[str] = []

            technical_state, _ = _declared_false_is_failure(checks, ("file_valid", "decoded"))
            status = str(candidate.get("status") or "").upper()
            status_not_adoptable = status in NOT_ADOPTABLE_CANDIDATE_STATUSES
            technical: list[str] = []
            if technical_state == CHECK_FAIL or status_not_adoptable:
                technical.append("FILE_NOT_DECODED" if technical_state == CHECK_FAIL else "CANDIDATE_STATUS_NOT_ADOPTABLE")
            elif technical_state == CHECK_UNKNOWN:
                unknown_checks.append(ADOPTION_STAGES[0])
                reasons.append("FILE_OR_DECODE_NOT_REPORTED")
            stages.append(
                {
                    "stage": ADOPTION_STAGES[0],
                    "state": CHECK_FAIL if technical else technical_state,
                    "blockers": technical,
                    "applicable": True,
                }
            )
            blockers.extend(technical)

            content_state, _ = _declared_false_is_failure(
                checks, ("content_relevant", "content_match")
            )
            content: list[str] = []
            if content_state == CHECK_FAIL:
                content.append("CONTENT_IRRELEVANT")
            elif content_state == CHECK_UNKNOWN:
                unknown_checks.append(ADOPTION_STAGES[1])
                reasons.append("CONTENT_RELEVANCE_NOT_REPORTED")
            stages.append(
                {
                    "stage": ADOPTION_STAGES[1],
                    "state": CHECK_FAIL if content else content_state,
                    "blockers": content,
                    "applicable": True,
                }
            )
            blockers.extend(content)

            constraints: list[str] = []
            constraint_states: list[str] = []
            declared_state, _ = _declared_false_is_failure(checks, ("identity_ok", "constraints_ok"))
            violations = checks.get("constraint_violations")
            declared_violations = [item for item in (violations or ())]
            # An empty (or absent) violation list is not itself a verdict: the check
            # is decided by the declared ``identity_ok``/``constraints_ok`` fields.  A
            # *non-empty* list is an explicit finding and fails the check outright.
            # When neither the verdict fields nor the list were recorded, the
            # declared state stays UNKNOWN and the candidate is not auto-adoptable.
            violation_state = CHECK_FAIL if declared_violations else CHECK_PASS
            actual_type = str(
                candidate.get("render_type_actual") or candidate.get("render_type") or ""
            )
            motion_state = CHECK_NOT_RUN
            if must_be_motion:
                motion_state, _ = _declared_true_is_failure(checks, "must_be_motion_violated")
                if motion_state == CHECK_UNKNOWN and actual_type in STILL_RENDER_TYPES:
                    # The beat requires motion and the material is a still: the
                    # requirement is provably violated without any extra flag.
                    motion_state = CHECK_FAIL
            elif checks.get("must_be_motion_violated") is True:
                # A candidate that reports a motion violation is refused even when
                # the beat did not ask for motion: non-motion material is preferred.
                motion_state = CHECK_FAIL
            constraint_states.extend((declared_state, violation_state, motion_state))
            if declared_state == CHECK_FAIL or violation_state == CHECK_FAIL:
                constraints.append("IDENTITY_CONSTRAINT_VIOLATED")
            if motion_state == CHECK_FAIL:
                constraints.append("MUST_BE_MOTION_VIOLATED")
            if not identity_applicable:
                identity_state = CHECK_NOT_RUN
                not_run_checks.append(ADOPTION_STAGES[2])
                reasons.append("BEAT_BINDS_NO_IDENTITY_REFERENCE")
            elif CHECK_FAIL in constraint_states:
                identity_state = CHECK_FAIL
            elif CHECK_UNKNOWN in constraint_states:
                identity_state = CHECK_UNKNOWN
                unknown_checks.append(ADOPTION_STAGES[2])
                reasons.append("IDENTITY_STATE_NOT_REPORTED")
            else:
                identity_state = CHECK_PASS
            stages.append(
                {
                    "stage": ADOPTION_STAGES[2],
                    "state": identity_state,
                    "blockers": constraints,
                    "applicable": identity_applicable,
                }
            )
            blockers.extend(constraints)

            text_applicable = (
                str(beat.get("render_type") or "") in TEXT_LAYER_RENDER_TYPES
                or any(
                    candidate.get(field) is not None
                    for field in ("text_readable", "readable", "audible")
                )
            )
            readability_state, _ = _declared_false_is_failure(
                checks, ("text_readable", "readable", "audible")
            )
            readability: list[str] = []
            if not text_applicable:
                readability_state = CHECK_NOT_RUN
                not_run_checks.append(ADOPTION_STAGES[3])
                reasons.append("BEAT_DRAWS_NO_READABLE_TEXT")
            elif readability_state == CHECK_FAIL:
                readability.append("TEXT_UNREADABLE")
                readability_state = CHECK_FAIL
            elif readability_state == CHECK_UNKNOWN:
                unknown_checks.append(ADOPTION_STAGES[3])
                reasons.append("READABILITY_NOT_REPORTED")
            stages.append(
                {
                    "stage": ADOPTION_STAGES[3],
                    "state": readability_state,
                    "blockers": readability,
                    "applicable": text_applicable,
                }
            )
            blockers.extend(readability)

            style_score = _optional_float(
                checks.get("aesthetic_score", checks.get("style_score"))
            )
            stages.append({"stage": ADOPTION_STAGES[4], "state": CHECK_PASS, "blockers": [], "applicable": True})

            # Machine adoption needs every applicable required check to have PASSED;
            # a human may still adopt with the gap recorded (ADR: only hard technical
            # failures are never skippable, and those are FAIL blockers).
            machine_allowed = not blockers and not unknown_checks
            failing_stage = next((stage["stage"] for stage in stages if stage["blockers"]), None)
            verdict = (
                failing_stage
                or (unknown_checks[0] if unknown_checks else None)
                or "PASSED_ALL_STAGES"
            )
            ranked.append(
                {
                    "candidate_id": identifier,
                    "candidate_kind": str(candidate.get("candidate_kind") or ""),
                    "variant_no": _optional_int(candidate.get("variant_no")),
                    "render_type_actual": actual_type or None,
                    "adoption_blockers": blockers,
                    "blocked": bool(blockers),
                    "eligible_for_adoption": machine_allowed,
                    "machine_adoption_allowed": machine_allowed,
                    "human_review_required": bool(unknown_checks),
                    "unknown_checks": unknown_checks,
                    "not_run_checks": not_run_checks,
                    "check_reasons": reasons,
                    "required_checks_state": {
                        stage["stage"]: stage["state"] for stage in stages[: len(REQUIRED_ADOPTION_CHECKS)]
                    },
                    "applicable_checks": [
                        stage["stage"]
                        for stage in stages[: len(REQUIRED_ADOPTION_CHECKS)]
                        if stage["applicable"]
                    ],
                    "adoption_tier": (
                        ADOPTION_STAGES.index(failing_stage) + 1
                        if failing_stage is not None
                        else len(ADOPTION_STAGES)
                    ),
                    "adoption_tier_name": verdict,
                    "stage_results": stages,
                    "style_score": style_score,
                    "constraint_violations": [dict(item) if isinstance(item, Mapping) else item for item in (violations or ())],
                }
            )
        eligible_items = sorted(
            (item for item in ranked if item["eligible_for_adoption"]),
            key=lambda item: (-float(item["style_score"]), item["candidate_id"]),
        )
        # A candidate with no FAIL but an unmeasured required check is *reviewable*,
        # not adoptable: it is ranked after every fully verified candidate so an
        # aesthetic score can never float it to the top, but before the candidates
        # that actually failed a check.
        review_items = sorted(
            (
                item
                for item in ranked
                if not item["eligible_for_adoption"] and not item["blocked"] and item["human_review_required"]
            ),
            key=lambda item: (-float(item["style_score"]), item["candidate_id"]),
        )
        blocked_items = sorted(
            (item for item in ranked if item["blocked"]),
            key=lambda item: (item["adoption_tier"], -float(item["style_score"]), item["candidate_id"]),
        )
        ordered = eligible_items + review_items + blocked_items
        for position, item in enumerate(ordered, start=1):
            item["rank"] = position
        return {
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "must_be_motion": must_be_motion,
            "identity_check_applicable": identity_applicable,
            "ranked": ordered,
            "eligible_count": len(eligible_items),
            "needs_review_count": len(review_items),
            "blocked_count": len(blocked_items),
            "recommended_candidate_id": eligible_items[0]["candidate_id"] if eligible_items else None,
            "adoption_rule": ADOPTION_RULE,
            "adoption_stages": list(ADOPTION_STAGES),
            "required_checks": list(REQUIRED_ADOPTION_CHECKS),
            "check_states": list(CHECK_STATES),
            "unknown_is_not_a_pass": True,
            "aesthetic_never_overrides_fact": True,
            "blocker_vocabulary": [
                "FILE_NOT_DECODED",
                "CONTENT_IRRELEVANT",
                "IDENTITY_CONSTRAINT_VIOLATED",
                "TEXT_UNREADABLE",
                "AUDIBLE_UNREADABLE",
                "MUST_BE_MOTION_VIOLATED",
                "CANDIDATE_STATUS_NOT_ADOPTABLE",
            ],
        }

    def adopt_selection(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_id: str,
        candidate_id: str,
        edition_id: str | None = None,
        authority: str = "MACHINE_POLICY",
        actor: str | None = None,
        policy_decision_id: str | None = None,
        purpose: str = "VISUAL",
        expected_selection_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Adopt one candidate as the ACTIVE selection without deleting history.

        Refuses to overwrite a human-locked scope unless ``authority="HUMAN"`` and
        refuses to adopt a still-image candidate as the final clip of a
        ``must_be_motion`` beat.

        ``purpose`` separates the three layers the design requires (spec §D2.2):
        ``REFERENCE`` (定妆参考), ``KEYFRAME`` (the adopted first frame) and
        ``VISUAL`` (the final composable clip).  Each purpose owns its own ACTIVE
        row per edition, so adopting a new first frame can no longer silently
        replace an adopted clip, and the ``must_be_motion`` gate applies only to the
        final ``VISUAL`` selection — a still candidate is exactly what an I2V job
        needs as its输入首帧.
        """

        self.repo.require_explainer_project(project_id)
        authority_value = _require_member(
            str(authority), ("MACHINE_POLICY", "HUMAN"), field="adoption_authority"
        )
        purpose_value = _require_member(str(purpose), CANDIDATE_PURPOSES, field="purpose")
        if authority_value == "HUMAN" and not str(actor or "").strip():
            raise ExplainerContractError(
                "SCHEMA_INVALID", "HUMAN 采用必须记录真实操作者", {"beat_id": beat_id}
            )
        beat = self.repo.get("explainer_visual_beats", beat_id)
        if str(beat["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "画面段不属于该解说作品",
                {"beat_id": beat_id, "video_id": video_id},
            )
        candidate = self.repo.get("explainer_media_candidates", candidate_id)
        if str(candidate["beat_id"]) != beat_id or str(candidate["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "候选不属于该画面段或该解说作品",
                {"candidate_id": candidate_id, "beat_id": beat_id, "video_id": video_id},
            )
        if str(candidate.get("purpose") or "VISUAL") != purpose_value:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "候选的用途与本次采用的目标不一致",
                {
                    "candidate_id": candidate_id,
                    "candidate_purpose": str(candidate.get("purpose") or "VISUAL"),
                    "purpose": purpose_value,
                },
            )
        if str(candidate.get("status") or "") != "READY":
            raise ExplainerContractError(
                "QC_BLOCKED",
                "只有已生成并登记媒体的候选才能采用",
                {"candidate_id": candidate_id, "status": str(candidate.get("status") or "")},
            )
        if edition_id is not None:
            edition = self.repo.get("explainer_editions", str(edition_id))
            if str(edition["video_id"]) != video_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "输出 edition 不属于该解说作品",
                    {"edition_id": str(edition_id), "video_id": video_id},
                )

        locked = self.repo.has_human_lock(beat_id, purpose=purpose_value)
        if locked and authority_value != "HUMAN":
            raise ExplainerContractError(
                "QC_BLOCKED",
                "该画面段已按人工锁定，自动流程不能替换；如需更新请以 HUMAN 权威重新采用",
                {"beat_id": beat_id, "authority": authority_value, "purpose": purpose_value},
            )
        if expected_selection_id is not None:
            current = self.repo.active_beat_selection(
                beat_id, str(edition_id) if edition_id else None, purpose=purpose_value
            )
            current_id = str(current["id"]) if current else None
            if current_id != str(expected_selection_id):
                raise ExplainerContractError(
                    "STALE_REVISION",
                    "该画面已更新，请刷新当前选择后重试。",
                    {
                        "purpose": purpose_value,
                        "expected_selection_id": str(expected_selection_id),
                        "current_selection_id": current_id,
                    },
                )

        render_type_planned = str(candidate.get("render_type_planned") or beat["render_type"])
        render_type_actual = str(candidate.get("render_type_actual") or render_type_planned)
        if purpose_value == "VISUAL" and bool(beat["must_be_motion"]) and render_type_actual in STILL_RENDER_TYPES:
            raise ExplainerContractError(
                "QC_BLOCKED",
                "必须运动的画面段不能采用静帧候选作为最终片段",
                {
                    "beat_id": beat_id,
                    "candidate_id": candidate_id,
                    "must_be_motion": True,
                    "render_type_actual": render_type_actual,
                    "hint": "静帧可以作为首帧候选（purpose=KEYFRAME）用于生成 AI 动态片段。",
                },
            )

        degraded = render_type_actual != render_type_planned
        fallback_reason = str(candidate.get("fallback_reason") or "").strip() or None
        fallback_reason_defaulted = False
        if degraded and fallback_reason is None:
            fallback_reason = f"DEGRADED_FROM_{render_type_planned}_TO_{render_type_actual}"
            fallback_reason_defaulted = True
        if not degraded:
            fallback_reason = None

        media = self.repo.require_same_project_media(
            project_id=project_id, media_version_id=str(candidate["media_version_id"])
        )
        snapshot = candidate.get("execution_snapshot_json") or {}
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        source_in_us = _optional_int(snapshot.get("source_in_us"))
        source_out_us = _optional_int(snapshot.get("source_out_us"))
        if source_in_us is None:
            source_in_ms = _optional_int(snapshot.get("source_in_ms"))
            source_in_us = source_in_ms * 1000 if source_in_ms is not None else None
        if source_out_us is None:
            source_out_ms = _optional_int(snapshot.get("source_out_ms"))
            source_out_us = source_out_ms * 1000 if source_out_ms is not None else None
        duration_ms = _optional_int(media.get("duration_ms"))
        if source_in_us is None and source_out_us is None and duration_ms:
            source_in_us = 0
            source_out_us = duration_ms * 1000
        if source_in_us is not None and source_out_us is not None and source_out_us < source_in_us:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "候选的源入点/出点区间不合法",
                {"candidate_id": candidate_id, "source_in_us": source_in_us, "source_out_us": source_out_us},
            )

        # Design §6.3: the adoption authority decides what must already hold.  A
        # machine adoption may only take a candidate whose applicable required checks
        # all PASSED — an unmeasured check is UNKNOWN and keeps the candidate out.  A
        # human may adopt past a content/identity/readability finding (they are the
        # reviewer of record) but never past a hard technical failure, because a
        # corrupt or undecodable file cannot be reviewed away.
        verdict = self.evaluate_candidates(beat_id=beat_id, candidates=[dict(candidate)])["ranked"][0]
        hard_failures = sorted(set(verdict["adoption_blockers"]) & HARD_TECHNICAL_BLOCKERS)
        if authority_value == "MACHINE_POLICY" and not verdict["machine_adoption_allowed"]:
            raise ExplainerContractError(
                "QC_BLOCKED",
                "候选的必需检查没有全部通过，机器流程不能自动采用",
                {
                    "beat_id": beat_id,
                    "candidate_id": candidate_id,
                    "adoption_blockers": verdict["adoption_blockers"],
                    "unknown_checks": verdict["unknown_checks"],
                    "required_checks_state": verdict["required_checks_state"],
                },
            )
        if authority_value == "HUMAN" and hard_failures:
            raise ExplainerContractError(
                "QC_BLOCKED",
                "候选存在技术硬错误，人工采用也不能跳过",
                {"beat_id": beat_id, "candidate_id": candidate_id, "hard_failures": hard_failures},
            )

        media_sha256 = str(candidate["media_sha256"] or media["sha256"])
        # An ACTIVE selection exists per (beat, purpose, edition) scope.  A NULL
        # edition_id is the video-wide fallback tier and a concrete edition_id is a
        # deliberate per-edition override, so adopting one must never supersede the
        # other (design §D2.2 item 2: the scope is exact, not "newest wins").
        previous = self.repo.active_beat_selection(
            beat_id, str(edition_id) if edition_id else None, purpose=purpose_value
        )
        if (
            previous is not None
            and str(previous["candidate_id"]) == str(candidate_id)
            and str(previous["media_sha256"]) == media_sha256
        ):
            # Re-adopting the same material is not a change: keep the existing
            # selection and, when this call carries a human lock, record the lock on
            # the beat.  Nothing downstream is invalidated and nothing is re-rendered
            # (design §6.3) — the old code superseded the row and rewrote it every
            # time the same candidate was chosen again.
            if authority_value == "HUMAN" and not locked:
                self.repo.update(
                    "explainer_visual_beats",
                    beat_id,
                    {"locked_by_human": True, "locked_by": str(actor), "locked_at": utc_now_iso()},
                )
            return {
                "selection": previous,
                "selection_id": str(previous["id"]),
                "video_id": video_id,
                "beat_id": beat_id,
                "beat_code": str(beat["code"]),
                "candidate_id": str(candidate_id),
                "edition_id": str(edition_id) if edition_id else None,
                "purpose": purpose_value,
                "adoption_authority": authority_value,
                "locked_by_human": authority_value == "HUMAN" or locked,
                "reused_existing_selection": True,
                "superseded_selection_id": None,
                "superseded_selection_deleted": False,
                "invalidated": [],
                "verdict": verdict["adoption_tier_name"],
                "unknown_checks": verdict["unknown_checks"],
                "source_window": {
                    "source_in_us": previous.get("source_in_us"),
                    "source_out_us": previous.get("source_out_us"),
                },
                "render_type_planned": render_type_planned,
                "render_type_actual": render_type_actual,
                "degraded": degraded,
                "fallback_reason": fallback_reason,
                "fallback_reason_defaulted": fallback_reason_defaulted,
                "counted_as_successful_planned_type": not degraded,
                "planned_and_actual_are_separate_fields": True,
            }

        superseded_selection_id: str | None = None
        if previous is not None:
            superseded_selection_id = str(previous["id"])
            self.repo.update("explainer_beat_selections", superseded_selection_id, {"status": "SUPERSEDED"})

        selection = self.repo.insert(
            "explainer_beat_selections",
            {
                "video_id": video_id,
                "beat_id": beat_id,
                "edition_id": str(edition_id) if edition_id else None,
                "purpose": purpose_value,
                "candidate_id": str(candidate_id),
                "media_asset_id": str(candidate["media_asset_id"] or media["media_asset_id"]),
                "media_version_id": str(candidate["media_version_id"]),
                "media_sha256": media_sha256,
                "source_in_us": source_in_us,
                "source_out_us": source_out_us,
                "adoption_authority": authority_value,
                "locked_by_human": authority_value == "HUMAN",
                "actor": str(actor) if actor else None,
                "decided_at": utc_now_iso(),
                "policy_decision_id": str(policy_decision_id) if policy_decision_id else None,
                "render_type_actual": render_type_actual,
                "fallback_reason": fallback_reason,
                "status": "ACTIVE",
            },
        )
        self.repo.update("explainer_media_candidates", str(candidate_id), {"adopted": True})
        return {
            "selection": selection,
            "selection_id": str(selection["id"]),
            "video_id": video_id,
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "candidate_id": str(candidate_id),
            "edition_id": str(edition_id) if edition_id else None,
            "purpose": purpose_value,
            "adoption_authority": authority_value,
            "locked_by_human": authority_value == "HUMAN",
            "reused_existing_selection": False,
            "superseded_selection_id": superseded_selection_id,
            "superseded_selection_deleted": False,
            "invalidated": sorted({"BEAT_SELECTION", "COMPOSITION_REVISION", "RENDER", "DELIVERY", "QC_REPORT"})
            if superseded_selection_id
            else [],
            "verdict": verdict["adoption_tier_name"],
            "unknown_checks": verdict["unknown_checks"],
            "source_window": {"source_in_us": source_in_us, "source_out_us": source_out_us},
            "render_type_planned": render_type_planned,
            "render_type_actual": render_type_actual,
            "degraded": degraded,
            "fallback_reason": fallback_reason,
            "fallback_reason_defaulted": fallback_reason_defaulted,
            "counted_as_successful_planned_type": not degraded,
            "planned_and_actual_are_separate_fields": True,
        }

    # ------------------------------------------------------------------ archive / edit
    def archive_candidate(
        self,
        *,
        project_id: str,
        video_id: str,
        candidate_id: str,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Stop offering one candidate without deleting its media (design §B5.3).

        ``不采用`` hides a candidate from the working list.  The physical media and the
        row are kept so the decision is reversible and auditable; only the *status*
        moves to ``REJECTED``, which the adoption rule already treats as not adoptable.
        The current adopted candidate can never be archived this way — removing the
        working version is an explicit unlock/replace action, not a cleanup.
        """

        self.repo.require_explainer_project(project_id)
        candidate = self.repo.get("explainer_media_candidates", candidate_id)
        if str(candidate["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "候选不属于该解说作品",
                {"candidate_id": candidate_id, "video_id": video_id},
            )
        beat_id = str(candidate.get("beat_id") or "")
        if beat_id:
            for purpose in CANDIDATE_PURPOSES:
                active = self.repo.active_beat_selection(beat_id, purpose=str(purpose))
                if active and str(active.get("candidate_id") or "") == candidate_id:
                    raise ExplainerContractError(
                        "QC_BLOCKED",
                        "该候选是当前采用版本，不能直接不采用；请先采用其他候选或解锁。",
                        {"candidate_id": candidate_id, "purpose": str(purpose)},
                    )
        status = str(candidate.get("status") or "")
        if status == "REJECTED":
            return {
                "candidate_id": candidate_id,
                "status": "REJECTED",
                "archived": False,
                "idempotent_replay": True,
                "media_kept": True,
            }
        if status in {"PENDING", "GENERATING"}:
            raise ExplainerContractError(
                "QC_BLOCKED",
                "该候选仍在生成中，完成或失败后才能归档",
                {"candidate_id": candidate_id, "status": status},
            )
        lineage = candidate.get("lineage_json") if isinstance(candidate.get("lineage_json"), Mapping) else {}
        updated = self.repo.update(
            "explainer_media_candidates",
            candidate_id,
            {
                "status": "REJECTED",
                "adopted": False,
                "lineage_json": {
                    **dict(lineage),
                    "archived_by": str(actor),
                    "archived_reason": str(reason or "用户选择不采用"),
                    "archived_at": utc_now_iso(),
                    "previous_status": status,
                },
            },
            actor=actor,
        )
        return {
            "candidate_id": candidate_id,
            "status": str(updated.get("status") or "REJECTED"),
            "previous_status": status,
            "archived": True,
            "idempotent_replay": False,
            "media_kept": True,
            "recoverable": True,
            "actor": str(actor),
        }

    def update_beat(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_id: str,
        changes: Mapping[str, Any],
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Save this beat's own description and presentation (design §B5.2).

        Only a person edits a beat outside the planner, so the new values are recorded
        as a ``HUMAN`` edit.  The full image/video prompt structure lives in
        ``shot_grammar_json.prompt_bundle`` while ``prompt_intent`` stays the compatible
        single-string field ``create_plan`` reads (design §C5.4), so both are written
        together and neither silently diverges.  A queued task keeps its own frozen
        snapshot: nothing already dispatched is rewritten.
        """

        beat = self.repo.get("explainer_visual_beats", beat_id)
        if str(beat["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id, "video_id": video_id}
            )
        if expected_revision is not None and int(expected_revision) != int(beat.get("revision") or 0):
            raise ExplainerContractError(
                "STALE_REVISION",
                "该画面段已被其他操作更新，请刷新后重试",
                {
                    "beat_id": beat_id,
                    "expected_revision": int(expected_revision),
                    "actual_revision": int(beat.get("revision") or 0),
                },
            )
        allowed = {
            "visual_intent",
            "prompt_intent",
            "negative_prompt",
            "camera_movement",
            "on_screen_text",
            "continuity_note",
            "render_type",
            "preferred_duration_ms",
            "must_be_motion",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "不允许修改这些字段", {"fields": unknown, "allowed": sorted(allowed)}
            )
        payload: dict[str, Any] = {}
        grammar = beat.get("shot_grammar_json") if isinstance(beat.get("shot_grammar_json"), Mapping) else {}
        prompt_bundle = dict(grammar.get("prompt_bundle") or {}) if isinstance(grammar, Mapping) else {}
        if "visual_intent" in changes:
            payload["visual_intent"] = str(changes["visual_intent"])
            prompt_bundle["visual_intent"] = str(changes["visual_intent"])
        if "prompt_intent" in changes:
            payload["prompt_intent"] = str(changes["prompt_intent"])
            prompt_bundle["prompt"] = str(changes["prompt_intent"])
        if "negative_prompt" in changes:
            prompt_bundle["negative_prompt"] = str(changes["negative_prompt"])
        if "camera_movement" in changes:
            prompt_bundle["camera_movement"] = str(changes["camera_movement"])
        if "on_screen_text" in changes:
            prompt_bundle["on_screen_text"] = [str(item) for item in (changes["on_screen_text"] or [])]
        if "continuity_note" in changes:
            prompt_bundle["continuity_note"] = str(changes["continuity_note"])
        if prompt_bundle:
            payload["shot_grammar_json"] = {**dict(grammar), "prompt_bundle": prompt_bundle}
        if "render_type" in changes and changes["render_type"] is not None:
            render_type = _require_member(
                str(changes["render_type"]), (item.value for item in RenderType), field="render_type"
            )
            payload["render_type"] = render_type
            if render_type in STILL_RENDER_TYPES and bool(beat.get("must_be_motion")):
                # Presenting a motion requirement as a still cannot be silent: it is
                # recorded as an explicit user decision that the requirement no longer
                # holds, and only this beat is affected (design §B6.2).
                payload["must_be_motion"] = False
                payload["fallback_reason"] = "USER_CHANGED_PRESENTATION_TO_STILL"
        if "preferred_duration_ms" in changes and changes["preferred_duration_ms"] is not None:
            payload["preferred_duration_ms"] = int(changes["preferred_duration_ms"])
        if "must_be_motion" in changes and changes["must_be_motion"] is not None:
            if bool(changes["must_be_motion"]) and str(payload.get("render_type") or beat.get("render_type")) in STILL_RENDER_TYPES:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "必须运动的镜头不能同时是静图呈现方式，请先改为 AI 动态",
                    {"beat_id": beat_id},
                )
            payload["must_be_motion"] = bool(changes["must_be_motion"])
        if not payload:
            raise ExplainerContractError("SCHEMA_INVALID", "没有需要保存的修改", {"beat_id": beat_id})
        updated = self.repo.update("explainer_visual_beats", beat_id, payload, actor=actor)
        return {
            "beat": updated,
            "beat_id": beat_id,
            "beat_code": str(updated.get("code") or beat.get("code") or ""),
            "changed_fields": sorted(payload),
            "edit_authority": "HUMAN",
            "actor": str(actor),
            "revision": int(updated.get("revision") or 0),
            "queued_tasks_keep_their_snapshot": True,
            "planned_and_actual_are_separate_fields": True,
        }

    def unlock_selection(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_id: str,
        selection_id: str,
        edition_id: str | None = None,
        purpose: str = "VISUAL",
        actor: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Remove the human lock from one ``(beat, purpose, edition)`` scope.

        Unlocking only changes the *replacement policy*: the current choice stays
        selected and its media is untouched (spec §B9 item 7).  It is a deliberate,
        actor-recorded command, so an automatic flow can never simulate a human
        unlock, and a lock on the adopted first frame is independent from a lock on
        the final clip.
        """

        self.repo.require_explainer_project(project_id)
        purpose_value = _require_member(str(purpose), CANDIDATE_PURPOSES, field="purpose")
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "解锁必须记录真实操作者，机器流程不能模拟人工解锁", {"beat_id": beat_id}
            )
        beat = self.repo.get("explainer_visual_beats", beat_id)
        if str(beat["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST", "画面段不属于该解说作品", {"beat_id": beat_id, "video_id": video_id}
            )
        if expected_revision is not None and int(expected_revision) != int(beat.get("revision") or 0):
            raise ExplainerContractError(
                "STALE_REVISION",
                "该画面已更新，请刷新后重试。",
                {
                    "expected_revision": int(expected_revision),
                    "actual_revision": int(beat.get("revision") or 0),
                },
            )
        current = self.repo.find("explainer_beat_selections", selection_id)
        if current is None or str(current.get("beat_id")) != beat_id:
            raise ExplainerContractError(
                "INVALID_REQUEST", "选择记录不属于该画面段", {"selection_id": selection_id, "beat_id": beat_id}
            )
        if str(current.get("purpose") or "VISUAL") != purpose_value:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "选择记录的用途与本次解锁目标不一致",
                {"selection_id": selection_id, "purpose": purpose_value},
            )
        if edition_id is not None and str(current.get("edition_id") or "") != str(edition_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "选择记录不属于该输出版本",
                {"selection_id": selection_id, "edition_id": str(edition_id)},
            )
        was_locked = bool(current.get("locked_by_human"))
        if was_locked:
            self.repo.update(
                "explainer_beat_selections",
                selection_id,
                {
                    "locked_by_human": False,
                    "actor": actor_value,
                    "decided_at": utc_now_iso(),
                },
            )
        if purpose_value == "VISUAL" and bool(beat.get("locked_by_human")):
            self.repo.update(
                "explainer_visual_beats",
                beat_id,
                {"locked_by_human": False, "locked_by": actor_value, "locked_at": utc_now_iso()},
            )
        return {
            "selection_id": selection_id,
            "beat_id": beat_id,
            "purpose": purpose_value,
            "edition_id": str(edition_id) if edition_id else None,
            "was_locked": was_locked,
            "locked_by_human": False,
            "actor": actor_value,
            "current_media_kept": True,
            "media_deleted": False,
            "unlocked_by_human": True,
        }

    def request_repair(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_ids: Sequence[str],
        issue_ids: Sequence[str] = (),
        budget: Budget | Mapping[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Compute the minimal repair closure for the requested beats.

        Human-locked beats are skipped and reported, never silently changed.
        This method plans work and reports which artifacts it would invalidate;
        it performs no write (``mutated`` is always ``False``) so the caller owns
        the stale-marking and submission steps.
        """

        self.repo.require_explainer_project(project_id)
        resolved_budget, run_id = self._budget_for_video(video_id, budget)
        invalidate_kinds = list(staleness_plan("BEAT_PLAN")["invalidates"])
        preserve_kinds = list(staleness_plan("BEAT_PLAN")["preserves"])
        beats: list[dict[str, Any]] = []
        locked_beats_skipped: list[dict[str, Any]] = []
        fallback_planned: dict[str, Any] = {}
        for beat_id in dict.fromkeys(str(item) for item in (beat_ids or ())):
            beat = self.repo.get("explainer_visual_beats", beat_id)
            if str(beat["video_id"]) != video_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "画面段不属于该解说作品",
                    {"beat_id": beat_id, "video_id": video_id},
                )
            if self.repo.has_human_lock(beat_id):
                locked_beats_skipped.append(
                    {"beat_id": beat_id, "beat_code": str(beat["code"]), "reason": "HUMAN_LOCKED_BEAT_NOT_REPAIRED"}
                )
                continue
            is_key_identity = bool(beat.get("entity_refs_json"))
            initial_allowance = plan_initial_candidates(is_key_identity=is_key_identity, budget=resolved_budget)
            creative_rows = self.repo.list_where(
                "explainer_media_candidates", {"beat_id": beat_id, "candidate_kind": "CREATIVE"}
            )
            repairs_used = max(0, len(creative_rows) - initial_allowance)
            repair_budget_exhausted = repairs_used >= resolved_budget.max_creative_repairs_per_beat
            decision: FallbackDecision = resolve_visual_fallback(
                beat_must_be_motion=bool(beat["must_be_motion"]),
                beat_locked_by_human=False,
                planned_render_type=str(beat["render_type"]),
                allowed_fallbacks=list(beat.get("allowed_fallbacks_json") or ()),
                repair_budget_exhausted=repair_budget_exhausted,
            )
            fallback_planned[str(beat["code"])] = {
                "beat_id": beat_id,
                "allowed": decision.allowed,
                "fallback": decision.fallback,
                "reason": decision.reason,
                "preserves_must_be_motion": decision.preserves_must_be_motion,
                "preserves_human_lock": decision.preserves_human_lock,
                "repair_budget_exhausted": repair_budget_exhausted,
                "creative_repairs_used": repairs_used,
                "max_creative_repairs_per_beat": resolved_budget.max_creative_repairs_per_beat,
            }
            beats.append(
                {
                    "beat_id": beat_id,
                    "beat_code": str(beat["code"]),
                    "render_type": str(beat["render_type"]),
                    "must_be_motion": bool(beat["must_be_motion"]),
                    "action": (
                        "TARGETED_REPAIR"
                        if decision.reason == "REPAIR_BUDGET_REMAINS_PREFER_TARGETED_REPAIR"
                        else "APPLY_PRE_AUTHORIZED_FALLBACK"
                        if decision.allowed
                        else "PAUSE_AND_REPORT"
                    ),
                    "fallback": decision.fallback,
                    "fallback_reason": decision.reason,
                    "repair_budget_exhausted": repair_budget_exhausted,
                }
            )
        return {
            "project_id": project_id,
            "video_id": video_id,
            "reason": str(reason),
            "issue_ids": [str(item) for item in (issue_ids or ())],
            "beats": beats,
            "invalidates": invalidate_kinds,
            "preserves": preserve_kinds,
            "task_count": sum(1 for item in beats if item["action"] != "PAUSE_AND_REPORT"),
            "locked_beats_skipped": locked_beats_skipped,
            "fallback_planned": fallback_planned,
            "budget": resolved_budget.as_dict(),
            "budget_source_run_id": run_id,
            "minimal_closure_rule": "ONLY_REQUESTED_BEATS_MINUS_HUMAN_LOCKED",
            "mutated": False,
            "superseded_variants_deleted": False,
        }

    # ------------------------------------------------------------------ infographic
    def confirm_infographic_layers(
        self, *, beat_id: str, layers: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Validate that every readable string is a deterministic layer.

        Readable text may only live in a ``TEXT`` layer (deterministic
        typography) and a sourced vector layer may only *reference* those text
        layers.  A raster/generated layer that claims to contain text is
        rejected, because an image model must never guess text.
        """

        beat = self.repo.get("explainer_visual_beats", beat_id)
        items = [dict(item) for item in (layers or ())]
        if not items:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "信息图层清单不能为空", {"beat_id": beat_id}
            )
        normalized: list[dict[str, Any]] = []
        text_layer_ids: list[str] = []
        vector_layer_ids: list[str] = []
        raster_layer_ids: list[str] = []
        schematic_layers: list[str] = []
        referenced_text_layers: list[str] = []
        for index, layer in enumerate(items):
            kind = str(layer.get("kind") or layer.get("layer_kind") or "").strip().upper()
            if not kind:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "图层必须声明 kind", {"beat_id": beat_id, "layer_index": index}
                )
            layer_id = str(layer.get("layer_id") or layer.get("id") or f"L{index + 1}")
            declared_text = _declared_text(layer)
            if kind in TEXT_LAYER_KINDS:
                text = str(layer.get("text") or "").strip()
                if not text:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "确定性文字层必须提供 text",
                        {"beat_id": beat_id, "layer_id": layer_id},
                    )
                font = str(layer.get("font") or layer.get("font_ref") or "").strip()
                if not font:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "确定性文字层必须声明 font，字体由排版层决定而不是图像模型",
                        {"beat_id": beat_id, "layer_id": layer_id},
                    )
                if str(layer.get("raster") or "").upper() in {"TRUE", "1"} or layer.get("raster") is True:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "可读文字必须由确定性排版层生成，不能来自栅格或生成图层",
                        {"beat_id": beat_id, "layer_id": layer_id},
                    )
                if layer.get("factual") is True and not str(layer.get("source_ref") or "").strip():
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "声明为事实的文字层必须提供 source_ref",
                        {"beat_id": beat_id, "layer_id": layer_id},
                    )
                text_layer_ids.append(layer_id)
                normalized.append(
                    {
                        "layer_id": layer_id,
                        "kind": "TEXT",
                        "text": text,
                        "font": font,
                        "source_ref": str(layer.get("source_ref") or "") or None,
                        "text_origin": "DETERMINISTIC_TYPOGRAPHY",
                    }
                )
                continue
            if kind in VECTOR_LAYER_KINDS:
                if declared_text:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "可读文字必须由确定性排版层生成，矢量图层只能引用文字层而不能自带文字",
                        {"beat_id": beat_id, "layer_id": layer_id, "layer_kind": kind},
                    )
                text_refs = [
                    str(item)
                    for item in (layer.get("text_layer_refs") or layer.get("label_layer_ids") or ())
                ]
                is_map = kind == "BASEMAP" or layer.get("is_map") is True
                source_ref = str(layer.get("source_ref") or "").strip()
                schematic = layer.get("schematic") is True
                if is_map and not source_ref and not schematic:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "真实地图必须使用有来源的底图，或显式标记为示意图",
                        {"beat_id": beat_id, "layer_id": layer_id},
                    )
                if schematic:
                    schematic_layers.append(layer_id)
                vector_layer_ids.append(layer_id)
                referenced_text_layers.extend(text_refs)
                normalized.append(
                    {
                        "layer_id": layer_id,
                        "kind": kind,
                        "source_ref": source_ref or None,
                        "schematic": schematic,
                        "text_layer_refs": text_refs,
                        "text_origin": "NONE",
                    }
                )
                continue
            if declared_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "可读文字必须由确定性排版层生成，不能在栅格或生成图层中声明文字",
                    {
                        "beat_id": beat_id,
                        "layer_id": layer_id,
                        "layer_kind": kind,
                        "declared_text_keys": declared_text,
                    },
                )
            if kind in RASTER_LAYER_KINDS:
                raster_layer_ids.append(layer_id)
            normalized.append(
                {
                    "layer_id": layer_id,
                    "kind": kind,
                    "source_ref": str(layer.get("source_ref") or "") or None,
                    "text_origin": "NONE",
                }
            )
        unresolved = sorted(
            {item for item in referenced_text_layers if item not in set(text_layer_ids)}
        )
        if unresolved:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "矢量图层引用了不存在的文字层",
                {"beat_id": beat_id, "unresolved_text_layer_refs": unresolved},
            )
        manifest = {
            "schema_version": "localdrama.explainer.infographic-layers.v1",
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "layers": normalized,
        }
        return {
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "layers": normalized,
            "layer_manifest_hash": content_hash(manifest),
            "text_layer_ids": text_layer_ids,
            "vector_layer_ids": vector_layer_ids,
            "raster_layer_ids": raster_layer_ids,
            "referenced_text_layer_ids": sorted(set(referenced_text_layers)),
            "schematic_layer_ids": schematic_layers,
            "text_authority": "DETERMINISTIC_TYPOGRAPHY_LAYER",
            "image_model_generated_text": False,
            "raster_layers_containing_text": [],
            "confirmed": True,
        }


def _declared_text(layer: Mapping[str, Any]) -> list[str]:
    """Keys on ``layer`` that claim the layer paints readable characters."""

    declared: list[str] = []
    for key in READABLE_TEXT_KEYS:
        if key not in layer:
            continue
        value = layer[key]
        if isinstance(value, str):
            if value.strip():
                declared.append(key)
        elif isinstance(value, (list, tuple, dict)) and len(value) > 0:
            declared.append(key)
    if layer.get("contains_text") is True:
        declared.append("contains_text")
    return declared
