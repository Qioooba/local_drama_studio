"""NARRATION_ALIGN job handler: forced alignment plus independent ASR review.

Given a verified narration take and the *authoritative* ``spoken_text`` of its
segment, this handler calls an injected aligner, then writes one
``narration_alignment_revisions`` row containing word timings, the sample window
(``sample_offset`` / ``total_samples``), the display map, the unaligned tokens
and the ASR review.

The rules it exists to enforce:

* Tokens the aligner did not return a timestamp for go into
  ``unaligned_tokens_json`` with their text and a reason.  They never receive an
  interpolated or "estimated" timestamp, and the row's ``alignment_status``
  becomes ``PARTIAL`` (``FAILED`` when nothing at all could be aligned).
* ASR output is *evidence*, never authority.  ``asr_review_json`` records the
  independent transcript, the normalised comparison and the resulting issue
  facts (``MISSING_SEGMENT``, ``REPEATED_WORD``, ``MISREAD_KEY_TERM``,
  ``MISREAD_NUMBER``).  The script is never edited here.
* ``display_map`` maps each spoken span to a range of the segment's
  ``display_text`` using the declared ``pronunciation_map`` and a monotonic
  character walk, so a subtitle can highlight the writing that was read.

What this handler deliberately does NOT do:

* It never writes ``narration_segments`` or creates a script revision: a misread
  word is a QC fact, not a script change.
* It never copies timestamps from another take, another locale or another
  edition, and never re-uses a previous alignment as if it were a new one.
* It never probes or regenerates audio.  The take's media hash is verified
  against the project media record instead.
* It never closes or opens QC issues in the database: the facts are returned so
  the QC layer records them with its own evidence rules.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import content_hash, normalize_locale, utc_now_iso
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]

_ASCII_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
#: CJK blocks *and* CJK punctuation.  A Chinese narration is tokenised per
#: character so the token count matches the character walk exactly; a Latin
#: narration is tokenised per word.
_CJK_RUN = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\uff00-\uffef]+"
)
#: CJK punctuation/symbol runs are *not* alignment tokens: a pause is a timing
#: fact, not a spoken sound.  They are reported in ``skipped_punctuation``.
_CJK_PUNCTUATION = re.compile(r"^[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]+$")
_NUMERIC_TOKEN = re.compile(r"\d+(?:[.:]\d+)*")
#: Numeral characters used to group a Chinese number that is read digit by digit.
_CJK_NUMERAL_CHARS = frozenset("零〇一二三四五六七八九十百千万亿两点两")
_MISREAD_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "of", "and", "to", "in", "is", "was", "were", "it", "that", "this", "for", "on", "with"}
)


class NarrationAlignerPort(Protocol):
    """Forced-alignment contract.

    ``align`` returns ``word_timings`` for the tokens it could align.  A token
    absent from that list is reported as unaligned by the handler; a concrete
    adapter must not fabricate timings to fill the list.
    """

    def align(
        self,
        *,
        media_path: Path,
        media_sha256: str,
        spoken_text: str,
        display_text: str,
        locale: str,
        sample_offset: int,
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol boundary
        ...


class NarrationAsrPort(Protocol):
    """Independent ASR transcript used only for review."""

    def transcribe(
        self, *, media_path: Path, locale: str, timeout_seconds: int
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol boundary
        ...


class NarrationAlignMediaPort(Protocol):
    """Verified content access for a narration take."""

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:  # pragma: no cover - protocol boundary
        ...


class WorkerPersistencePort(Protocol):
    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        commit()


def normalize_for_alignment(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _split_units(text: str) -> list[tuple[int, int, str, bool]]:
    """Ordered ``(start, end, unit, is_punctuation)`` units for a text."""

    units: list[tuple[int, int, str, bool]] = []
    for match in re.finditer(f"{_CJK_RUN.pattern}|{_ASCII_WORD.pattern}", str(text or "")):
        chunk = match.group(0)
        if _CJK_RUN.fullmatch(chunk):
            for index, character in enumerate(chunk):
                if character.isspace():
                    continue
                start = match.start() + index
                units.append((start, start + 1, character, bool(_CJK_PUNCTUATION.match(character))))
        else:
            units.append((match.start(), match.end(), chunk, False))
    return units


def tokenize_spoken(text: str) -> list[str]:
    """Deterministic tokenisation: CJK per character, Latin per word.

    CJK punctuation is skipped: ``他出生了。`` tokenises to the same tokens as
    ``他出生了``, so a trailing full stop can never become a phantom "missing
    word" in the alignment.
    """

    return [unit for _, _, unit, is_punctuation in _split_units(text) if not is_punctuation]


def punctuation_units(text: str) -> list[dict[str, Any]]:
    return [
        {"token": unit, "start_offset": start, "end_offset": end}
        for start, end, unit, is_punctuation in _split_units(text)
        if is_punctuation
    ]


def _display_token_spans(display_text: str, pronunciation_map: Sequence[Mapping[str, str]]) -> list[tuple[str, int, int]]:
    """Split display text into ``(text, start_offset, end_offset)`` spans.

    Spans preserve the original offsets so a highlight range always maps back to
    the writing the subtitle actually shows.
    """

    if not display_text:
        return []
    protected: list[tuple[int, int, str]] = []
    for pair in sorted(pronunciation_map, key=lambda item: len(str(item.get("display") or "")), reverse=True):
        display = str(pair.get("display") or "")
        start = 0
        while display and (index := display_text.find(display, start)) != -1:
            if not any(not (index + len(display) <= left or index >= right) for left, right, _ in protected):
                protected.append((index, index + len(display), display))
            start = index + len(display)
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for start, end, value in sorted(protected):
        if start > cursor:
            spans.extend(_plain_display_spans(display_text[cursor:start], cursor))
        spans.append((value, start, end))
        cursor = end
    if cursor < len(display_text):
        spans.extend(_plain_display_spans(display_text[cursor:], cursor))
    return spans


def _plain_display_spans(segment: str, offset: int) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for start, end, unit, is_punctuation in _split_units(segment):
        if is_punctuation:
            continue
        spans.append((unit, offset + start, offset + end))
    return spans


def _normalised_character_walk(text: str, pronunciation_map: Sequence[Mapping[str, str]]) -> list[tuple[str, int, int]]:
    """Ordered ``(char, start, end)`` list for the spoken text after mapping."""

    mapped = text
    for pair in sorted(pronunciation_map, key=lambda item: len(str(item.get("display") or "")), reverse=True):
        display = str(pair.get("display") or "")
        spoken = str(pair.get("spoken") or "")
        if display:
            mapped = mapped.replace(display, spoken)
    return [
        (unit, start, end)
        for start, end, unit, is_punctuation in _split_units(mapped)
        if not is_punctuation
    ]


def _chunk_spans(chunk: Mapping[str, Any], chunk_index: int) -> tuple[int, int] | None:
    start = chunk.get("start_sample", chunk.get("start_ms"))
    end = chunk.get("end_sample", chunk.get("end_ms"))
    if start is None or end is None:
        return None
    try:
        start_value = int(start)
        end_value = int(end)
    except (TypeError, ValueError):
        return None
    del chunk_index
    return (start_value, end_value)


def _match_chunks_to_tokens(
    *,
    chunks: Sequence[Mapping[str, Any]],
    expected_tokens: Sequence[str],
) -> list[dict[str, Any]]:
    """Attach aligner chunks to expected tokens by longest common subsequence.

    Order is preserved on both sides, so a chunk that only matches a *later*
    token can never consume an earlier one: the tokens in between keep no
    timestamp at all and are reported as unaligned instead of inheriting a
    neighbouring timing.  Chunks that match no token are dropped rather than
    forced onto the nearest token.
    """

    word_timings: list[dict[str, Any]] = []
    chunk_pairs: list[tuple[int, int, str, Any]] = []
    for index, chunk in enumerate(chunks):
        span = _chunk_spans(chunk, index)
        if span is None:
            continue
        start, end = span
        if end < start:
            start, end = end, start
        word = str(chunk.get("word") or chunk.get("token") or "").strip()
        chunk_pairs.append((start, end, word, chunk.get("confidence")))
    # Longest common subsequence over (expected token, aligner chunk) keeps the
    # order of both sides.  A chunk that matches only a *later* token therefore
    # cannot consume an earlier one, so the tokens in between stay unaligned
    # instead of inheriting a neighbouring timestamp.
    token_count = len(expected_tokens)
    chunk_count = len(chunk_pairs)
    table = [[0] * (chunk_count + 1) for _ in range(token_count + 1)]
    for token_index in range(1, token_count + 1):
        for chunk_index in range(1, chunk_count + 1):
            if _token_matches(expected_tokens[token_index - 1], chunk_pairs[chunk_index - 1][2]):
                table[token_index][chunk_index] = table[token_index - 1][chunk_index - 1] + 1
            else:
                table[token_index][chunk_index] = max(
                    table[token_index - 1][chunk_index], table[token_index][chunk_index - 1]
                )
    pairs: list[tuple[int, int]] = []
    token_index, chunk_index = token_count, chunk_count
    while token_index > 0 and chunk_index > 0:
        token = expected_tokens[token_index - 1]
        word = chunk_pairs[chunk_index - 1][2]
        if _token_matches(token, word):
            pairs.append((token_index - 1, chunk_index - 1))
            token_index -= 1
            chunk_index -= 1
        elif table[token_index - 1][chunk_index] >= table[token_index][chunk_index - 1]:
            token_index -= 1
        else:
            chunk_index -= 1
    pairs.reverse()
    grouped: dict[int, list[tuple[int, int, str, Any]]] = {}
    for token_position, chunk_position in pairs:
        grouped.setdefault(token_position, []).append(chunk_pairs[chunk_position])
    for token_position in sorted(grouped):
        token = expected_tokens[token_position]
        chunks_for_token = grouped[token_position]
        start = min(item[0] for item in chunks_for_token)
        end = max(item[1] for item in chunks_for_token)
        spoken_words = [item[2] for item in chunks_for_token if item[2]]
        confidences = [float(item[3]) for item in chunks_for_token if isinstance(item[3], (int, float))]
        matches = any(
            _token_matches(token, spoken)
            for spoken in (spoken_words or [token])
        )
        ratio = difflib.SequenceMatcher(None, token, "".join(spoken_words) or token).ratio()
        word_timings.append(
            {
                "token": token,
                "spoken_word": spoken_words[0] if spoken_words else token,
                "start_sample": start,
                "end_sample": end,
                "confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
                "text_match": matches,
                "match_ratio": round(ratio, 4),
                "chunk_count": len(chunks_for_token),
                "timestamp_source": "ALIGNER",
            }
        )
    return word_timings


def _token_matches(expected: str, spoken: str) -> bool:
    left = expected.casefold()
    right = spoken.casefold()
    if left == right:
        return True
    if left and right and (left in right or right in left):
        return True
    # Single-character tokens are only equal when they are equal: a fuzzy ratio
    # between two unrelated characters would silently hide a misread.
    if len(left) == 1 or len(right) == 1:
        return False
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.75


def _unaligned_tokens(
    *, expected_tokens: Sequence[str], word_timings: Sequence[Mapping[str, Any]], chunks_seen: int
) -> list[dict[str, Any]]:
    aligned = {str(item["token"]) for item in word_timings}
    unaligned: list[dict[str, Any]] = []
    for index, token in enumerate(expected_tokens):
        if token in aligned:
            continue
        unaligned.append(
            {
                "token": token,
                "token_index": index,
                "reason": "ALIGNER_RETURNED_NO_TIMESTAMP" if chunks_seen else "ALIGNER_RETURNED_NO_CHUNKS",
                "timestamp": None,
                "fabricated": False,
            }
        )
    return unaligned


def is_number_token(token: str) -> bool:
    """True when a token belongs to a number, in digits or in Chinese numerals.

    A Chinese number is read digit by digit (``一九六二``), so this predicate is
    what lets the review group adjacent numeral characters back into one number
    instead of comparing single characters.
    """

    if _NUMERIC_TOKEN.search(token):
        return True
    return bool(token) and all(character in _CJK_NUMERAL_CHARS for character in token)


def number_units(tokens: Sequence[str]) -> list[dict[str, Any]]:
    """Group adjacent numeral tokens into one numbered unit.

    ``一九六二年`` becomes the units ``一九六二`` (a number) and ``年``, so an ASR
    that heard ``一九六三`` produces one ``MISREAD_NUMBER`` fact rather than a
    per-character smear.
    """

    units: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if is_number_token(token):
            start = index
            parts: list[str] = []
            while index < len(tokens) and is_number_token(tokens[index]):
                parts.append(tokens[index])
                index += 1
            units.append({"text": "".join(parts), "token_start": start, "token_end": index})
            continue
        units.append({"text": token, "token_start": index, "token_end": index + 1})
        index += 1
    return units


def _unit_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return str(left["text"]).casefold() == str(right["text"]).casefold()


def _asr_issue_facts(
    *,
    expected_tokens: Sequence[str],
    asr_text: str,
    key_terms: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Independent ASR review turned into QC facts, never script edits.

    Both sides are tokenised with :func:`tokenize_spoken`, which skips CJK
    punctuation, and adjacent numeral tokens are grouped by
    :func:`number_units`, so a full stop is never a phantom word and
    ``一九六三`` is one misread number rather than three changed characters.
    """

    asr_tokens = tokenize_spoken(asr_text)
    expected_units = number_units(expected_tokens)
    heard_units = number_units(asr_tokens)
    normalized = difflib.SequenceMatcher(
        None,
        [str(item["text"]).casefold() for item in expected_units],
        [str(item["text"]).casefold() for item in heard_units],
        autojunk=False,
    )
    missing: list[dict[str, Any]] = []
    repeated: list[dict[str, Any]] = []
    misread: list[dict[str, Any]] = []
    key_term_set = {str(item).casefold() for item in key_terms if str(item).strip()}
    for tag, i1, i2, j1, j2 in normalized.get_opcodes():
        if tag == "equal":
            continue
        expected_slice = expected_units[i1:i2]
        heard_slice = heard_units[j1:j2]
        expected_texts = [str(item["text"]) for item in expected_slice]
        heard_texts = [str(item["text"]) for item in heard_slice]
        number_units_here = [item for item in expected_slice if is_number_token(str(item["text"]))]
        key_units_here = [item for item in expected_slice if str(item["text"]).casefold() in key_term_set]
        if tag == "insert":
            # The audio said something the script does not have at all.
            for unit in heard_slice:
                repeated.append(
                    {
                        "kind": "REPEATED_WORD",
                        "token": str(unit["text"]),
                        "expected": expected_texts,
                        "heard": heard_texts,
                    }
                )
            continue
        if number_units_here:
            # A number that came out different outranks an edit-distance label:
            # this is exactly the fact a reviewer must see.
            for unit in number_units_here:
                offset = expected_slice.index(unit)
                heard_unit = heard_slice[min(offset, len(heard_slice) - 1)] if heard_slice else None
                misread.append(
                    {
                        "kind": "MISREAD_NUMBER",
                        "token": str(unit["text"]),
                        "heard": str(heard_unit["text"]) if heard_unit is not None else "",
                        "token_index": int(unit["token_start"]),
                        "expected": expected_texts,
                        "heard_all": heard_texts,
                    }
                )
            continue
        if key_units_here:
            for unit in key_units_here:
                offset = expected_slice.index(unit)
                heard_unit = heard_slice[min(offset, len(heard_slice) - 1)] if heard_slice else None
                misread.append(
                    {
                        "kind": "MISREAD_KEY_TERM",
                        "token": str(unit["text"]),
                        "heard": str(heard_unit["text"]) if heard_unit is not None else "",
                        "token_index": int(unit["token_start"]),
                        "expected": expected_texts,
                        "heard_all": heard_texts,
                    }
                )
            continue
        if tag == "delete" or (i2 - i1) > (j2 - j1):
            for unit in expected_slice:
                missing.append(
                    {
                        "kind": "MISSING_SEGMENT",
                        "token": str(unit["text"]),
                        "token_index": int(unit["token_start"]),
                        "expected": expected_texts,
                        "heard": heard_texts,
                    }
                )
            continue
        for offset, unit in enumerate(expected_slice):
            heard_unit = heard_slice[min(offset, len(heard_slice) - 1)] if heard_slice else None
            heard_token = str(heard_unit["text"]) if heard_unit is not None else ""
            if str(unit["text"]).casefold() in _MISREAD_STOPWORDS or (
                heard_token and heard_token.casefold() in _MISREAD_STOPWORDS
            ):
                missing.append(
                    {
                        "kind": "MISSING_SEGMENT",
                        "token": str(unit["text"]),
                        "token_index": int(unit["token_start"]),
                        "expected": expected_texts,
                        "heard": heard_texts,
                    }
                )
            else:
                misread.append(
                    {
                        "kind": "MISREAD_KEY_TERM",
                        "token": str(unit["text"]),
                        "heard": heard_token,
                        "token_index": int(unit["token_start"]),
                        "expected": expected_texts,
                        "heard_all": heard_texts,
                    }
                )
    issues = missing + repeated + misread
    expected_number_tokens = [str(item["text"]) for item in expected_units if is_number_token(str(item["text"]))]
    heard_number_tokens = [str(item["text"]) for item in heard_units if is_number_token(str(item["text"]))]
    review = {
        "schema_version": "localdrama.explainer.asr-review.v1",
        "text_authority": False,
        "rewrites_script": False,
        "asr_text": asr_text,
        "expected_text": " ".join(expected_tokens),
        "expected_token_count": len(expected_tokens),
        "asr_token_count": len(asr_tokens),
        "similarity_ratio": round(normalized.ratio(), 4),
        "expected_numbers": expected_number_tokens,
        "heard_numbers": heard_number_tokens,
        "issues": issues,
        "issue_count": len(issues),
    }
    return review, issues


def _build_display_mapping(
    *,
    display_text: str,
    pronunciation_map: Sequence[Mapping[str, str]],
    word_timings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Map each aligned spoken token to a range of the displayed writing.

    Spoken tokens are matched against the display tokens with a monotonic
    cursor, so a fused display form such as ``1962`` is located once and every
    spoken token of its pronunciation (``一九六二``) points at that same range.
    """

    display_tokens = _display_token_spans(display_text, pronunciation_map)
    mapping: list[dict[str, Any]] = []
    cursor = 0
    for entry in word_timings:
        token = str(entry["token"])
        cursor, located = _locate_display_token(
            display_tokens, token=token, cursor=cursor, pronunciation_map=pronunciation_map
        )
        mapping.append(
            {
                "token": token,
                "spoken_word": entry.get("spoken_word"),
                "start_sample": int(entry["start_sample"]),
                "end_sample": int(entry["end_sample"]),
                "display_start": located[0] if located else None,
                "display_end": located[1] if located else None,
                "display_text": display_text[located[0] : located[1]] if located else "",
                "mapping_method": located[2] if located else "UNMAPPED",
                "text_match": bool(entry.get("text_match")),
            }
        )
    return mapping


def _locate_display_token(
    display_tokens: Sequence[tuple[str, int, int]],
    *,
    token: str,
    cursor: int,
    pronunciation_map: Sequence[Mapping[str, str]],
) -> tuple[int, tuple[int, int, str] | None]:
    """Locate the display span a spoken token came from, keeping the cursor monotonic."""

    for index in range(cursor, len(display_tokens)):
        span_text, start, end = display_tokens[index]
        if span_text.casefold() == token.casefold():
            return (index, (start, end, "EXACT_TOKEN"))
    for index in range(cursor, len(display_tokens)):
        span_text, start, end = display_tokens[index]
        if _token_matches(span_text, token):
            return (index, (start, end, "FUZZY_TOKEN"))
    # A fused display form (``1962``) is read as several spoken tokens that can
    # never match the display *text*; they point at the span currently being
    # read, but only when that span is genuinely a pronunciation-mapped form.
    # A token that is simply a misread stays unmapped.
    if 0 <= cursor < len(display_tokens):
        span_text, start, end = display_tokens[cursor]
        if _span_contains_pronunciation(
            display_text=span_text, token=token, pronunciation_map=pronunciation_map
        ):
            return (cursor, (start, end, "FUSED_DISPLAY_RANGE"))
    return (cursor, None)


def _span_contains_pronunciation(
    *, display_text: str, token: str, pronunciation_map: Sequence[Mapping[str, str]]
) -> bool:
    """True when ``token`` is one of the spoken readings of ``display_text``."""

    for pair in pronunciation_map:
        display = str(pair.get("display") or "")
        spoken = str(pair.get("spoken") or "")
        if display != display_text or not spoken:
            continue
        if token and token in spoken:
            return True
    return False


def _display_map(
    *,
    display_text: str,
    pronunciation_map: Sequence[Mapping[str, str]],
    spoken_text: str,
    word_timings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    del spoken_text
    return _build_display_mapping(
        display_text=display_text,
        pronunciation_map=pronunciation_map,
        word_timings=word_timings,
    )


def run_narration_align_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    database: WorkerPersistencePort,
    aligner: NarrationAlignerPort,
    media_ops: NarrationAlignMediaPort,
    atomic_writer: AtomicWriter,
    asr: NarrationAsrPort | None = None,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    semantic_inputs = snapshot.get("semantic_inputs") or {}
    if not isinstance(semantic_inputs, Mapping):
        raise DomainRuleError("NARRATION_ALIGN_JOB_SNAPSHOT_INVALID", "对齐 Job 的 semantic_inputs 必须是对象")
    with database.connect() as connection:
        repository = ExplainerRepository(connection)
        take_id = str(semantic_inputs.get("take_id") or "")
        if not take_id:
            raise DomainRuleError("NARRATION_ALIGN_JOB_SNAPSHOT_INVALID", "对齐 Job 必须提供 take_id")
        take = repository.find("narration_takes", take_id)
        if take is None:
            raise DomainRuleError("NARRATION_TAKE_NOT_FOUND", "旁白 take 不存在", {"take_id": take_id})
        segment = repository.get("narration_segments", str(take["segment_id"]))
        video = repository.get("explainer_videos", str(take["video_id"]))
        if str(video.get("project_id")) != str(job["project_id"]):
            raise DomainRuleError(
                "NARRATION_PROJECT_MISMATCH", "旁白 take 属于其他项目", {"take_id": take_id}
            )
        if bool(snapshot.get("require_selected", True)) and not bool(take.get("selected")):
            raise DomainRuleError(
                "NARRATION_TAKE_NOT_SELECTED",
                "强制对齐只能作用于已选定的旁白 take",
                {"take_id": take_id, "selected": bool(take.get("selected"))},
            )
        # Ownership check: a take's media must belong to the same project.  The
        # returned row is intentionally discarded; the validation is the point.
        repository.require_same_project_media(
            project_id=str(job["project_id"]), media_version_id=str(take["media_version_id"])
        )
        locale = normalize_locale(str(take["locale"]))
        expected_script_hash = str(snapshot.get("script_hash") or "")
        if expected_script_hash and expected_script_hash != str(segment["segment_hash"]):
            raise DomainRuleError(
                "NARRATION_ALIGN_SCRIPT_STALE",
                "讲稿段落已变更，该 take 的对齐结果不再具有权威性",
                {
                    "segment_id": str(segment["id"]),
                    "expected_script_hash": expected_script_hash,
                    "actual_script_hash": str(segment["segment_hash"]),
                },
            )
        spoken_text = str(segment["spoken_text"])
        display_text = str(segment["display_text"])
        pronunciation_map = [
            {"display": str(pair.get("display") or ""), "spoken": str(pair.get("spoken") or "")}
            for pair in (segment.get("pronunciation_map_json") or [])
        ]
        sample_rate_hz = int(take.get("sample_rate_hz") or snapshot.get("sample_rate_hz") or 0)
        if sample_rate_hz <= 0:
            raise DomainRuleError(
                "NARRATION_ALIGN_SAMPLE_RATE_UNKNOWN",
                "对齐需要 take 的真实采样率，当前 take 未记录采样率",
                {"take_id": take_id},
            )
        revision_row = repository.query_one(
            "SELECT COALESCE(MAX(revision_no), 0) AS current FROM narration_alignment_revisions WHERE take_id = ?",
            (take_id,),
        )
        revision_no = int(revision_row["current"] if revision_row is not None else 0) + 1
        video_id = str(take["video_id"])
        segment_id = str(segment["id"])
        canonical_segment_id = str(segment["canonical_segment_id"])
        take_media_sha256 = str(take["media_sha256"])
        segment_hash = str(segment["segment_hash"])
    meta, media_path = media_ops.content_path(str(take["media_version_id"]))
    actual_sha256 = str(meta.get("sha256") or "")
    if actual_sha256 and actual_sha256 != take_media_sha256:
        raise DomainRuleError(
            "NARRATION_ALIGN_MEDIA_MISMATCH",
            "take 记录的媒体哈希与当前媒体版本不一致",
            {"take_id": take_id, "expected_sha256": take_media_sha256, "actual_sha256": actual_sha256},
        )
    sample_offset = int(snapshot.get("sample_offset") or 0)
    if sample_offset < 0:
        raise DomainRuleError("NARRATION_ALIGN_JOB_SNAPSHOT_INVALID", "sample_offset 不能为负")
    aligned = aligner.align(
        media_path=media_path,
        media_sha256=take_media_sha256,
        spoken_text=spoken_text,
        display_text=display_text,
        locale=locale,
        sample_offset=sample_offset,
    ) or {}
    chunks = [item for item in (aligned.get("word_timings") or []) if isinstance(item, Mapping)]
    detector_version = str(aligned.get("detector_version") or "")
    expected_tokens = tokenize_spoken(spoken_text)
    word_timings = _match_chunks_to_tokens(chunks=chunks, expected_tokens=expected_tokens)
    unaligned = _unaligned_tokens(
        expected_tokens=expected_tokens, word_timings=word_timings, chunks_seen=len(chunks)
    )
    if not expected_tokens:
        alignment_status = "FAILED"
    elif unaligned and word_timings:
        alignment_status = "PARTIAL"
    elif unaligned and not word_timings:
        alignment_status = "FAILED"
    else:
        alignment_status = "ALIGNED"
    display_map = _display_map(
        display_text=display_text,
        pronunciation_map=pronunciation_map,
        spoken_text=spoken_text,
        word_timings=word_timings,
    )
    skipped_punctuation = punctuation_units(spoken_text)
    if asr is not None:
        asr_result = asr.transcribe(
            media_path=media_path, locale=locale, timeout_seconds=int(snapshot.get("asr_timeout_seconds") or 180)
        ) or {}
        asr_text = normalize_for_alignment(str(asr_result.get("text") or ""))
        key_terms = [str(item) for item in (snapshot.get("key_terms") or [])]
        asr_review, issue_facts = _asr_issue_facts(
            expected_tokens=expected_tokens, asr_text=asr_text, key_terms=key_terms
        )
        asr_review["model_ref"] = str(asr_result.get("model_ref") or "")
        asr_review["profile_version_id"] = str(asr_result.get("profile_version_id") or "")
        asr_review["text_authority"] = False
        asr_review["script_edited"] = False
        asr_review["skipped_punctuation"] = skipped_punctuation
    else:
        asr_review = {
            "schema_version": "localdrama.explainer.asr-review.v1",
            "text_authority": False,
            "rewrites_script": False,
            "status": "NOT_RUN",
            "reason": "ASR_REVIEW_NOT_CONFIGURED",
            "issues": [],
            "issue_count": 0,
            "skipped_punctuation": skipped_punctuation,
        }
        issue_facts = []
    measured_duration_ms = take.get("measured_duration_ms")
    total_samples = None
    if measured_duration_ms and sample_rate_hz:
        total_samples = int(round(int(measured_duration_ms) / 1000.0 * sample_rate_hz))
    elif aligned.get("total_samples") is not None:
        total_samples = int(aligned["total_samples"])
    if total_samples is not None and total_samples <= 0:
        raise DomainRuleError(
            "NARRATION_ALIGN_SAMPLE_COUNT_INVALID", "对齐的总采样数必须为正", {"total_samples": total_samples}
        )
    revision = {
        "take_id": take_id,
        "segment_id": segment_id,
        "video_id": video_id,
        "revision_no": revision_no,
        "locale": locale,
        "script_hash": segment_hash,
        "media_sha256": take_media_sha256,
        "sample_rate_hz": sample_rate_hz,
        "sample_offset": sample_offset,
        "total_samples": total_samples,
        "word_timings_json": word_timings,
        "display_map_json": display_map,
        "alignment_status": alignment_status,
        "unaligned_tokens_json": unaligned,
        "asr_review_json": asr_review,
        "detector_version": detector_version,
    }
    with database.connect() as connection:
        repository = ExplainerRepository(connection)
        stored = repository.insert(
            "narration_alignment_revisions",
            revision,
            actor=str(job.get("actor") or "narration-align-worker"),
        )
        _commit(connection)
    report = {
        "schema_version": "localdrama.explainer.narration-align-report.v1",
        "job_id": str(job.get("id") or ""),
        "video_id": video_id,
        "segment_id": segment_id,
        "canonical_segment_id": canonical_segment_id,
        "take_id": take_id,
        "alignment_revision_id": str(stored["id"]),
        "revision_no": revision_no,
        "alignment_status": alignment_status,
        "aligned_token_count": len(word_timings),
        "unaligned_token_count": len(unaligned),
        "unaligned_tokens": [item["token"] for item in unaligned],
        "total_samples": total_samples,
        "sample_offset": sample_offset,
        "asr_review_included": asr is not None,
        "asr_issue_facts": issue_facts,
        "asr_review_hash": content_hash(asr_review),
        "script_edited": False,
        "generated_at": utc_now_iso(),
        "network_contacted": False,
    }
    output = output_root / "narration-align-report.json"
    import json

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_writer(output, lambda target: target.write_text(payload, encoding="utf-8"))
    try:
        relative = output.relative_to(work_root).as_posix()
    except ValueError:
        relative = output.as_posix()
    return "NARRATION_ALIGNMENT_REPORT", relative
