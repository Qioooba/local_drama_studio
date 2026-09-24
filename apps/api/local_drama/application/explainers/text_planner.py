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
from typing import Any, Protocol

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
        self, *, repo: ExplainerRepository, project_id: str, video_id: str, packet_id: str
    ) -> dict[str, Any]: ...

    def plan_script(  # pragma: no cover - protocol boundary
        self, *, repo: ExplainerRepository, project_id: str, video_id: str
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
        titles = {str(item["id"]): str(item.get("title") or "未命名来源") for item in sources}
        lines: list[str] = []
        budget = self.max_evidence_characters
        for span in spans:
            quote = str(span.get("quote_text") or "").strip()
            if not quote:
                continue
            entry = f"[{span['id']}] 来源「{titles.get(str(span['source_id']), '未知来源')}」：{quote}"
            if len(entry) > budget:
                entry = entry[:budget]
            lines.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break
        return "\n".join(lines)

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
        self, *, repo: ExplainerRepository, project_id: str, video_id: str, packet_id: str
    ) -> dict[str, Any]:
        video = self._video(repo, project_id, video_id)
        packet, sources, spans = self._packet_and_spans(
            repo, project_id=project_id, video_id=video_id, packet_id=packet_id
        )
        is_fiction = str(video["content_kind"]) == ContentKind.ORIGINAL_FICTION.value
        user = (
            f"作品标题：{video['title']}\n主题：{video.get('topic') or '（未填写）'}\n"
            f"内容属性：{'原创虚构（事实包只是本片内部设定，不得声称是史实）' if is_fiction else '事实解说'}\n\n"
            "请从下面的来源片段中提取：\n"
            "1) claims：每条原子事实。code 用 C001 起的编号；statement 只写一条可独立判断真假的陈述；"
            "statement_kind 取 FACT / ORIGINAL_EXPLANATION / TRANSITION / FICTION / QUESTION；"
            "importance 取 CORE / KEY / SUPPORTING；evidence 数组里每项必须引用下面真实存在的片段编号，"
            "stance 取 SUPPORTS / REFUTES / CONTEXT。来源互相矛盾时，同一条 claim 下同时给出 SUPPORTS 与 REFUTES，"
            "不要拆成两条，也不要按来源数量决定真伪。\n"
            "2) events：时间线上的事件。story_time_start/end 只写原文给出的精度（例如 1936 或 21:17:00），"
            "原文没写就不要写；place_label 用原文地名；participant_entity_codes 与 claim_codes 引用本回答中的编号。\n"
            "3) entities：具名且影响视觉一致性的人物、地点、道具、机构。entity_type 取 "
            "REAL_PERSON / FICTIONAL_CHARACTER / GROUP / LOCATION / PROP / ORGANIZATION / CONCEPT；"
            "aliases 合并同一实体的不同写法；没有可靠肖像的真实人物把 descriptive_only 设为 true。"
            "state 只描述原文确实给出的年龄、服装、状态或随身道具。\n\n"
            "来源片段清单（这是唯一可引用的编号来源）：\n"
            + self._evidence_block(sources, spans)
        )
        result = self._chat(
            system=self.prompts.fact_system,
            user=user,
            schema=FACT_EXTRACTION_SCHEMA,
            max_tokens=16_000,
            num_ctx=49_152,
        )
        # The same validator the research service uses: an undeclared field or an
        # unknown span reference is rejected here, before any row is written.
        validated = validate_model_payload(result, FACT_EXTRACTION_SCHEMA, scope="fact_extraction")
        span_ids = {str(item["id"]) for item in spans}
        source_ids = {str(item["id"]) for item in sources}
        for claim in validated.get("claims") or []:
            for evidence in claim.get("evidence") or []:
                span_id = str(evidence["source_span_id"])
                if span_id not in span_ids:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "文本模型引用了不存在的来源片段",
                        {"claim_code": claim.get("code"), "source_span_id": span_id},
                    )
                declared = evidence.get("source_id")
                if declared and str(declared) not in source_ids:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "文本模型引用了不存在的来源",
                        {"claim_code": claim.get("code"), "source_id": str(declared)},
                    )
        date_repairs = _repair_story_time_dates(validated, sources, spans)
        return {
            "status": "PASS",
            "extracted": validated,
            "claim_count": len(validated.get("claims") or []),
            "event_count": len(validated.get("events") or []),
            "entity_count": len(validated.get("entities") or []),
            "verified_as_history": False,
            "reference_validation": "ALL_SPANS_RESOLVED",
            "story_time_date_repairs": date_repairs,
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
                    f"约 {character_budget} 个字符。请在保持同样章节结构与事实编号的前提下扩写："
                    "为每个现象补充因果链、机制细节、代表性例子与常见误解，使总字符数不少于 "
                    f"{character_budget}。不要重复已经写过的句子，不要引用不存在的 claim_code，"
                    "不要改变任何否定、数字或人名。"
                ),
                schema=SEGMENT_SCHEMA,
                max_tokens=16_000,
                num_ctx=49_152,
                client=client,
            )
            raw_segments = result.get("segments") or []
            if not raw_segments:
                raise ExplainerContractError("SCHEMA_INVALID", "文本模型没有返回任何叙述段落")
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
        }

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
        motion_capable = {"I2V", "PARALLAX", "LICENSED_MEDIA"}
        motion_coerced: list[dict[str, Any]] = []
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
                f"本次可用画面生成方式（render_type 只能取这些值）：{', '.join(allowed)}\n\n"
                "请把下面的叙述段落编排成画面段 beats：\n"
                "- code 用 B001 起的编号。\n"
                "- segment_ids 引用下面真实存在的段落编号；一句话可以跨两个画面段，"
                "一个画面段也可以承载相邻两句，不要机械地每 5 秒换一张无关图。\n"
                "- visual_intent 写这个画面要让观众看到什么（用一句中文）。\n"
                "- visual_factuality：有来源照片或原始记录用 DOCUMENTED；依据记载的画面重建用 RECONSTRUCTION；"
                "抽象示意用 SYMBOLIC；虚构编排用 FICTIONAL。\n"
                "- entity_codes 只能取下面实体名录里的编号；claim_codes 只能取事实编号。\n"
                "- must_be_motion 只在核心动作或关键转折上设为 true；"
                f"{'如果可用类型里没有运动类方式，必须全部为 false。' if not any(item in allowed for item in ('I2V', 'PARALLAX')) else '其余画面段保持 false。'}\n"
                "- 本批列出的每一个段落都必须至少被一个画面段引用，不能遗漏任何段落编号。\n"
                "- prompt_intent 写一段可直接用于生成画面提示的意图描述，只描述画面，不要写入任何可读文字内容"
                "（文字由确定性排版层绘制）。\n\n"
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
                    # The plan asked for motion but declared a still picture type,
                    # and this build's picture path is the deterministic
                    # still/graphic renderer.  Refusing the whole plan made the run
                    # stop on a contradiction the model introduced; the honest
                    # answer is to keep the declared type, drop the motion
                    # requirement and *record* the degradation, which is what the
                    # manifest and the delivery report then disclose.
                    coerced = True
                    must_be_motion = False
                if coerced:
                    motion_coerced.append({"code": code, "render_type": render_type})
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
            "motion_coerced_beats": motion_coerced,
            "mapping_is_many_to_many": True,
            "plan_hash": content_hash(
                {"beats": beats, "script_revision_id": script_revision_id, "usable": allowed}
            ),
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
        # The model call runs on a read-only connection: holding a write
        # transaction across a multi-minute local inference blocks the job's own
        # lease heartbeat, and the lease then expires while the model is thinking.
        with reader() as repo:
            result = planner_factory().plan_research(
                repo=repo, project_id=project_id, video_id=video_id, packet_id=packet_id
            )
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
                repo=repo, project_id=project_id, video_id=video_id, packet_id=packet_id
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
            },
            "summary": (
                f"已写入 {plan['claim_count']} 条事实、{plan['event_count']} 个事件、"
                f"{plan['entity_count']} 个实体；未解决核心冲突 "
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
        with reader() as repo:
            plan = planner_factory().plan_script(repo=repo, project_id=project_id, video_id=video_id)
        with repo_factory() as repo:
            video = repo.get("explainer_videos", video_id)
            service = build_narration_service(repo)
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
        produced: dict[str, Any] = {
            "script_revision_id": str(revision["id"]),
            "revision_no": revision.get("revision_no"),
            "segment_count": plan["segment_count"],
            "character_count": plan["character_count"],
            "timing_status": plan["timing_status"],
            "frozen": frozen is not None,
            "current_script_revision_updated": frozen is not None,
        }
        return {
            "status": "PASS",
            "machine_check": {"status": "PASS", "ok": True},
            "produced": produced,
            "summary": (
                f"已生成 {plan['segment_count']} 段解说稿（{plan['character_count']} 字符）；"
                + ("已冻结为不可变修订" if frozen is not None else "保持草稿")
            ),
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
        # The worker context carries the frozen capability snapshot; a missing
        # snapshot falls back to the still-motion/infographic path, never to I2V.
        usable = [str(item) for item in (payload.get("usable_render_types") or []) if str(item)]
        if not usable:
            usable = [RenderType.STILL_MOTION.value, RenderType.INFOGRAPHIC.value]
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
            # A beat whose motion requirement had to be dropped keeps the declared
            # still type and says why, so the degradation is visible in the beat and
            # in every report derived from it.
            coerced_codes = {str(item["code"]) for item in (plan.get("motion_coerced_beats") or [])}
            if coerced_codes:
                for beat in created.get("beats") or []:
                    if str(beat.get("code")) in coerced_codes:
                        repo.update(
                            "explainer_visual_beats",
                            str(beat["beat_id"]),
                            {
                                "actual_fallback_type": "STILL_MOTION",
                                "fallback_reason": "PLANNED_MOTION_DEGRADED_TO_STILL_PICTURE_PATH",
                            },
                            actor="local-text-planner",
                        )
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
