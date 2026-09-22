"""Regression matrix for the versioned source-text structure rules (NP09)."""

from __future__ import annotations

from local_drama.domain.source_text import (
    LEGACY_SOURCE_STRUCTURE_VERSION,
    SOURCE_STRUCTURE_VERSION,
    heading_title,
    looks_like_source_heading,
    recommends_newline_paragraphs,
    resolve_paragraph_layout,
    source_chapters,
    source_paragraphs,
)

# Explicit heading forms that must be recognised (report NP09 matrix).
RECOGNISED_HEADINGS = [
    "第一章雨夜",
    "第1章：雨夜",
    "第一章 雨夜",
    "第001章 雨夜",
    "序章 雨夜",
    "楔子",
    "引子",
    "尾声",
    "Chapter 1 Rain",
    "CHAPTER 12",
    "Prologue",
    "Epilogue",
    "# 第一章 雨夜",
]

# Narrative prose that merely contains 第/Chapter-ish words must stay prose.
REJECTED_PROSE = [
    "这不是第章",
    "正文第一段不是标题",
    "他想起第一章的往事。",
    "第一章的开头是雨夜。",
    "第一章里提到的那个男人后来死在雨夜。",
    "第一章雨夜的风很冷，他把窗关上。",
    "In this chapter 1 we learn nothing.",
]


def test_versioned_heading_matrix_recognises_explicit_labels() -> None:
    for heading in RECOGNISED_HEADINGS:
        assert looks_like_source_heading(heading) is True, heading


def test_versioned_heading_matrix_rejects_narrative_prose() -> None:
    for prose in REJECTED_PROSE:
        assert looks_like_source_heading(prose) is False, prose


def test_heading_title_strips_label_punctuation() -> None:
    assert heading_title("第一章：雨夜") == "第一章：雨夜"
    assert heading_title("# 第二章 清晨") == "第二章 清晨"
    assert heading_title("Chapter 1 Rain.") == "Chapter 1 Rain"


def test_structure_version_is_bumped_for_the_new_grammar() -> None:
    assert SOURCE_STRUCTURE_VERSION > LEGACY_SOURCE_STRUCTURE_VERSION


def test_blank_line_layout_keeps_legacy_paragraph_numbers() -> None:
    text = "第一段。\n\n第二段。\n第二段续行。\n\n\n第三段。"
    paragraphs = source_paragraphs(text)

    assert [item.text for item in paragraphs] == ["第一段。", "第二段。\n第二段续行。", "第三段。"]
    assert resolve_paragraph_layout(text) == "BLANK_LINE"


def test_newline_layout_splits_dense_single_newline_novel_into_chapters() -> None:
    text = "第一章雨夜\n林默推开门。\n第二章清晨\n苏晚醒来。"
    assert recommends_newline_paragraphs(text) is True

    paragraphs = source_paragraphs(text, "NEWLINE")
    assert [item.text for item in paragraphs] == ["第一章雨夜", "林默推开门。", "第二章清晨", "苏晚醒来。"]
    chapters = source_chapters(paragraphs, "NEWLINE")
    assert [(chapter["title"], chapter["start_paragraph"], chapter["end_paragraph"]) for chapter in chapters] == [
        ("第一章雨夜", 1, 2),
        ("第二章清晨", 3, 4),
    ]


def test_explicit_newline_layout_is_never_recommended_for_blank_line_text() -> None:
    text = "第一段。\n\n第二段。"
    assert recommends_newline_paragraphs(text) is False
    assert resolve_paragraph_layout(text, "NEWLINE") == "NEWLINE"
    assert [item.text for item in source_paragraphs(text, "NEWLINE")] == ["第一段。", "第二段。"]


def test_offsets_always_round_trip_for_both_layouts() -> None:
    samples = [
        "第一段。\n\n第二段。\n\n第三段。",
        "第一章雨夜\n正文。\n第二章清晨\n正文。",
        "😀 开篇\n\n第二段 emoji 🎬",
        "第一段。\r\n\r\n第二段。",
        "  \n\n缩进正文\n\n尾段  ",
    ]
    for layout in ("BLANK_LINE", "NEWLINE"):
        for raw in samples:
            text = raw.replace("\r\n", "\n").replace("\r", "\n")
            paragraphs = source_paragraphs(text, layout)  # type: ignore[arg-type]
            assert paragraphs, (layout, raw)
            assert [item.number for item in paragraphs] == list(range(1, len(paragraphs) + 1)), (layout, raw)
            for paragraph in paragraphs:
                assert text[paragraph.start : paragraph.end] == paragraph.text, (layout, raw, paragraph)


def test_crlf_normalisation_keeps_the_same_offsets_as_lf() -> None:
    lf = "第一章 雨夜\n\n正文一段。\n\n正文二段。"
    crlf = lf.replace("\n", "\r\n")
    normalised = crlf.replace("\r\n", "\n").replace("\r", "\n")

    assert normalised == lf
    assert [(item.start, item.end) for item in source_paragraphs(normalised)] == [
        (item.start, item.end) for item in source_paragraphs(lf)
    ]


def test_chapters_never_overlap_when_a_heading_shares_a_prose_paragraph() -> None:
    text = "开篇正文\n第一章 起点\n\n第一章 起点\n\n第二章 终点"
    paragraphs = source_paragraphs(text)
    chapters = source_chapters(paragraphs)

    starts = [int(chapter["start_paragraph"]) for chapter in chapters]
    ends = [int(chapter["end_paragraph"]) for chapter in chapters]
    assert starts == sorted(starts)
    assert all(start <= end for start, end in zip(starts, ends, strict=True))
    assert chapters[0]["end_paragraph"] < chapters[1]["start_paragraph"]
