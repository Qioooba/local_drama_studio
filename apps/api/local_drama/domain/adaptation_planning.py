"""Pure domain rules for long-form adaptation planning."""

from __future__ import annotations

import hashlib
import json
from typing import Final

ADAPTATION_MODES: Final[frozenset[str]] = frozenset(
    {"COMPLETE_WORK", "SERIAL_INCREMENTAL", "PRESEGMENTED_SCRIPT", "SINGLE_EPISODE"}
)
SEASON_STRATEGIES: Final[frozenset[str]] = frozenset(
    {"NONE", "AI_SUGGESTED", "FIXED_COUNT", "INHERIT_EXISTING"}
)

_CHUNK_MAX_BODY_CHARACTERS: Final[int] = 6_000
_CHUNK_CONTEXT_PARAGRAPHS: Final[int] = 2


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analysis_manifest_nodes(
    *,
    records: list[object],
    source_paragraph_start: int,
    source_paragraph_end: int,
    source_text_sha256: str,
) -> list[dict[str, object]]:
    """Plan bounded, overlapping Map/Reduce inputs without reading an LLM.

    Ranges stored on nodes are immutable Unicode offsets. Paragraph ranges live
    in the node descriptor so every later prompt can cite its exact source
    units without persisting a duplicate copy of the manuscript.
    """
    selected = [
        record
        for record in records
        if source_paragraph_start <= int(record.number) <= source_paragraph_end
    ]
    if not selected:
        return []

    chunks: list[list[object]] = []
    current: list[object] = []
    current_size = 0
    for record in selected:
        text_size = len(str(record.text))
        if current and current_size + text_size > _CHUNK_MAX_BODY_CHARACTERS:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(record)
        current_size += text_size
    if current:
        chunks.append(current)

    nodes: list[dict[str, object]] = []
    chunk_fingerprints: list[str] = []
    for ordinal, core in enumerate(chunks, start=1):
        context_start_index = max(0, selected.index(core[0]) - _CHUNK_CONTEXT_PARAGRAPHS)
        context_end_index = min(len(selected), selected.index(core[-1]) + _CHUNK_CONTEXT_PARAGRAPHS + 1)
        context = selected[context_start_index:context_end_index]
        descriptor = {
            "planning_state": "PLANNED",
            "primary_paragraph_range": [int(core[0].number), int(core[-1].number)],
            "context_paragraph_range": [int(context[0].number), int(context[-1].number)],
            "source_text_sha256": source_text_sha256,
        }
        fingerprint = _fingerprint({"contract": "adaptation-analysis/v1", **descriptor})
        chunk_fingerprints.append(fingerprint)
        nodes.append(
            {
                "node_key": f"chunk-map-{ordinal:03d}",
                "stage": "CHUNK_MAP",
                "core_source_start": int(core[0].start),
                "core_source_end": int(core[-1].end),
                "context_source_start": int(context[0].start),
                "context_source_end": int(context[-1].end),
                "input_fingerprint": fingerprint,
                "output": descriptor,
            }
        )
    upstream = _fingerprint({"contract": "adaptation-analysis/v1", "chunk_fingerprints": chunk_fingerprints})
    for stage, node_key in (
        ("ARC_REDUCE", "arc-reduce-001"),
        ("SEASON_PLAN", "season-plan-001"),
        ("EPISODE_BOUNDARY", "episode-boundary-001"),
        ("VALIDATE", "validate-001"),
    ):
        descriptor = {"planning_state": "PLANNED", "upstream_fingerprint": upstream}
        fingerprint = _fingerprint({"stage": stage, **descriptor})
        nodes.append(
            {
                "node_key": node_key,
                "stage": stage,
                "core_source_start": None,
                "core_source_end": None,
                "context_source_start": None,
                "context_source_end": None,
                "input_fingerprint": fingerprint,
                "output": descriptor,
            }
        )
    return nodes


def is_long_form(*, character_count: int, paragraph_count: int, chapter_count: int) -> bool:
    return character_count > 8_000 or paragraph_count >= 80 or chapter_count >= 6


def recommendation(*, character_count: int, paragraph_count: int, chapter_count: int) -> str:
    if is_long_form(
        character_count=character_count,
        paragraph_count=paragraph_count,
        chapter_count=chapter_count,
    ):
        return "COMPLETE_WORK"
    if character_count > 4_000 or paragraph_count >= 24 or chapter_count >= 2:
        return "SERIAL_INCREMENTAL"
    return "SINGLE_EPISODE"
