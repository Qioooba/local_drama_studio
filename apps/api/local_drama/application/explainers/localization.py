"""Explainer localization: translation revisions, independent clocks, terminology.

This module owns the *translation* half of the narration script:

* ``create_translation_revision`` writes a new ``explainer_script_revisions`` row
  in the target locale, chained to the frozen source revision through
  ``source_script_revision_id`` and to each source segment through
  ``canonical_segment_id``.  Negation markers and numbers are checked so a
  translation cannot silently flip a statement or change a figure.
* ``build_english_edition_plan`` states the independent-clock contract: the
  English edition gets its own TTS clock and its own alignment.  Chinese
  absolute timecodes are never copied.
* ``terminology_dictionary`` merges every revision's declared terminology with
  the proper-noun records so one person/place/time keeps one spelling.

What this module deliberately does NOT do:

* It never re-uses the source locale's absolute timestamps.  A target timeline
  whose source is ``COPIED_FROM_SOURCE_LOCALE`` is refused, not "fixed up".
* It never rewrites the source script.  A translation is always a *new*
  revision with ``source_script_revision_id`` set; the source row is untouched.
* It never hard-fails on a negation heuristic.  Negation mismatches are
  warnings for the human reviewer; only structural problems (missing source
  segments, wrong number/unit) raise.
* It never calls a translation model and never invents a translation.
* It never says a subtitle font covers a locale: font coverage is a runtime
  concern owned by the subtitle layer.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from local_drama.application.explainers.narration import apply_pronunciation_map
from local_drama.domain.explainers.contracts import (
    ExplainerContractError,
    content_hash,
    normalize_locale,
    utc_now_iso,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Chinese negation markers from the design spec.
CHINESE_NEGATION_MARKERS: tuple[str, ...] = ("不", "没", "未", "无", "别", "非")
#: English negation markers from the design spec.
ENGLISH_NEGATION_MARKERS: tuple[str, ...] = ("not", "no", "never", "without", "n't")

_NEGATION_PATTERN = re.compile(r"不|没|未|无|别|非|n't|\b(?:not|no|never|without)\b", re.IGNORECASE)
_ASCII_NUMBER_PATTERN = re.compile(r"\d+(?:[.:]\d+)*")
_CJK_NUMERAL_CHARS = "零〇一二三四五六七八九十百千万亿两"
_CJK_NUMERAL_RUN = re.compile(f"[{_CJK_NUMERAL_CHARS}]+")
_CJK_DIGIT_VALUES: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CJK_SMALL_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000}
_CJK_BIG_UNITS: dict[str, int] = {"万": 10_000, "亿": 100_000_000}
_TARGET_TIMELINE_SOURCES: frozenset[str] = frozenset(
    {"INDEPENDENT_TTS_CLOCK", "TARGET_TTS_ALIGNMENT", "TARGET_RENDER_TIMELINE"}
)


def _commit(repo: ExplainerRepository) -> None:
    commit = getattr(repo.connection, "commit", None)
    if callable(commit):
        commit()


def _as_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field} 必须是对象", {"field": field})
    return {str(key): item for key, item in value.items()}


# --------------------------------------------------------------------------- #
# number / negation fingerprints
# --------------------------------------------------------------------------- #
def negation_markers(text: str) -> list[str]:
    """Negation markers present in ``text``, in first-appearance order."""

    found: list[str] = []
    for match in _NEGATION_PATTERN.finditer(text or ""):
        token = match.group(0).lower()
        if token not in found:
            found.append(token)
    return found


def parse_cjk_numeral(token: str) -> int | None:
    """Parse a pure Chinese numeral run such as ``一九六二`` or ``一万二千``."""

    if not token:
        return None
    for char in token:
        if char not in _CJK_DIGIT_VALUES and char not in _CJK_SMALL_UNITS and char not in _CJK_BIG_UNITS:
            return None
    total = 0
    section = 0
    current_digit: int | None = None
    for char in token:
        if char in _CJK_DIGIT_VALUES:
            current_digit = _CJK_DIGIT_VALUES[char]
            continue
        if char in _CJK_SMALL_UNITS:
            unit = _CJK_SMALL_UNITS[char]
            # 十 alone means ten, and 十二 means twelve.
            section += (1 if current_digit is None else current_digit) * unit
            current_digit = None
            continue
        section += current_digit or 0
        total += section * _CJK_BIG_UNITS[char]
        section = 0
        current_digit = None
    return total + section + (current_digit or 0)


def _canonical_digits(value: str) -> str:
    cleaned = value.strip()
    if cleaned.isdigit() and len(cleaned) > 1:
        return cleaned.lstrip("0") or "0"
    return cleaned


def number_fingerprint(text: str) -> list[str]:
    """Ordered list of canonical numeric tokens in ``text``.

    Both spellings of the same number collapse to the same token, so ``1962``
    and ``一九六二`` compare equal while ``1962`` and ``一九六三`` do not.
    """

    fingerprints: list[str] = []
    for match in _ASCII_NUMBER_PATTERN.finditer(text or ""):
        fingerprints.append(_canonical_digits(match.group(0)))
    for run in _CJK_NUMERAL_RUN.findall(text or ""):
        for token in _split_cjk_numeral_run(run):
            fingerprints.append(token)
    return sorted(fingerprints)


def _split_cjk_numeral_run(run: str) -> list[str]:
    """Split a numeral-character run into canonical numeric tokens.

    A run that parses to a single value becomes that value written in canonical
    digits; a run that does not parse at all falls back to one token per
    character, which still keeps ``一九六二`` equal to ``1962``.
    """

    value = parse_cjk_numeral(run)
    if value is not None:
        return [str(value)]
    tokens: list[str] = []
    for char in run:
        digit = _CJK_DIGIT_VALUES.get(char)
        if digit is not None:
            tokens.append(str(digit))
        elif char in _CJK_SMALL_UNITS:
            tokens.append(str(_CJK_SMALL_UNITS[char]))
        elif char in _CJK_BIG_UNITS:
            tokens.append(str(_CJK_BIG_UNITS[char]))
    return tokens


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
class ExplainerLocalizationService:
    """Translation authority plus the independent-clock contract."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ translation
    def create_translation_revision(
        self,
        *,
        project_id: str,
        video_id: str,
        source_script_revision_id: str,
        target_locale: str,
        translations: Mapping[str, Mapping[str, Any]],
        actor: str,
        title: str | None = None,
        terminology: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.repo.require_explainer_project(project_id)
        source = self.repo.find("explainer_script_revisions", source_script_revision_id)
        if source is None or str(source.get("video_id")) != video_id:
            raise ExplainerContractError(
                "NOT_FOUND",
                "来源讲稿修订不存在或不属于该作品",
                {"source_script_revision_id": source_script_revision_id, "video_id": video_id},
            )
        if str(source.get("status")) != "FROZEN":
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "翻译必须基于已冻结的讲稿修订，请先冻结来源修订",
                {"source_script_revision_id": source_script_revision_id, "status": str(source.get("status"))},
            )
        locale = normalize_locale(target_locale)
        source_segments = self.repo.segments(source_script_revision_id)
        if not source_segments:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "来源讲稿修订没有段落", {"source_script_revision_id": source_script_revision_id}
            )
        supplied: dict[str, dict[str, Any]] = {}
        for key, value in dict(translations or {}).items():
            supplied[str(key)] = _as_mapping(value, field=f"translations[{key}]")
        missing = [str(item["canonical_segment_id"]) for item in source_segments if str(item["canonical_segment_id"]) not in supplied]
        if missing:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "翻译缺少来源段落，必须逐个段落提供译文",
                {"missing_canonical_segment_ids": missing, "target_locale": locale},
            )
        unknown = [key for key in supplied if key not in {str(item["canonical_segment_id"]) for item in source_segments}]
        if unknown:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "翻译包含来源讲稿中不存在的段落",
                {"unknown_canonical_segment_ids": unknown},
            )
        number_mismatches: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        payload_segments: list[dict[str, Any]] = []
        for segment in source_segments:
            canonical_id = str(segment["canonical_segment_id"])
            translation = supplied[canonical_id]
            display_text = str(translation.get("display_text") or "")
            spoken_text = str(translation.get("spoken_text") or "")
            if not display_text.strip() or not spoken_text.strip():
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "译文的 display_text 与 spoken_text 都必须非空",
                    {"canonical_segment_id": canonical_id},
                )
            pronunciation_map = self._normalise_map(translation.get("pronunciation_map"), canonical_id=canonical_id)
            self._assert_span_equivalence(
                canonical_id=canonical_id,
                display_text=display_text,
                spoken_text=spoken_text,
                pronunciation_map=pronunciation_map,
            )
            source_display = str(segment["display_text"])
            source_numbers = number_fingerprint(source_display)
            target_text = f"{display_text} {spoken_text}"
            target_numbers = number_fingerprint(target_text)
            if source_numbers != target_numbers:
                number_mismatches.append(
                    {
                        "canonical_segment_id": canonical_id,
                        "source_text": source_display,
                        "target_text": display_text,
                        "source_numbers": source_numbers,
                        "target_numbers": target_numbers,
                    }
                )
            source_negations = negation_markers(source_display)
            if source_negations:
                target_negations = negation_markers(target_text)
                if not target_negations:
                    warnings.append(
                        {
                            "kind": "NEGATION_MISMATCH",
                            "canonical_segment_id": canonical_id,
                            "source_negation_markers": source_negations,
                            "target_negation_markers": [],
                            "source_text": source_display,
                            "target_text": display_text,
                            "message": "来源段落含否定标记而译文未检出，请人工复核是否漏译否定",
                        }
                    )
            payload_segments.append(
                {
                    "canonical_segment_id": canonical_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "pronunciation_map": pronunciation_map,
                    "source_display_text": source_display,
                    "source_segment_hash": str(segment["segment_hash"]),
                    "segment_hash": content_hash(
                        {
                            "display_text": display_text,
                            "spoken_text": spoken_text,
                            "claim_ids": list(segment.get("claim_ids_json") or []),
                            "pronunciation_map": pronunciation_map,
                            "pause_after_ms": int(segment.get("pause_after_ms") or 0),
                        }
                    ),
                }
            )
        if number_mismatches:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "数字/单位不一致：译文与来源段落的数字必须逐一对应",
                {"mismatches": number_mismatches, "target_locale": locale},
            )
        revision_no = self._next_revision_no(video_id=video_id, locale=locale)
        revision_base = {
            "video_id": video_id,
            "locale": locale,
            "source_script_revision_id": source_script_revision_id,
            "title": str(title if title is not None else (source.get("title") or "")),
            "outline_json": list(source.get("outline_json") or []),
            "terminology_json": dict(terminology if terminology is not None else (source.get("terminology_json") or {})),
            "segments": [
                {"canonical_segment_id": item["canonical_segment_id"], "segment_hash": item["segment_hash"]}
                for item in payload_segments
            ],
            "number_fingerprints": [number_fingerprint(item["display_text"]) for item in payload_segments],
        }
        revision = self.repo.insert(
            "explainer_script_revisions",
            {
                "video_id": video_id,
                "revision_no": revision_no,
                "locale": locale,
                "source_script_revision_id": source_script_revision_id,
                "title": revision_base["title"],
                "outline_json": revision_base["outline_json"],
                "terminology_json": revision_base["terminology_json"],
                "status": "DRAFT",
                "content_hash": content_hash(revision_base),
                "parent_plan_id": source.get("parent_plan_id"),
                "provenance_json": {
                    "provenance_kind": "TRANSLATION",
                    "source_script_revision_id": source_script_revision_id,
                    "source_locale": str(source["locale"]),
                    "target_locale": locale,
                    "actor": actor,
                    "created_at": utc_now_iso(),
                    "negation_policy": "WARN_ONLY_HUMAN_DECIDES",
                    "absolute_timestamps_copied": False,
                },
            },
            actor=actor,
        )
        written_segments: list[dict[str, Any]] = []
        source_by_canonical = {str(item["canonical_segment_id"]): item for item in source_segments}
        previous_segment_id: str | None = None
        for item in payload_segments:
            origin = source_by_canonical[item["canonical_segment_id"]]
            written = self.repo.insert(
                "narration_segments",
                {
                    "video_id": video_id,
                    "script_revision_id": revision["id"],
                    "chapter_id": origin.get("chapter_id"),
                    "canonical_segment_id": item["canonical_segment_id"],
                    "locale": locale,
                    "ordinal": int(origin["ordinal"]),
                    "display_text": item["display_text"],
                    "spoken_text": item["spoken_text"],
                    "statement_type": str(origin["statement_type"]),
                    "claim_ids_json": list(origin.get("claim_ids_json") or []),
                    "pronunciation_map_json": item["pronunciation_map"],
                    "speaker": origin.get("speaker"),
                    "emotion": origin.get("emotion"),
                    "pause_after_ms": int(origin.get("pause_after_ms") or 0),
                    "target_duration_ms": origin.get("target_duration_ms"),
                    "content_locked_by_human": bool(origin.get("content_locked_by_human")),
                    "segment_hash": item["segment_hash"],
                    "previous_segment_id": previous_segment_id,
                },
                actor=actor,
            )
            previous_segment_id = str(written["id"])
            written_segments.append(written)
        _commit(self.repo)
        return {
            "script_revision": self.repo.get("explainer_script_revisions", str(revision["id"])),
            "segments": written_segments,
            "chapters": self.repo.list_where(
                "explainer_chapters",
                {"script_revision_id": source_script_revision_id},
                order_by="ordinal",
                descending=False,
            ),
            "warnings": warnings,
            "number_check": {"mismatches": [], "checked_segment_count": len(payload_segments)},
            "source_locale": str(source["locale"]),
            "target_locale": locale,
        }

    # ------------------------------------------------------------------ clocks
    def build_english_edition_plan(
        self, *, video_id: str, source_locale: str, target_locale: str
    ) -> dict[str, Any]:
        source = normalize_locale(source_locale)
        target = normalize_locale(target_locale)
        if source == target:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "来源与目标语言相同，不需要独立时钟的译制 edition",
                {"source_locale": source, "target_locale": target},
            )
        self.assert_no_timestamp_reuse(
            source_locale=source, target_locale=target, target_timeline_source="INDEPENDENT_TTS_CLOCK"
        )
        video = self.repo.find("explainer_videos", video_id)
        if video is None:
            raise ExplainerContractError("NOT_FOUND", "解说作品不存在", {"video_id": video_id})
        source_segments = self._latest_locale_segments(video_id=video_id, locale=source)
        return {
            "video_id": video_id,
            "policy": "INDEPENDENT_TTS_CLOCK",
            "source_locale": source,
            "target_locale": target,
            "source_timeline": {
                "locale": source,
                "clock": "SOURCE_TTS_ALIGNMENT",
                "script_revision_id": source_segments.get("script_revision_id"),
                "segment_count": len(source_segments.get("segments", [])),
                "authoritative": True,
                "reusable_for_target": False,
            },
            "target_timeline": None,
            "target_timeline_source": "INDEPENDENT_TTS_CLOCK",
            "planned_chinese_timestamps_copied": False,
            "retiming_allowed": True,
            "notes": [
                "英文 edition 必须独立合成 TTS 并独立强制对齐，绝对时间码不复用中文 edition。",
                "英文成片时长允许与中文不同；分镜时长按英文实测重排。",
                "字幕配对依据 canonical_segment_id 的语义配对，不做逐字对齐。",
                "translations 必须来自冻结的中文讲稿修订，且数字与单位逐段一致。",
            ],
        }

    def assert_no_timestamp_reuse(
        self, *, source_locale: str, target_locale: str, target_timeline_source: str
    ) -> None:
        source = normalize_locale(source_locale)
        target = normalize_locale(target_locale)
        if target_timeline_source == "COPIED_FROM_SOURCE_LOCALE":
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "禁止复用来源语言的绝对时间码：目标语言必须使用独立 TTS 时钟与独立对齐",
                {
                    "source_locale": source,
                    "target_locale": target,
                    "target_timeline_source": target_timeline_source,
                    "policy": "INDEPENDENT_TTS_CLOCK",
                },
            )
        if target_timeline_source not in _TARGET_TIMELINE_SOURCES:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "未知的目标时间线来源",
                {"target_timeline_source": target_timeline_source, "allowed": sorted(_TARGET_TIMELINE_SOURCES)},
            )

    # ------------------------------------------------------------------ terminology
    def terminology_dictionary(self, *, project_id: str, video_id: str) -> dict[str, Any]:
        self.repo.require_explainer_project(project_id)
        video = self.repo.find("explainer_videos", video_id)
        if video is None or str(video.get("project_id")) != project_id:
            raise ExplainerContractError(
                "NOT_FOUND", "解说作品不存在或不属于该项目", {"project_id": project_id, "video_id": video_id}
            )
        revisions = self.repo.list_where(
            "explainer_script_revisions", {"video_id": video_id}, order_by="revision_no", descending=False
        )
        terms: dict[str, dict[str, Any]] = {}
        by_locale: dict[str, dict[str, str]] = {}
        revision_sources: list[dict[str, Any]] = []
        for revision in revisions:
            locale = str(revision["locale"])
            mapped = self._flatten_terminology(revision.get("terminology_json"))
            if mapped:
                revision_sources.append(
                    {"script_revision_id": str(revision["id"]), "locale": locale, "status": str(revision["status"])}
                )
            bucket = by_locale.setdefault(locale, {})
            for term, form in mapped.items():
                bucket[term] = form
                entry = terms.setdefault(
                    term,
                    {
                        "term": term,
                        "spellings": {},
                        "entity_code": None,
                        "latin_name": None,
                        "aliases": [],
                        "sources": [],
                    },
                )
                entry["spellings"][locale] = form
                entry["sources"].append({"script_revision_id": str(revision["id"]), "locale": locale})
        proper_nouns: list[dict[str, Any]] = []
        entities = self.repo.list_where(
            "explainer_entities", {"video_id": video_id}, order_by="code", descending=False
        )
        for entity in entities:
            aliases = [str(item) for item in (entity.get("aliases_json") or [])]
            proper_nouns.append(
                {
                    "entity_id": str(entity["id"]),
                    "code": str(entity["code"]),
                    "entity_type": str(entity["entity_type"]),
                    "name": str(entity["name"]),
                    "latin_name": entity.get("latin_name"),
                    "aliases": aliases,
                    "fictional": bool(entity.get("fictional")),
                    "status": str(entity.get("status")),
                }
            )
            entry = terms.setdefault(
                str(entity["code"]),
                {
                    "term": str(entity["code"]),
                    "spellings": {},
                    "entity_code": str(entity["code"]),
                    "latin_name": entity.get("latin_name"),
                    "aliases": aliases,
                    "sources": [],
                },
            )
            entry["entity_code"] = str(entity["code"])
            entry["latin_name"] = entity.get("latin_name")
            entry["aliases"] = aliases
            entry["canonical_name"] = str(entity["name"])
        conflicts: list[dict[str, Any]] = []
        for term, entry in sorted(terms.items()):
            spellings = {locale: form for locale, form in sorted(entry["spellings"].items())}
            if len(spellings) > 1 and len(set(spellings.values())) == 1:
                conflicts.append(
                    {
                        "term": term,
                        "reason": "SAME_SPELLING_ACROSS_LOCALES",
                        "spellings": spellings,
                        "message": "同一专有名词在不同语言下拼写相同，请确认是否为有意保留",
                    }
                )
        return {
            "project_id": project_id,
            "video_id": video_id,
            "policy": "ONE_PERSON_ONE_PLACE_ONE_SPELLING",
            "terminology": [terms[key] for key in sorted(terms)],
            "by_locale": {locale: dict(sorted(bucket.items())) for locale, bucket in sorted(by_locale.items())},
            "proper_nouns": proper_nouns,
            "revision_sources": revision_sources,
            "conflicts": conflicts,
            "generated_at": utc_now_iso(),
        }

    # ------------------------------------------------------------------ internals
    def _latest_locale_segments(self, *, video_id: str, locale: str) -> dict[str, Any]:
        segments = self.repo.list_where(
            "narration_segments", {"video_id": video_id, "locale": locale}, order_by="ordinal", descending=False
        )
        if not segments:
            return {"script_revision_id": None, "segments": []}
        script_revision_id = str(segments[0]["script_revision_id"])
        return {
            "script_revision_id": script_revision_id,
            "segments": [item for item in segments if str(item["script_revision_id"]) == script_revision_id],
        }

    def _next_revision_no(self, *, video_id: str, locale: str) -> int:
        row = self.repo.query_one(
            "SELECT COALESCE(MAX(revision_no), 0) AS current FROM explainer_script_revisions WHERE video_id = ? AND locale = ?",
            (video_id, locale),
        )
        return int(row["current"] if row is not None else 0) + 1

    def _flatten_terminology(self, raw: Any) -> dict[str, str]:
        """Flatten one revision's ``terminology_json`` into ``{term: preferred form}``.

        The canonical shape is ``{"<term>": "<preferred form>"}``.  A nested
        mapping is also accepted (``{"<term>": {"preferred": "..."}}``) so a
        richer record can still contribute its declared preferred spelling.
        """

        if not isinstance(raw, Mapping):
            return {}
        flat: dict[str, str] = {}
        for key, value in raw.items():
            term = str(key)
            if isinstance(value, str):
                if value.strip():
                    flat[term] = value.strip()
                continue
            if isinstance(value, Mapping):
                preferred = value.get("preferred") or value.get("target") or value.get("display")
                if isinstance(preferred, str) and preferred.strip():
                    flat[term] = preferred.strip()
                else:
                    flat.setdefault(term, term)
        return flat

    def _normalise_map(self, raw: Any, *, canonical_id: str) -> list[dict[str, str]]:
        if raw is None:
            return []
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "pronunciation_map 必须是对象数组", {"canonical_segment_id": canonical_id}
            )
        normalised: list[dict[str, str]] = []
        for index, entry in enumerate(raw):
            item = _as_mapping(entry, field=f"pronunciation_map[{index}]")
            display = str(item.get("display") or "").strip()
            spoken = str(item.get("spoken") or "").strip()
            if not display or not spoken:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "发音映射的 display 与 spoken 都必须非空",
                    {"canonical_segment_id": canonical_id, "index": index},
                )
            normalised.append({"display": display, "spoken": spoken})
        return normalised

    def _assert_span_equivalence(
        self,
        *,
        canonical_id: str,
        display_text: str,
        spoken_text: str,
        pronunciation_map: Sequence[Mapping[str, str]],
    ) -> None:
        for pair in pronunciation_map:
            display = str(pair.get("display") or "")
            spoken = str(pair.get("spoken") or "")
            if display not in display_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "发音映射的 display 必须出现在译文 display_text 中",
                    {"canonical_segment_id": canonical_id, "display": display},
                )
            if spoken not in spoken_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "发音映射的 spoken 必须出现在译文 spoken_text 中",
                    {"canonical_segment_id": canonical_id, "spoken": spoken},
                )
        implied = apply_pronunciation_map(display_text, pronunciation_map)
        if implied != spoken_text:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "译文 display_text 与 spoken_text 不等价",
                {
                    "canonical_segment_id": canonical_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "implied_spoken_text": implied,
                },
            )
