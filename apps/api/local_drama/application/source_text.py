"""Compatibility re-exports for the domain source-text semantics.

The paragraph and chapter rules are pure domain logic and now live in
:mod:`local_drama.domain.source_text`.  Legacy application services keep
importing from here until their slices are migrated to the domain import.
"""

from __future__ import annotations

from local_drama.domain.source_text import (
    SourceParagraph,
    looks_like_source_heading,
    source_chapters,
    source_paragraphs,
)

__all__ = ["SourceParagraph", "looks_like_source_heading", "source_chapters", "source_paragraphs"]
