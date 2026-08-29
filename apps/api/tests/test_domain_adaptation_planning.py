from __future__ import annotations

from local_drama.domain.adaptation_planning import (
    ADAPTATION_MODES,
    SEASON_STRATEGIES,
    analysis_manifest_nodes,
    is_long_form,
    recommendation,
)
from local_drama.domain.source_text import source_paragraphs


def _record(paragraphs, index: int):
    return paragraphs[index - 1]


def test_manifest_builds_bounded_chunks_then_reduce_stages() -> None:
    text = "\n\n".join(f"第{index}段。" + "内容" * 40 for index in range(1, 9))
    paragraphs = source_paragraphs(text)
    records = [_record(paragraphs, index) for index in range(1, 9)]
    source_sha = "a" * 64

    nodes = analysis_manifest_nodes(
        records=records,
        source_paragraph_start=1,
        source_paragraph_end=8,
        source_text_sha256=source_sha,
    )

    stages = [node["stage"] for node in nodes]
    assert stages == ["CHUNK_MAP", "ARC_REDUCE", "SEASON_PLAN", "EPISODE_BOUNDARY", "VALIDATE"]
    chunk_nodes = [node for node in nodes if node["stage"] == "CHUNK_MAP"]
    assert len(chunk_nodes) == 1
    core = chunk_nodes[0]
    assert core["core_source_start"] is not None
    assert core["output"]["source_text_sha256"] == source_sha
    upstream = [node for node in nodes if node["stage"] != "CHUNK_MAP"]
    assert all(node["core_source_start"] is None for node in upstream)


def test_manifest_returns_empty_when_selection_has_no_records() -> None:
    assert analysis_manifest_nodes(records=[], source_paragraph_start=1, source_paragraph_end=5, source_text_sha256="a") == []


def test_long_form_thresholds_drive_recommendation() -> None:
    assert is_long_form(character_count=0, paragraph_count=80, chapter_count=0) is True
    assert is_long_form(character_count=8_001, paragraph_count=0, chapter_count=0) is True
    assert is_long_form(character_count=0, paragraph_count=0, chapter_count=6) is True
    assert is_long_form(character_count=1_000, paragraph_count=10, chapter_count=1) is False

    assert recommendation(character_count=20_000, paragraph_count=200, chapter_count=10) == "COMPLETE_WORK"
    assert recommendation(character_count=5_000, paragraph_count=30, chapter_count=2) == "SERIAL_INCREMENTAL"
    assert recommendation(character_count=1_000, paragraph_count=10, chapter_count=1) == "SINGLE_EPISODE"


def test_mode_and_strategy_dictionaries_stay_closed() -> None:
    assert ADAPTATION_MODES == frozenset({"COMPLETE_WORK", "SERIAL_INCREMENTAL", "PRESEGMENTED_SCRIPT", "SINGLE_EPISODE"})
    assert SEASON_STRATEGIES == frozenset({"NONE", "AI_SUGGESTED", "FIXED_COUNT", "INHERIT_EXISTING"})
