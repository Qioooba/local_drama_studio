"""Canonical source-text paragraph and chapter semantics.

The import preview and the LLM execution snapshot must use the same paragraph
numbers.  Two versioned strategies exist:

``BLANK_LINE`` (default)
    A paragraph is a non-empty block separated by at least one blank line;
    single newlines inside a block do not create a new user-visible number.

``NEWLINE`` (explicit user preview option)
    Every non-empty line is its own paragraph.  This is the only usable
    structure for the many plain-text novels that separate chapters with a
    single ``\\n`` and no blank lines at all.

Both strategies index the *normalised* text (platform newline translation is
applied before the text is frozen as the import authority), so every offset is
a Unicode-codepoint index into the stored ``*.extracted.txt`` file and
``text[start:end] == paragraph.text`` always holds.

Changing the strategy or the heading grammar creates a new structure version;
it never rewrites existing offsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SOURCE_STRUCTURE_VERSION = 3
LEGACY_SOURCE_STRUCTURE_VERSION = 2
SOURCE_PARSER_VERSION = 2
LEGACY_PARSER_VERSION = 1

ParagraphLayoutHint = Literal["AUTO", "BLANK_LINE", "NEWLINE"]

_CJK_DIGITS = "零〇一二三四五六七八九十百千万两0-9０-９"
# Explicit chapter labels only.  ``第`` alone never matches, so narrative prose
# such as "第一章里提到的男人" is not mistaken for a heading.
_CJK_NUMBERED_LABEL = rf"第[{_CJK_DIGITS}]{{1,12}}\s*[章节幕集卷部篇回]"
_CJK_SPECIAL_LABEL = r"序章|序言|楔子|尾声|引子|序幕|终章|后记|番外"
_ENGLISH_LABEL = r"(?:chapter|part|volume|prologue|epilogue)\s*[0-9]{1,4}[a-z]?"
_ENGLISH_BARE_LABEL = r"prologue|epilogue"
_HEADING_LABEL = re.compile(
    rf"(?:{_CJK_NUMBERED_LABEL}|{_CJK_SPECIAL_LABEL}|{_ENGLISH_LABEL}|{_ENGLISH_BARE_LABEL})",
    re.IGNORECASE,
)
_HEADING_TRAILING_STRIP = " \t\r:：、.,。．-—"
# A chapter label may run straight into its title ("第一章雨夜"), so the first
# character after the label decides whether the line is still prose.
_PROSE_CONTINUATION = {
    "的", "里", "中", "内", "后", "前", "末", "是", "在", "有", "把", "被",
    "让", "使", "讲", "说", "写", "提", "出现", "描述",
}
_PROSE_CONTINUATION_PREFIXES = ("提到", "描述", "讲述", "说的", "写的")

_LABEL_LENGTH_LIMIT = 60


@dataclass(frozen=True)
class SourceParagraph:
    number: int
    text: str
    start: int
    end: int


def _heading_label_match(stripped: str) -> re.Match[str] | None:
    """Return the leading explicit heading label, or ``None`` for prose."""
    match = _HEADING_LABEL.match(stripped)
    if match is None:
        return None
    rest = stripped[match.end() :]
    if not rest:
        return match
    # "第一章里提到的男人…" is prose that merely starts with a chapter word.
    if rest[0] in _PROSE_CONTINUATION or rest.startswith(_PROSE_CONTINUATION_PREFIXES):
        return None
    if rest[0] in _HEADING_TRAILING_STRIP:
        # "第1章：雨夜" and "第一章 雨夜" are separated titles.
        return match
    # "第一章雨夜": the title runs straight on. A narrative sentence ending in
    # the middle of the line is rejected; a true title is a short label.
    if any(mark in rest for mark in "。！？!?"):
        return None
    if len(match.group(0)) + len(rest) > 24:
        return None
    return match


def _is_heading_title(stripped: str) -> bool:
    return len(stripped) <= _LABEL_LENGTH_LIMIT and _heading_label_match(stripped) is not None


def looks_like_source_heading(value: str) -> bool:
    """Return whether one physical line is an explicit chapter heading."""
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        # Markdown headings stay version-2 compatible: any ATX heading counts.
        return len(stripped) <= 48
    return _is_heading_title(stripped)


def heading_title(value: str) -> str:
    """Return the display title of a recognised heading line."""
    return value.strip().lstrip("#").strip().rstrip(_HEADING_TRAILING_STRIP).strip()


def source_paragraph_layout_ratio(value: str) -> float:
    """Return the blank-line share of all physical lines (0.0 when empty)."""
    lines = value.split("\n")
    if not lines:
        return 0.0
    blank = sum(1 for line in lines if not line.strip())
    return blank / len(lines)


def non_empty_line_count(value: str) -> int:
    return sum(1 for line in value.split("\n") if line.strip())


def recommends_newline_paragraphs(value: str) -> bool:
    """Whether a dense document should be previewed with the newline strategy.

    Only documents that carry *no* blank-line separation at all (and more than
    one physical line) are flagged: for those the blank-line strategy collapses
    every chapter into a single canonical paragraph.  Anything else keeps the
    conservative blank-line design.
    """
    text = value.strip("\n")
    if not text.strip():
        return False
    return source_paragraph_layout_ratio(text) == 0.0 and non_empty_line_count(text) >= 2


def resolve_paragraph_layout(value: str, paragraph_layout: ParagraphLayoutHint | None = None) -> ParagraphLayoutHint:
    """Resolve ``AUTO`` to the strategy that matches the document's layout."""
    requested = paragraph_layout or "AUTO"
    if requested == "AUTO":
        return "NEWLINE" if recommends_newline_paragraphs(value) else "BLANK_LINE"
    return requested


def _blank_line_pattern() -> re.Pattern[str]:
    return re.compile(r"(?:^|\n[ \t]*\n)(.*?)(?=\n[ \t]*\n|\Z)", re.DOTALL)


def _newline_pattern() -> re.Pattern[str]:
    return re.compile(r"^([^\n]+)", re.MULTILINE)


def _spans(value: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in pattern.finditer(value):
        raw = match.group(1)
        if not raw.strip():
            continue
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        spans.append((match.start(1) + leading, match.start(1) + trailing))
    return spans


def source_paragraphs(
    value: str,
    paragraph_layout: ParagraphLayoutHint | None = None,
) -> list[SourceParagraph]:
    """Return immutable Unicode-codepoint spans for the requested strategy."""
    layout = resolve_paragraph_layout(value, paragraph_layout)
    pattern = _newline_pattern() if layout == "NEWLINE" else _blank_line_pattern()
    return [
        SourceParagraph(number=number, text=value[start:end], start=start, end=end)
        for number, (start, end) in enumerate(_spans(value, pattern), start=1)
    ]


def source_chapters(
    paragraphs: list[SourceParagraph],
    paragraph_layout: ParagraphLayoutHint | None = None,
) -> list[dict[str, int | str]]:
    """Build deterministic chapter shortcuts without changing paragraph numbers."""
    del paragraph_layout  # Layout already shaped the paragraph spans.
    headings: list[tuple[str, int]] = []
    for paragraph in paragraphs:
        lines = paragraph.text.splitlines()
        for line_index, line in enumerate(lines):
            if not looks_like_source_heading(line):
                continue
            # When a heading was appended to the previous prose line because the
            # source omitted one blank line, its chapter begins at the following
            # paragraph. The prose paragraph remains in the previous chapter, so
            # frozen paragraph numbers and ranges never overlap or shift.
            start_paragraph = paragraph.number if line_index == 0 else min(paragraph.number + 1, len(paragraphs))
            title = heading_title(line)
            if headings and headings[-1][1] == start_paragraph:
                continue
            headings.append((title, start_paragraph))
    chapters: list[dict[str, int | str]] = []
    for index, (title, start_paragraph) in enumerate(headings):
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        chapters.append(
            {
                "title": title,
                "start_paragraph": start_paragraph,
                "end_paragraph": (next_heading[1] - 1) if next_heading else len(paragraphs),
            }
        )
    return chapters
