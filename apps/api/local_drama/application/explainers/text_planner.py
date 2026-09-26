"""First-party local text planner for the explainer production stages.

This module is what turns the explainer graph from *contracts* into a runnable
pipeline without a human writing every payload by hand.  It drives four stages
through the **existing offline LLM capability**:

* ``RESEARCH_ACQUIRE``  - choose research queries / reference keywords from the
  frozen topic and source allowlist.
* ``FACT_EXTRACT``      - extract the claim ledger, events and entities from the
  stored source spans, reusing :data:`FACT_EXTRACTION_SCHEMA` and
  :func:`validate_model_payload` so an undeclared model field is rejected rather
  than silently persisted.
* ``NARRATION_WRITE``   - write the chapter outline and narration segments with a
  ``claim_code`` reference on every segment.
* ``EXPLAINER_STORYBOARD`` - allocate the visual beats (render type, linked
  segments, referenced entities/claims, visual intent).

Design boundaries this module keeps:

* **The model never invents an identifier.**  Source spans, claim codes and
  entity codes are supplied as a bounded catalogue and every reference the model
  returns is resolved against real rows; an unknown reference is a hard
  ``SCHEMA_INVALID``.
* **The model never decides capability.**  The usable render types are filtered
  from the frozen capability snapshot before the prompt is built, so a
  ``I2V``/``INFOGRAPHIC`` beat is only offered when that capability is actually
  available; an unavailable render type would be a preflight blocker, not a
  silent downgrade.
* **The model never decides factuality.**  ``verified_as_history`` stays
  ``False`` (written by the research service), and a ``FACT`` statement must cite
  at least one claim code or the write is refused.
* **No writes happen inside the LLM call.**  Each stage reads in a short
  connection, calls the model, then writes through the repository in the caller's
  transaction; a GPU/LLM wait never holds a SQLite transaction.
* **Inference stays local.**  The client comes from
  :class:`~local_drama.application.local_llm.LocalLLMService`, which enforces the
  loopback / controlled-private endpoint rules and the configured provider; this
  module never constructs an HTTP client itself.

The planner is expressed as a *port* (:class:`ExplainerStagePlanner`) so tests
inject a deterministic fake and no unit test needs a running model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any, Protocol

from local_drama.application.explainers.contracts_v2 import (
    CONTENT_EXTRACT_SCHEMA_VERSION,
    CONTENT_EXTRACT_SYSTEM,
    CONTENT_EXTRACT_USER,
    ENTITY_TYPE_TO_ASSET_KIND,
    FICTION_SEED_SCHEMA_VERSION,
    FICTION_SEED_SYSTEM,
    FORMAT_REPAIR_USER,
    PRESERVED_ANNOTATION_SYSTEM,
    PRESERVED_ANNOTATION_USER,
    PROMPT_VERSION,
    REFERENCE_DESIGN_SCHEMA_VERSION,
    REFERENCE_DESIGN_SYSTEM,
    SCRIPT_DRAFT_SCHEMA_VERSION,
    SCRIPT_DRAFT_SYSTEM,
    SCRIPT_DRAFT_USER,
    ScriptPolicy,
    apply_disambiguation_decisions,
    assert_allowed_ids,
    assert_chapter_indexes_in_outline_range,
    assert_full_coverage,
    assert_indexes_in_range,
    assert_no_model_generated_persistent_ids,
    assert_one_annotation_per_segment,
    assert_reference_design_coverage,
    assert_unverified_phrases_are_substrings,
    asset_kind_for_entity_type,
    build_preserved_segments,
    compile_reference_design,
    content_extract_auxiliary_metadata,
    content_extract_to_fact_extraction,
    contract_schema_for_model,
    dedupe_evidence,
    entity_merge_candidates,
    fiction_seed_input_hash,
    fiction_seed_setting_document,
    reference_design_input_hash,
    render_contract_prompt,
    render_prompt,
    resolve_script_policy,
    validate_contract,
    validate_fiction_seed,
    validate_preserved_concatenation,
    validate_with_single_repair,
)
from local_drama.application.explainers.evidence_chunks import (
    ANALYSIS_MANIFEST_FILENAME,
    DEFAULT_CHUNK_CHARACTER_BUDGET,
    DEFAULT_CONTEXT_CHARACTER_BUDGET,
    build_evidence_chunks,
    coverage_status,
    empty_manifest,
    mark_manifest_in_progress,
    pending_chunks,
    read_analysis_manifest,
    record_chunk_result,
    write_analysis_manifest,
)
from local_drama.application.explainers.narration import (
    ExplainerNarrationService,
    apply_pronunciation_map,
)
from local_drama.application.explainers.research import (
    FACT_EXTRACTION_SCHEMA,
    ExplainerResearchService,
    validate_model_payload,
)
from local_drama.application.explainers.storyboard import ExplainerStoryboardService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import (
    ContentKind,
    ExplainerContractError,
    ExplainerErrorCode,
    RenderType,
    StatementType,
    VisualFactuality,
    content_hash,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Bounds that keep one prompt inside a local model's context.
MAX_EVIDENCE_CHARACTERS = 24_000
#: Retained for callers that still pass an explicit evidence bound, but the
#: fact-extraction stage no longer truncates: it processes one whole-span chunk
#: per call (:mod:`evidence_chunks`) and proves coverage instead of stopping at a
#: character prefix.
MAX_EVIDENCE_CHUNK_CHARACTERS = DEFAULT_CHUNK_CHARACTER_BUDGET
MAX_EVIDENCE_CONTEXT_CHARACTERS = DEFAULT_CONTEXT_CHARACTER_BUDGET
#: Upper bound for a *vocabulary* catalogue (entities, claims).  Segments are no
#: longer truncated with a value like this one: the storyboard consumes every
#: segment through :func:`_segment_batches` (design §5.3).
MAX_CATALOGUE_ITEMS = 160
MAX_SEGMENTS = 320
MAX_BEATS = 320
#: Segments carried by one storyboard planning call.  The design fixes the *unit*
#: of batching as a partition of every segment — the starting configuration is
#: 40–80 per batch and the real bound is the model's context — so this number may
#: never be used as a silent truncation of the remaining script.
STORYBOARD_BATCH_SEGMENTS = 60
#: Characters per second used only for the *prompt* budget hint.  The real
#: duration is decided by measured TTS audio (design §7), never by this number.
#:
#: The value is calibrated against this machine's local narrator: a 1187-character
#: Chinese script measured 241 s of VoxCPM2 audio, i.e. 4.9 characters per second.
#: It was 4.2 before, which asked the model for far fewer characters than the
#: declared target needed and produced a film about 20% shorter than requested.
CHINESE_CHARS_PER_SECOND_HINT = 4.9
#: How much of the prompt's character budget the returned script must actually
#: reach before the planner accepts it (the rest is a normal writing tolerance).
SCRIPT_BUDGET_MINIMUM_RATIO = 0.98
#: Bounded shortfall repairs: each one is a fresh model call with the exact gap.
SCRIPT_BUDGET_MAX_REPAIRS = 3

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}

RESEARCH_KEYWORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["queries", "reference_keywords"],
    "properties": {
        "queries": {"type": "array", "items": _STRING},
        "reference_keywords": {"type": "array", "items": _STRING},
        "notes": _STRING,
    },
}

SEGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "outline": {"type": "array", "items": _STRING},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["canonical_segment_id", "display_text", "statement_type"],
                "properties": {
                    "canonical_segment_id": _STRING,
                    "display_text": _STRING,
                    "spoken_text": _STRING,
                    "statement_type": {"type": "string", "enum": [item.value for item in StatementType]},
                    "claim_code": _STRING,
                    "chapter_code": _STRING,
                    "pronunciation_map": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["display", "spoken"],
                            "properties": {"display": _STRING, "spoken": _STRING},
                        },
                    },
                    "pause_after_ms": _INTEGER,
                },
            },
        },
    },
}

BEAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["beats"],
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "render_type", "segment_ids", "visual_intent"],
                "properties": {
                    "code": _STRING,
                    "render_type": {"type": "string", "enum": [item.value for item in RenderType]},
                    "segment_ids": {"type": "array", "items": _STRING},
                    "visual_intent": _STRING,
                    "visual_factuality": {
                        "type": "string",
                        "enum": [item.value for item in VisualFactuality],
                    },
                    "entity_codes": {"type": "array", "items": _STRING},
                    "claim_codes": {"type": "array", "items": _STRING},
                    "must_be_motion": _BOOLEAN,
                    "prompt_intent": _STRING,
                },
            },
        },
    },
}


class ExplainerStagePlanner(Protocol):
    """What the stage handlers need from a planner.

    A fake implementation in tests returns fixed payloads; the production
    implementation is :class:`LocalTextPlanner`.
    """

    def plan_research(  # pragma: no cover - protocol boundary
        self, *, repo: ExplainerRepository, project_id: str, video_id: str, packet_id: str
    ) -> dict[str, Any]: ...

    def plan_fact_extraction(  # pragma: no cover - protocol boundary
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        packet_id: str,
        artifact_dir: Any | None = None,
    ) -> dict[str, Any]: ...

    def plan_script(  # pragma: no cover - protocol boundary
        self, *, repo: ExplainerRepository, project_id: str, video_id: str
    ) -> dict[str, Any]: ...

    def plan_preserved_script(  # pragma: no cover - protocol boundary
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        script_source_text: str | None = None,
        pronunciation_map: Sequence[Mapping[str, str]] = (),
    ) -> dict[str, Any]: ...

    def plan_storyboard(  # pragma: no cover - protocol boundary
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        usable_render_types: Sequence[str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PlannerPrompts:
    """System instructions, versioned with the code that ships them."""

    schema_version: str = "localdrama.explainer.text-planner.v1"
    research_system: str = (
        "你是本机离线解说工厂的资料研究助手。只输出 JSON。"
        "你只能提出检索关键词与参考线索，不得声称已经取得任何来源，不得编造 URL、日期或引文。"
    )
    fact_system: str = (
        "你是本机离线解说工厂的事实提取助手。只输出 JSON。"
        "只能使用输入中给出的来源片段 ID 与正文；任何引用都必须来自给定的片段清单，不得新增编号。"
        "不得补写输入中不存在的数字、对话、内心活动或服饰细节。"
        "来源之间意见不同时必须分别列出支持与反驳证据，不能按数量投票。"
    )
    script_system: str = (
        "你是本机离线解说工厂的解说稿作者。只输出 JSON。"
        "只能使用输入中列出的事实编号；不得改变否定词、结果、日期、人名，也不得新增因果关系。"
        "每条叙述段落必须给出一个 claim_code；纯过渡或提问段落可以留空。"
        "人物首次出现用姓名加一句角色说明，之后使用固定简称。"
    )
    storyboard_system: str = (
        "你是本机离线解说工厂的分镜助手。只输出 JSON。"
        "只能使用输入中列出的段落编号与实体编号；render_type 必须从给定的可用类型中选择。"
        "每段画面只承担一个主要动作、少量角色与明确视觉焦点。"
        "地图、数字、日期、关系线和可读文字必须由确定性排版层绘制，不要要求图像模型写字。"
        "解说片的活动画面一律由真实的 AI 图生视频（I2V）生成：不要规划任何“静图加推拉/位移”的画面方式，"
        "也不要把它当作 I2V 的替代或降级结果。"
        "推镜与位移不是人物动作，不能用推镜或位移冒充“必须发生的人物运动”。"
        "must_be_motion 只在核心动作或关键转折上设为 true；"
        "凡是 must_be_motion 的画面段，render_type 必须选择可产生真实运动的类型（I2V）。"
        "相邻画面段保持人物、衣着、场景与道具一致；不同年龄或服装是状态变化，不要另造一个同名对象。"
        "画面提示词只描述首帧状态、主体位置、构图与光线，以及从首帧开始的单一动作与一种镜头运动，"
        "不要把视频结束后的状态写成首帧已经发生，也不要让画面出现可读文字。"
    )


def _catalogue(items: Sequence[Mapping[str, Any]], fields: Sequence[str], *, limit: int = MAX_CATALOGUE_ITEMS) -> list[dict[str, Any]]:
    """Bounded projection of real rows handed to the model as its only vocabulary.

    This is only for *vocabulary* lists (entities, claims), where a shorter list
    narrows what the model may reference.  It must not be used for the narration
    segments a plan is required to cover — see :func:`_segment_batches`.
    """

    projected: list[dict[str, Any]] = []
    for item in items[:limit]:
        projected.append({field: item.get(field) for field in fields})
    return projected


def make_research_service(repo: Any) -> Any:
    """Port-style factory for the research service (architecture-debt guard).

    Constructing the concrete service inside a stage handler is reported as new
    cross-service debt; the construction belongs in a ``make_*`` scope.
    """

    return ExplainerResearchService(repo)


def _segment_batches(
    segments: Sequence[Mapping[str, Any]], *, batch_size: int = STORYBOARD_BATCH_SEGMENTS
) -> list[list[Mapping[str, Any]]]:
    """Partition *every* segment into in-order batches, keeping chapters whole.

    The union of the returned batches is exactly ``segments`` in narration order,
    with no segment dropped and none appearing twice.  A chapter that fits in one
    batch is never split across two, so a batch always has coherent context; only
    a chapter larger than ``batch_size`` is sliced.
    """

    if batch_size < 1:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "分镜批次大小必须是正数", {"batch_size": batch_size}
        )
    runs: list[list[Mapping[str, Any]]] = []
    for segment in segments:
        chapter = str(segment.get("chapter_code") or "")
        previous = str(runs[-1][0].get("chapter_code") or "") if runs else None
        if runs and previous == chapter:
            runs[-1].append(segment)
        else:
            runs.append([segment])
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for run in runs:
        if len(run) >= batch_size:
            if current:
                batches.append(current)
                current = []
            for start in range(0, len(run), batch_size):
                batches.append(list(run[start : start + batch_size]))
            continue
        if current and len(current) + len(run) > batch_size:
            batches.append(current)
            current = []
        current.extend(run)
    if current:
        batches.append(current)
    return batches


_ENTITY_TYPE_TO_ASSET_KIND = ENTITY_TYPE_TO_ASSET_KIND


def _asset_kind_for_entity(entity: Mapping[str, Any]) -> str:
    """The asset kind implied by an entity type (used when the caller omits it)."""

    return asset_kind_for_entity_type(str(entity.get("entity_type") or ""))


def _known_attributes_for(entity: Mapping[str, Any]) -> list[str]:
    """Known appearance/space fragments recorded on the entity, never invented.

    Only attributes the extraction actually stored are returned: an unknown
    feature stays unknown, which is what lets the compiler refuse a "faithful
    portrait" of a real person nobody documented.
    """

    disambiguation = entity.get("disambiguation_json")
    metadata = dict(disambiguation) if isinstance(disambiguation, Mapping) else {}
    fragments: list[str] = []
    appearance = metadata.get("known_appearance")
    if isinstance(appearance, Sequence) and not isinstance(appearance, (str, bytes)):
        for item in appearance:
            if isinstance(item, Mapping):
                attribute = str(item.get("attribute") or "").strip()
                value = str(item.get("value") or "").strip()
                if attribute or value:
                    fragments.append(f"{attribute}：{value}" if attribute else value)
            else:
                fragments.append(str(item))
    for key in ("known_attributes", "known_appearance_notes"):
        raw = metadata.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            fragments.extend(str(item) for item in raw if str(item).strip())
    return [item for item in fragments if item.strip()]


def _setting_fragments(value: Any) -> list[str]:
    """User-supplied visual settings for one entity, flattened deterministically."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        fragments: list[str] = []
        for key, item in value.items():
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                fragments.extend(str(entry) for entry in item if str(entry).strip())
            elif str(item).strip():
                fragments.append(f"{key}：{item}")
        return fragments
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _chapter_codes_for_preserved(segments: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Assign chapter codes to preserved segments without reordering anything.

    A chapter boundary may only sit *before* an existing segment: the annotation
    contract restricts breaks to supplied segment IDs, so a chapter comment can
    never reorder or split the manuscript.
    """

    codes: dict[str, str] = {}
    for index, segment in enumerate(segments):
        segment_id = str(segment["canonical_segment_id"])
        if index == 0 or bool(segment.get("chapter_break_before")):
            codes[segment_id] = f"ch_{len(codes) + 1:03d}"
        else:
            codes[segment_id] = f"ch_{max(1, len(codes)):03d}"
    return codes


def _script_characters(raw_segments: Sequence[Any]) -> int:
    """Characters the model's script actually contains (spoken text preferred)."""

    total = 0
    for item in raw_segments:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("spoken_text") or item.get("display_text") or "").strip()
        total += len(text)
    return total


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _apply_pronunciation_map(display_text: str, pronunciation_map: Sequence[Mapping[str, str]]) -> str:
    """The spoken form implied by a map, using the narration service's own rule.

    Delegating keeps the planner's proof identical to the check the narration
    service will run when the segment is persisted (longest display string first,
    all occurrences replaced).
    """

    return apply_pronunciation_map(display_text, pronunciation_map)


#: Characters a written form carries but a spoken form cannot say.  ``21:17``
#: reads as 二十一点十七分 and ``1962 年`` reads as 一九六二年, so a separator or a
#: space must not block the equivalence proof.
_SPOKEN_SEPARATORS = " \t\u3000:：./\\-,，、·"


def _spoken_signature(value: str) -> str:
    """The spoken content of a string with non-pronounceable separators removed."""

    return "".join(character for character in value if character not in _SPOKEN_SEPARATORS).strip()


def _unmapped_characters(text: str, pairs: Sequence[Mapping[str, str]], *, use_spoken: bool) -> str:
    """The characters of ``text`` that the map does not rewrite, in order.

    Mapped spans are consumed as whole units, so a mapped form containing single
    characters (like ``一九``) can never be mistaken for the surrounding prose, and
    separators a reading cannot say are dropped.  Comparing this projection of the
    canvas text with the same projection of the reading is what proves the reading
    only changed pronunciation and not wording.
    """

    forms = sorted(
        (str(pair.get("spoken") if use_spoken else pair.get("display") or "") for pair in pairs),
        key=len,
        reverse=True,
    )
    forms = [form for form in forms if form]
    output: list[str] = []
    index = 0
    while index < len(text):
        matched = next((form for form in forms if text.startswith(form, index)), None)
        if matched is not None:
            index += len(matched)
            continue
        character = text[index]
        index += 1
        if character in _SPOKEN_SEPARATORS:
            continue
        output.append(character)
    return "".join(output)


def _inverse_display_text(
    spoken_text: str, pairs: Sequence[Mapping[str, str]], display_text: str
) -> str | None:
    """Rebuild a separator-free canvas text the map provably turns into ``spoken``.

    Works from the reading backwards: every spoken form is replaced by the display
    form the map declares for it, then any separator the reading could not have
    said is dropped.  The result is accepted only when the prose the map does not
    rewrite is identical to the original canvas text and applying the map to the
    result reproduces the reading exactly, so a "reading" that silently changed
    wording is refused rather than written into the script.
    """

    rebuilt = spoken_text
    for pair in sorted(pairs, key=lambda item: len(str(item.get("spoken") or "")), reverse=True):
        spoken = str(pair.get("spoken") or "")
        display = str(pair.get("display") or "")
        if spoken and spoken in rebuilt:
            rebuilt = rebuilt.replace(spoken, display)
    rebuilt = "".join(character for character in rebuilt if character not in _SPOKEN_SEPARATORS)
    if apply_pronunciation_map(rebuilt, pairs) != spoken_text:
        return None
    if _unmapped_characters(rebuilt, pairs, use_spoken=False) != _unmapped_characters(
        display_text, pairs, use_spoken=False
    ):
        return None
    if _unmapped_characters(spoken_text, pairs, use_spoken=True) != _unmapped_characters(
        display_text, pairs, use_spoken=False
    ):
        return None
    return rebuilt


def _normalise_spoken_text(
    display_text: str, spoken_text: str | None, pronunciation_map: Sequence[Mapping[str, str]]
) -> tuple[str, list[dict[str, str]], str, str]:
    """Guarantee that the spoken form provably equals the display form.

    A narration segment persists both a canonical ``display_text`` and the
    ``spoken_text`` actually synthesised, and the service refuses a pair whose
    pronunciation map does not reproduce the spoken text exactly.  A local model
    can easily emit a plausible but non-reproducible reading, so this function is
    the single place that decides.  Preference order:

    1. no reading offered, or identical to the canvas text -> use the canvas text;
    2. the map rewrites the canvas text into the reading exactly;
    3. the map rewrites it into the reading once non-pronounceable separators and
       spaces are ignored (``21:17`` -> 二十一点十七分, ``1962 年`` -> 一九六二年).
       The canvas text is then rewritten to the separator-free form so the
       narration service's own exact check also passes;
    4. otherwise the model's reading is discarded and the canvas text is
       synthesised, with the reason reported, so an unprovable reading can never
       reach the TTS handler.

    Returns ``(spoken_text, applied_map, display_text, disposition)``.
    """

    raw_spoken = (spoken_text or "").strip()
    if not raw_spoken or raw_spoken == display_text:
        return display_text, [], display_text, "IDENTICAL"
    pairs = [
        {"display": str(pair.get("display") or ""), "spoken": str(pair.get("spoken") or "")}
        for pair in pronunciation_map
        if str(pair.get("display") or "") and str(pair.get("spoken") or "")
    ]
    if pairs:
        implied = apply_pronunciation_map(display_text, pairs)
        if implied == raw_spoken:
            return raw_spoken, pairs, display_text, "PROVEN_BY_MAP"
        if _spoken_signature(implied) == _spoken_signature(raw_spoken):
            rebuilt = _inverse_display_text(raw_spoken, pairs, display_text)
            if rebuilt is not None and apply_pronunciation_map(rebuilt, pairs) == raw_spoken:
                return raw_spoken, pairs, rebuilt, "PROVEN_BY_MAP_IGNORING_SEPARATORS"
    # The reading exists but cannot be proven equivalent: keep the canvas text as
    # the reading too rather than synthesising something the map cannot account
    # for, and record why so the operator can add a proper dictionary entry.
    return display_text, [], display_text, "MODEL_READING_UNPROVABLE_USED_DISPLAY_TEXT"


_TIME_ONLY = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")
#: A year, or a year-month, written the way a Chinese source writes it.
_YEAR_IN_TEXT = re.compile(r"(1[0-9]{3}|20[0-9]{2})\s*年(?:\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?)?")
_ISO_PREFIX = re.compile(r"^(\d{4}(?:-\d{2}(?:-\d{2})?)?)")


def _date_prefix_from_sources(
    sources: Sequence[Mapping[str, Any]], spans: Sequence[Mapping[str, Any]] = ()
) -> str | None:
    """Find the date the sources themselves declare for the event.

    Preference order is a stored date field on a source, then a date written in
    the source text.  Only a date the material actually contains is used, and only
    to complete a clock time the model reported separately: when nothing carries a
    usable date the bare clock time is left untouched and the event write raises
    the caller's normal validation error rather than inventing a day.
    """

    for source in sources:
        for key in ("event_date", "published_at", "updated_at_source"):
            match = _ISO_PREFIX.match(str(source.get(key) or "").strip())
            if match:
                return match.group(1)
    for span in spans:
        match = _YEAR_IN_TEXT.search(str(span.get("quote_text") or ""))
        if not match:
            continue
        year, month, day = match.group(1), match.group(2), match.group(3)
        if month and day:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        if month:
            return f"{year}-{int(month):02d}"
        return year
    return None


def _repair_story_time_dates(
    validated: dict[str, Any],
    sources: Sequence[Mapping[str, Any]],
    spans: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Combine a model-reported clock time with the source's own date.

    The model is asked for a full instant but local models frequently answer with
    only ``21:17:00``.  Persisting that would fail the event write, so the planner
    completes it from a date the source already declares and reports every repair
    instead of hiding it.
    """

    prefix = _date_prefix_from_sources(sources, spans)
    repairs: list[dict[str, Any]] = []
    if prefix is None:
        return repairs
    # ``YYYY-MM-DDTHH:MM:SS`` is the only accepted instant form, so a source that
    # only declares a year or a year-month is padded to a complete date.  The
    # declared precision is reported with the repair so nothing claims a day the
    # material never gave.
    complete_date, declared_precision = _complete_date(prefix)
    for event in validated.get("events") or []:
        for field in ("story_time_start", "story_time_end"):
            value = str(event.get(field) or "").strip()
            if not value or not _TIME_ONLY.match(value):
                continue
            repair_input = value
            event[field] = f"{complete_date}T{value}"
            repairs.append(
                {
                    "event_code": event.get("code"),
                    "field": field,
                    "input": repair_input,
                    "output": event[field],
                    "padded_from": prefix,
                    "source_declared_precision": declared_precision,
                }
            )
    return repairs


def _complete_date(prefix: str) -> tuple[str, str]:
    """Turn a partial date into a complete ``YYYY-MM-DD`` plus its precision."""

    parts = prefix.split("-")
    if len(parts) == 3:
        return prefix, "DAY"
    if len(parts) == 2:
        return f"{prefix}-01", "MONTH"
    return f"{prefix}-01-01", "YEAR"


class LocalTextPlanner:
    """Production :class:`ExplainerStagePlanner` over the offline local LLM."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any],
        prompts: PlannerPrompts | None = None,
        max_evidence_characters: int = MAX_EVIDENCE_CHARACTERS,
    ) -> None:
        self._client_factory = client_factory
        self.prompts = prompts or PlannerPrompts()
        self.max_evidence_characters = max_evidence_characters
        #: Why the design's ``script-draft.v2`` attempt was abandoned, when it was.
        self._script_fallback_reason: dict[str, Any] | None = None

    # ------------------------------------------------------------------ helpers
    def _client(self) -> Any:
        try:
            return self._client_factory()
        except DomainRuleError as error:
            raise ExplainerContractError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "本机没有可用的离线文本模型，无法执行解说文本规划阶段",
                {"cause": error.code, "cause_message": error.message},
            ) from error

    def _model_available(self) -> bool:
        """Whether the local text model can be resolved right now.

        Optional stages (preserved-mode annotation, design-document polishing) must
        degrade to their deterministic path when no model is installed instead of
        blocking the user (spec §F4).
        """

        try:
            self._client()
        except ExplainerContractError:
            return False
        return True

    def _chat(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        num_ctx: int,
        client: Any | None = None,
    ) -> dict[str, Any]:
        resolved = client if client is not None else self._client()
        result = self._chat_with_cold_start_retry(
            resolved,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            num_ctx=num_ctx,
        )
        if not isinstance(result, Mapping):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "文本模型返回的顶层不是对象", {"received_type": type(result).__name__}
            )
        return dict(result)

    #: Errors a locally hosted text runtime reports while it is being started,
    #: evicted or swapped back in.  They are explicitly retryable at the gateway.
    _COLD_START_ERROR_CODES = frozenset(
        {"LOCAL_LLM_LOOPBACK_UNAVAILABLE", "LLM_PROVIDER_UNAVAILABLE", "LLM_GATEWAY_BUSY"}
    )

    @classmethod
    def _chat_with_cold_start_retry(
        cls,
        client: Any,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        num_ctx: int,
    ) -> Any:
        """One text call, with a bounded retry for a cold single-GPU runtime.

        The managed llama.cpp runtime shares one GPU with ComfyUI, so the first
        request after an idle eviction (or after a Comfy job) can legitimately come
        back "unavailable, retryable" while the coordinator swaps the resident
        runtime.  Failing the whole stage on that transient would make an otherwise
        healthy local install look broken, so a small, bounded number of attempts
        is made; a persistent failure is still raised with its real code.
        """

        import time as _time

        attempts = 4
        delay_seconds = 10.0
        last_error: DomainRuleError | None = None
        for attempt in range(attempts):
            try:
                return client.chat_json(
                    system,
                    user,
                    json_schema=schema,
                    inference_options={
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "max_tokens": max_tokens,
                        "num_ctx": num_ctx,
                    },
                )
            except DomainRuleError as error:
                if error.code not in cls._COLD_START_ERROR_CODES or attempt == attempts - 1:
                    raise
                last_error = error
                _time.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2, 60.0)
        raise last_error if last_error is not None else DomainRuleError("LOCAL_LLM_LOOPBACK_UNAVAILABLE", "LLM 请求失败")

    @staticmethod
    def _video(repo: ExplainerRepository, project_id: str, video_id: str) -> Mapping[str, Any]:
        repo.require_explainer_project(project_id)
        video = repo.require_video_for_project(project_id)
        if str(video["id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST", "视频与项目不匹配", {"project_id": project_id, "video_id": video_id}
            )
        return video

    # ------------------------------------------------------------------ sources
    def _packet_and_spans(
        self, repo: ExplainerRepository, *, project_id: str, video_id: str, packet_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        packet = repo.get("explainer_research_packets", packet_id)
        if str(packet["video_id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST", "资料包不属于该解说作品", {"packet_id": packet_id, "video_id": video_id}
            )
        sources = repo.list_where(
            "explainer_sources", {"packet_id": packet_id}, order_by="created_at", descending=False
        )
        spans = repo.list_where(
            "explainer_source_spans", {"packet_id": packet_id}, order_by="ordinal", descending=False
        )
        if not sources or not spans:
            raise ExplainerContractError(
                ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                "资料包内没有可用的来源片段，无法提取事实",
                {"packet_id": packet_id, "source_count": len(sources), "span_count": len(spans)},
            )
        return packet, sources, spans

    def _evidence_block(self, sources: Sequence[Mapping[str, Any]], spans: Sequence[Mapping[str, Any]]) -> str:
        """Render every span of the packet as citable evidence text.

        The previous implementation stopped once a 24 000-character budget ran
        out and cut the span that crossed the boundary, so the tail of a long
        document never reached any model call at all.  Coverage is now decided by
        the chunk plan in :meth:`plan_fact_extraction`, and a caller that only
        wants a bounded *hint* (research keyword inference) applies its own
        explicit ``_excerpt``.  Nothing here silently truncates.
        """

        titles = {str(item["id"]): str(item.get("title") or "未命名来源") for item in sources}
        lines: list[str] = []
        for span in spans:
            quote = str(span.get("quote_text") or "").strip()
            if not quote:
                continue
            lines.append(f"[{span['id']}] 来源「{titles.get(str(span['source_id']), '未知来源')}」：{quote}")
        return "\n".join(lines)

    def _chunk_evidence_json(
        self, chunk: Mapping[str, Any], titles: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        """The per-chunk span list handed to the model, marking context fragments."""

        return [
            {
                "source_span_id": entry["source_span_id"],
                "source_id": entry["source_id"],
                "quote_text": entry["quote_text"],
                "source_title": titles.get(str(entry["source_id"]), "未知来源"),
                "context_only": bool(entry["context_only"]),
            }
            for entry in chunk.get("spans") or []
        ]


    # ------------------------------------------------------------------ research
    def plan_research(
        self, *, repo: ExplainerRepository, project_id: str, video_id: str, packet_id: str
    ) -> dict[str, Any]:
        video = self._video(repo, project_id, video_id)
        packet, sources, spans = self._packet_and_spans(
            repo, project_id=project_id, video_id=video_id, packet_id=packet_id
        )
        allowed_domains = list(video.get("research_allowed_domains_json") or [])
        user = (
            f"作品标题：{video['title']}\n主题：{video.get('topic') or '（未填写）'}\n"
            f"内容属性：{'原创虚构' if str(video['content_kind']) == ContentKind.ORIGINAL_FICTION.value else '事实解说'}\n"
            f"资料模式：{packet.get('mode')}\n"
            f"允许的域名：{', '.join(str(item) for item in allowed_domains) or '（纯离线，无外发许可）'}\n\n"
            "请给出：\n"
            "1) queries：最多 8 条可直接交给检索接口的查询词；如果资料模式是纯离线或没有允许域名，返回空数组。\n"
            "2) reference_keywords：最多 20 个后续定位原文用的关键词（人名、地名、时间、物件）。\n"
            "3) notes：一句话说明还需要补什么资料。\n\n"
            "已经导入的来源标题：\n"
            + "\n".join(f"- {item.get('title')}" for item in sources[:40])
            + "\n\n已导入正文摘录（用于推断关键词，不得当作可引用编号）：\n"
            + _excerpt(self._evidence_block(sources, spans), 8_000)
        )
        result = self._chat(
            system=self.prompts.research_system,
            user=user,
            schema=RESEARCH_KEYWORD_SCHEMA,
            max_tokens=1_200,
            num_ctx=16_384,
        )
        queries = [str(item).strip() for item in (result.get("queries") or []) if str(item).strip()]
        keywords = [str(item).strip() for item in (result.get("reference_keywords") or []) if str(item).strip()]
        if str(packet.get("mode")) == "OFFLINE_IMPORT" and queries:
            # An offline packet has no outbound allowance; the model's suggestions
            # are reported, never sent.
            queries = []
        return {
            "status": "PASS",
            "mode": str(packet.get("mode")),
            "queries": queries,
            "reference_keywords": keywords[:20],
            "notes": str(result.get("notes") or ""),
            "existing_source_count": len(sources),
            "existing_span_count": len(spans),
            "outbound_requests": 0,
            "network_contacted": False,
            "no_identifier_invented": True,
        }

    # ------------------------------------------------------------------ facts
    def plan_fact_extraction(
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        packet_id: str,
        artifact_dir: Any | None = None,
    ) -> dict[str, Any]:
        """Extract the claim ledger from **every** span, one chunk at a time.

        The stage is driven by :func:`build_evidence_chunks`: each whole-span
        chunk is one ordered model call, the response is validated against the
        strict ``content-extract.v2`` contract and the per-chunk allowed-ID
        whitelist, and the chunk's state/hash is written to an atomically written
        ``analysis-manifest.json`` in the task artifact directory.  Completion
        requires the deterministic check
        ``union(completed_owned_span_ids) == required_span_ids`` — a partial read
        is a failure with the missing spans named, never a PASS.

        A restart redoes only the chunks whose input hash changed or that never
        completed, and the model call never runs inside a write transaction.
        """

        video = self._video(repo, project_id, video_id)
        packet, sources, spans = self._packet_and_spans(
            repo, project_id=project_id, video_id=video_id, packet_id=packet_id
        )
        is_fiction = str(video["content_kind"]) == ContentKind.ORIGINAL_FICTION.value
        span_ids = {str(item["id"]) for item in spans}
        span_source_ids = {str(item["id"]): str(item["source_id"]) for item in spans}
        source_ids = {str(item["id"]) for item in sources}
        required_span_ids = sorted(span_ids)

        chunks = build_evidence_chunks(
            spans,
            chunk_character_budget=MAX_EVIDENCE_CHUNK_CHARACTERS,
            context_character_budget=MAX_EVIDENCE_CONTEXT_CHARACTERS,
        )
        manifest_path = (
            _Path(artifact_dir) / ANALYSIS_MANIFEST_FILENAME if artifact_dir is not None else None
        )
        manifest = read_analysis_manifest(manifest_path) if manifest_path is not None else None
        if manifest is None:
            manifest = empty_manifest(
                required_span_ids=required_span_ids,
                prompt_version=PROMPT_VERSION,
                contract=CONTENT_EXTRACT_SCHEMA_VERSION,
            )
        else:
            # A manifest written for another contract/prompt version is not a
            # resume point: every chunk's input hash differs, so nothing is reused.
            manifest = {**manifest, "required_span_ids": required_span_ids}

        todo = pending_chunks(
            chunks,
            manifest,
            prompt_version=PROMPT_VERSION,
            contract=CONTENT_EXTRACT_SCHEMA_VERSION,
        )
        titles = {str(item["id"]): str(item.get("title") or "未命名来源") for item in sources}
        namespace_needed = len(chunks) > 1
        entity_catalogue = _catalogue(
            repo.list_where("explainer_entities", {"video_id": video_id}, order_by="code", descending=False),
            ("code", "name", "entity_type", "aliases_json"),
            limit=120,
        )
        chunk_schema = contract_schema_for_model("content-extract.v2")

        extracted_chunks: list[dict[str, Any]] = []
        for chunk in todo:
            namespace = f"C{int(chunk['ordinal']):02d}-" if namespace_needed else ""
            allowed = [*chunk["owned_span_ids"], *chunk["context_span_ids"]]
            user = render_prompt(
                CONTENT_EXTRACT_USER,
                task_contract_json={
                    "contract": CONTENT_EXTRACT_SCHEMA_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "project_id": project_id,
                    "video_id": video_id,
                    "packet_id": packet_id,
                    "chunk_id": chunk["chunk_id"],
                    "chunk_ordinal": chunk["ordinal"],
                },
                scope_json={
                    "content_kind": "ORIGINAL_FICTION" if is_fiction else "FACTUAL_EXPLAINER",
                    "title": str(video["title"]),
                    "topic": str(video.get("topic") or ""),
                    "selected_chapter_scope": "FULL_PACKET",
                },
                existing_entities_json=entity_catalogue,
                owned_span_ids_json=chunk["owned_span_ids"],
                source_spans_json=self._chunk_evidence_json(chunk, titles),
            )
            raw = self._chat(
                system=CONTENT_EXTRACT_SYSTEM,
                user=user,
                schema=chunk_schema,
                max_tokens=16_000,
                num_ctx=49_152,
            )
            if str(raw.get("schema_version") or "") == CONTENT_EXTRACT_SCHEMA_VERSION:
                validated, repairs = validate_with_single_repair(
                    raw,
                    contract="content-extract.v2",
                    repair=lambda errors, chunk=chunk, raw=raw, user=user: self._repair_chunk_format(
                        chunk=chunk,
                        previous=raw,
                        errors=errors,
                        system=CONTENT_EXTRACT_SYSTEM,
                        user=user,
                        schema=chunk_schema,
                    ),
                )
                model = validated
                extracted = self._validated_chunk_extraction(
                    model,
                    chunk=chunk,
                    allowed_span_ids=allowed,
                    entity_catalogue=entity_catalogue,
                    namespace=namespace,
                    span_source_ids=span_source_ids,
                )
                auxiliary = content_extract_auxiliary_metadata(model, code_prefix=namespace)
                contract_used = CONTENT_EXTRACT_SCHEMA_VERSION
            else:
                # A legacy-shaped answer (the schema this planner shipped before
                # the v2 contracts) is still accepted and validated by the same
                # per-field validator the research service uses, so an existing
                # deployment and its tests keep working while the strict contract
                # becomes the default.
                repairs = []
                validated = validate_model_payload(raw, FACT_EXTRACTION_SCHEMA, scope="fact_extraction")
                self._validate_legacy_span_references(
                    validated, span_ids=span_ids, source_ids=source_ids, allowed=allowed
                )
                extracted = self._prefix_legacy_codes(validated, namespace=namespace)
                auxiliary = {}
                contract_used = "localdrama.explainer.fact-extraction.legacy"
            response_hash = content_hash(extracted)
            extracted_chunks.append(
                {
                    "chunk": chunk,
                    "extracted": extracted,
                    "auxiliary": auxiliary,
                    "contract": contract_used,
                    "format_repairs": repairs,
                }
            )
            manifest = record_chunk_result(
                manifest,
                chunk=chunk,
                input_hash=str(chunk.get("input_hash") or ""),
                response_hash=response_hash,
                report={
                    "contract": contract_used,
                    "claim_count": len(extracted.get("claims") or []),
                    "entity_count": len(extracted.get("entities") or []),
                    "event_count": len(extracted.get("events") or []),
                    "character_count": chunk["character_count"],
                    # The per-chunk result is part of the manifest so a resumed run
                    # can rebuild the whole ledger without re-calling the model and
                    # without duplicating a single piece of evidence.
                    "extracted": extracted,
                    "auxiliary": auxiliary,
                    "format_repairs": repairs,
                },
            )
            manifest = mark_manifest_in_progress(manifest)
            if manifest_path is not None:
                write_analysis_manifest(manifest_path, manifest)

        coverage = coverage_status(chunks, manifest, required_span_ids=required_span_ids)
        if manifest_path is not None:
            manifest = {
                **manifest,
                "status": "COMPLETED" if coverage["complete"] else "PARTIAL",
                "coverage": coverage,
            }
            write_analysis_manifest(manifest_path, manifest)
        # The full-text stage is complete only when the program's own union check
        # passes; a model saying "done" proves nothing (§C3.1).
        assert_full_coverage(
            required_span_ids=required_span_ids,
            owned_span_ids=coverage["completed_owned_span_ids"],
        )

        if artifact_dir is None:
            # No artifact directory: the whole document was processed in this run,
            # so the manifest is complete by construction and nothing was skipped.
            pass

        # Rebuild the ledger in chunk order from *either* this run's results or the
        # manifest's completed records, so a resumed run neither loses the tail nor
        # duplicates evidence a previous run already extracted.
        processed_by_chunk = {str(item["chunk"]["chunk_id"]): item for item in extracted_chunks}
        effective: list[dict[str, Any]] = []
        for chunk in chunks:
            chunk_id = str(chunk["chunk_id"])
            if chunk_id in processed_by_chunk:
                effective.append(processed_by_chunk[chunk_id])
                continue
            record = dict((manifest.get("chunks") or {}).get(chunk_id) or {})
            report = record.get("report") if isinstance(record.get("report"), Mapping) else {}
            if record.get("status") == "COMPLETED" and isinstance(report.get("extracted"), Mapping):
                effective.append(
                    {
                        "chunk": chunk,
                        "extracted": dict(report["extracted"]),
                        "auxiliary": dict(report.get("auxiliary") or {}),
                        "contract": str(report.get("contract") or ""),
                        "format_repairs": list(report.get("format_repairs") or []),
                    }
                )
                continue
            raise ExplainerContractError(
                "SOURCE_EVIDENCE_MISSING",
                "分块结果缺失，无法合并为完整事实账本",
                {"chunk_id": chunk_id, "manifest_path": str(manifest_path) if manifest_path else None},
            )

        merged = self._merge_chunk_extractions(effective)
        date_repairs = _repair_story_time_dates(merged["extraction"], sources, spans)
        return {
            "status": "PASS",
            "extracted": merged["extraction"],
            "claim_count": len(merged["extraction"].get("claims") or []),
            "event_count": len(merged["extraction"].get("events") or []),
            "entity_count": len(merged["extraction"].get("entities") or []),
            "verified_as_history": False,
            "reference_validation": "ALL_SPANS_RESOLVED",
            "story_time_date_repairs": date_repairs,
            "contract": CONTENT_EXTRACT_SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "chunk_count": len(chunks),
            "chunks_processed_this_run": [item["chunk"]["chunk_id"] for item in extracted_chunks],
            "chunk_reports": [
                {
                    "chunk_id": item["chunk"]["chunk_id"],
                    "ordinal": item["chunk"]["ordinal"],
                    "owned_span_count": len(item["chunk"]["owned_span_ids"]),
                    "context_span_count": len(item["chunk"]["context_span_ids"]),
                    "character_count": item["chunk"]["character_count"],
                    "contract": item["contract"],
                    "format_repairs": item["format_repairs"],
                }
                for item in extracted_chunks
            ],
            "coverage": coverage,
            "coverage_display": coverage["display"],
            "analysis_manifest_path": str(manifest_path) if manifest_path is not None else None,
            "context_only_excluded_from_coverage": True,
            "entity_merge_candidates": merged["merge_candidates"],
            "entity_review_items": merged["review_items"],
            "alias_evidence": merged["alias_evidence"],
            "auxiliary_metadata": merged["auxiliary"],
            "chunked_full_text_no_prefix_truncation": True,
        }

    def _repair_chunk_format(
        self,
        *,
        chunk: Mapping[str, Any],
        previous: Mapping[str, Any],
        errors: list[dict[str, Any]],
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """One bounded format repair (§C8.1): same inputs, same allowed IDs.

        Only a *format* failure reaches here.  The repair prompt forbids new
        facts, new references and dropping other legal objects, and the repaired
        answer is re-validated against the same contract and whitelist.
        """

        repair_user = f"{user}\n\n" + render_prompt(
            FORMAT_REPAIR_USER,
            validator_errors_json=errors,
            previous_response_json=dict(previous),
            allowed_ids_and_schema_json={
                "owned_span_ids": list(chunk.get("owned_span_ids") or []),
                "context_span_ids": list(chunk.get("context_span_ids") or []),
            },
        )
        result = self._chat(
            system=system,
            user=repair_user,
            schema=schema,
            max_tokens=16_000,
            num_ctx=49_152,
        )
        return result if isinstance(result, Mapping) else None

    def _validated_chunk_extraction(
        self,
        model: Any,
        *,
        chunk: Mapping[str, Any],
        allowed_span_ids: Sequence[str],
        entity_catalogue: Sequence[Mapping[str, Any]],
        namespace: str,
        span_source_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        """Whitelist/index checks, then the mapping to the legacy schema.

        Everything the static schema cannot prove is checked here: span IDs must
        be in this chunk's owned+context list, array indexes must fall inside the
        response's own arrays, and a ``same_as_entity_id`` may only name an entity
        the task supplied.
        """

        payload = model.model_dump() if hasattr(model, "model_dump") else dict(model)
        for index, entity in enumerate(payload.get("entities") or []):
            assert_allowed_ids(
                entity.get("source_span_ids") or [],
                allowed=allowed_span_ids,
                field=f"entities[{index}].source_span_ids",
            )
            for appearance_index, appearance in enumerate(entity.get("known_appearance") or []):
                assert_allowed_ids(
                    appearance.get("source_span_ids") or [],
                    allowed=allowed_span_ids,
                    field=f"entities[{index}].known_appearance[{appearance_index}].source_span_ids",
                )
            state = entity.get("state")
            if isinstance(state, Mapping):
                assert_allowed_ids(
                    state.get("source_span_ids") or [],
                    allowed=allowed_span_ids,
                    field=f"entities[{index}].state.source_span_ids",
                )
                assert_indexes_in_range(
                    state.get("carried_prop_entity_indexes") or [],
                    size=len(payload.get("entities") or []),
                    field=f"entities[{index}].state.carried_prop_entity_indexes",
                )
        claim_count = len(payload.get("claims") or [])
        entity_count = len(payload.get("entities") or [])
        for index, claim in enumerate(payload.get("claims") or []):
            assert_indexes_in_range(
                claim.get("entity_indexes") or [],
                size=entity_count,
                field=f"claims[{index}].entity_indexes",
            )
            for evidence_index, evidence in enumerate(claim.get("evidence") or []):
                assert_allowed_ids(
                    [evidence.get("source_span_id")],
                    allowed=allowed_span_ids,
                    field=f"claims[{index}].evidence[{evidence_index}].source_span_id",
                )
        for index, event in enumerate(payload.get("events") or []):
            assert_indexes_in_range(
                event.get("participant_entity_indexes") or [],
                size=entity_count,
                field=f"events[{index}].participant_entity_indexes",
            )
            assert_indexes_in_range(
                event.get("claim_indexes") or [], size=claim_count, field=f"events[{index}].claim_indexes"
            )
            place = event.get("place_entity_index")
            if place is not None:
                assert_indexes_in_range([place], size=entity_count, field=f"events[{index}].place_entity_index")
        for index, ambiguity in enumerate(payload.get("ambiguities") or []):
            assert_indexes_in_range(
                ambiguity.get("entity_indexes") or [],
                size=entity_count,
                field=f"ambiguities[{index}].entity_indexes",
            )
            assert_indexes_in_range(
                ambiguity.get("claim_indexes") or [],
                size=claim_count,
                field=f"ambiguities[{index}].claim_indexes",
            )
            assert_allowed_ids(
                ambiguity.get("source_span_ids") or [],
                allowed=allowed_span_ids,
                field=f"ambiguities[{index}].source_span_ids",
            )
        known_ids = {str(item.get("code") or "") for item in entity_catalogue}
        known_ids |= {str(item.get("id") or "") for item in entity_catalogue}
        for index, entity in enumerate(payload.get("entities") or []):
            same_as = entity.get("same_as_entity_id")
            if same_as and str(same_as) not in known_ids:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "same_as_entity_id 只能引用输入实体表中已有的实体",
                    {"entity_index": index, "same_as_entity_id": str(same_as)},
                )
        # The model may only cite IDs; it may never mint one.
        assert_no_model_generated_persistent_ids(
            payload,
            allowed_reference_keys={
                "source_span_id",
                "source_span_ids",
                "same_as_entity_id",
            },
        )
        return content_extract_to_fact_extraction(
            payload, code_prefix=namespace, span_source_ids=span_source_ids
        )

    def _validate_legacy_span_references(
        self,
        validated: Mapping[str, Any],
        *,
        span_ids: set[str],
        source_ids: set[str] | None = None,
        allowed: Sequence[str] | None = None,
    ) -> None:
        """The pre-v2 check: every cited span/source must exist in this task."""

        permitted = set(allowed) if allowed is not None else set(span_ids)
        known_sources = set(source_ids or ())
        for claim in validated.get("claims") or []:
            for evidence in claim.get("evidence") or []:
                span_id = str(evidence["source_span_id"])
                if span_id not in permitted or span_id not in span_ids:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "文本模型引用了不存在的来源片段",
                        {"claim_code": claim.get("code"), "source_span_id": span_id},
                    )
                declared = evidence.get("source_id")
                if declared and known_sources and str(declared) not in known_sources:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "文本模型引用了不存在的来源",
                        {"claim_code": claim.get("code"), "source_id": str(declared)},
                    )

    @staticmethod
    def _prefix_legacy_codes(validated: Mapping[str, Any], *, namespace: str) -> dict[str, Any]:
        """Namespace a legacy response's codes so several chunks cannot collide."""

        if not namespace:
            return dict(validated)
        def rename(value: Any) -> str:
            return f"{namespace}{value}" if str(value) else str(value)

        return {
            "claims": [
                {
                    **dict(claim),
                    "code": rename(claim.get("code")),
                    "evidence": [dict(item) for item in (claim.get("evidence") or [])],
                }
                for claim in validated.get("claims") or []
            ],
            "events": [
                {
                    **dict(event),
                    "code": rename(event.get("code")),
                    "participant_entity_codes": [
                        rename(item) for item in (event.get("participant_entity_codes") or [])
                    ],
                    "claim_codes": [rename(item) for item in (event.get("claim_codes") or [])],
                }
                for event in validated.get("events") or []
            ],
            "entities": [
                {
                    **dict(entity),
                    "code": rename(entity.get("code")),
                    "state": (
                        {
                            **dict(entity["state"]),
                            "carried_prop_entity_codes": [
                                rename(item)
                                for item in (dict(entity["state"]).get("carried_prop_entity_codes") or [])
                            ],
                        }
                        if isinstance(entity.get("state"), Mapping)
                        else entity.get("state")
                    ),
                }
                for entity in validated.get("entities") or []
            ],
        }

    def _merge_chunk_extractions(self, chunk_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Merge per-chunk arrays into one ledger, applying §C3.2 dedup/alias rules.

        Entities found in several chunks are merged only by the deterministic rule
        (normalised name + same type + no time/role conflict) or by an explicit
        in-source "X 又称 Y" declaration; a same-name/state conflict keeps both
        entities and produces a pending-review item instead.  Replies merged away
        are remapped in every event and carried-prop reference so no dangling code
        survives.
        """

        claims: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        auxiliary: list[dict[str, Any]] = []
        for result in chunk_results:
            extracted = result.get("extracted") or {}
            claims.extend(dict(item) for item in (extracted.get("claims") or []))
            events.extend(dict(item) for item in (extracted.get("events") or []))
            entities.extend(dict(item) for item in (extracted.get("entities") or []))
            if result.get("auxiliary"):
                auxiliary.append(dict(result["auxiliary"]))

        decisions: list[dict[str, Any]] = []
        for candidate in entity_merge_candidates(entities)["merge_candidates"]:
            decisions.append(
                {
                    "match_key": candidate["match_key"],
                    "entity_indexes": candidate["entity_indexes"],
                    "verdict": "SAME",
                    "evidence_span_ids": [],
                    "reason": candidate["basis"],
                }
            )
        applied = apply_disambiguation_decisions(entities, decisions=decisions)
        entities = applied["entities"]
        review_items = list(applied["review_items"])
        review_items.extend(entity_merge_candidates(entities)["review_items"])

        # Evidence dedup is keyed on the claim, so it is applied inside each claim.
        for claim in claims:
            claim["evidence"] = dedupe_evidence(claim.get("evidence") or [])

        all_codes = {str(item.get("code") or "") for item in entities}
        for event in events:
            event["participant_entity_codes"] = [
                code for code in (event.get("participant_entity_codes") or []) if code in all_codes
            ]
            event["claim_codes"] = [
                code for code in (event.get("claim_codes") or []) if any(code == c.get("code") for c in claims)
            ]
        for entity in entities:
            state = entity.get("state")
            if isinstance(state, Mapping):
                state["carried_prop_entity_codes"] = [
                    code for code in (state.get("carried_prop_entity_codes") or []) if code in all_codes
                ]
        return {
            "extraction": {"claims": claims, "events": events, "entities": entities},
            "merge_candidates": entity_merge_candidates(entities)["merge_candidates"],
            "review_items": review_items,
            "alias_evidence": applied["alias_evidence"],
            "auxiliary": auxiliary,
        }

    # ------------------------------------------------------------------ script
    def plan_script(
        self, *, repo: ExplainerRepository, project_id: str, video_id: str
    ) -> dict[str, Any]:
        video = self._video(repo, project_id, video_id)
        # Resolve the client before reading so a machine with no local text model
        # reports CAPABILITY_UNAVAILABLE instead of a misleading "no claims yet".
        client = self._client()
        claims = repo.list_where(
            "explainer_claims", {"video_id": video_id}, order_by="created_at", descending=False
        )
        if not claims:
            raise ExplainerContractError(
                ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                "还没有事实账本，无法编写解说稿",
                {"video_id": video_id},
            )
        conflicts = repo.open_core_conflicts(video_id)
        if conflicts:
            raise ExplainerContractError(
                ExplainerErrorCode.CLAIM_CONFLICT.value,
                "存在未解决的核心事实冲突，写稿前必须先处理",
                {"claim_codes": [str(item["code"]) for item in conflicts]},
            )
        entities = repo.list_where("explainer_entities", {"video_id": video_id}, order_by="code", descending=False)
        target_seconds = int(video["target_seconds"])
        locale = str(video["source_locale"])
        is_cjk = locale.lower().startswith("zh")
        character_budget = int(target_seconds * CHINESE_CHARS_PER_SECOND_HINT) if is_cjk else int(target_seconds * 2.4)
        claim_catalogue = _catalogue(
            claims, ("code", "statement", "statement_kind", "importance", "status")
        )
        entity_catalogue = _catalogue(entities, ("code", "name", "entity_type", "latin_name"), limit=60)
        events = repo.list_where("explainer_events", {"video_id": video_id}, order_by="created_at", descending=False)
        # §C5.3: the design's own prompt and contract are the primary path.  A model
        # that answers the strict ``script-draft.v2`` shape is consumed by
        # ``_plan_script_from_draft``; only a contract failure falls back to the
        # legacy segment shape below, and the fallback is reported in the plan.
        draft_plan = self._plan_script_contract_first(
            video=video,
            claims=claims,
            entities=entities,
            events=events,
            claim_catalogue=claim_catalogue,
            entity_catalogue=entity_catalogue,
            target_seconds=target_seconds,
            character_budget=character_budget,
            locale=locale,
            client=client,
        )
        if draft_plan is not None:
            return draft_plan
        user = (
            f"作品标题：{video['title']}\n目标时长：约 {target_seconds} 秒\n语言：{locale}\n"
            f"内容属性：{'原创虚构' if str(video['content_kind']) == ContentKind.ORIGINAL_FICTION.value else '事实解说'}\n"
            f"字数预算提示：约 {character_budget} 个字符（这只是写稿提示，真实时长由配音实测决定）\n\n"
            "请先给出章节纲要 outline（每行一章，写成「章节标题：本章要回答的问题」），再写 segments：\n"
            "- canonical_segment_id 用 seg_001 起的编号，必须唯一且连续。\n"
            "- display_text 是上屏文本，保留规范数字与人名；spoken_text 是朗读文本，"
            "把数字与符号展开成读法（例如 1962 → 一九六二年、21:17 → 二十一点十七分），"
            "朗读文本里不要保留冒号、斜杠等不可朗读的分隔符；"
            "pronunciation_map 必须能让 display_text 逐字替换后得到 spoken_text，否则整段会被判为不等价。\n"
            "- statement_type：复述来源事实用 FACT 并填 claim_code；自己的解释用 ORIGINAL_EXPLANATION；"
            "章节衔接用 TRANSITION；虚构故事里明确编排的情节用 FICTION；提出问题用 QUESTION。\n"
            "- 开头 1 到 2 段提出真实悬念或反常识问题，结尾回应它；不要虚构夸张结论引流。\n"
            "- 不得复述整段资料；每章只使用与该章相关的事实。\n\n"
            f"可用事实编号（claim_code 只能取这里的值）：\n{json.dumps(claim_catalogue, ensure_ascii=False)}\n\n"
            f"可用实体名录：\n{json.dumps(entity_catalogue, ensure_ascii=False)}"
        )
        result = self._chat(
            system=self.prompts.script_system,
            user=user,
            schema=SEGMENT_SCHEMA,
            max_tokens=16_000,
            num_ctx=49_152,
            client=client,
        )
        raw_segments = result.get("segments") or []
        if str(result.get("schema_version") or "") == SCRIPT_DRAFT_SCHEMA_VERSION:
            # The strict ``script-draft.v2`` contract: program-assigned segment IDs,
            # program-derived readings, and an explicit ``insufficient_content``
            # answer instead of padding the script with unsourced material (§C4.2).
            return self._plan_script_from_draft(
                result,
                video=video,
                claims=claims,
                item_entity_ids={str(item["id"]) for item in entities} | {str(item["code"]) for item in entities},
                target_seconds=target_seconds,
                character_budget=character_budget,
            )
        if not raw_segments:
            raise ExplainerContractError("SCHEMA_INVALID", "文本模型没有返回任何叙述段落")
        # A local model routinely returns a script well under the requested length.
        # The film's real length is the measured narration, so a short script means a
        # film that misses the operator's declared target.  The shortfall is repaired
        # with a bounded number of explicit re-asks that state the exact gap; if the
        # model still under-delivers, the plan reports it instead of pretending the
        # target was met.
        length_repairs: list[dict[str, Any]] = []
        while (
            len(length_repairs) < SCRIPT_BUDGET_MAX_REPAIRS
            and _script_characters(raw_segments) < int(character_budget * SCRIPT_BUDGET_MINIMUM_RATIO)
        ):
            produced = _script_characters(raw_segments)
            length_repairs.append(
                {
                    "attempt": len(length_repairs) + 1,
                    "produced_characters": produced,
                    "required_characters": character_budget,
                }
            )
            result = self._chat(
                system=self.prompts.script_system,
                user=(
                    f"{user}\n\n上一次输出只有 {produced} 个字符，少于目标时长 {target_seconds} 秒所需的"
                    f"约 {character_budget} 个字符。只允许更完整地表达**已有资料中已经出现的内容**："
                    "改善过渡、解释与结构，把已列出的事实讲清楚。"
                    "不得新增资料中没有的事实、数字、日期、外貌、对话、心理、因果或例子；"
                    "不得引用不存在的 claim_code；不得改变任何否定、数字或人名。"
                    "如果现有资料无法在不新增事实的前提下接近该长度，就不要再写更长："
                    "保留现有内容即可，不要为凑字数编造内容。"
                ),
                schema=SEGMENT_SCHEMA,
                max_tokens=16_000,
                num_ctx=49_152,
                client=client,
            )
            new_segments = result.get("segments") or []
            if not new_segments:
                raise ExplainerContractError("SCHEMA_INVALID", "文本模型没有返回任何叙述段落")
            if _script_characters(new_segments) <= produced:
                # No progress: the model kept the same text (measured on a real local
                # model: three re-asks returned byte-identical output and cost ~180 s).
                # Asking again cannot help, so the loop stops and the shortfall stays
                # recorded in ``length_repairs``.
                length_repairs[-1]["stopped_on_no_progress"] = True
                raw_segments = new_segments
                break
            raw_segments = new_segments
        if len(raw_segments) > MAX_SEGMENTS:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "叙述段落数量超过上限", {"count": len(raw_segments), "limit": MAX_SEGMENTS}
            )
        claim_codes = {str(item["code"]) for item in claims}
        segments: list[dict[str, Any]] = []
        spoken_dispositions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_segments, start=1):
            segment_id = str(raw.get("canonical_segment_id") or f"seg_{index:03d}").strip()
            if segment_id in seen:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "文本模型返回了重复的段落编号", {"canonical_segment_id": segment_id}
                )
            seen.add(segment_id)
            display_text = str(raw.get("display_text") or "").strip()
            if not display_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "叙述段落的 display_text 不能为空", {"canonical_segment_id": segment_id}
                )
            statement_type = str(raw.get("statement_type") or StatementType.FACT.value)
            claim_code = str(raw.get("claim_code") or "").strip()
            if statement_type == StatementType.FACT.value and not claim_code:
                raise ExplainerContractError(
                    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                    "标记为事实的段落必须引用一条事实编号",
                    {"canonical_segment_id": segment_id},
                )
            if claim_code and claim_code not in claim_codes:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "叙述段落引用了不存在的事实编号",
                    {"canonical_segment_id": segment_id, "claim_code": claim_code},
                )
            spoken_text = str(raw.get("spoken_text") or "").strip() or display_text
            raw_map = [
                {"display": str(pair.get("display") or ""), "spoken": str(pair.get("spoken") or "")}
                for pair in (raw.get("pronunciation_map") or [])
                if str(pair.get("display") or "") and str(pair.get("spoken") or "")
            ]
            spoken_text, pronunciation_map, display_text, spoken_disposition = _normalise_spoken_text(
                display_text, spoken_text, raw_map
            )
            spoken_dispositions.append(
                {"canonical_segment_id": segment_id, "disposition": spoken_disposition}
            )
            segments.append(
                {
                    "canonical_segment_id": segment_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "statement_type": statement_type,
                    "claim_ids": [claim_code] if claim_code else [],
                    "pronunciation_map": pronunciation_map,
                    "pause_after_ms": int(raw.get("pause_after_ms") or 0),
                    "chapter_code": str(raw.get("chapter_code") or "").strip() or None,
                }
            )
        return {
            "status": "PASS",
            "outline": [str(item) for item in (result.get("outline") or [])],
            "segments": segments,
            "segment_count": len(segments),
            "character_count": sum(len(item["display_text"]) for item in segments),
            "target_seconds": target_seconds,
            "character_budget": character_budget,
            "chars_per_second_hint": CHINESE_CHARS_PER_SECOND_HINT,
            "length_repairs": length_repairs,
            "budget_met": sum(len(item["display_text"]) for item in segments)
            >= int(character_budget * SCRIPT_BUDGET_MINIMUM_RATIO),
            "timing_status": "TEXT_BUDGET_HINT_NOT_MEASURED_TTS",
            "spoken_text_dispositions": spoken_dispositions,
            # The legacy segment shape was only used because the strict
            # ``script-draft.v2`` answer did not validate; the receipt names why so a
            # weaker local model is never silently reported as design-conformant.
            "contract_used": "legacy.segment.v1",
            "v2_fallback_reason": dict(self._script_fallback_reason or {}),
        }

    # --------------------------------------------------------- preserved script
    def _plan_script_contract_first(
        self,
        *,
        video: Mapping[str, Any],
        claims: Sequence[Mapping[str, Any]],
        entities: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        claim_catalogue: Sequence[Mapping[str, Any]],
        entity_catalogue: Sequence[Mapping[str, Any]],
        target_seconds: int,
        character_budget: int,
        locale: str,
        client: Any,
    ) -> dict[str, Any] | None:
        """Draft the script through the design's ``script-draft.v2`` prompt.

        Returns the plan, or ``None`` when the model's answer does not satisfy the
        strict contract — the caller then runs the legacy segment path, and the plan it
        produces records ``v2_fallback_reason`` so the receipt never hides that the
        design contract was not met.

        A real local model with the design prompt is also re-asked at most once when the
        draft is well under the operator's character budget; an answer that does not
        change is never re-asked again (measured: three identical re-asks cost ~180 s
        and produced byte-identical text).
        """

        is_fiction = str(video["content_kind"]) == ContentKind.ORIGINAL_FICTION.value
        writing_request = {
            "title": str(video["title"]),
            "locale": locale,
            "target_seconds": target_seconds,
            "character_budget_hint": character_budget,
            "content_kind": "ORIGINAL_FICTION" if is_fiction else "FACTUAL_EXPLAINER",
            "tone": "解释性、克制，不煽情",
            "budget_note": "字数只是提示，真实时长由配音实测决定；不得为凑长度新增事实。",
        }
        chapter_context = {
            "chapter_count": "由你给出",
            "outline_rule": "outline 至少 2 章、最多 8 章，每章写成「章节标题：本章要回答的问题」。",
            "scope": "本次一次性返回 outline 与全部 segments，segment 的 chapter_index 必须落在 outline 范围内。",
        }
        selected_claims = [
            {
                "code": str(item["code"]),
                "statement": str(item["statement"]),
                "statement_kind": str(item.get("statement_kind") or ""),
                "importance": str(item.get("importance") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in claim_catalogue
        ]
        entity_event_catalogue = {
            "entities": [dict(item) for item in entity_catalogue],
            "events": [
                {
                    "code": str(item.get("code") or ""),
                    "title": str(item.get("title") or ""),
                    "participant_entity_codes": list(item.get("participant_entity_codes") or []),
                    "claim_codes": list(item.get("claim_codes") or []),
                }
                for item in events[:MAX_CATALOGUE_ITEMS]
            ],
        }
        locked_constraints = {
            "preserved_source": False,
            "user_note": "本次没有用户已定稿的段落；不得复述整段资料，FACT 段必须引用给定 claim_ids。",
        }
        user = render_prompt(
            SCRIPT_DRAFT_USER,
            writing_request_json=writing_request,
            chapter_context_json=chapter_context,
            selected_claims_json=selected_claims,
            entity_event_catalogue_json=entity_event_catalogue,
            locked_constraints_json=locked_constraints,
            schema_json=contract_schema_for_model("script-draft.v2"),
        )
        schema = contract_schema_for_model("script-draft.v2")
        try:
            draft = self._chat(
                system=SCRIPT_DRAFT_SYSTEM,
                user=user,
                schema=schema,
                max_tokens=16_000,
                num_ctx=49_152,
                client=client,
            )
            plan = self._plan_script_from_draft(
                draft,
                video=video,
                claims=claims,
                item_entity_ids={str(item["id"]) for item in entities} | {str(item["code"]) for item in entities},
                target_seconds=target_seconds,
                character_budget=character_budget,
            )
        except ExplainerContractError as error:
            self._script_fallback_reason = {
                "code": error.code,
                "message": error.message,
                "details": dict(error.details or {}),
            }
            return None

        plan["contract_used"] = SCRIPT_DRAFT_SCHEMA_VERSION
        plan["prompt_used"] = "script-draft.v2"
        if not plan.get("insufficient_content") and int(plan.get("character_count") or 0) < int(
            character_budget * SCRIPT_BUDGET_MINIMUM_RATIO
        ):
            note = (
                f"\n\n上一次输出只有 {plan.get('character_count')} 个字符，少于目标时长 {target_seconds} 秒所需的"
                f"约 {character_budget} 个字符。只允许更完整地表达**已有资料中已经出现的内容**："
                "改善过渡、解释与结构，把已列出的事实讲清楚。"
                "不得新增资料中没有的事实、数字、日期、外貌、对话、心理、因果或例子；"
                "不得引用不存在的 claim_id；不得改变任何否定、数字或人名。"
                "如果现有资料无法在不新增事实的前提下接近该长度，就不要再写更长：保留现有内容即可。"
            )
            try:
                longer = self._chat(
                    system=SCRIPT_DRAFT_SYSTEM,
                    user=f"{user}{note}",
                    schema=schema,
                    max_tokens=16_000,
                    num_ctx=49_152,
                    client=client,
                )
                longer_plan = self._plan_script_from_draft(
                    longer,
                    video=video,
                    claims=claims,
                    item_entity_ids={str(item["id"]) for item in entities}
                    | {str(item["code"]) for item in entities},
                    target_seconds=target_seconds,
                    character_budget=character_budget,
                )
            except ExplainerContractError:
                longer_plan = None
            plan["length_repairs"] = [
                {
                    "attempt": 1,
                    "produced_characters": int(plan.get("character_count") or 0),
                    "required_characters": character_budget,
                    "reask_contract_failed": longer_plan is None,
                    "reask_characters": (longer_plan or {}).get("character_count"),
                }
            ]
            if longer_plan is not None and int(longer_plan.get("character_count") or 0) > int(
                plan.get("character_count") or 0
            ):
                longer_plan["contract_used"] = SCRIPT_DRAFT_SCHEMA_VERSION
                longer_plan["prompt_used"] = "script-draft.v2+length-reask"
                longer_plan["length_repairs"] = plan["length_repairs"]
                return longer_plan
        return plan

    def _plan_script_from_draft(
        self,
        draft: Mapping[str, Any],
        *,
        video: Mapping[str, Any],
        claims: Sequence[Mapping[str, Any]],
        item_entity_ids: set[str] | None,
        target_seconds: int,
        character_budget: int,
    ) -> dict[str, Any]:
        """Consume a ``script-draft.v2`` answer (§C5.3).

        Program responsibilities: assign ``canonical_segment_id``, verify the
        chapter range, resolve the cited claim/entity IDs against the real ledger,
        derive the reading from the declared pronunciation suggestions, and — when
        the model reports ``insufficient_content`` — refuse to produce a padded
        script instead of asking it to invent more material.
        """

        validated = validate_contract("script-draft.v2", draft)
        assert_chapter_indexes_in_outline_range(validated)
        outline_payload = [
            f"{item.title}：{item.audience_question}" if item.audience_question else item.title
            for item in validated.outline
        ]
        if validated.insufficient_content:
            return {
                "status": "INSUFFICIENT_CONTENT",
                "contract": SCRIPT_DRAFT_SCHEMA_VERSION,
                "outline": outline_payload,
                "segments": [],
                "segment_count": 0,
                "character_count": 0,
                "target_seconds": target_seconds,
                "character_budget": character_budget,
                "chars_per_second_hint": CHINESE_CHARS_PER_SECOND_HINT,
                "length_repairs": [],
                "budget_met": False,
                "insufficient_content": True,
                "missing_content_note": validated.missing_content_note,
                "timing_status": "TEXT_BUDGET_HINT_NOT_MEASURED_TTS",
                "spoken_text_dispositions": [],
                "unsourced_padding_refused": True,
            }
        if not validated.segments:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "script-draft.v2 既未声明内容不足，也没有返回任何段落",
                {"outline_count": len(validated.outline)},
            )
        claim_codes = {str(item["code"]) for item in claims}
        claim_ids = {str(item["id"]) for item in claims}
        if item_entity_ids is None:
            item_entity_ids = set()
        segments: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        for index, item in enumerate(validated.segments, start=1):
            segment_id = f"seg_{index:03d}"
            if item.claim_ids:
                assert_allowed_ids(item.claim_ids, allowed=claim_codes | claim_ids, field="claim_ids")
            if item.entity_ids:
                assert_allowed_ids(item.entity_ids, allowed=item_entity_ids, field="entity_ids")
            if item.statement_type == StatementType.FACT and not item.claim_ids:
                raise ExplainerContractError(
                    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                    "标记为事实的段落必须引用一条事实编号",
                    {"canonical_segment_id": segment_id},
                )
            pairs: list[dict[str, str]] = []
            for suggestion in item.pronunciation_suggestions:
                if suggestion.display not in item.display_text:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "读音建议的 display 必须是本段正文的实际片段",
                        {"canonical_segment_id": segment_id, "display": suggestion.display},
                    )
                pairs.append({"display": suggestion.display, "spoken": suggestion.spoken})
            spoken_text = apply_pronunciation_map(item.display_text, pairs)
            dispositions.append(
                {
                    "canonical_segment_id": segment_id,
                    "disposition": "PROGRAM_DERIVED_FROM_EXPLICIT_MAP" if pairs else "IDENTICAL",
                }
            )
            segments.append(
                {
                    "canonical_segment_id": segment_id,
                    "display_text": item.display_text,
                    "spoken_text": spoken_text,
                    "statement_type": item.statement_type.value,
                    "claim_ids": list(item.claim_ids),
                    "pronunciation_map": pairs,
                    "pause_after_ms": int(item.pause_after_ms),
                    "chapter_code": f"ch_{item.chapter_index + 1:03d}",
                }
            )
        produced = sum(len(item["display_text"]) for item in segments)
        return {
            "status": "PASS",
            "contract": SCRIPT_DRAFT_SCHEMA_VERSION,            "outline": outline_payload,
            "segments": segments,
            "segment_count": len(segments),
            "character_count": produced,
            "target_seconds": target_seconds,
            "character_budget": character_budget,
            "chars_per_second_hint": CHINESE_CHARS_PER_SECOND_HINT,
            "length_repairs": [],
            "budget_met": produced >= int(character_budget * SCRIPT_BUDGET_MINIMUM_RATIO),
            "insufficient_content": False,
            "missing_content_note": validated.missing_content_note,
            "timing_status": "TEXT_BUDGET_HINT_NOT_MEASURED_TTS",
            "spoken_text_dispositions": dispositions,
            "program_assigned_segment_ids": True,
        }

    # --------------------------------------------------------- preserved script
    def plan_preserved_script(        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        script_source_text: str | None = None,
        pronunciation_map: Sequence[Mapping[str, str]] = (),
        annotate: bool = False,
    ) -> dict[str, Any]:
        """Segment a finished manuscript without letting anyone rewrite it.

        The program owns the body (§C4.1):

        * the split is deterministic at sentence-final punctuation and paragraph
          boundaries, and every character keeps its original order;
        * each segment records ``source_start/source_end`` and
          ``display_text = script_source_text[start:end]``, and the gap between two
          segments is kept as an explicit ``separator``;
        * the concatenation plus the SHA-256 must equal the canonical manuscript
          exactly, or the whole batch is refused — never "approximately equal";
        * ``spoken_text`` defaults to the display text and is derived only from an
          explicit ``pronunciation_map``.  ``_normalise_spoken_text()`` is *not*
          called on this branch and no ``.strip()`` touches the body;
        * this branch never asks the model for a rewrite or an expansion, so
          ``length_expansion_requested`` is always ``False`` and
          ``max_script_revisions`` is ``0``.

        ``annotate=True`` lets the same local text model add labels, fact bindings,
        entity references and pronunciation suggestions through the
        ``preserved-script-annotations.v1`` contract; the body still never crosses
        the model boundary.
        """

        video = self._video(repo, project_id, video_id)
        policy = resolve_script_policy(video.get("input_payload_json", {}).get("script_policy"))
        if policy != ScriptPolicy.PRESERVE_ORIGINAL.value:
            # Not a hard failure: a caller may explicitly measure a preserved plan,
            # but the result says which policy it belongs to.
            pass
        source = script_source_text if script_source_text is not None else self._preserved_source(repo, video_id)
        if not source:
            raise ExplainerContractError(
                ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                "没有可用的原稿正文：请粘贴正文或先导入文稿，再重试",
                {"project_id": project_id, "video_id": video_id, "script_policy": policy},
            )
        built = build_preserved_segments(source, pronunciation_map=pronunciation_map)
        validation = validate_preserved_concatenation(
            built["segments"],
            script_source_text=built["script_source_text"],
            leading_separator=str(built.get("leading_separator") or ""),
        )
        segments = [dict(item) for item in built["segments"]]
        claim_codes = {
            str(row["code"])
            for row in repo.query_all(
                "SELECT code FROM explainer_claims WHERE video_id = ?", (video_id,)
            )
        }
        entity_codes = {
            str(row["code"])
            for row in repo.query_all(
                "SELECT code FROM explainer_entities WHERE video_id = ?", (video_id,)
            )
        }
        model_calls = 0
        annotations_applied = False
        if annotate and self._model_available():
            payload = self._preserved_annotation_call(
                repo=repo,
                video=video,
                built=built,
                claim_codes=sorted(claim_codes),
                entity_codes=sorted(entity_codes),
                pronunciation_map=pronunciation_map,
            )
            model_calls = 1
            annotations_applied = True
            validated_annotations = payload
            segments = self._apply_preserved_annotations(
                segments=segments,
                annotations=validated_annotations,
                claim_codes=claim_codes,
                entity_codes=entity_codes,
            )
        chapter_codes = _chapter_codes_for_preserved(segments)

        for segment in segments:
            segment["chapter_code"] = chapter_codes[segment["canonical_segment_id"]]
            if not segment["display_text"].strip():
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "原稿切片为空，无法作为讲稿段落",
                    {"canonical_segment_id": segment["canonical_segment_id"]},
                )
        plan = {
            "status": "PASS",
            "script_policy": ScriptPolicy.PRESERVE_ORIGINAL.value,
            "preserved": True,
            "outline": [],
            "segments": segments,
            "segment_count": len(segments),
            "character_count": sum(len(item["display_text"]) for item in segments),
            "script_source_text": built["script_source_text"],
            "script_source_hash": built["script_source_hash"],
            "leading_separator": str(built.get("leading_separator") or ""),
            "span_map": [
                {
                    "canonical_segment_id": item["canonical_segment_id"],
                    "source_start": item["source_start"],
                    "source_end": item["source_end"],
                    "separator": item["separator"],
                }
                for item in segments
            ],
            "validation": validation,
            "max_script_revisions": 0,
            "length_expansion_requested": False,
            "rewrite_forbidden": True,
            "display_text_is_program_slice": True,
            "spoken_text_normaliser_bypassed": True,
            "annotations_applied": annotations_applied,
            "annotation_model_calls": model_calls,
            "timing_status": "TEXT_BUDGET_HINT_NOT_MEASURED_TTS",
            "spoken_text_dispositions": [
                {
                    "canonical_segment_id": item["canonical_segment_id"],
                    "disposition": "PROGRAM_DERIVED_FROM_EXPLICIT_MAP" if item["pronunciation_map"] else "IDENTICAL",
                }
                for item in segments
            ],
        }
        plan["plan_hash"] = content_hash(
            {
                "script_source_hash": plan["script_source_hash"],
                "span_map": plan["span_map"],
                "segments": [
                    (item["canonical_segment_id"], item["display_text"], item["spoken_text"])
                    for item in segments
                ],
                "script_policy": plan["script_policy"],
            }
        )
        plan["provenance"] = {
            "script_policy": ScriptPolicy.PRESERVE_ORIGINAL.value,
            "preserved_original": True,
            "script_source_text": plan["script_source_text"],
            "script_source_hash": plan["script_source_hash"],
            "leading_separator": plan["leading_separator"],
            "segment_spans": plan["span_map"],
            "max_script_revisions": 0,
            "length_expansion_requested": False,
            "spoken_text_never_rewritten_by_normaliser": True,
            "annotations_applied": annotations_applied,
            "plan_hash": plan["plan_hash"],
        }
        return plan

    def _preserved_source(self, repo: ExplainerRepository, video_id: str) -> str | None:
        """The exact manuscript of this video, from the revision or the input payload.

        Deliberately does *not* reconstruct the body from evidence spans: those
        are trimmed, offset-normalised fragments and their concatenation is not
        the user's manuscript, so offering it as "the original" would silently
        change the text the preserved mode promised to protect.
        """

        preserved = repo.preserved_script_revision(video_id)
        if preserved is not None:
            provenance = preserved.get("provenance_json")
            if isinstance(provenance, Mapping) and provenance.get("script_source_text"):
                return str(provenance["script_source_text"])
        payload = repo.video_input_payload(video_id)
        exact = payload.get("preserved_original")
        if isinstance(exact, Mapping) and exact.get("script_source_text"):
            return str(exact["script_source_text"])
        pasted = payload.get("pasted_text")
        if isinstance(pasted, Mapping) and pasted.get("text"):
            return str(pasted["text"])
        return None

    def _preserved_annotation_call(
        self,
        *,
        repo: ExplainerRepository,
        video: Mapping[str, Any],
        built: Mapping[str, Any],
        claim_codes: Sequence[str],
        entity_codes: Sequence[str],
        pronunciation_map: Sequence[Mapping[str, str]],
    ) -> dict[str, Any]:
        """Ask the local model for annotations only (never for the body)."""

        segments_json = [
            {
                "canonical_segment_id": item["canonical_segment_id"],
                "display_text": item["display_text"],
                "source_start": item["source_start"],
                "source_end": item["source_end"],
            }
            for item in built["segments"]
        ]
        claims = repo.list_where("explainer_claims", {"video_id": str(video["id"])}, order_by="code")
        entities = repo.list_where("explainer_entities", {"video_id": str(video["id"])}, order_by="code")
        user = render_prompt(
            PRESERVED_ANNOTATION_USER,
            script_source_hash=built["script_source_hash"],
            segments_json=segments_json,
            claims_json=_catalogue(claims, ("code", "statement", "status"), limit=160),
            entities_and_pronunciations_json={
                "entities": _catalogue(entities, ("code", "name", "entity_type"), limit=120),
                "known_claim_codes": list(claim_codes),
                "known_entity_codes": list(entity_codes),
                "pronunciation_dictionary": [dict(pair) for pair in pronunciation_map],
            },
        )
        raw = self._chat(
            system=PRESERVED_ANNOTATION_SYSTEM,
            user=user,
            schema=contract_schema_for_model("preserved-script-annotations.v1"),
            max_tokens=8_000,
            num_ctx=32_768,
        )
        validated, _repairs = validate_with_single_repair(raw, contract="preserved-script-annotations.v1")
        return validated.model_dump() if hasattr(validated, "model_dump") else dict(validated)

    @staticmethod
    def _apply_preserved_annotations(
        *,
        segments: Sequence[Mapping[str, Any]],
        annotations: Mapping[str, Any],
        claim_codes: set[str],
        entity_codes: set[str],
    ) -> list[dict[str, Any]]:
        """Attach validated annotations to program-owned segments."""

        validated = validate_contract("preserved-script-annotations.v1", annotations)
        assert_one_annotation_per_segment(
            validated.annotations,
            required_segment_ids=[str(item["canonical_segment_id"]) for item in segments],
        )
        assert_unverified_phrases_are_substrings(
            validated.annotations,
            segment_texts={str(item["canonical_segment_id"]): str(item["display_text"]) for item in segments},
        )
        breaks = {str(item) for item in validated.chapter_break_before_segment_ids}
        by_id = {item.canonical_segment_id: item for item in validated.annotations}
        output: list[dict[str, Any]] = []
        for segment in segments:
            annotation = by_id[str(segment["canonical_segment_id"])]
            assert_allowed_ids(annotation.claim_ids, allowed=claim_codes, field="claim_ids")
            assert_allowed_ids(annotation.entity_ids, allowed=entity_codes, field="entity_ids")
            merged = [dict(pair) for pair in segment["pronunciation_map"]]
            known = {pair["display"] for pair in merged}
            for suggestion in annotation.pronunciation_suggestions:
                if suggestion.display in known:
                    continue
                if suggestion.display not in str(segment["display_text"]):
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "读音建议的 display 必须是本段正文的实际片段",
                        {
                            "canonical_segment_id": segment["canonical_segment_id"],
                            "display": suggestion.display,
                        },
                    )
                merged.append({"display": suggestion.display, "spoken": suggestion.spoken})
                known.add(suggestion.display)
            spoken = apply_pronunciation_map(str(segment["display_text"]), merged)
            output.append(
                {
                    **dict(segment),
                    "statement_type": annotation.statement_type.value,
                    "claim_ids": list(annotation.claim_ids),
                    "entity_ids": list(annotation.entity_ids),
                    "pause_after_ms": int(annotation.pause_after_ms),
                    "pronunciation_map": merged,
                    "spoken_text": spoken,
                    "chapter_break_before": str(segment["canonical_segment_id"]) in breaks,
                    "unverified_phrases": [item.model_dump() for item in annotation.unverified_phrases],
                }
            )
        return output

    # ------------------------------------------------------------------ beats
    def plan_storyboard(
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        usable_render_types: Sequence[str],
    ) -> dict[str, Any]:
        video = self._video(repo, project_id, video_id)
        script_revision_id = str(video.get("current_script_revision_id") or "")
        if not script_revision_id:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "还没有冻结的讲稿修订，无法生成分镜", {"video_id": video_id}
            )
        segments = repo.segments(script_revision_id)
        if not segments:
            raise ExplainerContractError("SCHEMA_INVALID", "讲稿修订里没有叙述段落", {"video_id": video_id})
        entities = repo.list_where("explainer_entities", {"video_id": video_id}, order_by="code", descending=False)
        claims = repo.list_where("explainer_claims", {"video_id": video_id}, order_by="code", descending=False)
        allowed = [str(item) for item in usable_render_types if str(item)]
        if not allowed:
            raise ExplainerContractError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "没有任何可用的画面生成方式，无法生成分镜",
                {"video_id": video_id},
            )
        segment_fields = (
            "canonical_segment_id",
            "display_text",
            "statement_type",
            "claim_ids_json",
            "speaker",
        )
        entity_catalogue = _catalogue(entities, ("code", "name", "entity_type"), limit=60)
        is_fiction = str(video["content_kind"]) == ContentKind.ORIGINAL_FICTION.value
        # Every segment is planned, in batches, in narration order.  The old code
        # handed the model one catalogue capped at ``MAX_CATALOGUE_ITEMS`` and then
        # reported the dropped tail as "uncovered" while still returning PASS, so a
        # 320-segment script could become a film covering only its first 160.
        batches = _segment_batches(segments)
        # Several batches mean several model calls contribute to one plan, so the
        # model's per-batch ``B001`` numbering is namespaced before the merge.  A
        # single batch keeps the codes exactly as the model wrote them.
        namespaced = len(batches) > 1
        segment_ids = {str(item["canonical_segment_id"]) for item in segments}
        entity_codes = {str(item["code"]) for item in entities}
        claim_codes = {str(item["code"]) for item in claims}
        motion_capable = {"I2V", "LICENSED_MEDIA"}
        motion_promoted: list[dict[str, Any]] = []
        beats: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        batch_reports: list[dict[str, Any]] = []
        for batch_no, batch in enumerate(batches, start=1):
            segment_catalogue = _catalogue(batch, segment_fields, limit=len(batch))
            scope_hint = (
                f"本批是全部 {len(batches)} 批中的第 {batch_no} 批，共 {len(batch)} 段。"
                "只为下面列出的段落编排画面段，不要引用也不要用假设补写其它批次的段落。\n"
                if namespaced
                else ""
            )
            user = (
                f"作品标题：{video['title']}\n目标时长：约 {int(video['target_seconds'])} 秒\n"
                f"内容属性：{'原创虚构' if is_fiction else '事实解说'}\n"
                f"本次可用画面生成方式（render_type 只能取这些值）：{', '.join(allowed)}\n"
                "本片所有活动画面一律由真实的 AI 图生视频（I2V）产生；不存在“静图推拉”或任何静图动效方案。\n\n"
                "请把下面的叙述段落编排成画面段 beats：\n"
                "- code 用 B001 起的编号。\n"
                "- segment_ids 引用下面真实存在的段落编号；一句话可以跨两个画面段，"
                "一个画面段也可以承载相邻两句，不要机械地每 5 秒换一张无关图。\n"
                "- render_type 按下面的规则选择：需要活动画面的镜头用 I2V（真实图生视频）；"
                "数据、关系、流程用 INFOGRAPHIC；确有真实素材可用时才用 LICENSED_MEDIA。"
                "不要规划任何静图加推拉/位移的画面方式，也不要把静图动效当作 I2V 的替代或降级。"
                "推镜与位移不是人物动作，不能用推镜或位移冒充“必须发生的人物运动”。\n"
                "- visual_intent 写这个画面要让观众看到什么（用一句中文）。\n"
                "- visual_factuality：有来源照片或原始记录用 DOCUMENTED；依据记载的画面重建用 RECONSTRUCTION；"
                "抽象示意用 SYMBOLIC；虚构编排用 FICTIONAL。\n"
                "- entity_codes 只能取下面实体名录里的编号；claim_codes 只能取事实编号。\n"
                "- must_be_motion 只在核心动作或关键转折上设为 true；"
                "设为 true 的画面段 render_type 必须是 I2V，"
                "如果本次可用类型里没有 I2V，就保持 must_be_motion 为 false 并在能力限制里报告，"
                "不能把必须运动悄悄改成不运动。\n"
                "- 本批列出的每一个段落都必须至少被一个画面段引用，不能遗漏任何段落编号。\n"
                "- prompt_intent 写一段可直接用于生成画面提示的意图描述，只描述画面，不要写入任何可读文字内容"
                "（文字由确定性排版层绘制）；首帧要写清主体位置、构图与光线，运动只写从首帧开始的单一动作与一种镜头运动，"
                "与项目语言一致使用中文。\n\n"
                f"{scope_hint}"
                f"叙述段落清单：\n{json.dumps(segment_catalogue, ensure_ascii=False)}\n\n"
                f"实体名录：\n{json.dumps(entity_catalogue, ensure_ascii=False)}\n\n"
                f"事实编号：{json.dumps([str(item['code']) for item in claims], ensure_ascii=False)}"
            )
            result = self._chat(
                system=self.prompts.storyboard_system,
                user=user,
                schema=BEAT_SCHEMA,
                max_tokens=14_000,
                num_ctx=49_152,
            )
            raw_beats = result.get("beats") or []
            if not raw_beats:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "文本模型没有返回任何画面段",
                    {"batch": batch_no, "batch_count": len(batches)},
                )
            if len(raw_beats) > MAX_BEATS:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "画面段数量超过上限", {"count": len(raw_beats), "limit": MAX_BEATS}
                )
            namespace = f"B{batch_no:02d}-" if namespaced else ""
            for index, raw in enumerate(raw_beats, start=1):
                raw_code = str(raw.get("code") or f"B{index:03d}").strip()
                code = f"{namespace}{raw_code}"
                if code in seen_codes:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "文本模型返回了重复的画面段编号",
                        {"code": code, "batch": batch_no},
                    )
                seen_codes.add(code)
                render_type = str(raw.get("render_type") or "").strip()
                if render_type not in allowed:
                    raise ExplainerContractError(
                        ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                        "文本模型选择了本次不可用的画面生成方式",
                        {"code": code, "render_type": render_type, "usable_render_types": allowed},
                    )
                linked = [str(item).strip() for item in (raw.get("segment_ids") or []) if str(item).strip()]
                unknown_segments = sorted(set(linked) - segment_ids)
                if unknown_segments:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "画面段引用了不存在的叙述段落",
                        {"code": code, "unknown_segment_ids": unknown_segments},
                    )
                referenced_entities = [
                    str(item).strip() for item in (raw.get("entity_codes") or []) if str(item).strip()
                ]
                unknown_entities = sorted(set(referenced_entities) - entity_codes)
                if unknown_entities:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "画面段引用了不存在的实体",
                        {"code": code, "unknown_entity_codes": unknown_entities},
                    )
                referenced_claims = [
                    str(item).strip() for item in (raw.get("claim_codes") or []) if str(item).strip()
                ]
                unknown_claims = sorted(set(referenced_claims) - claim_codes)
                if unknown_claims:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "画面段引用了不存在的事实编号",
                        {"code": code, "unknown_claim_codes": unknown_claims},
                    )
                must_be_motion = bool(raw.get("must_be_motion"))
                coerced = False
                if must_be_motion and render_type not in motion_capable:
                    # A beat that must really move cannot be planned as a still
                    # picture or a static graphic.  The retired behaviour silently
                    # dropped the motion requirement and kept the still type, which
                    # is exactly the 静图推拉 substitution the product no longer
                    # offers.  Promote the beat to the one real motion type when
                    # this run can execute it, and refuse the plan otherwise rather
                    # than quietly pretending the action is not required.
                    if "I2V" in allowed:
                        render_type = "I2V"
                        coerced = True
                    else:
                        raise ExplainerContractError(
                            ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                            "画面段要求真实运动，但本次没有可执行的图生视频能力",
                            {
                                "code": code,
                                "declared_render_type": str(raw.get("render_type") or ""),
                                "usable_render_types": allowed,
                            },
                        )
                if coerced:
                    motion_promoted.append({"code": code, "render_type": render_type})
                beats.append(
                    {
                        "code": code,
                        "render_type": render_type,
                        "segment_canonical_ids": linked,
                        "entity_codes": referenced_entities,
                        "claim_codes": referenced_claims,
                        "visual_intent": str(raw.get("visual_intent") or "").strip(),
                        "visual_factuality": str(
                            raw.get("visual_factuality") or VisualFactuality.RECONSTRUCTION.value
                        ),
                        "must_be_motion": must_be_motion,
                        "motion_requirement_coerced": coerced,
                        "prompt_intent": str(raw.get("prompt_intent") or "").strip(),
                    }
                )
            batch_reports.append(
                {
                    "batch": batch_no,
                    "batch_count": len(batches),
                    "segment_count": len(batch),
                    "beat_count": len(raw_beats),
                    "first_segment_id": str(batch[0]["canonical_segment_id"]),
                    "last_segment_id": str(batch[-1]["canonical_segment_id"]),
                }
            )
        if len(beats) > MAX_BEATS:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "画面段数量超过上限",
                {"count": len(beats), "limit": MAX_BEATS, "batch_count": len(batches)},
            )
        covered = {item for beat in beats for item in beat["segment_canonical_ids"]}
        uncovered = sorted(segment_ids - covered)
        if uncovered:
            # Design §5.3: the plan either covers the whole frozen script or it is
            # refused.  Returning PASS with a reported-but-ignored shortfall is how
            # a 320-segment script became a film that silently dropped its tail.
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "画面段计划没有覆盖全部叙述段落，已拒绝该计划",
                {
                    "video_id": video_id,
                    "script_revision_id": script_revision_id,
                    "uncovered_segment_ids": uncovered,
                    "uncovered_count": len(uncovered),
                    "segment_count": len(segment_ids),
                    "batch_count": len(batches),
                },
            )
        ordinal_by_canonical = {
            str(item["canonical_segment_id"]): int(item.get("ordinal") or 0) for item in segments
        }
        previous_ordinal = -1
        for beat in beats:
            positions = [
                ordinal_by_canonical[item]
                for item in beat["segment_canonical_ids"]
                if item in ordinal_by_canonical
            ]
            if not positions:
                continue
            if min(positions) < previous_ordinal:
                # Beats may overlap (a shared sentence, or one picture carrying two
                # adjacent sentences) but they may not run backwards: the picture
                # clock is allocated in beat order, so a reversed plan would put
                # later narration before earlier narration.
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "画面段顺序与旁白顺序不一致，已拒绝该计划",
                    {
                        "beat_code": str(beat["code"]),
                        "segment_ordinal": min(positions),
                        "previous_segment_ordinal": previous_ordinal,
                    },
                )
            previous_ordinal = max(previous_ordinal, min(positions))
        return {
            "status": "PASS",
            "beats": beats,
            "beat_count": len(beats),
            "uncovered_segment_ids": uncovered,
            "covered_segment_count": len(covered),
            "segment_count": len(segment_ids),
            "batch_count": len(batches),
            "batches": batch_reports,
            "usable_render_types": allowed,
            "motion_promoted_beats": motion_promoted,
            "mapping_is_many_to_many": True,
            "plan_hash": content_hash(
                {"beats": beats, "script_revision_id": script_revision_id, "usable": allowed}
            ),
        }


    # -------------------------------------------------------- reference design
    def plan_reference_design(
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        requested: Sequence[Mapping[str, Any]],
        style: Sequence[str] = (),
        adopted_references: Sequence[Mapping[str, Any]] = (),
        user_visual_settings: Mapping[str, Any] | None = None,
        allow_creative_choices: bool = False,
        polish_with_model: bool = False,
    ) -> dict[str, Any]:
        """Compile independent setting prompts for people/scenes/props (§C4.4).

        Deterministic by default: when the known attributes, user settings,
        adopted-reference invariants and style are already present, no model call
        is made at all.  The compile order is fixed
        (asset-kind purpose → known appearance/space → user settings → adopted
        invariants → style → reference view/composition requirements), each field
        is limited and de-duplicated *before* concatenation, and a field over
        budget is reported by name instead of being cut — so the trailing
        identity/view hard requirements can never be truncated away.

        The plan never reads a final beat ID: it works from the entity records and
        the requested reference kinds, so step 2 cannot depend on step 4.
        """

        video = self._video(repo, project_id, video_id)
        entities = repo.list_where("explainer_entities", {"video_id": video_id})
        by_id = {str(item["id"]): item for item in entities}
        by_code = {str(item.get("code")): item for item in entities}
        settings = dict(user_visual_settings or {})
        adopted_by_entity: dict[str, list[Mapping[str, Any]]] = {}
        for reference in adopted_references:
            adopted_by_entity.setdefault(str(reference.get("entity_id") or ""), []).append(reference)

        items: list[dict[str, Any]] = []
        over_budget: list[dict[str, Any]] = []
        canonical_inputs: list[dict[str, Any]] = []
        for index, request in enumerate(requested):
            entity_id = str(request.get("entity_id") or "")
            entity = by_id.get(entity_id) or by_code.get(entity_id)
            if entity is None:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "参考设定请求引用了本项目不存在的人物/场景",
                    {"entity_id": entity_id, "video_id": video_id, "request_index": index},
                )
            canonical_id = str(entity["id"])
            asset_kind = str(request.get("asset_kind") or _asset_kind_for_entity(entity))
            reference_kind = str(request.get("reference_kind") or "HERO")
            known = _known_attributes_for(entity)
            user_settings_for_entity = _setting_fragments(settings.get(canonical_id) or settings.get(entity_id))
            adopted = [
                *(str(item.get("invariant") or "") for item in adopted_by_entity.get(canonical_id, [])),
                *(str(item.get("invariant") or "") for item in adopted_by_entity.get(entity_id, [])),
            ]
            media_version_ids = [
                str(item)
                for item in (
                    request.get("reference_media_version_ids")
                    or [
                        media
                        for item in adopted_by_entity.get(canonical_id, [])
                        for media in (item.get("reference_media_version_ids") or [])
                    ]
                )
                if str(item)
            ]
            if media_version_ids:
                repo.require_same_project_media_many(
                    project_id=project_id, media_version_ids=media_version_ids
                )
            unknown_real_person = (
                str(entity.get("entity_type")) == "REAL_PERSON"
                and not known
                and bool(entity.get("descriptive_only"))
            )
            compiled = compile_reference_design(
                entity_name=str(entity.get("name") or ""),
                asset_kind=asset_kind,
                reference_kind=reference_kind,
                known_attributes=known,
                user_settings=user_settings_for_entity,
                adopted_invariants=[item for item in adopted if item],
                style=style,
                description_prompt=str(request.get("description_prompt") or ""),
                allow_creative_choices=allow_creative_choices,
                creative_choices=[dict(item) for item in (request.get("creative_choices") or [])],
                unknown_real_person=unknown_real_person,
                unresolved_constraints=[str(item) for item in (request.get("unresolved_constraints") or [])],
            )
            if compiled["over_budget_fields"]:
                over_budget.extend(
                    [{**item, "entity_id": canonical_id, "reference_kind": reference_kind} for item in compiled["over_budget_fields"]]
                )
            items.append(
                {
                    "entity_id": canonical_id,
                    "asset_kind": asset_kind,
                    "reference_kind": reference_kind,
                    "description_prompt": compiled["description_prompt"],
                    "negative_prompt": compiled["negative_prompt"],
                    "known_attribute_keys": [str(item) for item in (request.get("known_attribute_keys") or [])],
                    "user_setting_keys": [str(item) for item in (request.get("user_setting_keys") or [])],
                    "reference_media_version_ids": media_version_ids,
                    "creative_choices": compiled["creative_choices"],
                    "unresolved_constraints": compiled["unresolved_constraints"],
                    "prompt_hash": compiled["prompt_hash"],
                    "compile_order": compiled["compile_order"],
                    "reference_view_hard_requirements_tail_kept": True,
                }
            )
            canonical_inputs.append(
                {
                    "entity_id": canonical_id,
                    "entity_name": str(entity.get("name") or ""),
                    "asset_kind": asset_kind,
                    "reference_kind": reference_kind,
                    "known_attributes": known,
                    "user_settings": user_settings_for_entity,
                    "adopted_invariants": [item for item in adopted if item],
                    "style": list(style),
                    "unknown_real_person": unknown_real_person,
                }
            )

        if over_budget:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "参考设定字段超过预算，请缩短对应字段后重试（不做截断）",
                {"over_budget_fields": over_budget},
            )
        if not items:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "没有需要整理的参考设定请求", {"video_id": video_id}
            )
        model_calls = 0
        polish_note: str | None = None
        if polish_with_model:
            # Optional model tidying (§C4.4): the model may only re-word the
            # descriptive part, in the same contract shape, and the program still
            # appends the fixed type/view/style hard requirements afterwards.
            if not self._model_available():
                polish_note = "MODEL_UNAVAILABLE_DETERMINISTIC_COMPILE_USED"
            else:
                polished, model_calls = self._polish_reference_design(
                    video=video,
                    items=items,
                    canonical_inputs=canonical_inputs,
                    style=style,
                    allow_creative_choices=allow_creative_choices,
                )
                if polished is not None:
                    items = polished
        design = {"schema_version": REFERENCE_DESIGN_SCHEMA_VERSION, "items": items}
        # The response contract is the same one the model would fill, so the
        # deterministic path and the model path cannot diverge in shape.
        assert_reference_design_coverage(
            design,
            requested=[{"entity_id": item["entity_id"], "reference_kind": item["reference_kind"]} for item in items],
        )
        input_hash = reference_design_input_hash({"items": canonical_inputs, "allow_creative_choices": bool(allow_creative_choices)})
        return {
            "status": "PASS",
            "design": design,
            "items": items,
            "item_count": len(items),
            "input_hash": input_hash,
            "compiled_deterministically": model_calls == 0,
            "model_calls": model_calls,
            "model_polish_note": polish_note,
            "polish_with_model": bool(polish_with_model),
            "style_version_independent_of_beats": True,
            "deterministic_compile_order": list(items[0]["compile_order"]) if items else [],
            "facts_ledger_untouched": True,
            "creative_choices_are_visual_settings_only": True,
        }

    def _polish_reference_design(
        self,
        *,
        video: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        canonical_inputs: Sequence[Mapping[str, Any]],
        style: Sequence[str],
        allow_creative_choices: bool,
    ) -> tuple[list[dict[str, Any]] | None, int]:
        """Let the local model re-word the descriptive part of each item.

        The reply must satisfy ``reference-design.v1``; the program then re-runs
        the deterministic compiler with the model's prose as the descriptive
        fragment, so the fixed type/view/style hard requirements still come last
        and an over-budget field is still reported instead of truncated.
        """

        entity_by_id = {str(item["entity_id"]): item for item in items}
        inputs_by_id = {str(item["entity_id"]): item for item in canonical_inputs}
        user = render_contract_prompt(
            "reference-design.v1",
            reference_design_policy_json={
                "allow_creative_choices": bool(allow_creative_choices),
                "prompt_version": PROMPT_VERSION,
            },
            requested_assets_and_views_json=[
                {
                    "entity_id": str(item["entity_id"]),
                    "asset_kind": str(item["asset_kind"]),
                    "reference_kind": str(item["reference_kind"]),
                }
                for item in items
            ],
            entities_known_appearance_json=list(canonical_inputs),
            user_visual_settings_json={
                str(item["entity_id"]): list(item.get("user_settings") or []) for item in canonical_inputs
            },
            style_snapshot_json=list(style),
            adopted_reference_snapshot_json=[],
            asset_spec_and_capability_json={
                "asset_kinds": sorted({str(item["asset_kind"]) for item in items}),
                "reference_kinds": sorted({str(item["reference_kind"]) for item in items}),
            },
        )[1]
        raw = self._chat(
            system=REFERENCE_DESIGN_SYSTEM,
            user=user,
            schema=contract_schema_for_model("reference-design.v1"),
            max_tokens=8_000,
            num_ctx=32_768,
        )
        validated = validate_contract("reference-design.v1", raw)
        rebuilt: list[dict[str, Any]] = []
        for response_item in validated.items:
            current = entity_by_id.get(response_item.entity_id)
            source = inputs_by_id.get(response_item.entity_id)
            if current is None or source is None:
                # A reply for something we did not ask for is ignored rather than
                # allowed to replace a requested asset's setting.
                continue
            if response_item.reference_kind.value != str(current["reference_kind"]):
                continue
            assert_allowed_ids(
                response_item.reference_media_version_ids,
                allowed=current.get("reference_media_version_ids") or [],
                field="reference_media_version_ids",
            )
            # The model may only re-word the descriptive part: every program-owned
            # field (name, known attributes, user settings, adopted invariants,
            # style, view hard requirements) is recompiled deterministically.
            compiled = compile_reference_design(
                entity_name=str(source.get("entity_name") or ""),
                asset_kind=str(current["asset_kind"]),
                reference_kind=str(current["reference_kind"]),
                known_attributes=list(source.get("known_attributes") or []),
                user_settings=list(source.get("user_settings") or []),
                adopted_invariants=list(source.get("adopted_invariants") or []),
                style=list(style),
                description_prompt=response_item.description_prompt,
                allow_creative_choices=allow_creative_choices,
                creative_choices=[item.model_dump() for item in response_item.creative_choices],
                unknown_real_person=bool(source.get("unknown_real_person")),
                unresolved_constraints=list(response_item.unresolved_constraints),
                negative_prompt=[response_item.negative_prompt],
            )
            if compiled["over_budget_fields"]:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "参考设定字段超过预算，请缩短对应字段后重试（不做截断）",
                    {
                        "over_budget_fields": [
                            {**entry, "entity_id": response_item.entity_id, "polish_with_model": True}
                            for entry in compiled["over_budget_fields"]
                        ]
                    },
                )
            rebuilt.append(
                {
                    **dict(current),
                    "description_prompt": compiled["description_prompt"],
                    "negative_prompt": compiled["negative_prompt"],
                    "creative_choices": compiled["creative_choices"],
                    "unresolved_constraints": compiled["unresolved_constraints"],
                    "prompt_hash": compiled["prompt_hash"],
                    "model_polished": True,
                }
            )
        if not rebuilt:
            return None, 1
        by_id = {str(item["entity_id"]): item for item in rebuilt}
        ordered = [by_id.get(str(item["entity_id"]), item) for item in items]
        return ordered, 1

    # ------------------------------------------------------------ story seed
    def plan_story_seed(
        self,
        *,
        repo: ExplainerRepository,
        project_id: str,
        video_id: str,
        register: bool = True,
        revision_request: str = "",
        creative_scope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create the bounded authored-fiction setting document (§C4.5).

        Only ``CREATE_FROM_TOPIC`` + ``ORIGINAL_FICTION`` may call this: a factual
        topic must go through :meth:`plan_research`, which records real fetched
        sources and never invents a URL.  The seed's premise, characters and
        causal step order are program-validated, serialised deterministically into
        a full setting document, and registered through the ordinary source path as
        ``source_kind=AUTHORED_FICTION_PACK`` / ``credibility_kind=AUTHORED_FICTION``
        with the model/prompt/input hashes.  It can never receive a
        fact-verification status, and the same input hash reuses the finished seed.
        """

        video = self._video(repo, project_id, video_id)
        policy = resolve_script_policy(video.get("input_payload_json", {}).get("script_policy"))
        content_kind = str(video.get("content_kind"))
        if policy != ScriptPolicy.CREATE_FROM_TOPIC.value or content_kind != ContentKind.ORIGINAL_FICTION.value:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "plan_story_seed 只用于 CREATE_FROM_TOPIC + ORIGINAL_FICTION；事实主题请使用 plan_research",
                {"script_policy": policy, "content_kind": content_kind},
            )
        scope = dict(creative_scope or {})
        seed_input_hash = fiction_seed_input_hash(
            topic=str(video.get("topic") or video.get("title") or ""),
            target_seconds=int(video["target_seconds"]),
            source_locale=str(video["source_locale"]),
            story_tone=str(scope.get("story_tone") or ""),
            allowed_settings=tuple(str(item) for item in (scope.get("allowed_settings") or [])),
            forbidden_settings=tuple(str(item) for item in (scope.get("forbidden_settings") or [])),
            character_limit=scope.get("character_limit"),
            revision_request=revision_request,
        )
        existing = repo.story_seed_source(video_id, input_hash=seed_input_hash)
        if existing is not None:
            rights = existing.get("rights_json")
            rights = dict(rights) if isinstance(rights, Mapping) else {}
            return {
                "status": "PASS",
                "reused": True,
                "seed_input_hash": seed_input_hash,
                "source_id": str(existing["id"]),
                "source_kind": str(existing.get("source_kind")),
                "credibility_kind": str(existing.get("credibility_kind")),
                "seed": rights.get("seed"),
                "setting_document": rights.get("setting_document"),
                "model_calls": 0,
                "verified_as_history": False,
            }

        user = render_contract_prompt(
            "fiction-seed.v1",
            topic_json={
                "title": str(video["title"]),
                "topic": str(video.get("topic") or ""),
                "content_kind": content_kind,
            },
            story_request_json={
                "target_seconds": int(video["target_seconds"]),
                "source_locale": str(video["source_locale"]),
                "story_tone": str(scope.get("story_tone") or ""),
            },
            fixed_story_constraints_json=dict(scope.get("fixed_constraints") or {}),
            creative_scope_json={
                "allowed_settings": list(scope.get("allowed_settings") or []),
                "forbidden_settings": list(scope.get("forbidden_settings") or []),
                "character_limit": scope.get("character_limit"),
            },
            previous_seed_json=scope.get("previous_seed"),
            revision_request_json=revision_request,
        )
        raw = self._chat(
            system=FICTION_SEED_SYSTEM,
            user=user,
            schema=contract_schema_for_model("fiction-seed.v1"),
            max_tokens=8_000,
            num_ctx=32_768,
        )
        validated, repairs = validate_with_single_repair(raw, contract="fiction-seed.v1")
        seed = validated.model_dump() if hasattr(validated, "model_dump") else dict(validated)
        checks = validate_fiction_seed(seed)
        if not checks["freezable"]:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "故事种子不能满足用户的题材/约束，先处理 scope_conflicts 再冻结",
                {"scope_conflicts": checks["scope_conflicts"]},
            )
        document = fiction_seed_setting_document(seed)
        source: dict[str, Any] | None = None
        if register:
            # Port-style factory: the concrete service is named in the ``make_*``
            # factory below, which is the exempt composition scope.
            service = make_research_service(repo)
            packet = repo.list_where(
                "explainer_research_packets",
                {"video_id": video_id},
                order_by="revision_no",
                descending=True,
                limit=1,
            )
            if not packet:
                created = service.create_packet(
                    project_id=project_id,
                    video_id=video_id,
                    mode=str(video.get("research_mode") or "OFFLINE_IMPORT"),
                    topic=str(video.get("topic") or video.get("title") or ""),
                    allowed_domains=[
                        str(item) for item in (video.get("research_allowed_domains_json") or [])
                    ],
                    max_external_requests=0,
                )
                packet = [created]
            source = service.import_document(
                project_id=project_id,
                video_id=video_id,
                packet_id=str(packet[0]["id"]),
                text=document,
                title=f"原创虚构设定：{seed.get('title') or video['title']}",
                source_kind="AUTHORED_FICTION_PACK",
                rights={
                    "credibility_kind": "AUTHORED_FICTION",
                    "verified_as_history": False,
                    "seed_input_hash": seed_input_hash,
                    "seed": seed,
                    "setting_document": document,
                    "prompt_version": PROMPT_VERSION,
                    "model_identity": str(getattr(self, "model_identity", "") or ""),
                    "prompt_hash": content_hash({"system": FICTION_SEED_SYSTEM, "user": user}),
                    "response_hash": content_hash(seed),
                    "format_repairs": repairs,
                },
            )
        return {
            "status": "PASS",
            "reused": False,
            "seed": seed,
            "setting_document": document,
            "seed_input_hash": seed_input_hash,
            "source_id": str(source["id"]) if source else None,
            "source_kind": "AUTHORED_FICTION_PACK",
            "credibility_kind": "AUTHORED_FICTION",
            "checks": checks,
            "model_calls": 1,
            "schema_version": FICTION_SEED_SCHEMA_VERSION,
            "verified_as_history": False,
            "fact_verification_status": None,
            "prompt_version": PROMPT_VERSION,
            "format_repairs": repairs,
        }


# --------------------------------------------------------------------------- #
# stage handlers
# --------------------------------------------------------------------------- #
def _payload(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("semantic_inputs")
    return value if isinstance(value, Mapping) else {}


def _require(payload: Mapping[str, Any], key: str, *, stage: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{stage} 缺少必需输入 {key}", {"semantic_inputs": sorted(payload)}
        )
    return value


def make_research_acquire_handler(
    planner_factory: Callable[[], ExplainerStagePlanner],
    repo_factory: Callable[[], Any],
    *,
    read_repo_factory: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``RESEARCH_ACQUIRE``: plan the research queries and reference keywords."""

    reader = read_repo_factory or repo_factory

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = _payload(context)
        project_id = _require(payload, "project_id", stage="RESEARCH_ACQUIRE")
        video_id = _require(payload, "video_id", stage="RESEARCH_ACQUIRE")
        with repo_factory() as repo:
            packet_id = str(payload.get("packet_id") or "").strip()
            if not packet_id:
                packets = repo.list_where(
                    "explainer_research_packets",
                    {"video_id": video_id},
                    order_by="revision_no",
                    descending=True,
                    limit=1,
                )
                if not packets:
                    # A topic-driven work has no imported source document, but the
                    # stage still owns "来源与资料包": its topic declaration *is* the
                    # research scope, and the stage must materialise that scope
                    # rather than refuse to run.  Offline mode keeps the external
                    # request budget at zero, so this never opens a network call.
                    video = repo.get("explainer_videos", video_id)
                    created = make_research_service(repo).create_packet(
                        project_id=project_id,
                        video_id=video_id,
                        mode=str(video.get("research_mode") or "OFFLINE_IMPORT"),
                        topic=str(video.get("topic") or video.get("title") or ""),
                        allowed_domains=[
                            str(item) for item in (video.get("research_allowed_domains_json") or [])
                        ],
                        max_external_requests=0,
                    )
                    packets = [created]
                packet_id = str(packets[0]["id"])
                sources = repo.list_where("explainer_sources", {"packet_id": packet_id})
                if not sources:
                    video = repo.get("explainer_videos", video_id)
                    input_kind = str(video.get("input_kind") or "")
                    script_text = planner_factory()._preserved_source(repo, video_id)
                    if not script_text and video.get("topic"):
                        script_text = str(video.get("topic"))
                    if script_text:
                        title = str(video.get("title") or "讲稿原稿")
                        make_research_service(repo).import_document(
                            project_id=project_id,
                            video_id=video_id,
                            packet_id=packet_id,
                            text=script_text,
                            title=f"{title}（文稿）" if input_kind == "PASTED_SCRIPT" else f"{title}（主题）",
                            source_kind="DOCUMENT_IMPORT",
                            retrieved_via="OFFLINE_IMPORT",
                        )
        # The model call runs on a read-only connection: holding a write
        # transaction across a multi-minute local inference blocks the job's own
        # lease heartbeat, and the lease then expires while the model is thinking.
        with reader() as repo:
            video = repo.get("explainer_videos", video_id)
            policy = resolve_script_policy((video.get("input_payload_json") or {}).get("script_policy"))
            content_kind = str(video.get("content_kind") or "")
            # §C4.5: a pure topic with no material has nothing to acquire, so the
            # original-fiction branch creates the bounded setting document first and
            # registers it as an AUTHORED_FICTION_PACK source.  The factual branch keeps
            # using plan_research, which records real fetched sources and never invents
            # a URL — a factual topic without material must still report that gap.
            seed_result: dict[str, Any] | None = None
            if (
                policy == ScriptPolicy.CREATE_FROM_TOPIC.value
                and content_kind == ContentKind.ORIGINAL_FICTION.value
            ):
                seed_result = planner_factory().plan_story_seed(
                    repo=repo, project_id=project_id, video_id=video_id
                )
            result = planner_factory().plan_research(
                repo=repo, project_id=project_id, video_id=video_id, packet_id=packet_id
            )
        if seed_result is not None:
            return {
                "status": seed_result.get("status", "PASS"),
                "machine_check": {"status": "PASS", "ok": True},
                "produced": {
                    "research_plan": result,
                    "packet_id": packet_id,
                    "story_seed": seed_result,
                    "source_kind": "AUTHORED_FICTION_PACK",
                    "credibility_kind": "AUTHORED_FICTION",
                    "verified_as_history": False,
                    "queries_not_sent": True,
                },
                "summary": (
                    "已生成原创虚构故事设定并登记为本片原创设定来源"
                    f"（{len(seed_result.get('characters') or [])} 个人物、"
                    f"{len(seed_result.get('story_steps') or [])} 个故事步骤）；"
                    "该设定不会获得史实验证状态。"
                ),
            }
        return {
            "status": result.get("status", "PASS"),
            "machine_check": {"status": "PASS", "ok": True},
            "produced": {
                "research_plan": result,
                "packet_id": packet_id,
                "queries_not_sent": True,
            },
            "summary": (
                f"已生成 {len(result.get('queries') or [])} 条检索词与 "
                f"{len(result.get('reference_keywords') or [])} 个定位关键词（本阶段不发起外发请求）"
            ),
        }

    return handler


def make_fact_extract_handler(
    planner_factory: Callable[[], ExplainerStagePlanner],
    repo_factory: Callable[[], Any],
    *,
    research_service_factory: Callable[[Any], Any] | None = None,
    read_repo_factory: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``FACT_EXTRACT``: extract and persist the claim ledger, events and entities."""

    reader = read_repo_factory or repo_factory

    def build_research_service(repo: Any) -> Any:
        # Port wiring, not business logic: the caller may inject a double, and the
        # default is the real service over the same repository/transaction.
        if research_service_factory is not None:
            return research_service_factory(repo)
        return ExplainerResearchService(repo)

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = _payload(context)
        project_id = _require(payload, "project_id", stage="FACT_EXTRACT")
        video_id = _require(payload, "video_id", stage="FACT_EXTRACT")
        with repo_factory() as repo:
            packet_id = str(payload.get("packet_id") or "").strip()
            if not packet_id:
                packets = repo.list_where(
                    "explainer_research_packets",
                    {"video_id": video_id},
                    order_by="revision_no",
                    descending=True,
                    limit=1,
                )
                if not packets:
                    raise ExplainerContractError(
                        ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                        "还没有资料包，无法提取事实",
                        {"video_id": video_id},
                    )
                packet_id = str(packets[0]["id"])
        with reader() as repo:
            plan = planner_factory().plan_fact_extraction(
                repo=repo,
                project_id=project_id,
                video_id=video_id,
                packet_id=packet_id,
                artifact_dir=context.get("output_root"),
            )
        with repo_factory() as repo:
            applied = build_research_service(repo).apply_fact_extraction(
                project_id=project_id,
                video_id=video_id,
                packet_id=packet_id,
                extracted=plan["extracted"],
            )
            reconciled = build_research_service(repo).reconcile_claims(video_id=video_id)
        return {
            "status": "PASS",
            "machine_check": {"status": "PASS", "ok": True},
            "produced": {
                "claim_count": plan["claim_count"],
                "event_count": plan["event_count"],
                "entity_count": plan["entity_count"],
                "applied": applied,
                "core_conflicts": reconciled.get("core_conflicts") or [],
                "verified_as_history": False,
                "reference_validation": plan["reference_validation"],
                "chunk_count": plan.get("chunk_count"),
                "chunks_processed_this_run": plan.get("chunks_processed_this_run"),
                "coverage": plan.get("coverage"),
                "coverage_display": plan.get("coverage_display"),
                "analysis_manifest_path": plan.get("analysis_manifest_path"),
                "entity_review_items": plan.get("entity_review_items") or [],
                "full_text_no_prefix_truncation": True,
            },
            "summary": (
                f"已分 {plan.get('chunk_count')} 块分析全文，写入 {plan['claim_count']} 条事实、"
                f"{plan['event_count']} 个事件、{plan['entity_count']} 个实体"
                f"（{plan.get('coverage_display')}）；未解决核心冲突 "
                f"{len(reconciled.get('core_conflicts') or [])} 项"
            ),
        }

    return handler


def make_narration_write_handler(
    planner_factory: Callable[[], ExplainerStagePlanner],
    repo_factory: Callable[[], Any],
    *,
    narration_service_factory: Callable[[Any], Any] | None = None,
    read_repo_factory: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``NARRATION_WRITE``: draft, persist and freeze the narration script."""

    reader = read_repo_factory or repo_factory

    def build_narration_service(repo: Any) -> Any:
        if narration_service_factory is not None:
            return narration_service_factory(repo)
        return ExplainerNarrationService(repo)

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        del job
        payload = _payload(context)
        project_id = _require(payload, "project_id", stage="NARRATION_WRITE")
        video_id = _require(payload, "video_id", stage="NARRATION_WRITE")
        freeze = bool(payload.get("freeze", True))
        artifact_dir = context.get("output_root")
        with reader() as repo:
            # §C4.1: the branch is decided by the frozen processing policy, not by
            # the input channel.  A preserved manuscript never reaches plan_script,
            # so the bounded "expand for length" re-asks cannot run on it.
            policy = resolve_script_policy(repo.stored_script_policy(video_id))
            planner = planner_factory()
            if policy == ScriptPolicy.PRESERVE_ORIGINAL.value:
                plan = planner.plan_preserved_script(
                    repo=repo, project_id=project_id, video_id=video_id
                )
            else:
                plan = planner.plan_script(repo=repo, project_id=project_id, video_id=video_id)
        if plan.get("insufficient_content"):
            # §C4.2/README 3.4: an insufficient-content answer is *not* a script.
            # It must not be frozen, and the stage must not report success.
            return {
                "status": "BLOCKED",
                "machine_check": {"status": "BLOCKED", "ok": False},
                "produced": {
                    "insufficient_content": True,
                    "missing_content_note": plan.get("missing_content_note") or "",
                    "segment_count": 0,
                    "script_revision_id": None,
                    "script_policy": policy,
                    "next_step": "补充资料或缩短目标时长；不得用无来源的因果与例子凑时长。",
                },
                "summary": (
                    "资料不足以在不新增事实的前提下写稿，已停止并保留现有内容："
                    + (plan.get("missing_content_note") or "未说明")
                ),
            }
        with repo_factory() as repo:
            video = repo.get("explainer_videos", video_id)
            service = build_narration_service(repo)
            if plan.get("preserved"):
                existing = repo.preserved_script_revision(
                    video_id, script_source_hash=str(plan["script_source_hash"])
                )
                if existing is not None and str(existing.get("status")) == "FROZEN":
                    revision = existing
                    frozen = {"script_revision": existing}
                    reused = True
                else:
                    created = service.create_script_revision(
                        project_id=project_id,
                        video_id=video_id,
                        locale=str(video["source_locale"]),
                        title=str(video["title"]),
                        outline=[],
                        segments=plan["segments"],
                        status="DRAFT",
                        actor="local-text-planner",
                        provenance=plan["provenance"],
                    )
                    revision = created["script_revision"]
                    frozen = (
                        service.freeze_script(script_revision_id=str(revision["id"]), actor="local-text-planner")
                        if freeze
                        else None
                    )
                    if frozen is not None:
                        repo.update(
                            "explainer_videos", video_id, {"current_script_revision_id": str(revision["id"])}
                        )
                    reused = False
            else:
                created = service.create_script_revision(
                    project_id=project_id,
                    video_id=video_id,
                    locale=str(video["source_locale"]),
                    title=str(video["title"]),
                    outline=plan["outline"],
                    segments=plan["segments"],
                    status="DRAFT",
                )
                revision = created["script_revision"]
                frozen = service.freeze_script(script_revision_id=str(revision["id"]), actor="local-text-planner") if freeze else None
                if frozen is not None:
                    repo.update(
                        "explainer_videos", video_id, {"current_script_revision_id": str(revision["id"])}
                    )
                reused = False
        produced: dict[str, Any] = {
            "script_revision_id": str(revision["id"]),
            "revision_no": revision.get("revision_no"),
            "segment_count": plan["segment_count"],
            "character_count": plan["character_count"],
            "timing_status": plan["timing_status"],
            "frozen": frozen is not None,
            "current_script_revision_updated": frozen is not None,
            "script_policy": policy,
            "reused_existing_revision": reused,
        }
        if plan.get("preserved"):
            produced.update(
                {
                    "preserved": True,
                    "script_source_hash": plan["script_source_hash"],
                    "max_script_revisions": 0,
                    "length_expansion_requested": False,
                    "rewrite_forbidden": True,
                    "span_map_segment_count": len(plan["span_map"]),
                    "annotations_applied": bool(plan.get("annotations_applied")),
                    "presentation": "原稿保字：程序分段，未调用任何改写或扩写",
                }
            )
        else:
            produced["length_repairs"] = plan.get("length_repairs") or []
            # Which contract the script really came from: the design's
            # ``script-draft.v2``, or the legacy segment shape after a contract failure.
            produced["contract_used"] = plan.get("contract_used") or "legacy.segment.v1"
            produced["prompt_used"] = plan.get("prompt_used") or "planner.prompts.script_system"
            if plan.get("v2_fallback_reason"):
                produced["v2_fallback_reason"] = plan["v2_fallback_reason"]
            produced["budget_met"] = bool(plan.get("budget_met"))
        return {
            "status": "PASS",
            "machine_check": {"status": "PASS", "ok": True},
            "produced": produced,
            "summary": (
                (
                    f"已登记原稿保字讲稿 {plan['segment_count']} 段（{plan['character_count']} 字符）；"
                    + ("已冻结为不可变修订" if frozen is not None else "保持草稿")
                )
                if plan.get("preserved")
                else (
                    f"已生成 {plan['segment_count']} 段解说稿（{plan['character_count']} 字符）；"
                    + ("已冻结为不可变修订" if frozen is not None else "保持草稿")
                )
            ),
            "preserved_script_revision_id": (
                str(revision["id"]) if plan.get("preserved") else None
            ),
            "analyzed_source": {"artifact_dir_available": artifact_dir is not None},
        }

    return handler


def make_storyboard_handler(
    planner_factory: Callable[[], ExplainerStagePlanner],
    repo_factory: Callable[[], Any],
    *,
    storyboard_service_factory: Callable[[Any], Any] | None = None,
    read_repo_factory: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """``EXPLAINER_STORYBOARD``: allocate beats and, when real audio exists, frames."""

    reader = read_repo_factory or repo_factory

    def build_storyboard_service(repo: Any) -> Any:
        if storyboard_service_factory is not None:
            return storyboard_service_factory(repo)
        return ExplainerStoryboardService(repo)

    def handler(job: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        payload = _payload(context)
        project_id = _require(payload, "project_id", stage="EXPLAINER_STORYBOARD")
        video_id = _require(payload, "video_id", stage="EXPLAINER_STORYBOARD")
        # The worker context carries the frozen capability snapshot, which is the
        # authority for what this run may plan.  A missing snapshot is not an excuse
        # to fall back to a still-picture path: the explainer's moving pictures are
        # real AI image-to-video or nothing, so an empty set makes the planner refuse
        # the plan with a named capability blocker.
        usable = [str(item) for item in (payload.get("usable_render_types") or []) if str(item)]
        with reader() as repo:
            plan = planner_factory().plan_storyboard(
                repo=repo, project_id=project_id, video_id=video_id, usable_render_types=usable
            )
        with repo_factory() as repo:
            service = build_storyboard_service(repo)
            created = service.create_plan(
                project_id=project_id,
                video_id=video_id,
                beats=plan["beats"],
                # Attribute the beats to this very plan step so a later plan for the
                # same video neither collides with these codes nor is read as part of
                # the same plan (audit A11).  A standalone call without a step
                # binding keeps the legacy NULL attribution.
                plan_step_binding_id=str(payload.get("step_binding_id") or "").strip() or None,
            )
            mapping = created.get("mapping") or {}
            mapping = created.get("mapping") or {}
            durations: dict[str, Any] | None = None
            edition_id = str(payload.get("edition_id") or "").strip()
            measured = repo.selected_takes(video_id)
            if edition_id and measured:
                segment_durations: dict[str, int] = {}
                for take in measured:
                    value = take.get("measured_duration_ms")
                    if value:
                        segment_durations[str(take["canonical_segment_id"])] = int(value)
                if segment_durations:
                    durations = service.plan_durations_from_real_audio(
                        video_id=video_id, edition_id=edition_id, segment_durations_ms=segment_durations
                    )
        return {
            "status": "PASS",
            "machine_check": {"status": "PASS", "ok": True},
            "produced": {
                "beat_count": int(mapping.get("beat_count") or len(created.get("beats") or [])),
                "link_count": int(mapping.get("link_count") or 0),
                "uncovered_segment_ids": plan["uncovered_segment_ids"],
                "usable_render_types": plan["usable_render_types"],
                "plan_hash": created.get("plan_hash") or plan["plan_hash"],
                "beats_missing_revision_refs": created.get("beats_missing_revision_refs") or [],
                "frame_plan": durations,
                "timing_authority": "MEASURED_TTS" if durations else "PLANNED_HINT_NOT_MEASURED",
            },
            "summary": (
                f"已编排 {int(mapping.get('beat_count') or 0)} 个画面段、"
                f"{int(mapping.get('link_count') or 0)} 条旁白映射；"
                + ("已按真实配音实测时长分配帧区间" if durations else "尚无实测配音，帧区间保持计划值")
            ),
        }

    return handler


def build_stage_handlers(
    *,
    planner_factory: Callable[[], ExplainerStagePlanner],
    repo_factory: Callable[[], Any],
    read_repo_factory: Callable[[], Any] | None = None,
) -> dict[str, Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]]:
    """All four first-party explainer text stages, keyed by their stage code.

    ``read_repo_factory`` yields a repository on a plain connection.  The model
    call runs on it so a multi-minute local inference never holds the write
    transaction that the job's own lease heartbeat needs.
    """

    return {
        "RESEARCH_ACQUIRE": make_research_acquire_handler(
            planner_factory, repo_factory, read_repo_factory=read_repo_factory
        ),
        "FACT_EXTRACT": make_fact_extract_handler(
            planner_factory, repo_factory, read_repo_factory=read_repo_factory
        ),
        "NARRATION_WRITE": make_narration_write_handler(
            planner_factory, repo_factory, read_repo_factory=read_repo_factory
        ),
        "EXPLAINER_STORYBOARD": make_storyboard_handler(
            planner_factory, repo_factory, read_repo_factory=read_repo_factory
        ),
    }


def strip_code_fences(value: str) -> str:
    """Remove a ```json fence a local model may wrap its answer in."""

    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()
