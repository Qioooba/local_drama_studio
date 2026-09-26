"""Source/binary bookkeeping helpers for the explainer factory (W02).

This module is pure: no HTTP, no SQLite, no model calls.  It owns the three
decisions the rest of the explainer pipeline must not re-implement:

* **Byte -> text decoding.**  ``decode_document_bytes`` is the only supported
  entry point for uploaded binaries.  It reports *how* the bytes were decoded
  (encoding, BOM, newline style, replacement-character count) instead of
  silently guessing, because a wrong encoding turns a citable quote into a
  different claim.  A run that needed replacement characters is still returned
  but flagged ``decoded=False`` so the caller can refuse it; text with no usable
  character at all is rejected outright.
* **Body -> spans.**  ``split_paragraph_spans`` produces offset-addressed
  quotations.  Offsets index the *normalised* body produced by
  :func:`normalise_document_text`; a caller that persists the body must persist
  exactly that string or every offset drifts.  Over-long paragraphs are split on
  sentence boundaries and never truncated: a quote that is silently cut in half
  is worse than two quotes.
* **Date -> (literal, precision).**  ``normalise_event_date`` never fabricates a
  day or a minute the source did not state.  ``1936`` stays ``YEAR``; the three
  separate timestamp fields (``published_at``, ``updated_at_source``,
  ``event_date``) therefore stay genuinely independent.
"""

from __future__ import annotations

import codecs
import re
from datetime import date, datetime
from typing import Any, Iterator, NoReturn
from urllib.parse import urlsplit

from local_drama.domain.explainers.contracts import (
    CredibilityKind,
    DatePrecision,
    ExplainerContractError,
    text_hash,
)

#: BOM -> codec pairs.  UTF-32 must be tested before UTF-16 because
#: ``BOM_UTF32_LE`` starts with the ``BOM_UTF16_LE`` byte sequence; testing in
#: the wrong order would decode a UTF-32 document as garbage UTF-16.
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: Characters trimmed from both ends of a paragraph before it becomes a span.
_TRIM_CHARS = " \t\u3000\n\u00a0"

#: Paragraph separators: a blank line (LF/CRLF/CR, optionally indented) or a run
#: of ideographic spaces.  The latter exists because Chinese manuscripts often
#: use ``\u3000\u3000`` as the only paragraph marker on an otherwise single-line
#: file; without it the whole document would collapse into one giant span.
_PARAGRAPH_SEPARATOR_RE = re.compile(
    r"(?:\r\n|\r|\n)[ \t\u3000]*(?:(?:\r\n|\r|\n)[ \t\u3000]*)+|[ \t]*\u3000{2,}[ \t]*"
)

#: Sentence boundaries used when one paragraph exceeds ``max_span_chars``.
_SENTENCE_BOUNDARY_RE = re.compile(r"[^。！？!?；;…\n]*(?:[。！？!?；;…]+[”’』」）\)\]]*|\n)")

#: Chinese date/time spelling, e.g. 1936年12月31日5时30分20秒.  Parsed with a
#: dedicated pattern instead of token substitution: blind ``日 -> ""`` fixes only
#: the time component after the ``秒`` token boundary, so a blind substitution
#: turns ``31日5时30分`` into the nonsense ``315:30``.
_CN_DATETIME_RE = re.compile(
    r"^(?P<year>\d{4})年"
    r"(?:(?P<month>\d{1,2})月)?"
    r"(?:(?P<day>\d{1,2})[日号])?"
    r"(?:(?P<hour>\d{1,2})[时点](?:(?P<minute>\d{1,2})分?)?(?:(?P<second>\d{1,2})秒?)?)?$"
)

_YEAR_RE = re.compile(r"^(?P<year>\d{4})$")
_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$")
_DAY_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$")
_MINUTE_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})[T ](?P<hour>\d{1,2}):(?P<minute>\d{2})$"
)
_SECOND_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})[T ]"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})(?P<fraction>\.\d+)?(?P<offset>Z|[+-]\d{2}:?\d{2})?$"
)
_DOTTED_DATE_RE = re.compile(r"^\d{4}[./]\d{1,2}(?:[./]\d{1,2})?(?:[T ].*)?$")

#: Declared-first credibility hints keyed by registrable domain fragment.
_PRIMARY_DOMAIN_MARKERS: tuple[str, ...] = (
    ".gov",
    ".gov.cn",
    ".mil",
    ".edu",
    ".edu.cn",
    ".ac.uk",
    "who.int",
    "un.org",
    "unesco.org",
    "europa.eu",
    "loc.gov",
    "archives.gov",
    "si.edu",
    "bl.uk",
    "nasa.gov",
    "noaa.gov",
)
_SECONDARY_DOMAIN_MARKERS: tuple[str, ...] = (
    "news.",
    "reuters.",
    "apnews.",
    "bbc.",
    "bbc.co",
    "nytimes.",
    "theguardian.",
    "xinhuanet.",
    "people.com.cn",
    "thepaper.cn",
    "caixin.",
    "yicai.",
    "chinanews.",
    "nature.com",
    "science.org",
    "sciencedirect.",
    "jstor.org",
    "arxiv.org",
    "pubmed.",
    "ncbi.nlm.nih.",
)
_AGGREGATOR_DOMAIN_MARKERS: tuple[str, ...] = (
    "news.google.",
    "google.com",
    "bing.com",
    "baidu.com",
    "so.com",
    "sogou.com",
    "toutiao.com",
    "msn.com",
    "news.yahoo.",
    "flipboard.com",
    "apple.news",
    "smartnews.",
    "newsnow.",
)
_USER_GENERATED_DOMAIN_MARKERS: tuple[str, ...] = (
    "weibo.com",
    "twitter.com",
    "x.com",
    "reddit.com",
    "zhihu.com",
    "tieba.baidu.com",
    "douban.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "tiktok.com",
    "medium.com",
    "substack.com",
    "wordpress.com",
    "blogspot.",
    "tumblr.com",
    "quora.com",
    "pinterest.",
    "v.qq.com",
    "ixigua.com",
)
_FICTION_URL_MARKERS: tuple[str, ...] = (
    "/fiction/",
    "/fictions/",
    "/novel/",
    "/novels/",
    "fanfiction",
    "archiveofourown",
    "ao3.org",
    "pixiv.net",
    "royalroad.com",
    "wattpad.com",
    "qidian.com",
    "jjwxc.net",
    "zhihu.com/market",
    "小说",
    "剧本",
)

#: Every credibility value this module can return; the classifier never invents
#: a value outside this set and never claims certainty (see the docstring of
#: :func:`classify_source_credibility`).
CREDIBILITY_HEURISTIC_IS_A_HINT = True


def normalise_document_text(text: str) -> str:
    """Freeze a platform-independent body representation.

    Windows newline translation must not be able to desynchronise a span offset
    from the stored body, so CRLF/CR are folded to LF and the whole string is
    stripped once, here, before any offset is computed.

    This is the *evidence* body.  A manuscript the user declared as a finished
    narration script uses :func:`canonical_script_source_text` instead, which
    keeps leading/trailing whitespace (§C2 item 5); the two are related by
    :func:`canonical_body_offset_map`, never by re-normalising one into the other.
    """

    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def canonical_script_source_text(text: str) -> str:
    """The exact preserved-mode manuscript: newline folding and nothing else.

    No ``strip()``, no punctuation change, no character substitution: the body is
    the user's own definition and every later step must be able to reproduce it
    byte-for-byte from the stored slices (§C2 item 5, §C4.1).  A single leading
    BOM is dropped because it is a transport byte-order marker rather than
    manuscript content — the same rule the pasted-manuscript record uses, so one
    file cannot produce two different "canonical" bodies.
    """

    if not isinstance(text, str):
        raise ExplainerContractError(
            "SCHEMA_INVALID", "原稿正文必须是字符串", {"received_type": type(text).__name__}
        )
    folded = text.replace("\r\n", "\n").replace("\r", "\n")
    return folded[1:] if folded.startswith("\ufeff") else folded


def script_source_hash(text: str) -> str:
    """SHA-256 of the canonical preserved-mode manuscript."""

    return text_hash(canonical_script_source_text(text))


def canonical_body_offset_map(canonical_text: str, body: str) -> dict[str, Any]:
    """Explicit mapping between the exact manuscript and the evidence body.

    The two normalisations must never be mixed when computing an offset, so the
    relation is recorded instead of being assumed: ``body`` is located inside the
    canonical text and every canonical offset converts by a single ``shift``.

    A leading U+FEFF in the *evidence* body is a transport marker rather than
    manuscript content, so it is dropped before locating the body and the drop is
    reported as ``bom_normalised``.  Without this, a file whose bytes carried a
    BOM would look like a one-character offset drift between the two texts.
    """

    canonical = canonical_script_source_text(canonical_text)
    body_text = str(body or "")
    bom_normalised = body_text.startswith("\ufeff")
    if bom_normalised:
        body_text = body_text[1:]
    if not body_text:
        return {
            "explicit_offset_mapping": True,
            "body_is_substring": False,
            "bom_normalised": bom_normalised,
            "offset_shift": None,
            "body_start_in_canonical": None,
            "canonical_character_count": len(canonical),
            "body_character_count": 0,
        }
    index = canonical.find(body_text)
    return {
        "explicit_offset_mapping": True,
        "body_is_substring": index >= 0,
        "bom_normalised": bom_normalised,
        "offset_shift": index,
        "body_start_in_canonical": index if index >= 0 else None,
        "canonical_character_count": len(canonical),
        "body_character_count": len(body_text),
        "leading_whitespace_characters": index if index >= 0 else None,
    }


def detect_newline_style(text: str) -> str:
    """Report the newline convention of *text* (before normalisation)."""

    crlf = text.count("\r\n")
    bare_lf = text.count("\n") - crlf
    bare_cr = text.count("\r") - crlf
    present = [kind for kind, count in (("CRLF", crlf), ("LF", bare_lf), ("CR", bare_cr)) if count > 0]
    if not present:
        return "NONE"
    if len(present) > 1:
        return "MIXED"
    return present[0]


def _decode_bytes(payload: bytes) -> tuple[str, str, str]:
    """Return ``(encoding, confidence, text)`` for *payload*.

    BOM-backed decodings are trusted (the BOM is explicit evidence).  Without a
    BOM, UTF-8 is tried strictly first; GB18030 is the low-confidence fallback
    because it is a superset of GBK/GB2312 and accepts almost any byte stream,
    which is exactly why it must be reported as low confidence rather than as a
    fact.
    """

    for bom, encoding in _BOM_ENCODINGS:
        if payload.startswith(bom):
            try:
                return encoding, "HIGH", payload.decode(encoding)
            except UnicodeDecodeError:
                return encoding, "HIGH", payload.decode(encoding, errors="replace")
    try:
        return "utf-8", "HIGH", payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return "gb18030", "LOW", payload.decode("gb18030")
    except UnicodeDecodeError:
        return "gb18030", "LOW", payload.decode("gb18030", errors="replace")


def _usable_character_count(text: str) -> int:
    """Count characters that can carry meaning (not whitespace/NUL/U+FFFD)."""

    return sum(1 for char in text if not char.isspace() and char not in {"\ufffd", "\x00"})


def decode_document_bytes(payload: bytes) -> dict[str, Any]:
    """Decode an uploaded document and report *how*, never silently.

    ``decoded`` is ``False`` whenever a replacement character was produced, so a
    caller can refuse a lossy decode instead of importing mojibake as a source.
    A payload with no usable character at all raises ``SCHEMA_INVALID``.
    """

    if not isinstance(payload, (bytes, bytearray)):
        raise ExplainerContractError(
            "SCHEMA_INVALID", "decode_document_bytes 只接受字节内容", {"type": type(payload).__name__}
        )
    raw = bytes(payload)
    if not raw:
        raise ExplainerContractError("SCHEMA_INVALID", "文档内容为空，无法导入", {"byte_size": 0})

    encoding, confidence, decoded_text = _decode_bytes(raw)
    replacement_count = decoded_text.count("\ufffd")
    newline_style = detect_newline_style(decoded_text)
    normalised = normalise_document_text(decoded_text)
    usable = _usable_character_count(normalised)
    if usable == 0:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "文档解码后没有可用字符；该文件可能是二进制、空壳或损坏文件",
            {
                "encoding": encoding,
                "byte_size": len(raw),
                "replacement_char_count": replacement_count,
                "usable_character_count": 0,
            },
        )

    return {
        "text": normalised,
        "encoding": encoding,
        "confidence": confidence,
        "had_bom": any(raw.startswith(bom) for bom, _ in _BOM_ENCODINGS),
        "newline_style": newline_style,
        "replacement_char_count": replacement_count,
        "decoded": replacement_count == 0,
    }


def _paragraph_ranges(body: str) -> Iterator[tuple[int, int]]:
    cursor = 0
    for match in _PARAGRAPH_SEPARATOR_RE.finditer(body):
        if match.start() > cursor:
            yield (cursor, match.start())
        cursor = match.end()
    if cursor < len(body):
        yield (cursor, len(body))


def _trim_range(body: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and body[start] in _TRIM_CHARS:
        start += 1
    while end > start and body[end - 1] in _TRIM_CHARS:
        end -= 1
    return None if start >= end else (start, end)


def _last_sentence_boundary(body: str, start: int, window_end: int) -> int | None:
    best: int | None = None
    for match in _SENTENCE_BOUNDARY_RE.finditer(body, start, window_end):
        stop = match.end()
        if start < stop <= window_end:
            best = stop
    return best


def _segment_paragraph(body: str, start: int, end: int, max_span_chars: int) -> Iterator[tuple[int, int]]:
    """Cut one paragraph into ``<= max_span_chars`` slices without truncation.

    Each slice ends on a sentence boundary when one exists inside the window.
    When a single sentence is longer than the window there is no boundary to cut
    on, so the slice is hard-split at the window edge — the paragraph is still
    covered end to end, which is the property that matters (a truncated tail
    would silently drop the end of the evidence).
    """

    cursor = start
    while end - cursor > max_span_chars:
        window_end = cursor + max_span_chars
        cut = _last_sentence_boundary(body, cursor, window_end)
        if cut is None or cut <= cursor:
            cut = window_end
        yield (cursor, cut)
        cursor = cut
        while cursor < end and body[cursor] in _TRIM_CHARS:
            cursor += 1
    if end > cursor:
        yield (cursor, end)


def split_paragraph_spans(
    text: str, *, max_span_chars: int = 1200, max_spans: int = 4000
) -> list[dict[str, Any]]:
    """Split *text* into offset-addressed, non-empty paragraph spans.

    Offsets are relative to :func:`normalise_document_text` of *text*.  The cap
    is enforced by raising rather than by dropping spans: silently importing
    3 999 of 5 000 paragraphs would look like a complete evidence base.
    """

    if max_span_chars <= 0 or max_spans <= 0:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "切分参数必须是正整数",
            {"max_span_chars": max_span_chars, "max_spans": max_spans},
        )
    body = normalise_document_text(text)
    if not body:
        return []

    spans: list[dict[str, Any]] = []
    ordinal = 0
    paragraph_no = 0
    for raw_start, raw_end in _paragraph_ranges(body):
        trimmed = _trim_range(body, raw_start, raw_end)
        if trimmed is None:
            continue
        start, end = trimmed
        paragraph_no += 1
        for span_start, span_end in _segment_paragraph(body, start, end, max_span_chars):
            if ordinal >= max_spans:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    f"文档切分后的片段数超过上限 {max_spans}，请拆分文档后分批导入",
                    {"max_spans": max_spans, "paragraph_no": paragraph_no},
                )
            quote_text = body[span_start:span_end]
            spans.append(
                {
                    "ordinal": ordinal,
                    "start_offset": span_start,
                    "end_offset": span_end,
                    "quote_text": quote_text,
                    "span_hash": text_hash(quote_text),
                    "paragraph_no": paragraph_no,
                }
            )
            ordinal += 1
    return spans


def classify_source_credibility(url: str | None, *, declared: str | None = None) -> str:
    """Classify a source as a *hint*, never as a verdict.

    A declared value wins when it is a known credibility kind (an operator who
    recorded ``PRIMARY`` made a decision this heuristic cannot second-guess).  A
    declared ``UNKNOWN`` falls through to the URL heuristic; an unparseable
    declared value also falls through instead of being echoed, so garbage never
    reaches the ``credibility_kind`` check constraint.  When nothing matches the
    answer is ``UNKNOWN`` — this function never claims certainty.
    """

    valid_kinds = {item.value for item in CredibilityKind}
    declared_value = (declared or "").strip().upper()
    if declared_value in valid_kinds and declared_value != CredibilityKind.UNKNOWN.value:
        return declared_value

    folded_url = (url or "").strip()
    if not folded_url:
        return CredibilityKind.UNKNOWN.value
    parsed = urlsplit(folded_url if "//" in folded_url else f"//{folded_url}")
    host = (parsed.hostname or "").casefold().rstrip(".")
    path = (parsed.path or "").casefold()
    haystack = f"{host}{path}"

    for marker in _FICTION_URL_MARKERS:
        if marker in haystack:
            return CredibilityKind.AUTHORED_FICTION.value
    for marker in _USER_GENERATED_DOMAIN_MARKERS:
        if host == marker.rstrip(".") or host.endswith(marker) or marker in host:
            return CredibilityKind.USER_GENERATED.value
    for marker in _AGGREGATOR_DOMAIN_MARKERS:
        if host == marker.rstrip(".") or host.endswith(marker) or marker in host:
            return CredibilityKind.AGGREGATOR.value
    for marker in _PRIMARY_DOMAIN_MARKERS:
        if host.endswith(marker) or marker in host:
            return CredibilityKind.PRIMARY.value
    if "/blog" in path or "/post/" in path or "/u/" in path or "/user/" in path:
        return CredibilityKind.USER_GENERATED.value
    for marker in _SECONDARY_DOMAIN_MARKERS:
        if host.endswith(marker.rstrip(".")) or marker in host:
            return CredibilityKind.SECONDARY.value
    if "/news/" in path or "/article/" in path or "/story/" in path or "/press/" in path:
        return CredibilityKind.SECONDARY.value
    return CredibilityKind.UNKNOWN.value


def _fail_date(value: str, reason: str) -> NoReturn:
    raise ExplainerContractError(
        "SCHEMA_INVALID",
        f"日期无法解析：{value!r}（{reason}）",
        {"value": value, "reason": reason},
    )


def _from_parts(
    raw: str,
    *,
    year: int,
    month: str | None = None,
    day: str | None = None,
    hour: str | None = None,
    minute: str | None = None,
    second: str | None = None,
    fraction: str = "",
    offset: str = "",
) -> tuple[str, str]:
    """Assemble a date literal from already-parsed components.

    Precision is derived from which components the source actually supplied, so
    a missing day/minute is reported as a coarser precision instead of being
    padded.  An hour without minutes is rejected: "about 5 o'clock" cannot be
    stored as an exact minute without inventing information.
    """

    if not 1 <= year <= 9999:
        _fail_date(raw, "年份超出范围")
    if month is None:
        return f"{year:04d}", DatePrecision.YEAR.value
    month_value = int(month)
    if not 1 <= month_value <= 12:
        _fail_date(raw, "月份超出范围")
    if day is None:
        return f"{year:04d}-{month_value:02d}", DatePrecision.MONTH.value
    day_value = int(day)
    if hour is None:
        try:
            date(year, month_value, day_value)
        except ValueError as error:
            _fail_date(raw, str(error))
        return f"{year:04d}-{month_value:02d}-{day_value:02d}", DatePrecision.DAY.value
    if minute is None:
        _fail_date(raw, "只有小时没有分钟，无法确定为精确时间")
    hour_value, minute_value = int(hour), int(minute)
    if second is None:
        try:
            datetime(year, month_value, day_value, hour_value, minute_value)
        except ValueError as error:
            _fail_date(raw, str(error))
        return (
            f"{year:04d}-{month_value:02d}-{day_value:02d}T{hour_value:02d}:{minute_value:02d}",
            DatePrecision.MINUTE.value,
        )
    second_value = int(second)
    try:
        datetime(year, month_value, day_value, hour_value, minute_value, second_value)
    except ValueError as error:
        _fail_date(raw, str(error))
    return (
        f"{year:04d}-{month_value:02d}-{day_value:02d}T"
        f"{hour_value:02d}:{minute_value:02d}:{second_value:02d}{fraction}{offset}",
        DatePrecision.SECOND.value,
    )


def normalise_event_date(value: str | None) -> tuple[str | None, str]:
    """Return ``(literal_or_None, precision)`` without fabricating precision.

    ``1936`` returns ``("1936", "YEAR")`` and ``1936-12`` returns
    ``("1936-12", "MONTH")``; neither is padded to a day or a minute, because a
    fabricated ``01-01T00:00`` would later read as an exact documented instant.
    Chinese date tokens (年/月/日) and ``.``/``/`` separators are accepted so the
    same rule applies to Chinese-language sources.  An empty value is
    ``(None, "UNKNOWN")``; a non-empty unparseable value raises instead of being
    coerced into ``UNKNOWN`` (silently losing a stated date is worse).
    """

    if value is None:
        return None, DatePrecision.UNKNOWN.value
    raw = str(value).strip()
    if not raw:
        return None, DatePrecision.UNKNOWN.value

    chinese = _CN_DATETIME_RE.match(re.sub(r"\s+", "", raw))
    if chinese:
        return _from_parts(
            raw,
            year=int(chinese.group("year")),
            month=chinese.group("month"),
            day=chinese.group("day"),
            hour=chinese.group("hour"),
            minute=chinese.group("minute"),
            second=chinese.group("second"),
            offset="",
        )

    candidate = re.sub(r"\s+", " ", raw).strip()
    if candidate.endswith(("-", ":", " ")):
        candidate = candidate[:-1]
    if _DOTTED_DATE_RE.match(candidate):
        candidate = candidate.replace(".", "-").replace("/", "-")

    matched = _YEAR_RE.match(candidate)
    if matched:
        year = int(matched.group("year"))
        if not 1 <= year <= 9999:
            _fail_date(raw, "年份超出范围")
        return f"{year:04d}", DatePrecision.YEAR.value

    matched = _MONTH_RE.match(candidate)
    if matched:
        year, month = int(matched.group("year")), int(matched.group("month"))
        if not 1 <= year <= 9999 or not 1 <= month <= 12:
            _fail_date(raw, "月份或年份超出范围")
        return f"{year:04d}-{month:02d}", DatePrecision.MONTH.value

    matched = _DAY_RE.match(candidate)
    if matched:
        return _from_parts(
            raw,
            year=int(matched.group("year")),
            month=matched.group("month"),
            day=matched.group("day"),
        )

    matched = _MINUTE_RE.match(candidate)
    if matched:
        return _from_parts(
            raw,
            year=int(matched.group("year")),
            month=matched.group("month"),
            day=matched.group("day"),
            hour=matched.group("hour"),
            minute=matched.group("minute"),
        )

    matched = _SECOND_RE.match(candidate)
    if matched:
        offset = (matched.group("offset") or "").replace(":", "")
        if offset and offset != "Z" and len(offset) == 5:
            offset = f"{offset[:3]}:{offset[3:]}"
        return _from_parts(
            raw,
            year=int(matched.group("year")),
            month=matched.group("month"),
            day=matched.group("day"),
            hour=matched.group("hour"),
            minute=matched.group("minute"),
            second=matched.group("second"),
            fraction=matched.group("fraction") or "",
            offset=offset,
        )

    _fail_date(raw, "支持 YYYY / YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS] 或中文年月日写法")
