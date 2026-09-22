"""Explainer subtitles: independent revisions, bilingual pairing, reflow, export.

This module owns subtitle *text layout*, never narration audio:

* ``create_revision`` writes one ``explainer_subtitle_revisions`` row whose text
  authority is ``NARRATION_SCRIPT`` (source language) or ``TRANSLATED_SCRIPT``
  (a translation), plus a per-cue layout report produced from
  ``subtitle_reading_rates``.
* ``build_bilingual_cues`` pairs cues by ``segment_canonical_id`` and emits one
  line per language.  Nothing here aligns word by word: a pair that would exceed
  the per-language line budget is split into two sequential semantic cues.
* ``reflow_for_aspect`` recomputes wrapping, per-line character budget and safe
  area for the target aspect ratio.  It never centre-crops a wide layout into a
  portrait frame.
* ``serialize`` / ``parse`` round-trip SRT, VTT and ASS without losing text.

What this module deliberately does NOT do:

* It never re-runs TTS.  ``style_change_impact`` reports ``retriggers_tts: False``
  and the invalidation set is read from ``staleness_plan``.
* It never fabricates or repairs timings: a cue that is negative, overlapping or
  past the film end is rejected, not clamped silently.
* It never claims font glyph coverage.  ``font_glyph_report`` returns
  ``UNCHECKED`` because real font files cannot be inspected in this layer.
* It never stores an absolute timestamp copied from another locale's edition.
* It never invents a ``paired_text``: a bilingual cue without a matching pair on
  the other language keeps its own text only.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    ExplainerContractError,
    content_hash,
    normalize_locale,
    subtitle_reading_rates,
)
from local_drama.domain.explainers.policies import STALENESS_PRESERVED, staleness_plan
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

SUPPORTED_FORMATS: tuple[str, ...] = ("SRT", "VTT", "ASS")

#: Calibratable product defaults, explicitly *not* platform standards.
MIN_CUE_MS = 1_000
MAX_CUE_MS = 6_000
DEFAULT_LINES_PER_LANGUAGE = 2
DEFAULT_MAX_CHARS_PER_LINE_CJK = 16
DEFAULT_MAX_CHARS_PER_LINE_LATIN = 42
MIN_CHARS_PER_LINE = 6

#: Per-aspect character-per-line budget multiplier.  Portrait frames keep the
#: same font size but have far less horizontal room, so the budget shrinks.
_ASPECT_LINE_BUDGET: dict[str, dict[str, float]] = {
    "16:9": {"CJK": 1.0, "LATIN": 1.0},
    "1:1": {"CJK": 0.8, "LATIN": 0.85},
    "3:4": {"CJK": 0.66, "LATIN": 0.72},
    "9:16": {"CJK": 0.5, "LATIN": 0.55},
}

#: Default safe areas as 0-1 fractions of the frame.  Portrait keeps more room
#: at the bottom for platform UI, so the bottom inset is larger.
_DEFAULT_SAFE_AREA: dict[str, dict[str, float]] = {
    "16:9": {"top": 0.05, "bottom": 0.08, "left": 0.05, "right": 0.05},
    "1:1": {"top": 0.06, "bottom": 0.10, "left": 0.06, "right": 0.06},
    "3:4": {"top": 0.08, "bottom": 0.14, "left": 0.08, "right": 0.08},
    "9:16": {"top": 0.10, "bottom": 0.20, "left": 0.08, "right": 0.08},
}

PAIRED_TEXT_MARKER = "⟦PAIRED⟧"

_VTT_TIME = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})$")
_SRT_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")
_CJK_RE = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _commit(repo: ExplainerRepository) -> None:
    commit = getattr(repo.connection, "commit", None)
    if callable(commit):
        commit()


def is_cjk_locale(locale: str) -> bool:
    return str(locale).lower().startswith(("zh", "ja", "ko"))


def contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _as_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field} 必须是对象", {"field": field})
    return {str(key): item for key, item in value.items()}


def _require_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field} 必须是整数毫秒", {"field": field, "value": value})
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field} 必须是整数毫秒", {"field": field, "value": value}
        ) from error


# --------------------------------------------------------------------------- #
# layout primitives
# --------------------------------------------------------------------------- #
def default_safe_area(aspect_ratio: str) -> dict[str, float]:
    if aspect_ratio not in _DEFAULT_SAFE_AREA:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "不支持画幅比例", {"aspect_ratio": aspect_ratio, "supported": sorted(_DEFAULT_SAFE_AREA)}
        )
    return dict(_DEFAULT_SAFE_AREA[aspect_ratio])


def _normalise_safe_area(aspect_ratio: str, safe_area: Mapping[str, Any] | None) -> dict[str, float]:
    base = default_safe_area(aspect_ratio)
    if safe_area is None:
        return base
    provided = _as_mapping(safe_area, field="safe_area")
    for key in ("top", "bottom", "left", "right"):
        if key not in provided:
            continue
        try:
            value = float(provided[key])
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "安全区必须是 0–1 的小数", {"safe_area": dict(provided), "key": key}
            ) from error
        if not 0.0 <= value < 1.0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "安全区必须是 0–1 的小数", {"safe_area": dict(provided), "key": key, "value": value}
            )
        base[key] = value
    if base["top"] + base["bottom"] >= 1.0 or base["left"] + base["right"] >= 1.0:
        raise ExplainerContractError("SCHEMA_INVALID", "安全区上下或左右相加必须小于整幅画面", {"safe_area": base})
    return base


def character_budget_per_line(
    *, aspect_ratio: str, locale: str, max_lines: int = DEFAULT_LINES_PER_LANGUAGE
) -> int:
    """Characters that fit on one line for this aspect and language."""

    if aspect_ratio not in _ASPECT_LINE_BUDGET:
        raise ExplainerContractError("SCHEMA_INVALID", "不支持画幅比例", {"aspect_ratio": aspect_ratio})
    if max_lines < 1:
        raise ExplainerContractError("SCHEMA_INVALID", "每语言行数必须为正", {"max_lines": max_lines})
    budget = _ASPECT_LINE_BUDGET[aspect_ratio]
    cjk = is_cjk_locale(locale)
    base = DEFAULT_MAX_CHARS_PER_LINE_CJK if cjk else DEFAULT_MAX_CHARS_PER_LINE_LATIN
    factor = budget["CJK"] if cjk else budget["LATIN"]
    return max(MIN_CHARS_PER_LINE, int(round(base * factor)))


def wrap_text(text: str, *, max_chars_per_line: int) -> list[str]:
    """Deterministic greedy wrapping: CJK breaks per character, Latin per word."""

    if max_chars_per_line < 1:
        raise ExplainerContractError("SCHEMA_INVALID", "每行字符上限必须为正")
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if contains_cjk(stripped):
            for start in range(0, len(stripped), max_chars_per_line):
                lines.append(stripped[start : start + max_chars_per_line])
            continue
        current = ""
        for word in stripped.split():
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= max_chars_per_line or not current:
                current = candidate
                continue
            lines.append(current)
            current = word
        if current:
            lines.append(current)
    return lines or [""]


def estimate_line_count(text: str, *, max_chars_per_line: int) -> int:
    return len(wrap_text(text, max_chars_per_line=max_chars_per_line))


def _halve_by_lines(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    if not lines:
        return [], []
    half = max(1, len(lines) // 2)
    return list(lines[:half]), list(lines[half:])


def _split_text_for_lines(text: str, *, budget: int, max_lines: int) -> tuple[str, str]:
    """Split ``text`` so each half fits the per-language line budget."""

    lines = wrap_text(text, max_chars_per_line=budget)
    if len(lines) <= max_lines:
        return text, ""
    keep = max(1, max_lines * 2 // 3)
    first = "\n".join(lines[:keep])
    second = "\n".join(lines[keep:])
    if not second:
        words = text.split()
        if len(words) > 1:
            half = max(1, len(words) // 2)
            return " ".join(words[:half]), " ".join(words[half:])
        return text, ""
    return first, second


# --------------------------------------------------------------------------- #
# timestamp codecs
# --------------------------------------------------------------------------- #
def format_srt_timestamp(milliseconds: int) -> str:
    hours, rest = divmod(int(milliseconds), 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_vtt_timestamp(milliseconds: int) -> str:
    return format_srt_timestamp(milliseconds).replace(",", ".")


def format_ass_timestamp(milliseconds: int) -> str:
    centiseconds, _ = divmod(int(milliseconds), 10)
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    seconds, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def parse_srt_timestamp(value: str) -> int:
    match = _SRT_TIME.match(value.strip())
    if not match:
        raise ExplainerContractError("SCHEMA_INVALID", "SRT 时间戳格式不合法", {"value": value})
    hours, minutes, seconds, millis = (int(item) for item in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_vtt_timestamp(value: str) -> int:
    match = _VTT_TIME.match(value.strip())
    if not match:
        raise ExplainerContractError("SCHEMA_INVALID", "VTT 时间戳格式不合法", {"value": value})
    hours, minutes, seconds, millis = match.groups()
    total_hours = int(hours or 0)
    return ((total_hours * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def parse_ass_timestamp(value: str) -> int:
    match = _ASS_TIME.match(value.strip())
    if not match:
        raise ExplainerContractError("SCHEMA_INVALID", "ASS 时间戳格式不合法", {"value": value})
    hours, minutes, seconds, centiseconds = (int(item) for item in match.groups())
    return (((hours * 60 + minutes) * 60 + seconds) * 100) * 10 + centiseconds * 10


def _encode_payload(text: str, paired_text: str) -> str:
    core = str(text or "").replace("\r\n", "\n").strip()
    if not paired_text:
        return core
    return f"{PAIRED_TEXT_MARKER}{paired_text.strip()}{PAIRED_TEXT_MARKER}{core}"


def _decode_payload(payload: str) -> tuple[str, str]:
    stripped = payload.strip()
    if stripped.startswith(PAIRED_TEXT_MARKER):
        end = stripped.find(PAIRED_TEXT_MARKER, len(PAIRED_TEXT_MARKER))
        if end != -1:
            paired = stripped[len(PAIRED_TEXT_MARKER) : end].strip()
            core = stripped[end + len(PAIRED_TEXT_MARKER) :].strip()
            return core, paired
    return stripped, ""


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
class ExplainerSubtitleService:
    """Subtitle revision authority for one explainer video."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ create
    def create_revision(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str,
        locale: str,
        cues: Sequence[Mapping[str, Any]],
        text_authority: str,
        script_revision_id: str,
        narration_alignment_revision_ids: Sequence[str] = (),
        paired_locale: str | None = None,
        style: Mapping[str, Any] | None = None,
        format: str = "JSON",
        status: str = "DRAFT",
        actor: str = "local-user",
        max_lines_per_language: int = DEFAULT_LINES_PER_LANGUAGE,
    ) -> dict[str, Any]:
        self.repo.require_explainer_project(project_id)
        edition = self.repo.find("explainer_editions", edition_id)
        if edition is None or str(edition.get("video_id")) != video_id:
            raise ExplainerContractError(
                "NOT_FOUND", "解说成片版本不存在或不属于该作品", {"edition_id": edition_id, "video_id": video_id}
            )
        target_locale = normalize_locale(locale)
        if text_authority not in {"NARRATION_SCRIPT", "TRANSLATED_SCRIPT", "LEGACY_SCRIPT"}:
            raise ExplainerContractError("SCHEMA_INVALID", "text_authority 取值不合法", {"text_authority": text_authority})
        if text_authority == "TRANSLATED_SCRIPT":
            if paired_locale and normalize_locale(paired_locale) == target_locale:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "译文字幕的目标语言不能与配对语言相同", {"locale": target_locale}
                )
        if format not in ("JSON", "SRT", "VTT", "ASS"):
            raise ExplainerContractError("SCHEMA_INVALID", "字幕格式不合法", {"format": format})
        if status not in {"DRAFT", "FROZEN", "SUPERSEDED"}:
            raise ExplainerContractError("SCHEMA_INVALID", "字幕状态不合法", {"status": status})
        script_revision = self.repo.find("explainer_script_revisions", script_revision_id)
        if script_revision is None or str(script_revision.get("video_id")) != video_id:
            raise ExplainerContractError(
                "NOT_FOUND",
                "字幕文本权威讲稿修订不存在或不属于该作品",
                {"script_revision_id": script_revision_id},
            )
        if str(script_revision.get("locale")) != target_locale:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "字幕语言必须与文本权威讲稿修订的语言一致",
                {"locale": target_locale, "script_locale": str(script_revision.get("locale"))},
            )
        alignment_ids = [str(item) for item in narration_alignment_revision_ids]
        for alignment_id in alignment_ids:
            alignment = self.repo.find("narration_alignment_revisions", alignment_id)
            if alignment is None or str(alignment.get("video_id")) != video_id:
                raise ExplainerContractError(
                    "NOT_FOUND",
                    "对齐修订不存在或不属于该作品",
                    {"narration_alignment_revision_id": alignment_id},
                )
        aspect_ratio = str(edition.get("aspect_ratio") or "16:9")
        normalised_cues = self._validate_cues(
            cues=cues,
            edition=edition,
            locale=target_locale,
            max_lines_per_language=max_lines_per_language,
        )
        style_payload = dict(style or {})
        layout_report = self._layout_report(
            cues=normalised_cues,
            locale=target_locale,
            aspect_ratio=aspect_ratio,
            style=style_payload,
            max_lines_per_language=max_lines_per_language,
        )
        revision_no = self._next_revision_no(video_id=video_id, locale=target_locale)
        content_text = self.serialize(cues=normalised_cues, format="SRT")
        content = content_hash(
            {
                "video_id": video_id,
                "edition_id": edition_id,
                "locale": target_locale,
                "paired_locale": normalize_locale(paired_locale) if paired_locale else None,
                "text_authority": text_authority,
                "script_revision_id": script_revision_id,
                "narration_alignment_revision_ids": alignment_ids,
                "style": style_payload,
                "cues": [
                    {
                        "start_ms": item["start_ms"],
                        "end_ms": item["end_ms"],
                        "text": item["text"],
                        "paired_text": item.get("paired_text", ""),
                        "segment_canonical_id": item.get("segment_canonical_id"),
                    }
                    for item in normalised_cues
                ],
            }
        )
        revision = self.repo.insert(
            "explainer_subtitle_revisions",
            {
                "video_id": video_id,
                "edition_id": edition_id,
                "revision_no": revision_no,
                "locale": target_locale,
                "paired_locale": normalize_locale(paired_locale) if paired_locale else None,
                "format": format,
                "text_authority": text_authority,
                "script_revision_id": script_revision_id,
                "narration_alignment_revision_ids_json": alignment_ids,
                "style_version_id": style_payload.get("style_version_id"),
                "content_text": content_text,
                "cues_json": normalised_cues,
                "content_hash": content,
                "layout_report_json": layout_report,
                "status": status,
            },
            actor=actor,
        )
        _commit(self.repo)
        return {
            "subtitle_revision": revision,
            "cues": normalised_cues,
            "layout_report": layout_report,
            "content_hash": content,
        }

    # ------------------------------------------------------------------ bilingual
    def build_bilingual_cues(
        self,
        *,
        source_revision_id: str,
        translated_revision_id: str,
        locale_source: str,
        locale_target: str,
        max_lines_per_language: int = DEFAULT_LINES_PER_LANGUAGE,
    ) -> list[dict[str, Any]]:
        source_locale = normalize_locale(locale_source)
        target_locale = normalize_locale(locale_target)
        if source_locale == target_locale:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "双语字幕需要两种不同的语言", {"locale": source_locale}
            )
        source_rows = self._cues_of(source_revision_id, expected_locale=source_locale)
        target_rows = self._cues_of(translated_revision_id, expected_locale=target_locale)
        target_by_segment: dict[str, dict[str, Any]] = {}
        for row in target_rows:
            key = str(row.get("segment_canonical_id") or "")
            if key:
                target_by_segment.setdefault(key, row)
        source_budget = character_budget_per_line(
            aspect_ratio="16:9", locale=source_locale, max_lines=max_lines_per_language
        )
        target_budget = character_budget_per_line(
            aspect_ratio="16:9", locale=target_locale, max_lines=max_lines_per_language
        )
        paired: list[dict[str, Any]] = []
        for row in source_rows:
            key = str(row.get("segment_canonical_id") or "")
            partner = target_by_segment.pop(key, None)
            start_ms = int(row["start_ms"])
            end_ms = int(row["end_ms"])
            text = str(row["text"])
            paired_text = str(partner["text"]) if partner is not None else ""
            if partner is not None:
                start_ms = min(start_ms, int(partner["start_ms"]))
                end_ms = max(end_ms, int(partner["end_ms"]))
            first_lines = estimate_line_count(text, max_chars_per_line=source_budget)
            second_lines = estimate_line_count(paired_text, max_chars_per_line=target_budget) if paired_text else 0
            if max(first_lines, second_lines) <= max_lines_per_language:
                paired.append(
                    {
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "text": text,
                        "paired_text": paired_text,
                        "segment_canonical_id": key,
                    }
                )
                continue
            paired.extend(
                self._split_pair(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    paired_text=paired_text,
                    segment_canonical_id=key,
                    source_budget=source_budget,
                    target_budget=target_budget,
                    max_lines_per_language=max_lines_per_language,
                )
            )
        for key, row in target_by_segment.items():
            paired.append(
                {
                    "start_ms": int(row["start_ms"]),
                    "end_ms": int(row["end_ms"]),
                    "text": "",
                    "paired_text": str(row["text"]),
                    "segment_canonical_id": key,
                }
            )
        return paired

    # ------------------------------------------------------------------ reflow
    def reflow_for_aspect(
        self, *, cues: Sequence[Mapping[str, Any]], aspect_ratio: str, safe_area: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        area = _normalise_safe_area(aspect_ratio, safe_area)
        reflowed = False
        overflow_count = 0
        fallback_required = False
        reasons: list[str] = []
        output: list[dict[str, Any]] = []
        for index, raw in enumerate(cues):
            cue = dict(raw)
            if "text" not in cue:
                raise ExplainerContractError("SCHEMA_INVALID", "字幕 cue 必须包含 text", {"index": index})
            locale = str(cue.get("locale") or cue.get("text_locale") or "zh-CN")
            for field in ("start_ms", "end_ms"):
                if field not in cue:
                    raise ExplainerContractError("SCHEMA_INVALID", f"字幕 cue 缺少 {field}", {"index": index})
            start_ms = _require_int(cue["start_ms"], field="start_ms")
            end_ms = _require_int(cue["end_ms"], field="end_ms")
            text = str(cue.get("text") or "")
            paired_text = str(cue.get("paired_text") or "")
            per_language_lines = int(cue.get("max_lines_per_language") or DEFAULT_LINES_PER_LANGUAGE)
            source_budget = character_budget_per_line(
                aspect_ratio=aspect_ratio, locale=locale, max_lines=per_language_lines
            )
            target_locale = str(cue.get("paired_locale") or ("en-US" if is_cjk_locale(locale) else "zh-CN"))
            target_budget = character_budget_per_line(
                aspect_ratio=aspect_ratio, locale=target_locale, max_lines=per_language_lines
            )
            source_lines = wrap_text(text, max_chars_per_line=source_budget)
            target_lines = wrap_text(paired_text, max_chars_per_line=target_budget) if paired_text else []
            overflow = max(len(source_lines), len(target_lines)) > per_language_lines
            if overflow and end_ms - start_ms >= 2 * MIN_CUE_MS:
                reflowed = True
                mid = start_ms + (end_ms - start_ms) // 2
                first_source, second_source = _halve_by_lines(source_lines)
                first_target, second_target = (
                    _halve_by_lines(target_lines) if target_lines else ([], [])
                )
                output.append(
                    {
                        **cue,
                        "start_ms": start_ms,
                        "end_ms": mid,
                        "text": "\n".join(first_source),
                        "paired_text": "\n".join(first_target),
                        "lines": first_source,
                        "paired_lines": first_target,
                        "sub_index": 0,
                        "wrapped_at_chars_per_line": source_budget,
                    }
                )
                output.append(
                    {
                        **cue,
                        "start_ms": mid,
                        "end_ms": end_ms,
                        "text": "\n".join(second_source),
                        "paired_text": "\n".join(second_target),
                        "lines": second_source,
                        "paired_lines": second_target,
                        "sub_index": 1,
                        "wrapped_at_chars_per_line": source_budget,
                    }
                )
                continue
            if overflow:
                overflow_count += 1
                fallback_required = True
                reasons.append(
                    f"cue[{index}] 在 {aspect_ratio} 安全区内仍超出每语言 {per_language_lines} 行且时长不足以切分"
                )
            output.append(
                {
                    **cue,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": "\n".join(source_lines),
                    "paired_text": "\n".join(target_lines),
                    "lines": source_lines,
                    "paired_lines": target_lines,
                    "wrapped_at_chars_per_line": source_budget,
                    "paired_wrapped_at_chars_per_line": target_budget,
                }
            )
        reason = "；".join(reasons) if reasons else "按目标画幅重算换行、每行字符预算与安全区，无需额外回退"
        return {
            "cues": output,
            "reflowed": reflowed,
            "overflow_count": overflow_count,
            "fallback_required": fallback_required,
            "reason": reason,
            "aspect_ratio": aspect_ratio,
            "safe_area": area,
            "policy": "RECOMPUTE_WRAP_AND_BUDGET_NOT_CENTRE_CROP",
        }

    # ------------------------------------------------------------------ export
    def serialize(
        self, *, cues: Sequence[Mapping[str, Any]], format: str, style: Mapping[str, Any] | None = None
    ) -> str:
        target_format = str(format).upper()
        if target_format not in SUPPORTED_FORMATS:
            raise ExplainerContractError("SCHEMA_INVALID", "不支持的字幕导出格式", {"format": format})
        normalised = [self._normalise_export_cue(item, index=index) for index, item in enumerate(cues)]
        if target_format == "SRT":
            blocks: list[str] = []
            for index, cue in enumerate(normalised, start=1):
                payload = _encode_payload(cue["text"], cue["paired_text"]).replace("\n", "\r\n")
                blocks.append(
                    f"{index}\r\n{format_srt_timestamp(cue['start_ms'])} --> {format_srt_timestamp(cue['end_ms'])}\r\n{payload}"
                )
            return "\r\n\r\n".join(blocks) + ("\r\n" if blocks else "")
        if target_format == "VTT":
            lines = ["WEBVTT", ""]
            for cue in normalised:
                lines.append(f"{format_vtt_timestamp(cue['start_ms'])} --> {format_vtt_timestamp(cue['end_ms'])}")
                lines.append(_encode_payload(cue["text"], cue["paired_text"]))
                lines.append("")
            return "\n".join(lines)
        style_payload = dict(style or {})
        return self._serialize_ass(cues=normalised, style=style_payload)

    def parse(self, *, content_text: str, format: str) -> list[dict[str, Any]]:
        target_format = str(format).upper()
        if target_format not in SUPPORTED_FORMATS:
            raise ExplainerContractError("SCHEMA_INVALID", "不支持的字幕解析格式", {"format": format})
        if target_format == "SRT":
            return self._parse_srt(content_text)
        if target_format == "VTT":
            return self._parse_vtt(content_text)
        return self._parse_ass(content_text)

    # ------------------------------------------------------------------ impact
    def style_change_impact(self, *, subtitle_revision_id: str) -> dict[str, Any]:
        revision = self.repo.get("explainer_subtitle_revisions", subtitle_revision_id)
        plan = staleness_plan("SUBTITLE_STYLE")
        preserves = list(STALENESS_PRESERVED.get("SUBTITLE_STYLE", ()))
        expected_invalidates = ("SUBTITLE_REVISION", "COMPOSITION_REVISION")
        if tuple(plan["invalidates"]) != expected_invalidates:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "字幕样式变更的失效集合与设计规范不一致，请先更新策略表",
                {"policy_invalidates": list(plan["invalidates"]), "expected": list(expected_invalidates)},
            )
        return {
            "subtitle_revision_id": subtitle_revision_id,
            "video_id": str(revision["video_id"]),
            "edition_id": revision.get("edition_id"),
            "invalidates": ["SUBTITLE_REVISION", "COMPOSITION_REVISION"],
            "preserves": ["NARRATION_AUDIO", "SOURCE_IMAGE_VIDEO"],
            "retriggers_tts": False,
            "policy_preserves": preserves,
            "note": "字幕样式只影响字幕与合成产物，必须复用已冻结的旁白音频与源画面。",
        }

    # ------------------------------------------------------------------ fonts
    def font_glyph_report(self, *, locale: str, font_family: str) -> dict[str, Any]:
        """Glyph coverage is a runtime fact this layer cannot observe.

        Returning ``UNCHECKED`` is the honest answer: claiming coverage without
        opening the real font file would be a fabricated pass.
        """

        normalize_locale(locale)
        if not str(font_family or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "必须提供字体名称")
        return {
            "status": "UNCHECKED",
            "reason": "FONT_INSPECTION_NOT_IMPLEMENTED_IN_THIS_LAYER",
            "requires_runtime_check": True,
            "claimed_cover": None,
            "locale": locale,
            "font_family": font_family,
        }

    # ------------------------------------------------------------------ internals
    def _next_revision_no(self, *, video_id: str, locale: str) -> int:
        row = self.repo.query_one(
            "SELECT COALESCE(MAX(revision_no), 0) AS current FROM explainer_subtitle_revisions WHERE video_id = ? AND locale = ?",
            (video_id, locale),
        )
        return int(row["current"] if row is not None else 0) + 1

    def _cues_of(self, revision_id: str, *, expected_locale: str) -> list[dict[str, Any]]:
        revision = self.repo.find("explainer_subtitle_revisions", revision_id)
        if revision is None:
            raise ExplainerContractError("NOT_FOUND", "字幕修订不存在", {"subtitle_revision_id": revision_id})
        if normalize_locale(str(revision["locale"])) != expected_locale:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "字幕修订语言与请求语言不一致",
                {"subtitle_revision_id": revision_id, "actual": str(revision["locale"]), "expected": expected_locale},
            )
        raw = revision.get("cues_json") or []
        if not isinstance(raw, Sequence):
            raise ExplainerContractError("SCHEMA_INVALID", "字幕修订的 cue 数据损坏", {"subtitle_revision_id": revision_id})
        return [_as_mapping(item, field="cues_json") for item in raw]

    def _validate_cues(
        self,
        *,
        cues: Sequence[Mapping[str, Any]],
        edition: Mapping[str, Any],
        locale: str,
        max_lines_per_language: int,
    ) -> list[dict[str, Any]]:
        if isinstance(cues, (str, bytes)) or not isinstance(cues, Sequence):
            raise ExplainerContractError("SCHEMA_INVALID", "cues 必须是数组")
        known_segments: set[str] = set()
        script_revision_id = str(edition.get("frozen_script_revision_id") or "")
        if script_revision_id:
            known_segments = {
                str(item["canonical_segment_id"]) for item in self.repo.segments(script_revision_id)
            }
        duration_ms = self._edition_duration_ms(edition)
        cursor = 0
        normalised: list[dict[str, Any]] = []
        for index, raw in enumerate(cues):
            cue = _as_mapping(raw, field=f"cues[{index}]")
            for field in ("start_ms", "end_ms", "text"):
                if field not in cue:
                    raise ExplainerContractError("SCHEMA_INVALID", f"字幕 cue 缺少 {field}", {"index": index})
            start_ms = _require_int(cue["start_ms"], field="start_ms")
            end_ms = _require_int(cue["end_ms"], field="end_ms")
            text = str(cue["text"])
            paired_text = str(cue.get("paired_text") or "")
            if end_ms <= start_ms:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "字幕区间必须满足 end_ms > start_ms（不允许负时长或零时长）",
                    {"index": index, "start_ms": start_ms, "end_ms": end_ms},
                )
            if start_ms < cursor:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "字幕区间必须单调且不允许重叠",
                    {"index": index, "start_ms": start_ms, "previous_end_ms": cursor},
                )
            if duration_ms is not None and end_ms > duration_ms:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "字幕超出成片时长",
                    {"index": index, "end_ms": end_ms, "edition_duration_ms": duration_ms},
                )
            if not text.strip():
                raise ExplainerContractError("SCHEMA_INVALID", "字幕文本不能为空", {"index": index})
            segment_id = cue.get("segment_canonical_id")
            if segment_id and known_segments and str(segment_id) not in known_segments:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "字幕引用了权威讲稿中不存在的段落",
                    {"index": index, "segment_canonical_id": str(segment_id)},
                )
            cursor = end_ms
            normalised.append(
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "paired_text": paired_text,
                    "segment_canonical_id": str(segment_id) if segment_id else None,
                    "locale": locale,
                    "max_lines_per_language": max_lines_per_language,
                }
            )
        if not normalised:
            raise ExplainerContractError("SCHEMA_INVALID", "字幕修订至少需要一个 cue")
        return normalised

    def _edition_duration_ms(self, edition: Mapping[str, Any]) -> int | None:
        target_seconds = edition.get("target_seconds")
        policy = str(edition.get("duration_policy") or "NATURAL_NARRATION")
        if policy == "FIXED_FRAMES" and target_seconds:
            return int(round(float(target_seconds) * 1000))
        return None

    def _layout_report(
        self,
        *,
        cues: Sequence[Mapping[str, Any]],
        locale: str,
        aspect_ratio: str,
        style: Mapping[str, Any],
        max_lines_per_language: int,
    ) -> dict[str, Any]:
        per_cue: list[dict[str, Any]] = []
        overflow_count = 0
        values: list[float] = []
        budget = character_budget_per_line(
            aspect_ratio=aspect_ratio, locale=locale, max_lines=max_lines_per_language
        )
        for index, cue in enumerate(cues):
            duration_ms = int(cue["end_ms"]) - int(cue["start_ms"])
            rate = subtitle_reading_rates(locale=locale, text=str(cue["text"]), duration_ms=duration_ms)
            line_count = estimate_line_count(str(cue["text"]), max_chars_per_line=budget)
            too_short = duration_ms < MIN_CUE_MS
            too_long = duration_ms > MAX_CUE_MS
            if rate["exceeds_limit"] or too_short or too_long or line_count > max_lines_per_language:
                overflow_count += 1
            values.append(float(rate["characters_per_second"]))
            per_cue.append(
                {
                    "index": index,
                    "start_ms": int(cue["start_ms"]),
                    "end_ms": int(cue["end_ms"]),
                    "duration_ms": duration_ms,
                    "character_count": rate["character_count"],
                    "characters_per_second": rate["characters_per_second"],
                    "limit_characters_per_second": rate["limit_characters_per_second"],
                    "exceeds_limit": bool(rate["exceeds_limit"]),
                    "estimated_line_count": line_count,
                    "max_lines_per_language": max_lines_per_language,
                    "characters_per_line_budget": budget,
                    "too_short_for_minimum_cue": too_short,
                    "too_long_for_maximum_cue": too_long,
                    "segment_canonical_id": cue.get("segment_canonical_id"),
                }
            )
        return {
            "schema_version": "localdrama.explainer.subtitle-layout.v1",
            "locale": locale,
            "aspect_ratio": aspect_ratio,
            "defaults_are_product_defaults_not_platform_standards": True,
            "max_lines_per_language": max_lines_per_language,
            "characters_per_line_budget": budget,
            "cue_min_ms": MIN_CUE_MS,
            "cue_max_ms": MAX_CUE_MS,
            "max_characters_per_second": per_cue[0]["limit_characters_per_second"] if per_cue else None,
            "cue_count": len(per_cue),
            "exceeds_limit_count": overflow_count,
            "max_observed_characters_per_second": max(values) if values else 0.0,
            "style": dict(style),
            "cues": per_cue,
        }

    def _split_pair(
        self,
        *,
        start_ms: int,
        end_ms: int,
        text: str,
        paired_text: str,
        segment_canonical_id: str,
        source_budget: int,
        target_budget: int,
        max_lines_per_language: int,
    ) -> list[dict[str, Any]]:
        total_chars = max(1, len(text) + len(paired_text))
        split_at = start_ms + int(round((end_ms - start_ms) * (len(text) / total_chars)))
        if split_at <= start_ms:
            split_at = start_ms + 1
        if split_at >= end_ms:
            split_at = end_ms - 1
        first_text, second_text = _split_text_for_lines(text, budget=source_budget, max_lines=max_lines_per_language)
        if paired_text:
            first_paired, second_paired = _split_text_for_lines(
                paired_text, budget=target_budget, max_lines=max_lines_per_language
            )
        else:
            first_paired, second_paired = "", ""
        return [
            {
                "start_ms": start_ms,
                "end_ms": split_at,
                "text": first_text,
                "paired_text": first_paired,
                "segment_canonical_id": segment_canonical_id,
                "split_at_semantic_cue_boundary": True,
                "sub_index": 0,
            },
            {
                "start_ms": split_at,
                "end_ms": end_ms,
                "text": second_text,
                "paired_text": second_paired,
                "segment_canonical_id": segment_canonical_id,
                "split_at_semantic_cue_boundary": True,
                "sub_index": 1,
            },
        ]

    # ------------------------------------------------------------------ export internals
    def _normalise_export_cue(self, item: Mapping[str, Any], *, index: int) -> dict[str, Any]:
        cue = _as_mapping(item, field=f"cues[{index}]")
        for field in ("start_ms", "end_ms", "text"):
            if field not in cue:
                raise ExplainerContractError("SCHEMA_INVALID", f"导出 cue 缺少 {field}", {"index": index})
        start_ms = _require_int(cue["start_ms"], field="start_ms")
        end_ms = _require_int(cue["end_ms"], field="end_ms")
        if end_ms <= start_ms:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "导出字幕区间必须满足 end_ms > start_ms",
                {"index": index, "start_ms": start_ms, "end_ms": end_ms},
            )
        return {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": "\n".join(line for line in str(cue["text"]).replace("\r\n", "\n").split("\n")),
            "paired_text": "\n".join(
                line for line in str(cue.get("paired_text") or "").replace("\r\n", "\n").split("\n")
            ),
        }

    def _serialize_ass(self, *, cues: Sequence[Mapping[str, Any]], style: Mapping[str, Any]) -> str:
        position_to_alignment = {"TOP": 8, "CENTER": 5, "BOTTOM": 2}
        hex_color = str(style.get("color") or "#FFFFFF").lstrip("#").upper()
        while len(hex_color) < 6:
            hex_color += "0"
        ass_rgb = f"{hex_color[4:6]}{hex_color[2:4]}{hex_color[0:2]}"
        fontname = str(style.get("font") or "Microsoft YaHei")
        fontsize = int(style.get("size") or 48)
        outline = int(style.get("outline") or 2)
        alignment = position_to_alignment.get(str(style.get("position") or "BOTTOM"), 2)
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{fontname},{fontsize},&H00{ass_rgb}&,&H000000FF&,&H00000000&,&H80000000&,"
            f"0,0,0,0,100,100,0,0,1,{outline},0,{alignment},10,10,10,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for cue in cues:
            payload = _encode_payload(str(cue["text"]), str(cue["paired_text"]))
            lines.append(
                "Dialogue: 0,"
                + format_ass_timestamp(int(cue["start_ms"]))
                + ","
                + format_ass_timestamp(int(cue["end_ms"]))
                + ",Default,,0,0,0,,"
                + payload.replace("\n", "\\N")
            )
        return "\n".join(lines) + "\n"

    def _parse_srt(self, content_text: str) -> list[dict[str, Any]]:
        text = str(content_text or "").replace("\r\n", "\n").replace("\r", "\n")
        blocks = [block for block in re.split(r"\n{2,}", text) if block.strip()]
        cues: list[dict[str, Any]] = []
        for block in blocks:
            lines = block.split("\n")
            if lines and lines[0].strip().isdigit():
                lines = lines[1:]
            if not lines:
                continue
            timing = lines[0].strip()
            if "-->" not in timing:
                raise ExplainerContractError("SCHEMA_INVALID", "SRT 块缺少时间行", {"block": block})
            start_raw, _, end_raw = timing.partition("-->")
            payload = "\n".join(lines[1:]).strip()
            core, paired = _decode_payload(payload)
            cues.append(
                {
                    "start_ms": parse_srt_timestamp(start_raw),
                    "end_ms": parse_srt_timestamp(end_raw),
                    "text": core,
                    "paired_text": paired,
                }
            )
        return cues

    def _parse_vtt(self, content_text: str) -> list[dict[str, Any]]:
        text = str(content_text or "").replace("\r\n", "\n").replace("\r", "\n")
        stripped = text.lstrip("\ufeff")
        if not stripped.startswith("WEBVTT"):
            raise ExplainerContractError("SCHEMA_INVALID", "VTT 内容必须以 WEBVTT 开头")
        body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        cues: list[dict[str, Any]] = []
        for block in [item for item in re.split(r"\n{2,}", body) if item.strip()]:
            lines = [line for line in block.split("\n")]
            while lines and (not lines[0].strip() or lines[0].strip().startswith(("NOTE", "STYLE", "REGION"))):
                lines = lines[1:]
            if not lines:
                continue
            if "-->" not in lines[0]:
                lines = lines[1:]
            if not lines or "-->" not in lines[0]:
                continue
            start_raw, _, rest = lines[0].partition("-->")
            end_raw = rest.strip().split(" ")[0]
            payload = "\n".join(lines[1:]).strip()
            core, paired = _decode_payload(payload)
            cues.append(
                {
                    "start_ms": parse_vtt_timestamp(start_raw),
                    "end_ms": parse_vtt_timestamp(end_raw),
                    "text": core,
                    "paired_text": paired,
                }
            )
        return cues

    def _parse_ass(self, content_text: str) -> list[dict[str, Any]]:
        text = str(content_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if "[Events]" not in text:
            raise ExplainerContractError("SCHEMA_INVALID", "ASS 内容缺少 [Events] 段")
        section: list[str] = []
        in_events = False
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_events = stripped.lower() == "[events]"
                continue
            if in_events and stripped:
                section.append(stripped)
        field_order: list[str] = []
        cues: list[dict[str, Any]] = []
        for line in section:
            if line.lower().startswith("format:"):
                field_order = [item.strip() for item in line.split(":", 1)[1].split(",")]
                continue
            if not line.lower().startswith("dialogue:"):
                continue
            if not field_order:
                raise ExplainerContractError("SCHEMA_INVALID", "ASS 事件缺少 Format 行")
            values = line.split(":", 1)[1].split(",", len(field_order) - 1)
            if len(values) != len(field_order):
                raise ExplainerContractError("SCHEMA_INVALID", "ASS 事件字段数与 Format 不一致", {"line": line})
            record = {name.lower(): value for name, value in zip(field_order, values, strict=True)}
            payload = str(record.get("text") or "").replace("\\N", "\n").replace("\\n", "\n")
            core, paired = _decode_payload(payload)
            cues.append(
                {
                    "start_ms": parse_ass_timestamp(str(record.get("start") or "")),
                    "end_ms": parse_ass_timestamp(str(record.get("end") or "")),
                    "text": core,
                    "paired_text": paired,
                }
            )
        return cues

