"""Canonical source-text paragraph and chapter semantics.

The import preview and the LLM execution snapshot must use the same paragraph
numbers. A paragraph is a non-empty block separated by at least one blank
line; single newlines inside a block do not create a new user-visible number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CHINESE_HEADING = re.compile(r"^第[零〇一二三四五六七八九十百千万两0-9]+[章节幕集卷部篇](?:\s+.*)?$")


@dataclass(frozen=True)
class SourceParagraph:
    number: int
    text: str
    start: int
    end: int


def looks_like_source_heading(value: str) -> bool:
    stripped = value.strip()
    return bool(len(stripped) <= 48 and (stripped.startswith("#") or _CHINESE_HEADING.fullmatch(stripped)))


def source_paragraphs(value: str) -> list[SourceParagraph]:
    """Return immutable Unicode-codepoint spans using blank-line paragraphs."""
    result: list[SourceParagraph] = []
    pattern = re.compile(r"(?:^|\n[ \t]*\n)(.*?)(?=\n[ \t]*\n|\Z)", re.DOTALL)
    for match in pattern.finditer(value):
        raw = match.group(1)
        if not raw.strip():
            continue
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        start = match.start(1) + leading
        end = match.start(1) + trailing
        result.append(SourceParagraph(number=len(result) + 1, text=value[start:end], start=start, end=end))
    return result


def source_chapters(paragraphs: list[SourceParagraph]) -> list[dict[str, int | str]]:
    """Build deterministic chapter shortcuts without changing source facts."""
    headings = [paragraph for paragraph in paragraphs if looks_like_source_heading(paragraph.text)]
    chapters: list[dict[str, int | str]] = []
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        chapters.append(
            {
                "title": heading.text.lstrip("#").strip(),
                "start_paragraph": heading.number,
                "end_paragraph": (next_heading.number - 1) if next_heading else len(paragraphs),
            }
        )
    return chapters
