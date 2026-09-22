"""Compatibility re-exports for the domain source-text semantics.

The paragraph and chapter rules are pure domain logic and now live in
:mod:`local_drama.domain.source_text`.  Legacy application services keep
importing from here until their slices are migrated to the domain import.
"""

from __future__ import annotations

from local_drama.domain.source_text import (
    LEGACY_PARSER_VERSION,
    LEGACY_SOURCE_STRUCTURE_VERSION,
    SOURCE_PARSER_VERSION,
    SOURCE_STRUCTURE_VERSION,
    ParagraphLayoutHint,
    SourceParagraph,
    heading_title,
    looks_like_source_heading,
    non_empty_line_count,
    recommends_newline_paragraphs,
    resolve_paragraph_layout,
    source_chapters,
    source_paragraph_layout_ratio,
    source_paragraphs,
)

__all__ = [
    "LEGACY_PARSER_VERSION",
    "LEGACY_SOURCE_STRUCTURE_VERSION",
    "SOURCE_PARSER_VERSION",
    "SOURCE_STRUCTURE_VERSION",
    "ParagraphLayoutHint",
    "SourceParagraph",
    "heading_title",
    "looks_like_source_heading",
    "non_empty_line_count",
    "recommends_newline_paragraphs",
    "resolve_paragraph_layout",
    "source_chapters",
    "source_paragraph_layout_ratio",
    "source_paragraphs",
]
