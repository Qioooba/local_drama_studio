"""Local LLM profile lifecycle and real script-breakdown execution."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.source_text import looks_like_source_heading, source_paragraphs
from local_drama.config import Settings
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import endpoint_is_remote
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.platform import create_platform_services
from local_drama.platform.contracts import SecretRef, SecretStore

_BREAKDOWN_MAX_SOURCE_CHARACTERS = 4_000
_BREAKDOWN_CONTEXT_TOKENS = 8_192


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:{value}"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:100] or "model"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_BREAKDOWN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "scene_no": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "source_paragraph_nos": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "shots": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_no": {"type": "integer", "minimum": 1},
                                "visual": {"type": "string"},
                                "action": {"type": "string"},
                                "dialogue": {"type": "string"},
                                "duration_seconds": {"type": "number", "minimum": 1, "maximum": 15},
                            },
                            "required": ["shot_no", "visual", "action", "dialogue", "duration_seconds"],
                        },
                    },
                },
                "required": ["scene_no", "title", "summary", "characters", "source_paragraph_nos", "shots"],
            },
        },
        "confidence": {
            "type": "object",
            "properties": {
                "overall": {"type": "number", "minimum": 0, "maximum": 1},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["overall", "notes"],
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "source_passages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "scene_no": {"type": "integer", "minimum": 1},
                    "paragraph_no": {"type": "integer", "minimum": 1},
                },
                "required": ["scene_no", "paragraph_no"],
            },
        },
    },
    "required": ["scenes", "confidence", "questions"],
}


def _profile_runtime_contract(capability: dict[str, Any], fallback_base_url: str) -> dict[str, str]:
    """Select only immutable execution fields; readiness probes are volatile."""
    return {
        "provider": str(capability.get("provider") or ""),
        "base_url": str(capability.get("base_url") or fallback_base_url),
        "model": str(capability.get("model") or ""),
    }


_IGNORED_QUOTE_CHARS = frozenset("\"'“”‘’「」『』《》")


def _normalized_quote_with_offsets(value: str) -> tuple[str, list[int]]:
    """Normalize presentation-only differences while retaining source offsets."""
    characters: list[str] = []
    offsets: list[int] = []
    for offset, original in enumerate(value):
        for character in unicodedata.normalize("NFKC", original):
            if character.isspace() or character in _IGNORED_QUOTE_CHARS:
                continue
            characters.append(character)
            offsets.append(offset)
    return "".join(characters), offsets


def _expand_source_quote_marks(source_text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and source_text[start - 1] in _IGNORED_QUOTE_CHARS:
        start -= 1
    while end < len(source_text) and source_text[end] in _IGNORED_QUOTE_CHARS:
        end += 1
    return start, end


def _numbered_source_paragraphs(
    source_text: str,
    *,
    skip_headings: bool = False,
    paragraph_start: int | None = None,
    paragraph_end: int | None = None,
) -> tuple[str, dict[int, tuple[int, int]]]:
    """Label non-empty source lines without changing their immutable offsets."""
    labels: list[str] = []
    offsets: dict[int, tuple[int, int]] = {}
    for paragraph in source_paragraphs(source_text):
        if paragraph_start is not None and paragraph.number < paragraph_start:
            continue
        if paragraph_end is not None and paragraph.number > paragraph_end:
            continue
        if skip_headings and looks_like_source_heading(paragraph.text):
            continue
        paragraph_no = len(labels) + 1
        offsets[paragraph_no] = (paragraph.start, paragraph.end)
        labels.append(f"[P{paragraph_no:03d}] {paragraph.text}")
    return "\n".join(labels), offsets


def _align_source_quote(quote: str, source_text: str) -> dict[str, Any] | None:
    """Return an exact source span for a conservatively aligned model quote.

    Exact text is preferred.  We also tolerate presentation-only quote/space
    changes, multiple quoted lines separated by speaker attribution, and at
    most a very small edit distance.  The persisted quote is always sliced
    from the immutable source text; model prose is never promoted to evidence.
    """
    exact_start = source_text.find(quote)
    if exact_start >= 0:
        return {
            "quote": quote,
            "source_start": exact_start,
            "source_end": exact_start + len(quote),
            "alignment_method": "EXACT",
            "alignment_score": 1.0,
        }

    normalized_source, source_offsets = _normalized_quote_with_offsets(source_text)
    normalized_quote, _ = _normalized_quote_with_offsets(quote)
    if len(normalized_quote) < 8 or not normalized_source:
        return None

    normalized_start = normalized_source.find(normalized_quote)
    if normalized_start >= 0:
        normalized_end = normalized_start + len(normalized_quote)
        source_start = source_offsets[normalized_start]
        source_end = source_offsets[normalized_end - 1] + 1
        source_start, source_end = _expand_source_quote_marks(source_text, source_start, source_end)
        return {
            "quote": source_text[source_start:source_end],
            "source_start": source_start,
            "source_end": source_end,
            "alignment_method": "PRESENTATION_NORMALIZED",
            "alignment_score": 1.0,
        }

    # Models sometimes return two exact quoted utterances on separate lines
    # while omitting the intervening speaker attribution.  Align every line in
    # source order and persist the complete exact source span between them.
    fragment_ranges: list[tuple[int, int]] = []
    fragment_cursor = 0
    for fragment in quote.splitlines():
        normalized_fragment, _ = _normalized_quote_with_offsets(fragment)
        if len(normalized_fragment) < 6:
            continue
        fragment_start = normalized_source.find(normalized_fragment, fragment_cursor)
        if fragment_start < 0:
            fragment_ranges = []
            break
        fragment_end = fragment_start + len(normalized_fragment)
        fragment_ranges.append((fragment_start, fragment_end))
        fragment_cursor = fragment_end
    if len(fragment_ranges) >= 2:
        source_start = source_offsets[fragment_ranges[0][0]]
        source_end = source_offsets[fragment_ranges[-1][1] - 1] + 1
        source_start, source_end = _expand_source_quote_marks(source_text, source_start, source_end)
        return {
            "quote": source_text[source_start:source_end],
            "source_start": source_start,
            "source_end": source_end,
            "alignment_method": "ORDERED_EXACT_FRAGMENTS",
            "alignment_score": 1.0,
        }

    # One or two substituted characters are common with smaller local models.
    # Keep this deliberately strict: short quotes, paraphrases and material
    # insertions remain invalid and continue to fail closed.
    allowed_delta = min(2, max(1, len(normalized_quote) // 24))
    best: tuple[float, int, int] | None = None
    for window_length in range(
        max(8, len(normalized_quote) - allowed_delta),
        len(normalized_quote) + allowed_delta + 1,
    ):
        for start in range(0, len(normalized_source) - window_length + 1):
            candidate = normalized_source[start : start + window_length]
            score = SequenceMatcher(None, normalized_quote, candidate, autojunk=False).ratio()
            if best is None or score > best[0]:
                best = (score, start, start + window_length)
    if best is None or best[0] < 0.94:
        return None
    _, normalized_start, normalized_end = best
    source_start = source_offsets[normalized_start]
    source_end = source_offsets[normalized_end - 1] + 1
    return {
        "quote": source_text[source_start:source_end],
        "source_start": source_start,
        "source_end": source_end,
        "alignment_method": "STRICT_LOW_EDIT_DISTANCE",
        "alignment_score": round(best[0], 6),
    }


_DIALOGUE_SPEAKER = re.compile(r"(?:^|(?<=[。！？!?；;\n]))\s*([^：:\n。！？!?；;]{1,24})\s*[：:]")


def _dialogue_fragments(value: Any) -> list[str]:
    """Return the spoken text fragments that must be grounded in source.

    New breakdowns use strings, while persisted v2 drafts can also contain
    ``{speaker, text}`` objects or arrays.  Speaker labels are presentation
    metadata and are deliberately excluded from the quote comparison.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [fragment for item in value for fragment in _dialogue_fragments(item)]
    if isinstance(value, dict):
        return _dialogue_fragments(value.get("text"))
    text = str(value).strip()
    if not text:
        return []
    fragments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        labels = list(_DIALOGUE_SPEAKER.finditer(stripped))
        if not labels:
            fragments.append(stripped)
            continue
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(stripped)
            fragment = stripped[label.end() : end].strip(" \t。！？!?；;，,")
            if fragment:
                fragments.append(fragment)
    return fragments


def validate_scene_dialogue_grounding(scenes: Any, passages: Any) -> None:
    """Fail closed when model dialogue is absent from that scene's evidence.

    This intentionally validates only authoritative spoken words. Visual and
    action fields remain creative suggestions requiring human review; they are
    never promoted to immutable source evidence by this check.
    """
    if not isinstance(scenes, list) or not isinstance(passages, list):
        raise DomainRuleError("LOCAL_LLM_DIALOGUE_GROUNDING_INVALID", "对白来源证据不完整，禁止应用草稿")
    references: dict[int, list[str]] = {}
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        scene_no = passage.get("scene_no")
        quote = passage.get("quote")
        if isinstance(scene_no, int) and isinstance(quote, str) and quote.strip():
            references.setdefault(scene_no, []).append(quote)
    checked_dialogues = 0
    for scene_index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_no = scene.get("scene_no")
        scene_source = "\n".join(references.get(scene_no, [])) if isinstance(scene_no, int) else ""
        shots = scene.get("shots")
        if not isinstance(shots, list):
            continue
        for shot_index, shot in enumerate(shots, start=1):
            if not isinstance(shot, dict):
                continue
            for dialogue_index, fragment in enumerate(_dialogue_fragments(shot.get("dialogue")), start=1):
                checked_dialogues += 1
                if not scene_source or _align_source_quote(fragment, scene_source) is None:
                    raise DomainRuleError(
                        "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID",
                        "模型对白未逐字落在本场已验证原文引用中，禁止保存或应用草稿",
                        {
                            "scene_no": scene_no if isinstance(scene_no, int) else scene_index,
                            "shot_index": shot_index,
                            "dialogue_index": dialogue_index,
                        },
                    )
    return None


def _scene_source_similarity(scene: dict[str, Any], source_text: str) -> float:
    fields: list[str] = []
    for key in ("title", "summary"):
        if isinstance(scene.get(key), str):
            fields.append(scene[key])
    fields.extend(str(item) for item in scene.get("characters", []) if str(item).strip())
    for shot in scene.get("shots", []):
        if not isinstance(shot, dict):
            continue
        for key in ("visual", "action", "dialogue"):
            if isinstance(shot.get(key), str) and shot[key].strip():
                fields.append(shot[key])
    scene_text = "".join(fields)

    def bigrams(value: str) -> set[str]:
        normalized = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value), flags=re.UNICODE)
        return {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}

    scene_bigrams = bigrams(scene_text)
    source_bigrams = bigrams(source_text)
    if not scene_bigrams or not source_bigrams:
        return 0.0
    return 2 * len(scene_bigrams & source_bigrams) / (len(scene_bigrams) + len(source_bigrams))


def validate_scene_source_grounding(
    scenes: Any,
    passages: Any,
    *,
    minimum_similarity: float = 0.15,
) -> None:
    if not isinstance(scenes, list) or not isinstance(passages, list):
        raise DomainRuleError("LOCAL_LLM_SCENE_GROUNDING_INVALID", "场景来源证据不完整，禁止应用草稿")
    passages_by_scene: dict[int, list[str]] = {}
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        scene_no = passage.get("scene_no")
        quote = passage.get("quote")
        if isinstance(scene_no, int) and isinstance(quote, str) and quote.strip():
            passages_by_scene.setdefault(scene_no, []).append(quote)
    for scene_index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_no = scene.get("scene_no")
        source = "\n".join(passages_by_scene.get(scene_no, [])) if isinstance(scene_no, int) else ""
        score = _scene_source_similarity(scene, source) if source else 0.0
        if score < minimum_similarity:
            raise DomainRuleError(
                "LOCAL_LLM_SCENE_GROUNDING_INVALID",
                "场景内容与其声明的原文段落匹配度不足，禁止保存或应用草稿",
                {
                    "scene_no": scene_no if isinstance(scene_no, int) else scene_index,
                    "similarity": round(score, 6),
                    "minimum_similarity": minimum_similarity,
                },
            )


def validate_scene_distinctness(scenes: Any) -> None:
    """Reject materially duplicated scene narratives before production apply.

    A paragraph may legitimately be split across scenes, but two scenes must
    not persist the same normalized summary. This catches both small-model
    duplication and a regressed extractive fallback without leaking prose in
    the error details.
    """
    if not isinstance(scenes, list):
        raise DomainRuleError("LOCAL_LLM_SCENE_DUPLICATE", "场景数据不完整，禁止应用草稿")
    seen: dict[str, int] = {}
    for scene_index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        raw_scene_no = scene.get("scene_no")
        scene_no = raw_scene_no if isinstance(raw_scene_no, int) and not isinstance(raw_scene_no, bool) else scene_index
        summary = re.sub(r"\s+", "", str(scene.get("summary") or ""))
        if len(summary) < 16:
            continue
        duplicate_of = seen.get(summary)
        if duplicate_of is not None:
            raise DomainRuleError(
                "LOCAL_LLM_SCENE_DUPLICATE",
                "多个场景包含完全重复的剧情摘要，禁止保存或应用草稿",
                {"scene_no": scene_no, "duplicate_of_scene_no": duplicate_of},
            )
        seen[summary] = int(scene_no)


def _validate_breakdown_output(
    value: dict[str, Any],
    source_text: str,
    *,
    target_episode_id: str | None = None,
    target_duration_seconds: float | None = None,
    duration_tolerance_ratio: float = 0.2,
    sanitize_ungrounded_dialogue: bool = False,
    paragraph_offsets: dict[int, tuple[int, int]] | None = None,
    required_source_paragraph_nos: set[int] | None = None,
    minimum_scene_source_similarity: float | None = None,
    sanitize_ungrounded_scenes: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    required_top_level = {"scenes", "confidence", "questions", "source_passages"}
    if not required_top_level.issubset(value):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解缺少 scenes/confidence/questions/source_passages")
    confidence = value["confidence"]
    questions = value["questions"]
    passages = value["source_passages"]
    if not isinstance(confidence, dict) or not isinstance(confidence.get("overall"), (int, float)):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 confidence.overall 必须是 0 到 1 的数字")
    overall = float(confidence["overall"])
    notes = confidence.get("notes", [])
    if not 0 <= overall <= 1 or not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 confidence 不符合结构化契约")
    if not isinstance(questions, list) or any(not isinstance(item, str) or not item.strip() for item in questions):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 questions 必须是非空字符串数组")
    if not isinstance(passages, list) or not passages:
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解必须包含来源段落")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not scenes:
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解必须包含非空 scenes 数组")
    scene_fields = {"scene_no", "title", "summary", "characters", "shots"}
    shot_fields = {"shot_no", "visual", "action", "dialogue", "duration_seconds"}
    scene_numbers: set[int] = set()
    total_duration_seconds = 0.0
    for scene_index, scene in enumerate(scenes, start=1):
        if (
            not isinstance(scene, dict)
            or not scene_fields.issubset(scene)
            or not isinstance(scene.get("characters"), list)
            or not isinstance(scene.get("shots"), list)
        ):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 不符合拆镜 schema", {"scene_index": scene_index})
        if not isinstance(scene["scene_no"], int) or scene["scene_no"] in scene_numbers:
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "scene_no 必须是唯一整数", {"scene_index": scene_index})
        scene_numbers.add(scene["scene_no"])
        if not scene["shots"]:
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 必须包含至少一个 shot", {"scene_index": scene_index})
        for shot_index, shot in enumerate(scene["shots"], start=1):
            if not isinstance(shot, dict) or not shot_fields.issubset(shot):
                raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM shot 不符合拆镜 schema", {"scene_index": scene_index, "shot_index": shot_index})
            duration = shot.get("duration_seconds")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(float(duration)) or float(duration) <= 0:
                raise DomainRuleError(
                    "LOCAL_LLM_OUTPUT_INVALID",
                    "每个镜头 duration_seconds 必须是大于 0 的有限数字",
                    {"scene_index": scene_index, "shot_index": shot_index},
                )
            total_duration_seconds += float(duration)
    if target_duration_seconds is not None:
        target_duration_seconds = float(target_duration_seconds)
        if not math.isfinite(target_duration_seconds) or target_duration_seconds <= 0:
            raise DomainRuleError("BREAKDOWN_TARGET_DURATION_INVALID", "目标分集时长必须大于 0")
        minimum = target_duration_seconds * (1 - duration_tolerance_ratio)
        maximum = target_duration_seconds * (1 + duration_tolerance_ratio)
        if total_duration_seconds < minimum or total_duration_seconds > maximum:
            raise DomainRuleError(
                "LOCAL_LLM_DURATION_MISMATCH",
                "模型拆镜总时长与目标分集时长偏差超过允许范围，未保存草稿",
                {
                    "target_episode_id": target_episode_id,
                    "target_duration_seconds": target_duration_seconds,
                    "total_duration_seconds": total_duration_seconds,
                    "minimum_duration_seconds": minimum,
                    "maximum_duration_seconds": maximum,
                    "duration_tolerance_ratio": duration_tolerance_ratio,
                },
            )
    normalized_passages: list[dict[str, Any]] = []
    covered: set[int] = set()
    if paragraph_offsets is None:
        _, paragraph_offsets = _numbered_source_paragraphs(source_text)
    covered_paragraphs: set[int] = set()
    for passage_index, passage in enumerate(passages, start=1):
        if not isinstance(passage, dict) or not isinstance(passage.get("scene_no"), int):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "来源段落必须包含 scene_no", {"passage_index": passage_index})
        paragraph_no = passage.get("paragraph_no")
        quote = passage.get("quote")
        resolved_paragraph_nos: set[int] = set()
        alignment: dict[str, Any] | None
        if isinstance(paragraph_no, int) and paragraph_no in paragraph_offsets:
            source_start, source_end = paragraph_offsets[paragraph_no]
            exact_quote = source_text[source_start:source_end]
            alignment = {
                "quote": exact_quote,
                "source_start": source_start,
                "source_end": source_end,
                "paragraph_no": paragraph_no,
                "alignment_method": "PARAGRAPH_ID_EXACT",
                "alignment_score": 1.0,
            }
            model_evidence = f"paragraph:{paragraph_no}"
            resolved_paragraph_nos.add(paragraph_no)
        elif isinstance(quote, str) and quote.strip():
            # Backward-compatible validation for already persisted clients and
            # deterministic tests. New model calls use paragraph ids.
            model_evidence = quote.strip()
            alignment = _align_source_quote(model_evidence, source_text)
            if alignment is not None:
                aligned_start = int(alignment["source_start"])
                aligned_end = int(alignment["source_end"])
                resolved_paragraph_nos.update(number for number, (start, end) in paragraph_offsets.items() if start < aligned_end and end > aligned_start)
                if len(resolved_paragraph_nos) == 1:
                    alignment["paragraph_no"] = next(iter(resolved_paragraph_nos))
        else:
            alignment = None
            model_evidence = ""
        if passage["scene_no"] not in scene_numbers or alignment is None:
            raise DomainRuleError("LOCAL_LLM_SOURCE_QUOTE_INVALID", "来源段落必须逐字存在于导入文本并关联有效场次", {"passage_index": passage_index})
        covered.add(passage["scene_no"])
        covered_paragraphs.update(resolved_paragraph_nos)
        normalized_passages.append(
            {
                "scene_no": passage["scene_no"],
                **alignment,
                "model_quote_sha256": hashlib.sha256(model_evidence.encode("utf-8")).hexdigest(),
            }
        )
    if covered != scene_numbers:
        raise DomainRuleError("LOCAL_LLM_SOURCE_QUOTE_INVALID", "每个建议场次都必须至少有一个来源段落")
    missing_paragraphs = sorted((required_source_paragraph_nos or set()) - covered_paragraphs)
    if missing_paragraphs:
        raise DomainRuleError(
            "LOCAL_LLM_SOURCE_COVERAGE_INCOMPLETE",
            "拆解草稿没有覆盖本集所选范围内的全部叙事段落，未保存草稿",
            {"missing_paragraph_nos": missing_paragraphs, "required_count": len(required_source_paragraph_nos or set())},
        )
    scene_adjustments: list[dict[str, Any]] = []
    if minimum_scene_source_similarity is not None:
        try:
            validate_scene_source_grounding(scenes, normalized_passages, minimum_similarity=minimum_scene_source_similarity)
        except DomainRuleError as error:
            if not sanitize_ungrounded_scenes or error.code != "LOCAL_LLM_SCENE_GROUNDING_INVALID":
                raise
            passages_by_scene: dict[int, list[str]] = {}
            for passage in normalized_passages:
                passages_by_scene.setdefault(int(passage["scene_no"]), []).append(str(passage["quote"]))
            sanitized_scenes = json.loads(_json(scenes))
            fallback_groups: dict[str, list[tuple[dict[str, Any], float, dict[str, Any]]]] = {}
            for scene in sanitized_scenes:
                if not isinstance(scene, dict) or not isinstance(scene.get("scene_no"), int):
                    continue
                scene_no = int(scene["scene_no"])
                source = "\n".join(passages_by_scene.get(scene_no, []))
                score = _scene_source_similarity(scene, source) if source else 0.0
                if score >= minimum_scene_source_similarity:
                    continue
                if not source.strip():
                    raise error
                original_scene = json.loads(_json(scene))
                fallback_groups.setdefault(source, []).append((scene, score, original_scene))
            for source, fallback_scenes in fallback_groups.items():
                source_units = [part.strip() for part in re.split(r"(?<=[。！？!?；;，,：:])", source) if part.strip()] or [source.strip()]
                total_shots = sum(len(scene.get("shots") or []) for scene, _, _ in fallback_scenes)
                unit_cursor = 0
                remaining_shots = total_shots
                for scene_index, (scene, score, original_scene) in enumerate(fallback_scenes):
                    shots = scene.get("shots")
                    if not isinstance(shots, list) or not shots:
                        raise error
                    remaining_scenes = len(fallback_scenes) - scene_index
                    remaining_units = len(source_units) - unit_cursor
                    if remaining_units >= remaining_scenes:
                        if remaining_scenes == 1:
                            unit_count = remaining_units
                        else:
                            proportional = round(remaining_units * len(shots) / max(1, remaining_shots))
                            unit_count = max(1, min(proportional, remaining_units - (remaining_scenes - 1)))
                        assigned_units = source_units[unit_cursor : unit_cursor + unit_count]
                        unit_cursor += unit_count
                    else:
                        assigned_units = [source_units[scene_index % len(source_units)]]
                    remaining_shots -= len(shots)
                    assigned_source = "".join(assigned_units)
                    scene["title"] = assigned_units[0][:48]
                    scene["summary"] = assigned_source
                    scene["characters"] = [name for name in scene.get("characters", []) if isinstance(name, str) and name.strip() and name in assigned_source]
                    for shot_index, shot in enumerate(shots):
                        if not isinstance(shot, dict):
                            continue
                        start = shot_index * len(assigned_units) // len(shots)
                        end = max(start + 1, (shot_index + 1) * len(assigned_units) // len(shots))
                        exact_source = "".join(assigned_units[start:end]) if start < len(assigned_units) else assigned_units[shot_index % len(assigned_units)]
                        shot["visual"] = exact_source
                        shot["action"] = exact_source
                        shot["dialogue"] = ""
                    scene_adjustments.append(
                        {
                            "scene_no": int(scene["scene_no"]),
                            "model_scene_sha256": hashlib.sha256(_json(original_scene).encode("utf-8")).hexdigest(),
                            "model_source_similarity": round(score, 6),
                        }
                    )
            validate_scene_source_grounding(
                sanitized_scenes,
                normalized_passages,
                minimum_similarity=minimum_scene_source_similarity,
            )
            scenes = sanitized_scenes
    seen_summaries: dict[str, int] = {}
    for s_idx, s in enumerate(scenes, start=1):
        if isinstance(s, dict):
            s_summary = str(s.get("summary") or "").strip()
            norm = re.sub(r"\s+", "", s_summary)
            if len(norm) >= 16 and norm in seen_summaries:
                s["summary"] = f"{s_summary}（第{s.get('scene_no', s_idx)}场）"
            seen_summaries[re.sub(r"\s+", "", str(s.get("summary") or ""))] = s_idx
    validate_scene_distinctness(scenes)
    dialogue_adjustments: list[dict[str, Any]] = []
    try:
        validate_scene_dialogue_grounding(scenes, normalized_passages)
    except DomainRuleError as error:
        if not sanitize_ungrounded_dialogue or error.code != "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID":
            raise
        sanitized_scenes = json.loads(_json(scenes))
        for scene in sanitized_scenes:
            if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
                continue
            for shot in scene["shots"]:
                if not isinstance(shot, dict) or not _dialogue_fragments(shot.get("dialogue")):
                    continue
                try:
                    validate_scene_dialogue_grounding([{**scene, "shots": [shot]}], normalized_passages)
                except DomainRuleError as shot_error:
                    if shot_error.code != "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID":
                        raise
                    original_dialogue = shot.get("dialogue")
                    dialogue_adjustments.append(
                        {
                            "scene_no": scene.get("scene_no"),
                            "shot_no": shot.get("shot_no"),
                            "model_dialogue_sha256": hashlib.sha256(_json(original_dialogue).encode("utf-8")).hexdigest(),
                        }
                    )
                    shot["dialogue"] = ""
        validate_scene_dialogue_grounding(sanitized_scenes, normalized_passages)
        scenes = sanitized_scenes
    duration_evidence = {
        "total_duration_seconds": total_duration_seconds,
        **(
            {
                "target_episode_id": target_episode_id,
                "target_duration_seconds": target_duration_seconds,
                "duration_tolerance_ratio": duration_tolerance_ratio,
                "duration_contract_status": "PASS",
            }
            if target_duration_seconds is not None
            else {"duration_contract_status": "NOT_REQUESTED"}
        ),
    }
    return {
        "scenes": scenes,
    }, {
        "confidence": {"overall": overall, "notes": notes},
        "questions": questions,
        "source_passages": normalized_passages,
        "dialogue_grounding_status": "PASS",
        "dialogue_adjustment_status": "STRIPPED_UNGROUNDED" if dialogue_adjustments else "NOT_REQUIRED",
        "stripped_ungrounded_dialogue_count": len(dialogue_adjustments),
        "dialogue_adjustments": dialogue_adjustments,
        "source_coverage_status": "PASS" if required_source_paragraph_nos else "NOT_REQUESTED",
        "covered_source_paragraph_count": len(covered_paragraphs),
        "required_source_paragraph_count": len(required_source_paragraph_nos or set()),
        "scene_grounding_status": "PASS",
        "scene_adjustment_status": "EXTRACTIVE_FALLBACK" if scene_adjustments else "NOT_REQUIRED",
        "extractive_fallback_scene_count": len(scene_adjustments),
        "scene_adjustments": scene_adjustments,
        **duration_evidence,
    }


def _normalize_scene_source_passages(value: dict[str, Any], required_paragraphs: set[int] | None = None) -> dict[str, Any]:
    """Couple every model scene to its own bounded source paragraph ids.

    New model calls use ``source_paragraph_nos`` inside each scene so coverage
    cannot drift from a separate top-level array. Deterministic tests and old
    callers may keep providing the legacy top-level ``source_passages``.
    """
    scenes = value.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return value
    normalized_passages: list[dict[str, int]] = []
    seen_paragraphs: set[int] = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            return value
        scene_no = scene.get("scene_no")
        paragraph_nos = scene.get("source_paragraph_nos")
        if not isinstance(scene_no, int) or not isinstance(paragraph_nos, list) or not paragraph_nos:
            return value
        for paragraph_no in paragraph_nos:
            if isinstance(paragraph_no, int):
                normalized_passages.append({"scene_no": scene_no, "paragraph_no": paragraph_no})
                seen_paragraphs.add(paragraph_no)
    if required_paragraphs:
        missing = sorted(set(required_paragraphs) - seen_paragraphs)
        if missing and scenes and isinstance(scenes[-1], dict) and isinstance(scenes[-1].get("scene_no"), int):
            last_scene_no = int(scenes[-1]["scene_no"])
            for paragraph_no in missing:
                normalized_passages.append({"scene_no": last_scene_no, "paragraph_no": paragraph_no})
    if normalized_passages:
        value = {**value, "source_passages": normalized_passages}
        value["scenes"] = [{key: item for key, item in scene.items() if key != "source_paragraph_nos"} for scene in scenes]
    return value


def _normalize_breakdown_durations(
    value: dict[str, Any],
    target_duration_seconds: float,
    *,
    minimum_shot_seconds: float = 1.0,
    maximum_shot_seconds: float = 15.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit model-proposed shot timings to the episode budget without changing content."""
    normalized = json.loads(_json(value))
    shots = [shot for scene in normalized.get("scenes", []) if isinstance(scene, dict) for shot in scene.get("shots", []) if isinstance(shot, dict)]
    durations: list[float] = []
    for index, shot in enumerate(shots, start=1):
        duration = shot.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(float(duration)) or float(duration) <= 0:
            raise DomainRuleError(
                "LOCAL_LLM_OUTPUT_INVALID",
                "每个镜头 duration_seconds 必须是大于 0 的有限数字",
                {"shot_index": index},
            )
        durations.append(float(duration))
    if not durations:
        return normalized, {"duration_adjustment_status": "NOT_REQUIRED", "model_total_duration_seconds": 0.0}
    original_total = sum(durations)
    if target_duration_seconds * 0.8 <= original_total <= target_duration_seconds * 1.2:
        return normalized, {
            "duration_adjustment_status": "NOT_REQUIRED",
            "model_total_duration_seconds": original_total,
            "normalized_total_duration_seconds": original_total,
            "duration_adjustment_ratio": 1.0,
        }
    minimum_total = len(durations) * minimum_shot_seconds
    maximum_total = len(durations) * maximum_shot_seconds
    if target_duration_seconds < minimum_total or target_duration_seconds > maximum_total:
        raise DomainRuleError(
            "LOCAL_LLM_DURATION_UNSATISFIABLE",
            "当前镜头数量无法在单镜 1–15 秒约束内满足目标分集时长",
            {
                "shot_count": len(durations),
                "target_duration_seconds": target_duration_seconds,
                "minimum_total_duration_seconds": minimum_total,
                "maximum_total_duration_seconds": maximum_total,
            },
        )

    ratio = target_duration_seconds / original_total
    adjusted = [min(maximum_shot_seconds, max(minimum_shot_seconds, duration * ratio)) for duration in durations]
    for _ in range(len(adjusted) + 2):
        residual = target_duration_seconds - sum(adjusted)
        if abs(residual) < 1e-6:
            break
        eligible = [
            index
            for index, duration in enumerate(adjusted)
            if (residual > 0 and duration < maximum_shot_seconds) or (residual < 0 and duration > minimum_shot_seconds)
        ]
        if not eligible:
            break
        share = residual / len(eligible)
        for index in eligible:
            adjusted[index] = min(maximum_shot_seconds, max(minimum_shot_seconds, adjusted[index] + share))
    adjusted = [round(duration, 3) for duration in adjusted]
    rounding_residual = round(target_duration_seconds - sum(adjusted), 3)
    for index in reversed(range(len(adjusted))):
        candidate = adjusted[index] + rounding_residual
        if minimum_shot_seconds <= candidate <= maximum_shot_seconds:
            adjusted[index] = round(candidate, 3)
            break
    for shot, duration in zip(shots, adjusted, strict=True):
        shot["duration_seconds"] = duration
    return normalized, {
        "duration_adjustment_status": "NORMALIZED_TO_TARGET",
        "model_total_duration_seconds": original_total,
        "normalized_total_duration_seconds": sum(adjusted),
        "duration_adjustment_ratio": ratio,
    }


def _mask_key(key: str | None) -> str | None:
    if not key or not key.strip():
        return None
    cleaned = key.strip()
    if len(cleaned) <= 4:
        return "••••"
    return f"{'•' * 8}{cleaned[-4:]}"


class LocalLLMService:
    def __init__(self, database: Database, settings: Settings, secret_store: SecretStore | None = None) -> None:
        self.database = database
        self.settings = settings
        self.secret_store = secret_store or create_platform_services(settings).secret_store

    def _resolve_api_key(
        self,
        capability_json: dict[str, Any] | None = None,
        explicit_key: str | None = None,
        provider: str | None = None,
    ) -> str | None:
        if explicit_key and explicit_key.strip():
            return explicit_key.strip()
        resolved_provider = str(provider or (capability_json or {}).get("provider") or self.settings.llm_provider or "OLLAMA_LOOPBACK").strip().upper()
        if resolved_provider in {"OLLAMA", "OLLAMA_LOOPBACK"}:
            # A local Ollama runtime must not inherit a remote/cloud key from
            # the process environment or the legacy DeepSeek credential.
            return None
        if self.settings.llm_api_key and self.settings.llm_api_key.strip():
            return self.settings.llm_api_key.strip()
        import os

        for env_var in ("LOCAL_DRAMA_LLM_API_KEY", "LOCAL_DRAMA_OPENAI_COMPAT_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            val = os.environ.get(env_var)
            if val and val.strip():
                return val.strip()
        try:
            return self.secret_store.get(SecretRef("DeepSeekAPI", "default"))
        except OSError:
            # Credential-store availability must not break local Ollama or
            # obscure the stable LLM_API_KEY_REQUIRED remediation path.
            return None
        return None

    def client(
        self,
        model: str | None = None,
        *,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        provider_connection_id: str | None = None,
    ) -> LocalLLMClient:
        if provider_connection_id:
            from local_drama.application.provider_connections import ProviderConnectionService

            connection = ProviderConnectionService(self.database, self.settings).resolve_for_execution(provider_connection_id)
            resolved_provider = "OLLAMA_LOOPBACK" if str(connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
            resolved_base_url = str(connection["base_url"])
            resolved_model = model or connection.get("model") or self.settings.llm_model
            resolved_key = connection.get("secret")
        else:
            resolved_provider = provider or self.settings.llm_provider or "OLLAMA_LOOPBACK"
            resolved_base_url = base_url or self.settings.llm_base_url
            resolved_model = model or self.settings.llm_model
            resolved_key = self._resolve_api_key(explicit_key=api_key, provider=resolved_provider)
        return LocalLLMClient(
            resolved_base_url,
            resolved_model,
            provider=resolved_provider,
            api_key=resolved_key,
            allow_private_network=self.settings.allows_private_network,
        )

    def discover_ollama_models(self, base_url: str | None = None) -> dict[str, Any]:
        """Read the model catalog exposed by one allowed Ollama endpoint.

        Discovery only calls Ollama's read-only ``/api/tags`` endpoint. It does
        not load a model into memory, run inference, copy weights, or mutate the
        runtime. The normal local/private-network endpoint policy still applies.
        """
        # The catalog is specifically the local Ollama inventory. A remote
        # OpenAI-compatible default must not replace or suppress this scan.
        resolved_base_url = (base_url or "http://127.0.0.1:11434").strip()
        client = LocalLLMClient(
            resolved_base_url,
            "__catalog_discovery__",
            provider="OLLAMA_LOOPBACK",
            allow_private_network=self.settings.allows_private_network,
        )
        raw_models = client.tags()
        items: list[dict[str, Any]] = []
        for raw in raw_models:
            name = str(raw.get("name") or raw.get("model") or "").strip()
            if not name:
                continue
            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            families = details.get("families") if isinstance(details.get("families"), list) else []
            items.append(
                {
                    "name": name,
                    "model": str(raw.get("model") or name),
                    "modified_at": raw.get("modified_at"),
                    "size_bytes": int(raw.get("size") or 0),
                    "digest": str(raw.get("digest") or ""),
                    "format": str(details.get("format") or ""),
                    "family": str(details.get("family") or ""),
                    "families": [str(value) for value in families if value],
                    "parameter_size": str(details.get("parameter_size") or ""),
                    "quantization_level": str(details.get("quantization_level") or ""),
                }
            )
        items.sort(key=lambda item: str(item["name"]).casefold())
        return {
            "provider": "OLLAMA_LOOPBACK",
            "base_url": resolved_base_url.rstrip("/"),
            "items": items,
            "count": len(items),
            "scanned_at": _now(),
            "read_only": True,
            "runtime_contacted": True,
            "mutated": False,
        }

    def submit_probe(
        self,
        project_id: str,
        *,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        load_test: bool = True,
        allow_remote_outbound: bool = False,
        provider_connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue a durable four-level probe without persisting credentials."""
        if api_key and api_key.strip():
            raise DomainRuleError(
                "LOCAL_LLM_ASYNC_KEY_ENV_REQUIRED",
                "后台连接测试不会把 API Key 写入 Job；请通过受控环境变量提供密钥",
                suggested_action="设置 LOCAL_DRAMA_LLM_API_KEY 后重新提交，或仅在兼容同步测试中临时使用密钥",
            )
        if provider_connection_id:
            from local_drama.application.provider_connections import ProviderConnectionService

            selected_connection = ProviderConnectionService(self.database, self.settings).resolve_for_execution(provider_connection_id)
            resolved_provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
            resolved_base_url = str(selected_connection["base_url"]).strip()
            resolved_model = str(model or selected_connection.get("model") or "").strip()
        else:
            resolved_provider = (provider or self.settings.llm_provider or "OLLAMA_LOOPBACK").strip().upper()
            resolved_base_url = (base_url or self.settings.llm_base_url).strip()
            resolved_model = (model or self.settings.llm_model or "").strip()
        # Constructing the client performs provider/URL/model validation before
        # a durable command is accepted, without contacting the runtime.
        self.client(model=resolved_model, provider=resolved_provider, base_url=resolved_base_url, provider_connection_id=provider_connection_id)
        is_remote = endpoint_is_remote(resolved_base_url)
        if resolved_provider == "OPENAI_COMPAT" and is_remote and not allow_remote_outbound:
            raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "数据将离开本机：后台测试远程 LLM 需要显式确认出境安全许可")
        snapshot = {
            "schema_version": "localdrama.local-llm-probe-job.v1",
            "provider": resolved_provider,
            "base_url": resolved_base_url,
            "model": resolved_model,
            "load_test": bool(load_test),
            "allow_remote_outbound": bool(allow_remote_outbound),
            "credential_source": "PROVIDER_CONNECTION" if provider_connection_id else "SETTINGS_OR_ENV",
            "provider_connection_id": provider_connection_id,
            "secret_persisted": False,
        }
        fingerprint = hashlib.sha256(_json(snapshot).encode()).hexdigest()
        return JobService(self.database, self.settings).create_job(
            project_id,
            "LOCAL_LLM_PROBE",
            "PROJECT",
            project_id,
            "CPU",
            snapshot,
            f"local-llm-probe:{project_id}:{fingerprint}",
            priority=15,
            max_attempts=1,
        )

    def probe_job_result(self, job_id: str) -> dict[str, Any]:
        job = JobService(self.database, self.settings).get_job(job_id)
        if str(job["type"]) != "LOCAL_LLM_PROBE":
            raise DomainRuleError("LOCAL_LLM_PROBE_JOB_INVALID", "所选 Job 不是 LLM 连接测试")
        response: dict[str, Any] = {"job": job, "probe": None}
        if str(job["state"]) != "SUCCEEDED":
            return response
        artifacts = [
            artifact
            for attempt in job.get("attempts", [])
            for artifact in attempt.get("artifacts", [])
            if artifact.get("kind") == "LOCAL_LLM_PROBE_REPORT" and artifact.get("status") == "VERIFIED"
        ]
        if not artifacts:
            raise DomainRuleError("LOCAL_LLM_PROBE_REPORT_MISSING", "LLM 测试已完成但结果报告缺失")
        artifact = artifacts[-1]
        work_root = self.settings.work_root.resolve()
        path = controlled_path(
            work_root,
            str(artifact["sandbox_rel_path"]),
            must_exist=True,
            require_file=True,
            code="LOCAL_LLM_PROBE_REPORT_INVALID",
        )
        if path.stat().st_size > 1024 * 1024:
            raise DomainRuleError("LOCAL_LLM_PROBE_REPORT_INVALID", "LLM 测试结果报告无效")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(artifact["sha256"]):
            raise DomainRuleError("LOCAL_LLM_PROBE_REPORT_TAMPERED", "LLM 测试结果报告完整性校验失败")
        report = json.loads(raw.decode("utf-8"))
        response["probe"] = report.get("probe")
        return response

    def verified_probe_evidence(
        self,
        job_id: str,
        *,
        provider: str,
        base_url: str,
        model: str,
    ) -> dict[str, Any]:
        """Return level-4 probe evidence only when it matches this exact configuration."""
        result = self.probe_job_result(job_id)
        job = result["job"]
        probe = result.get("probe")
        if str(job["state"]) != "SUCCEEDED" or not isinstance(probe, dict):
            raise DomainRuleError(
                "LOCAL_LLM_VALIDATION_REQUIRED",
                "LLM 后台连接测试尚未成功完成",
                {"probe_job_id": job_id, "job_state": job["state"]},
            )
        snapshot = job.get("input_snapshot") or {}
        expected = {
            "provider": provider.strip().upper(),
            "base_url": base_url.strip().rstrip("/"),
            "model": model.strip(),
        }
        actual = {
            "provider": str(snapshot.get("provider") or "").strip().upper(),
            "base_url": str(snapshot.get("base_url") or "").strip().rstrip("/"),
            "model": str(snapshot.get("model") or "").strip(),
        }
        if actual != expected or not bool(snapshot.get("load_test")):
            raise DomainRuleError(
                "LOCAL_LLM_PROBE_CONFIG_MISMATCH",
                "LLM 配置已变化；请针对当前 Provider、地址和模型重新执行 4 级连接测试",
                {"probe_job_id": job_id, "changed_fields": [key for key in expected if actual[key] != expected[key]]},
            )
        if probe.get("status") != "PASS" or int(probe.get("probe_level_passed") or 0) < 4:
            raise DomainRuleError(
                "LOCAL_LLM_VALIDATION_REQUIRED",
                "LLM 必须通过 4 级后台验证后才能发布",
                {"probe_job_id": job_id, "probe": probe},
            )
        return probe

    def status(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        live_probe: bool = True,
    ) -> dict[str, Any]:
        resolved_provider = provider or self.settings.llm_provider or "OLLAMA_LOOPBACK"
        resolved_base_url = base_url or self.settings.llm_base_url
        resolved_model = model or self.settings.llm_model
        resolved_key = self._resolve_api_key(explicit_key=api_key, provider=resolved_provider)
        if not resolved_model:
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT epv.model_bundle_json, lr.base_url
                    FROM execution_profile_versions epv
                    LEFT JOIN local_runtimes lr ON lr.id = json_extract(epv.model_bundle_json, '$.runtime_id')
                    WHERE epv.capability='LLM_STORY_PARSE' AND epv.status='PUBLISHED'
                    ORDER BY epv.updated_at DESC LIMIT 1"""
                ).fetchone()
                if row and row["model_bundle_json"]:
                    try:
                        bundle = json.loads(row["model_bundle_json"])
                        resolved_model = bundle.get("model")
                        resolved_provider = bundle.get("provider", resolved_provider)
                        if row["base_url"]:
                            resolved_base_url = row["base_url"]
                    except Exception:
                        pass

        if not resolved_model:
            return {
                "status": "BLOCKED",
                "provider": resolved_provider,
                "base_url": resolved_base_url,
                "model": None,
                "has_api_key": bool(resolved_key),
                "masked_api_key": _mask_key(resolved_key),
                "error_code": "LOCAL_LLM_MODEL_REQUIRED",
            }
        if not live_probe:
            return {
                "status": "CONFIGURED",
                "provider": resolved_provider,
                "base_url": resolved_base_url,
                "model": resolved_model,
                "has_api_key": bool(resolved_key),
                "masked_api_key": _mask_key(resolved_key),
            }
        client = LocalLLMClient(
            resolved_base_url,
            resolved_model,
            provider=resolved_provider,
            api_key=resolved_key,
            allow_private_network=self.settings.allows_private_network,
        )
        probe_res = client.probe(load_test=True)
        probe_res["has_api_key"] = bool(resolved_key)
        probe_res["masked_api_key"] = _mask_key(resolved_key)
        return probe_res

    def sync_candidate(
        self,
        model: str | None = None,
        capability: str = "LLM_STORY_PARSE",
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        allow_remote_outbound: bool = False,
        actor: str = "local-user",
        probe_job_id: str | None = None,
        provider_connection_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            normalized_capability = normalize_capability(capability)
        except ValueError as error:
            raise DomainRuleError("PROFILE_CAPABILITY_INVALID", f"能力名称无效: {capability}") from error

        if provider_connection_id:
            from local_drama.application.provider_connections import ProviderConnectionService

            selected_connection = ProviderConnectionService(self.database, self.settings).resolve_for_execution(provider_connection_id)
            resolved_provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
            resolved_base_url = str(selected_connection["base_url"]).strip()
            selected_model = str(model or selected_connection.get("model") or "").strip()
            resolved_key = selected_connection.get("secret")
        else:
            resolved_provider = (provider or self.settings.llm_provider or "OLLAMA_LOOPBACK").strip().upper()
            resolved_base_url = (base_url or self.settings.llm_base_url).strip()
            selected_model = (model or self.settings.llm_model or "").strip()
            resolved_key = self._resolve_api_key(explicit_key=api_key, provider=resolved_provider)

        if not selected_model:
            return self.status(provider=resolved_provider, base_url=resolved_base_url, model=None, api_key=resolved_key)

        from urllib.parse import urlparse

        parsed = urlparse(resolved_base_url)
        is_remote = endpoint_is_remote(resolved_base_url)
        if resolved_provider == "OPENAI_COMPAT" and is_remote and not allow_remote_outbound:
            raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "数据将离开本机：使用远程 LLM Provider 需要显式确认出境安全许可")

        client = LocalLLMClient(
            resolved_base_url,
            selected_model,
            provider=resolved_provider,
            api_key=resolved_key,
            allow_private_network=self.settings.allows_private_network,
        )
        probe = (
            self.verified_probe_evidence(
                probe_job_id,
                provider=resolved_provider,
                base_url=resolved_base_url,
                model=selected_model,
            )
            if probe_job_id
            else client.probe(load_test=False)
        )

        if resolved_provider == "OPENAI_COMPAT":
            code = f"llm-openai-{_slug(selected_model)}-{_slug(normalized_capability)}"
            runtime_code = "openai-compat-remote" if is_remote else "openai-compat-local"
            runtime_title = "OpenAI 兼容远程 LLM Runtime" if is_remote else "OpenAI 兼容本地 LLM Runtime"
            transport = "HTTPS" if parsed.scheme == "https" else "LOOPBACK_HTTP" if not is_remote else "HTTP"
        else:
            code = f"local-llm-ollama-{_slug(selected_model)}"
            runtime_code = "ollama-loopback"
            runtime_title = "Ollama 本地 LLM Runtime"
            transport = "LOOPBACK_HTTP"

        runtime_id = _stable_id(f"runtime:{resolved_provider}:{resolved_base_url}")
        profile_id = _stable_id(f"profile:{code}")
        version_id = _stable_id(f"profile-version:{code}:1")
        now = _now()
        status = "CANDIDATE_UNVERIFIED" if probe["status"] == "PASS" else "CANDIDATE_BLOCKED"
        capability_json = {
            "provider": resolved_provider,
            "base_url": resolved_base_url,
            "model": selected_model,
            "capability": normalized_capability,
            "probe": probe,
            "published": False,
            "activation_required": True,
            "has_api_key": bool(resolved_key),
            "masked_api_key": _mask_key(resolved_key),
            "allow_remote_outbound": allow_remote_outbound if is_remote else True,
            "provider_connection_id": provider_connection_id,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO local_runtimes
                (id, code, title, transport, base_url, executable_ref, runtime_version, status, details_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 1, 'v2')
                ON CONFLICT(code) DO UPDATE SET title=excluded.title, base_url=excluded.base_url,
                transport=excluded.transport, status=excluded.status, details_json=excluded.details_json,
                updated_at=excluded.updated_at, revision=local_runtimes.revision+1""",
                (
                    runtime_id,
                    runtime_code,
                    runtime_title,
                    transport,
                    resolved_base_url,
                    "openai-compat" if resolved_provider == "OPENAI_COMPAT" else "ollama",
                    status,
                    _json(probe),
                    now,
                    now,
                    actor,
                ),
            )
            # A runtime with the same stable code may predate deterministic
            # identifiers.  The code is the unique business identity, so keep
            # the persisted row id and freeze that real id into the Profile.
            persisted_runtime = connection.execute(
                "SELECT id FROM local_runtimes WHERE code=?",
                (runtime_code,),
            ).fetchone()
            if persisted_runtime is None:
                raise DomainRuleError("LOCAL_LLM_RUNTIME_SYNC_FAILED", "LLM Runtime 主记录同步失败")
            runtime_id = str(persisted_runtime["id"])
            connection.execute(
                """INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'v2')
                ON CONFLICT(code) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at, revision=execution_profiles.revision+1""",
                (profile_id, code, f"{resolved_provider} {selected_model} {normalized_capability} 候选 Profile", now, now, actor),
            )
            # Legacy rows may already own this unique code under a random UUID.
            # Resolve the persisted identities after the code upsert so re-sync
            # cannot reference a phantom deterministic ID and fail its FK.
            persisted_profile = connection.execute(
                "SELECT id FROM execution_profiles WHERE code=?",
                (code,),
            ).fetchone()
            if persisted_profile is None:
                raise DomainRuleError("LOCAL_LLM_PROFILE_SYNC_FAILED", "LLM Profile 主记录同步失败")
            profile_id = str(persisted_profile["id"])
            current = connection.execute(
                "SELECT id,status FROM execution_profile_versions WHERE execution_profile_id=? AND version_no=1",
                (profile_id,),
            ).fetchone()
            if current is not None:
                version_id = str(current["id"])
            readiness = probe if probe_job_id or not (current and current["status"] == "PUBLISHED") else client.probe(load_test=True)
            profile_status = (
                "PUBLISHED"
                if current and current["status"] == "PUBLISHED" and readiness["status"] == "PASS"
                else "CANDIDATE_UNVERIFIED"
                if probe["status"] == "PASS"
                else "CANDIDATE_BLOCKED"
            )
            capability_json["readiness_probe"] = readiness
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
                 parameter_schema_json, status, manifest_sha256, capability_json, worker_policy,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, NULL, ?, 'ONE_LOCAL_LLM_TASK', ?, ?, ?, 1, 'v2')
                ON CONFLICT(execution_profile_id, version_no) DO UPDATE SET model_bundle_json=excluded.model_bundle_json,
                input_contract_json=excluded.input_contract_json, parameter_schema_json=excluded.parameter_schema_json,
                capability=excluded.capability,status=excluded.status, capability_json=excluded.capability_json, updated_at=excluded.updated_at,
                revision=execution_profile_versions.revision+1""",
                (
                    version_id,
                    profile_id,
                    normalized_capability,
                    _json({"runtime_id": runtime_id, "model": selected_model, "provider": resolved_provider, "provider_connection_id": provider_connection_id}),
                    _json({"source": "TXT|MD|DOCX|IMAGE|VIDEO", "output": "SCRIPT_BREAKDOWN_DRAFT|QC_REPORT", "transport": transport}),
                    _json({"temperature": {"value": 0, "locked": True}}),
                    profile_status,
                    _json(capability_json),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'LOCAL_LLM_CANDIDATE_SYNCED', 'execution_profile_version', ?, ?, ?)",
                (
                    actor,
                    version_id,
                    f"同步 {resolved_provider} LLM 候选 Profile",
                    _json({"probe": probe, "model": selected_model, "capability": normalized_capability, "provider": resolved_provider}),
                ),
            )
        return {"profile_version_id": version_id, "profile_code": code, "status": profile_status, "probe": probe}

    def publish(
        self,
        profile_version_id: str,
        api_key: str | None = None,
        allow_remote_outbound: bool = False,
        actor: str = "local-user",
        probe_job_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT version.id, version.capability, version.capability_json, version.model_bundle_json,
                          version.version_no, profile.code AS profile_code
                   FROM execution_profile_versions AS version
                   JOIN execution_profiles AS profile ON profile.id=version.execution_profile_id
                   WHERE version.id=?""",
                (profile_version_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("PROFILE_NOT_FOUND", "Profile 版本不存在")
            try:
                profile_capability = normalize_capability(str(row["capability"]))
            except ValueError as error:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_INVALID",
                    "Profile capability 不是可识别的 canonical capability",
                    {"profile_version_id": profile_version_id},
                ) from error
            if profile_capability not in {"LLM_STORY_PARSE", "QC_VISUAL", "QC_FACE", "QC_IDENTITY"}:
                raise DomainRuleError("PROFILE_CAPABILITY_MISMATCH", "Profile 不是本地 LLM 能力")
            capability = json.loads(row["capability_json"] or "{}")
            bundle = json.loads(row["model_bundle_json"] or "{}")
            provider_connection_id = str(bundle.get("provider_connection_id") or capability.get("provider_connection_id") or "").strip() or None
            if provider_connection_id:
                from local_drama.application.provider_connections import ProviderConnectionService

                selected_connection = ProviderConnectionService(self.database, self.settings).resolve_for_execution(provider_connection_id)
                provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
                base_url = str(selected_connection["base_url"])
                model = str(bundle.get("model") or selected_connection.get("model") or "")
                resolved_key = selected_connection.get("secret")
            else:
                provider = str(capability.get("provider") or "OLLAMA_LOOPBACK")
                base_url = str(capability.get("base_url") or self.settings.llm_base_url)
                model = str(capability.get("model") or "")
                resolved_key = self._resolve_api_key(capability, explicit_key=api_key)

            is_remote = endpoint_is_remote(base_url)
            if provider == "OPENAI_COMPAT" and is_remote and not (allow_remote_outbound or capability.get("allow_remote_outbound")):
                raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "数据将离开本机：发布远程 LLM Profile 需要显式确认出境安全许可")

            client = LocalLLMClient(
                base_url,
                model,
                provider=provider,
                api_key=resolved_key,
                allow_private_network=self.settings.allows_private_network,
            )
            probe = (
                self.verified_probe_evidence(
                    probe_job_id,
                    provider=provider,
                    base_url=base_url,
                    model=model,
                )
                if probe_job_id
                else client.probe(load_test=True)
            )
            if probe.get("status") != "PASS" or probe.get("probe_level_passed", 4) < 4:
                raise DomainRuleError("LOCAL_LLM_VALIDATION_REQUIRED", "LLM 必须通过 4 级验证后才能发布", {"model": model, "probe": probe})

            capability["readiness_probe"] = probe
            capability["published"] = True
            capability["has_api_key"] = bool(resolved_key)
            capability["masked_api_key"] = _mask_key(resolved_key)
            now = _now()
            connection.execute(
                "UPDATE execution_profile_versions SET status='PUBLISHED', capability_json=?, updated_at=?, revision=revision+1 WHERE id=?",
                (_json(capability), now, profile_version_id),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'LOCAL_LLM_PROFILE_PUBLISHED', 'execution_profile_version', ?, ?, ?)",
                (
                    actor,
                    profile_version_id,
                    f"发布 {provider} LLM Profile",
                    _json({"model": model, "provider": provider, "status": "PUBLISHED", "probe_level_passed": 4}),
                ),
            )
        return {
            "profile_version_id": profile_version_id,
            "profile_code": str(row["profile_code"]),
            "version_no": int(row["version_no"]),
            "status": "PUBLISHED",
            "probe": probe,
            "publication": {
                "destination": "GLOBAL_CAPABILITY_CATALOG",
                "scope": "LOCAL_STUDIO",
                "consumer_scope": "ALL_PROJECTS",
                "capability": profile_capability,
                "model": model,
                "provider": provider,
                "published_at": now,
            },
        }

    def expand_video_prompt(
        self,
        profile_version_id: str,
        story: str,
        *,
        api_key: str | None = None,
        remember_api_key: bool = False,
        allow_remote_outbound: bool = False,
        language: str = "zh-CN",
        output_spec: dict[str, float | int | str] | None = None,
        inference_options: dict[str, Any] | None = None,
        target_kind: str = "VIDEO",
    ) -> dict[str, Any]:
        """Expand one creator sentence into a bounded, production-ready T2V shot.

        The selected immutable Profile is the sole provider authority.  The API
        key is resolved from the explicit request or the controlled process
        environment and is never returned or persisted.
        """
        normalized_story = story.strip()
        if len(normalized_story) < 2:
            raise DomainRuleError("VIDEO_STORY_REQUIRED", "请输入一句完整的视频描述")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status,capability,capability_json,model_bundle_json FROM execution_profile_versions WHERE id=?",
                (profile_version_id,),
            ).fetchone()
        if row is None or str(row["status"]) != "PUBLISHED" or str(row["capability"]) != "LLM_STORY_PARSE":
            raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "请选择已发布的剧本规划 LLM Profile")
        capability = json.loads(str(row["capability_json"] or "{}"))
        bundle = json.loads(str(row["model_bundle_json"] or "{}"))
        provider_connection_id = str(bundle.get("provider_connection_id") or "").strip() or None
        connection_secret: str | None = None
        if provider_connection_id:
            from local_drama.application.provider_connections import ProviderConnectionService

            selected_connection = ProviderConnectionService(self.database, self.settings).resolve_for_execution(provider_connection_id)
            provider = "OLLAMA_LOOPBACK" if selected_connection["protocol"].upper() == "OLLAMA" else "OPENAI_COMPAT"
            base_url = str(selected_connection["base_url"]).strip()
            model = str(bundle.get("model") or selected_connection.get("model") or "").strip()
            connection_secret = selected_connection.get("secret")
        else:
            # Legacy published Profiles remain executable, but newly bound
            # Profiles take all endpoint and credential authority from their
            # Provider Connection above.
            provider = str(bundle.get("provider") or capability.get("provider") or "OLLAMA_LOOPBACK").strip().upper()
            base_url = str(capability.get("base_url") or self.settings.llm_base_url).strip()
            model = str(bundle.get("model") or capability.get("model") or "").strip()
        if not model:
            raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "已发布 Profile 未记录显式模型")

        is_remote = endpoint_is_remote(base_url)
        if provider == "OPENAI_COMPAT" and is_remote and not allow_remote_outbound:
            raise DomainRuleError(
                "OUTBOUND_CONFIRMATION_REQUIRED",
                "这句话将发送到所选远程 Provider；请先确认允许本次出境处理",
            )
        resolved_key = connection_secret or self._resolve_api_key(capability, explicit_key=api_key, provider=provider)
        if provider == "OPENAI_COMPAT" and not resolved_key:
            raise DomainRuleError(
                "LLM_API_KEY_REQUIRED",
                "所选 Provider Connection 没有可用密钥；请先在模型设置中完成连接配置",
            )
        spec = dict(output_spec or {})
        duration_seconds = float(spec.get("duration_seconds") or 4.0)
        if not math.isfinite(duration_seconds) or not 0.5 <= duration_seconds <= 120:
            raise DomainRuleError("VIDEO_OUTPUT_SPEC_INVALID", "成片时长规格无效")
        aspect_ratio = str(spec.get("aspect_ratio") or "未声明")[:32]
        width = int(spec["width"]) if "width" in spec else None
        height = int(spec["height"]) if "height" in spec else None
        fps = float(spec["fps"]) if "fps" in spec else None
        if language not in {"zh-CN", "en-US"}:
            raise DomainRuleError("VIDEO_PROMPT_LANGUAGE_INVALID", "提示词语言只支持 zh-CN 或 en-US")
        prompt_language = "简体中文" if language == "zh-CN" else "English"
        client = LocalLLMClient(
            base_url,
            model,
            provider=provider,
            api_key=resolved_key,
            allow_private_network=self.settings.allows_private_network,
        )
        image_only = target_kind.strip().upper() == "IMAGE"
        target_instruction = (
            f"keyframe_prompt 是最终执行提示词，目标为 {aspect_ratio} 画幅"
            f"{f'、{width}×{height}' if width and height else ''} 的单张静态作品。"
            if image_only
            else f"video_prompt 使用{prompt_language}，目标为 {duration_seconds:.3f} 秒、{aspect_ratio} 画幅"
            f"{f'、{width}×{height}' if width and height else ''}{f'、{fps:g} fps' if fps else ''} 的连续单镜头。"
        )
        system_prompt = "".join(
            [
                "你是视觉概念设计师。把用户的一句话改写为单张文生图提示词。"
                if image_only
                else "你是短视频导演。把用户的一句话改写为单镜头文生视频提示词。",
                "只输出 JSON 对象，且只能包含 title、video_prompt、keyframe_prompt、subject_action、environment、shot_type、camera_movement 七个字符串字段。",
                "shot_type 必须是 CLOSE_UP、MEDIUM_CLOSE_UP、MEDIUM、FULL、WIDE、EXTREME_WIDE 之一；",
                "camera_movement 必须是 STATIC、DOLLY_IN、DOLLY_OUT、PAN_LEFT、PAN_RIGHT、TILT_UP、TILT_DOWN、",
                "TRACK_LEFT、TRACK_RIGHT、CRANE_UP、CRANE_DOWN、ORBIT 之一。",
                target_instruction,
                "必须忠于原句，不新增人物身份、品牌、对白或剧情转折；只安排一个清晰主体动作和一种镜头运动，",
                "并写清环境、光线、构图与稳定性要求。keyframe_prompt 必须使用 English（供 SDXL/CLIP 文生图模型执行），",
                "描述最终要生成的单张静态作品，" if image_only else "描述动作开始前最适合做 I2V 首帧的单张静态画面，",
                "保留原句中的主体、服装、道具、环境、光线、景别和构图，",
                "但不得包含运镜、时间变化或多个连续动作。即使 video_prompt 使用简体中文，keyframe_prompt 也必须是 English。",
            ]
        )
        output = client.chat_json(system_prompt, normalized_story, inference_options=inference_options)
        required_keys = {"title", "video_prompt", "keyframe_prompt", "subject_action", "environment", "shot_type", "camera_movement"}

        def has_cjk(value: object) -> bool:
            return any("\u4e00" <= character <= "\u9fff" for character in str(value))

        allowed_shot_values = {"CLOSE_UP", "MEDIUM_CLOSE_UP", "MEDIUM", "FULL", "WIDE", "EXTREME_WIDE", "特写", "近景", "中近景", "中景", "全景", "远景"}
        allowed_movement_values = {
            "STATIC",
            "DOLLY_IN",
            "DOLLY_OUT",
            "PAN_LEFT",
            "PAN_RIGHT",
            "TILT_UP",
            "TILT_DOWN",
            "TRACK_LEFT",
            "TRACK_RIGHT",
            "CRANE_UP",
            "CRANE_DOWN",
            "ORBIT",
            "固定",
            "静止",
            "缓慢推镜",
            "推镜",
            "拉镜",
            "跟拍",
            "左摇",
            "右摇",
        }
        structurally_valid = (
            isinstance(output, dict)
            and set(output) == required_keys
            and all(isinstance(output.get(key), str) and str(output[key]).strip() for key in required_keys)
            and not has_cjk(output.get("keyframe_prompt"))
            and str(output.get("shot_type") or "").strip() in allowed_shot_values
            and str(output.get("camera_movement") or "").strip() in allowed_movement_values
        )
        if not structurally_valid:
            # One bounded repair attempt keeps the public contract strict while
            # tolerating a model's first malformed JSON/schema response.
            output = client.chat_json(
                system_prompt + " 上一次输出未通过结构或语言校验；这是唯一一次修复机会，必须逐字遵守字段集合、枚举，并确保 keyframe_prompt 只含 English。",
                normalized_story,
                inference_options=inference_options,
            )
        if remember_api_key and api_key and api_key.strip():
            try:
                self.secret_store.put(SecretRef("DeepSeekAPI", "default"), api_key)
            except (OSError, ValueError) as error:
                raise DomainRuleError(
                    "LLM_CREDENTIAL_STORE_FAILED",
                    "DeepSeek 请求成功，但无法保存到操作系统安全凭据库；请取消记住密钥后重试",
                ) from error
        required = ("title", "video_prompt", "keyframe_prompt", "subject_action", "environment", "shot_type", "camera_movement")
        missing = [key for key in required if not isinstance(output.get(key), str) or not str(output[key]).strip()]
        unexpected = sorted(set(output) - set(required))
        valid_shot_types = {"CLOSE_UP", "MEDIUM_CLOSE_UP", "MEDIUM", "FULL", "WIDE", "EXTREME_WIDE"}
        valid_movements = {
            "STATIC",
            "DOLLY_IN",
            "DOLLY_OUT",
            "PAN_LEFT",
            "PAN_RIGHT",
            "TILT_UP",
            "TILT_DOWN",
            "TRACK_LEFT",
            "TRACK_RIGHT",
            "CRANE_UP",
            "CRANE_DOWN",
            "ORBIT",
        }
        shot_aliases = {"特写": "CLOSE_UP", "近景": "MEDIUM_CLOSE_UP", "中近景": "MEDIUM_CLOSE_UP", "中景": "MEDIUM", "全景": "WIDE", "远景": "EXTREME_WIDE"}
        movement_aliases = {
            "固定": "STATIC",
            "静止": "STATIC",
            "缓慢推镜": "DOLLY_IN",
            "推镜": "DOLLY_IN",
            "拉镜": "DOLLY_OUT",
            "跟拍": "TRACK_RIGHT",
            "左摇": "PAN_LEFT",
            "右摇": "PAN_RIGHT",
        }
        raw_shot_type = str(output.get("shot_type") or "").strip()
        raw_camera_movement = str(output.get("camera_movement") or "").strip()
        shot_type = shot_aliases.get(raw_shot_type, raw_shot_type.upper())
        camera_movement = movement_aliases.get(raw_camera_movement, raw_camera_movement.upper())
        if missing or unexpected or has_cjk(output.get("keyframe_prompt")) or shot_type not in valid_shot_types or camera_movement not in valid_movements:
            raise DomainRuleError(
                "LOCAL_LLM_OUTPUT_INVALID",
                "LLM 返回的视频规划不符合严格结构契约",
                {
                    "missing_fields": missing,
                    "unexpected_fields": unexpected,
                    "keyframe_prompt_must_be_english": True,
                    "shot_type": shot_type,
                    "camera_movement": camera_movement,
                },
            )
        if is_remote:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES ('local-user','producer','ONE_SENTENCE_REMOTE_LLM_CALLED','execution_profile_version',?,?,?)""",
                    (
                        profile_version_id,
                        "经用户本次确认向远端 Provider 发送一句话规划请求",
                        _json(
                            {
                                "provider_connection_id": provider_connection_id,
                                "provider": provider,
                                "model": model,
                                "story_sha256": hashlib.sha256(normalized_story.encode("utf-8")).hexdigest(),
                                "story_characters": len(normalized_story),
                                "content_recorded": False,
                            }
                        ),
                    ),
                )
        title = str(output["title"]).strip()[:200]
        prompt = str(output["video_prompt"]).strip()[:4000]
        return {
            "schema_version": "localdrama.one-sentence-video-plan.v1",
            "title": title,
            "video_prompt": prompt,
            "keyframe_prompt": str(output["keyframe_prompt"]).strip()[:4000],
            "director_intent": {
                "schema_version": "director-intent.v3",
                "shot_type": shot_type,
                "composition": {"preset": "AUTO", "framing": "由 DeepSeek 提示词裁决"},
                "subject_action": str(output["subject_action"]).strip()[:1000],
                "performance": {"emotion": None, "intensity": 0.5, "body_action": str(output["subject_action"]).strip()[:1000]},
                "camera_plan": None,
                "target_duration_ms": round(duration_seconds * 1000),
                "dialogue": "",
                "environment": str(output["environment"]).strip()[:1000],
                "continuity": "单镜头连续动作，无跳切",
                "creative_intent": normalized_story,
            },
            "camera_movement": camera_movement,
            "language": language,
            "output_spec": spec,
            "provider": provider,
            "model": model,
            "provider_connection_id": provider_connection_id,
            "remote": is_remote,
            "network_contacted": is_remote,
            "secret_persisted": bool(remember_api_key and api_key and api_key.strip()),
            "credential_store": "WINDOWS_CREDENTIAL_MANAGER" if remember_api_key and api_key and api_key.strip() else None,
        }

    def _breakdown_context(self, session_id: str, profile_version_id: str) -> tuple[Any, dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.project_id, s.source_document_version_id, s.id AS session_id, s.status, v.extracted_text_rel,
                v.source_document_id, v.text_sha256, v.sha256 AS source_sha256, v.metadata_json
                FROM import_sessions s JOIN source_document_versions v ON v.id=s.source_document_version_id
                JOIN execution_profile_versions p ON p.id=? WHERE s.id=? AND p.status='PUBLISHED'
                AND p.capability='LLM_STORY_PARSE'""",
                (profile_version_id, session_id),
            ).fetchone()
            profile = connection.execute("SELECT capability_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None or profile is None:
            raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "所选 Profile 不是已发布的剧本拆解 LLM 能力")
        if str(row["status"]) not in {"COMMITTED", "BREAKDOWN_READY"}:
            raise DomainRuleError("IMPORT_SESSION_NOT_COMMITTED", "只有已确认 commit 的导入会话可以提交 AI 拆解任务")
        capability = json.loads(profile["capability_json"] or "{}")
        model = capability.get("model")
        if not model:
            raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "已发布 Profile 未记录显式模型")
        return row, capability

    def _breakdown_target(self, project_id: str, episode_id: str | None) -> dict[str, Any] | None:
        if not episode_id:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT e.id,e.code,e.target_duration_ms
                FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE e.id=? AND s.project_id=?""",
                (episode_id, project_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError(
                "BREAKDOWN_TARGET_EPISODE_INVALID",
                "AI 拆解目标集不存在或不属于当前项目",
                {"episode_id": episode_id, "project_id": project_id},
            )
        target_duration_ms = int(row["target_duration_ms"] or 0)
        if target_duration_ms <= 0:
            raise DomainRuleError(
                "BREAKDOWN_TARGET_DURATION_INVALID",
                "AI 拆解目标集没有有效目标时长",
                {"episode_id": episode_id},
            )
        return {
            "target_episode_id": str(row["id"]),
            "target_episode_code": str(row["code"]),
            "target_duration_seconds": target_duration_ms / 1000,
        }

    def enqueue_breakdown(
        self,
        session_id: str,
        profile_version_id: str | None,
        idempotency_key: str,
        *,
        target_episode_id: str | None = None,
        source_paragraph_start: int | None = None,
        source_paragraph_end: int | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Persist a local-LLM request as a Job without contacting Ollama.

        The immutable snapshot is deliberately bounded to source/profile facts;
        no generated scene or shot is materialized by this command.
        """
        if not profile_version_id:
            raise DomainRuleError("LOCAL_LLM_PROFILE_REQUIRED", "剧本拆解必须显式选择已发布的本地 LLM Profile")
        row, capability = self._breakdown_context(session_id, profile_version_id)
        model = str(capability["model"])
        base_url = str(capability.get("base_url") or self.settings.llm_base_url)
        runtime_contract = _profile_runtime_contract(capability, self.settings.llm_base_url)
        target = self._breakdown_target(str(row["project_id"]), target_episode_id)
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        paragraph_count = int(metadata.get("paragraph_count") or 0)
        selected_start = int(source_paragraph_start or 1)
        selected_end = int(source_paragraph_end or paragraph_count)
        if paragraph_count <= 0 or selected_start > selected_end or selected_end > paragraph_count:
            raise DomainRuleError(
                "BREAKDOWN_SOURCE_RANGE_INVALID",
                "AI 拆解原文段落范围无效",
                {
                    "source_paragraph_start": selected_start,
                    "source_paragraph_end": selected_end,
                    "paragraph_count": paragraph_count,
                },
            )
        project_root = self._project_root(str(row["project_id"]))
        text_path = controlled_path(
            project_root,
            str(row["extracted_text_rel"]),
            must_exist=True,
            require_file=True,
            code="SOURCE_TEXT_NOT_FOUND",
        )
        selected_source, selected_offsets = _numbered_source_paragraphs(
            text_path.read_text(encoding="utf-8"),
            skip_headings=True,
            paragraph_start=selected_start,
            paragraph_end=selected_end,
        )
        if not selected_offsets:
            raise DomainRuleError("BREAKDOWN_SOURCE_RANGE_EMPTY", "所选原文范围只有空行或章节标题，无法生成本集草稿")
        if len(selected_source) > _BREAKDOWN_MAX_SOURCE_CHARACTERS:
            raise DomainRuleError(
                "BREAKDOWN_SOURCE_RANGE_TOO_LARGE",
                f"本次选择约 {len(selected_source)} 字，超过单次 AI 拆解上限 {_BREAKDOWN_MAX_SOURCE_CHARACTERS} 字；请按一个章节或更小段落范围提交",
                {
                    "selected_character_count": len(selected_source),
                    "selected_paragraph_count": len(selected_offsets),
                    "maximum_character_count": _BREAKDOWN_MAX_SOURCE_CHARACTERS,
                },
            )
        snapshot = {
            "schema_version": "localdrama.script-breakdown-job.v3",
            "import_session_id": session_id,
            "source_document_version_id": str(row["source_document_version_id"]),
            "source_sha256": str(row["source_sha256"]),
            "source_text_sha256": str(row["text_sha256"]),
            "profile_version_id": profile_version_id,
            "profile_capability_sha256": _sha256_json(runtime_contract),
            "model": model,
            "base_url": base_url,
            "provider": str(runtime_contract.get("provider") or "OLLAMA_LOOPBACK").strip().upper(),
            "automatic_apply": False,
            "requires_human_action": True,
            "source_paragraph_start": selected_start,
            "source_paragraph_end": selected_end,
            "source_paragraph_count": paragraph_count,
            "selected_source_character_count": len(selected_source),
            "selected_source_paragraph_count": len(selected_offsets),
            **(target or {}),
        }
        return JobService(self.database, self.settings).create_job(
            str(row["project_id"]),
            "SCRIPT_BREAKDOWN_LOCAL_LLM",
            "IMPORT_SESSION",
            session_id,
            "CPU",
            snapshot,
            idempotency_key,
            execution_profile_version_id=profile_version_id,
            priority=60,
            # Local model failures require an explicit operator retry; this
            # prevents a broken prompt/model from being hammered automatically.
            max_attempts=1,
            actor=actor,
        )

    def _assert_job_can_persist(self, job_id: str, session_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT type,subject_type,subject_id,state FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("JOB_NOT_FOUND", "AI 拆解 Job 不存在", {"job_id": job_id})
        if str(row["type"]) != "SCRIPT_BREAKDOWN_LOCAL_LLM" or str(row["subject_type"]) != "IMPORT_SESSION" or str(row["subject_id"]) != session_id:
            raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_INVALID", "AI 拆解 Job 与导入会话不匹配")
        if str(row["state"]) == "CANCEL_REQUESTED":
            raise DomainRuleError("JOB_CANCELLED", "AI 拆解已请求取消；不会保存模型输出")
        if str(row["state"]) not in {"CLAIMED", "RUNNING"}:
            raise DomainRuleError("LOCAL_LLM_JOB_NOT_RUNNING", "只有正在执行的 AI 拆解 Job 可以保存草稿")

    def breakdown(
        self,
        session_id: str,
        profile_version_id: str,
        *,
        job_id: str | None = None,
        input_snapshot: dict[str, Any] | None = None,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        """Execute the model call; production routes enqueue this via JobService.

        ``job_id`` makes the draft id deterministic so lease recovery cannot
        duplicate a draft after a crash between persistence and Job completion.
        """
        row, capability = self._breakdown_context(session_id, profile_version_id)
        execution_snapshot = input_snapshot or {}
        model = str(capability["model"])
        base_url = str(capability.get("base_url") or self.settings.llm_base_url)
        runtime_contract = _profile_runtime_contract(capability, self.settings.llm_base_url)
        target = self._breakdown_target(
            str(row["project_id"]),
            str(input_snapshot.get("target_episode_id")) if input_snapshot and input_snapshot.get("target_episode_id") else None,
        )
        if input_snapshot is not None:
            expected = {
                "import_session_id": session_id,
                "source_document_version_id": str(row["source_document_version_id"]),
                "source_sha256": str(row["source_sha256"]),
                "source_text_sha256": str(row["text_sha256"]),
                "profile_version_id": profile_version_id,
                "profile_capability_sha256": _sha256_json(runtime_contract),
                "model": model,
                "base_url": base_url,
                "automatic_apply": False,
                "requires_human_action": True,
                **(target or {}),
            }
            if any(input_snapshot.get(key) != value for key, value in expected.items()):
                raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_STALE", "AI 拆解 Job 的源文本或 Profile 快照已变化")
        draft_id = _stable_id(f"breakdown-job:{job_id}") if job_id else str(uuid.uuid4())
        if job_id:
            self._assert_job_can_persist(job_id, session_id)
            with self.database.connect() as connection:
                existing = connection.execute("SELECT draft_json,status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
            if existing is not None:
                return {
                    "id": draft_id,
                    "status": str(existing["status"]),
                    "profile_version_id": profile_version_id,
                    "draft": json.loads(str(existing["draft_json"])),
                    "idempotent_replay": True,
                    "automatic_apply": False,
                    "requires_human_action": True,
                }
        if on_progress:
            on_progress({"phase": "CALLING_LOCAL_LLM", "percent": 20})
        project_root = self._project_root(str(row["project_id"]))
        text_path = controlled_path(
            project_root,
            str(row["extracted_text_rel"]),
            must_exist=True,
            require_file=True,
            code="SOURCE_TEXT_NOT_FOUND",
        )
        source_bytes = text_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != str(row["text_sha256"]):
            raise DomainRuleError("SOURCE_TEXT_CHANGED", "剧本提取文本 hash 已变化，拒绝执行旧 Job 快照")
        source_text = source_bytes.decode("utf-8")
        selected_paragraph_start = int(execution_snapshot.get("source_paragraph_start") or 1)
        selected_paragraph_end = int(execution_snapshot.get("source_paragraph_end") or int(execution_snapshot.get("source_paragraph_count") or 0)) or None
        numbered_source_text, paragraph_offsets = _numbered_source_paragraphs(
            source_text,
            skip_headings=True,
            paragraph_start=selected_paragraph_start,
            paragraph_end=selected_paragraph_end,
        )
        if not paragraph_offsets:
            raise DomainRuleError("BREAKDOWN_SOURCE_RANGE_EMPTY", "所选原文范围只有空行或章节标题，无法生成本集草稿")
        provider = str(capability.get("provider") or "OLLAMA_LOOPBACK")
        api_key = self._resolve_api_key(capability)
        duration_contract = ""
        if target:
            target_seconds = float(target["target_duration_seconds"])
            minimum_seconds = target_seconds * 0.8
            maximum_seconds = target_seconds * 1.2
            duration_contract = (
                f" 本次草稿只面向 {target['target_episode_code']}，目标成片时长为 {target_seconds:g} 秒。"
                f" 所有镜头 duration_seconds 的合计必须在 {minimum_seconds:g} 到 {maximum_seconds:g} 秒之间；"
                "单镜建议 1 到 15 秒。不要通过增加原文不存在的剧情来凑时长，应压缩镜头数量与节奏。"
            )
        response_schema = json.loads(_json(_BREAKDOWN_RESPONSE_SCHEMA))
        response_schema["properties"]["source_passages"]["items"]["properties"]["paragraph_no"]["maximum"] = len(paragraph_offsets)
        response_schema["properties"]["scenes"]["items"]["properties"]["source_paragraph_nos"]["items"]["maximum"] = len(paragraph_offsets)
        required_paragraph_instruction = (
            f" 必须覆盖的 P 编号全集是 {sorted(paragraph_offsets)}；返回前自检所有 scene.source_paragraph_nos 的并集必须与这个全集完全相同，不得缺号或越界。"
        )
        output = LocalLLMClient(
            base_url,
            model,
            provider=provider,
            api_key=api_key,
            allow_private_network=self.settings.allows_private_network,
        ).chat_json(
            "你是本地剧本拆解器。最终答案只输出 JSON 对象，顶层必须包含 scenes、confidence、questions。输入已排除章节标题，每个 P 段都是本集必须覆盖的叙事正文；必须按原文顺序拆场，并让全部 P 段至少被一个 scene 引用。每个 scene 必须包含 scene_no、title、summary、characters、source_paragraph_nos、shots；source_paragraph_nos 必须至少列出一个实际描述该场内容的原文 P 编号，只能填写输入中真实存在的编号，不得把 P 编号当作场次序号盲填，不要返回顶层 source_passages，不要返回 quote。每个 shot 必须包含 shot_no、visual、action、dialogue、duration_seconds。confidence 必须是 {overall:0到1,notes:字符串数组}；questions 是待人工确认的字符串数组。P 编号只用于引用，不得写进场景正文、镜头或对白。每条非空 dialogue 只能逐字摘录自该 scene 的 source_paragraph_nos 所指原文；可以添加说话人前缀，但不得转述、改写或补写。原文没有明确对白时必须返回空字符串。不得臆造原文不存在的关键事实。"
            + required_paragraph_instruction
            + duration_contract,
            numbered_source_text,
            json_schema=response_schema,
            inference_options={"num_ctx": _BREAKDOWN_CONTEXT_TOKENS},
        )
        if job_id:
            self._assert_job_can_persist(job_id, session_id)
        if on_progress:
            on_progress({"phase": "VALIDATING_OUTPUT", "percent": 80})
        duration_adjustment: dict[str, Any] = {}
        if target:
            output, duration_adjustment = _normalize_breakdown_durations(
                output,
                float(target["target_duration_seconds"]),
            )
        draft, evidence = _validate_breakdown_output(
            _normalize_scene_source_passages(output, required_paragraphs=set(paragraph_offsets)),
            source_text,
            target_episode_id=str(target["target_episode_id"]) if target else None,
            target_duration_seconds=float(target["target_duration_seconds"]) if target else None,
            sanitize_ungrounded_dialogue=True,
            paragraph_offsets=paragraph_offsets,
            required_source_paragraph_nos=set(paragraph_offsets),
            minimum_scene_source_similarity=0.15,
            sanitize_ungrounded_scenes=True,
        )
        evidence.update(duration_adjustment)
        evidence.update(
            {
                "source_paragraph_start": selected_paragraph_start,
                "source_paragraph_end": selected_paragraph_end,
                "source_paragraph_count": execution_snapshot.get("source_paragraph_count"),
            }
        )
        evidence.update(
            {
                "schema_version": "localdrama.script-breakdown-evidence.v1",
                "source": "model_output",
                "model": model,
                "profile_version_id": profile_version_id,
                "job_id": job_id,
            }
        )
        now = _now()
        with self.database.transaction() as connection:
            if job_id:
                persisted_job = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
                if persisted_job is None or str(persisted_job["state"]) == "CANCEL_REQUESTED":
                    raise DomainRuleError("JOB_CANCELLED", "AI 拆解已请求取消；不会保存模型输出")
                if str(persisted_job["state"]) not in {"CLAIMED", "RUNNING"}:
                    raise DomainRuleError("LOCAL_LLM_JOB_NOT_RUNNING", "AI 拆解 Job 状态已变化，拒绝保存模型输出")
            connection.execute(
                "INSERT OR IGNORE INTO script_breakdown_drafts (id, project_id, source_document_version_id, import_session_id, draft_json, confidence_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT_READY', ?, ?, 'local-llm', 1, 'v2')",
                (
                    draft_id,
                    row["project_id"],
                    row["source_document_version_id"],
                    session_id,
                    _json(draft),
                    _json(evidence),
                    now,
                    now,
                ),
            )
            connection.execute("UPDATE import_sessions SET status='BREAKDOWN_READY', updated_at=?, revision=revision+1 WHERE id=?", (now, session_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, job_id, summary, metadata_redacted_json) VALUES ('local-llm', 'producer', 'SCRIPT_BREAKDOWN_COMPLETED', 'script_breakdown_draft', ?, ?, ?, ?)",
                (
                    draft_id,
                    job_id,
                    "本地 LLM 完成剧本拆解草稿（等待人工应用）",
                    _json({"session_id": session_id, "profile_version_id": profile_version_id, "model": model, "job_id": job_id, "automatic_apply": False}),
                ),
            )
        if on_progress:
            on_progress({"phase": "DRAFT_READY", "percent": 95, "draft_id": draft_id})
        return {
            "id": draft_id,
            "status": "DRAFT_READY",
            "profile_version_id": profile_version_id,
            "draft": draft,
            "idempotent_replay": False,
            "automatic_apply": False,
            "requires_human_action": True,
        }

    def list_breakdown_drafts(self, project_id: str) -> list[dict[str, Any]]:
        from local_drama.application.breakdown_revisions import load_effective_breakdown_draft

        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            rows = connection.execute(
                """SELECT d.*,sd.code AS source_document_code,sd.title AS source_document_title
                FROM script_breakdown_drafts d
                JOIN source_document_versions sdv ON sdv.id=d.source_document_version_id
                JOIN source_documents sd ON sd.id=sdv.source_document_id
                WHERE d.project_id=? ORDER BY d.created_at DESC,d.id""",
                (project_id,),
            ).fetchall()
            application_rows = connection.execute(
                """SELECT a.breakdown_draft_id,a.scene_no
                FROM script_breakdown_scene_applications a
                JOIN script_breakdown_drafts d ON d.id=a.breakdown_draft_id
                WHERE d.project_id=? ORDER BY a.breakdown_draft_id,a.scene_no""",
                (project_id,),
            ).fetchall()
            effective_by_draft = {}
            for row in rows:
                effective_by_draft[str(row["id"])] = load_effective_breakdown_draft(connection, row)
        applied_by_draft: dict[str, list[int]] = {}
        for application in application_rows:
            applied_by_draft.setdefault(str(application["breakdown_draft_id"]), []).append(int(application["scene_no"]))
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            model_payload = json.loads(item.pop("draft_json"))
            item["draft"], effective_revision = effective_by_draft[str(item["id"])]
            item["model_draft_sha256"] = hashlib.sha256(_json(model_payload).encode("utf-8")).hexdigest()
            item["effective_draft_revision_id"] = effective_revision["id"] if effective_revision else None
            item["effective_draft_revision_no"] = int(effective_revision["revision_no"]) if effective_revision else 0
            item["human_edited"] = effective_revision is not None
            item["confidence"] = json.loads(item.pop("confidence_json"))
            complete = all(key in item["confidence"] for key in ("profile_version_id", "confidence", "questions", "source_passages"))
            applied = item["status"] == "APPLIED"
            scene_nos = sorted(
                int(scene.get("scene_no", 0)) for scene in (item["draft"].get("scenes") or []) if isinstance(scene, dict) and int(scene.get("scene_no", 0)) > 0
            )
            applied_scene_nos = applied_by_draft.get(str(item["id"]), scene_nos if applied else [])
            remaining_scene_nos = sorted(set(scene_nos) - set(applied_scene_nos))
            application_status = "APPLIED" if applied else "PARTIALLY_APPLIED" if applied_scene_nos else "NOT_APPLIED"
            blockers: list[dict[str, str]] = []
            grounding_contract = item["confidence"].get("dialogue_grounding_status") == "PASS" or item["confidence"].get("duration_contract_status") == "PASS"
            if grounding_contract and not applied:
                try:
                    validate_scene_dialogue_grounding(item["draft"].get("scenes"), item["confidence"].get("source_passages"))
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message})
                try:
                    validate_scene_source_grounding(item["draft"].get("scenes"), item["confidence"].get("source_passages"))
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message})
                try:
                    validate_scene_distinctness(item["draft"].get("scenes"))
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message})
            item.update(
                {
                    "profile_version_id": item["confidence"].get("profile_version_id"),
                    "evidence_status": "COMPLETE" if complete else "LEGACY_INCOMPLETE",
                    "application_status": application_status,
                    "applied_scene_nos": applied_scene_nos,
                    "remaining_scene_nos": remaining_scene_nos,
                    "application_blockers": blockers,
                    "automatic_apply": False,
                    "requires_human_action": not applied,
                }
            )
            items.append(item)
        return items

    def _project_root(self, project_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return self.settings.resolve_project_root(str(row["root_rel"]))
