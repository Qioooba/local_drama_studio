from __future__ import annotations

from local_drama.domain.source_text import (
    looks_like_source_heading,
    source_chapters,
    source_paragraphs,
)


def test_paragraph_numbers_use_blank_line_blocks_only() -> None:
    text = "第一段。\n\n第二段。\n第二段续行。\n\n\n第三段。"
    paragraphs = source_paragraphs(text)

    assert [item.number for item in paragraphs] == [1, 2, 3]
    assert [item.text for item in paragraphs] == ["第一段。", "第二段。\n第二段续行。", "第三段。"]
    assert text[paragraphs[0].start : paragraphs[0].end] == "第一段。"


def test_heading_detection_accepts_markdown_and_chinese_chapter_labels() -> None:
    assert looks_like_source_heading("# 第一章") is True
    assert looks_like_source_heading("第十二章 雨夜") is True
    assert looks_like_source_heading("第三章") is True
    assert looks_like_source_heading("这不是第章") is False
    assert looks_like_source_heading("正文第一段不是标题") is False


def test_chapters_start_after_prose_line_and_never_overlap() -> None:
    text = "开篇正文\n第一章 起点\n\n第一章 起点\n\n第二章 终点"
    paragraphs = source_paragraphs(text)
    chapters = source_chapters(paragraphs)

    assert [chapter["title"] for chapter in chapters] == ["第一章 起点", "第二章 终点"]
    starts = [int(chapter["start_paragraph"]) for chapter in chapters]
    ends = [int(chapter["end_paragraph"]) for chapter in chapters]
    assert starts == sorted(starts)
    assert all(start <= end for start, end in zip(starts, ends))
    assert chapters[0]["end_paragraph"] < chapters[1]["start_paragraph"]


def test_chapter_without_trailing_heading_covers_final_paragraph() -> None:
    text = "# 序\n\n序言内容\n\n# 正文\n\n收尾内容"
    paragraphs = source_paragraphs(text)
    chapters = source_chapters(paragraphs)

    assert chapters[-1]["end_paragraph"] == len(paragraphs)
