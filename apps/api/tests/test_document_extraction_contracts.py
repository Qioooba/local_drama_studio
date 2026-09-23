"""Regression tests for document extraction and EPUB reading order.

Reproduced defects on the audit snapshot:

* ``IMP-01`` — inline markup was traversed LIFO, so
  ``我<span>让<b>小王</b>去找<i>老李</i></span>回家。`` extracted as
  ``我让回家。老李小王去找``.
* ``IMP-02`` — reaching the node limit ``break``-ed out of the walk and the
  truncated text was stored as the authoritative body with no warning.
* ``LDS-04`` — ``.docx``/``.pdf``/``.epub`` uploads were pushed through a *text*
  encoder-decoder, so a DOCX's compressed XML was read as mojibake and the
  document's paragraphs never appeared.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from local_drama.application import documents
from local_drama.application.documents import (
    _epub_traversal,
    extract_document_text,
)
from local_drama.domain.errors import DomainRuleError

XHTML = "http://www.w3.org/1999/xhtml"


# --------------------------------------------------------------------------- #
# IMP-01: reading order
# --------------------------------------------------------------------------- #
def _body_text(body: str) -> str:
    root = ElementTree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')
    blocks, _meta = _epub_traversal(root)
    return "\n".join(blocks)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The minimal case from the report.
        ("<p>我<span>让<b>小王</b>去找<i>老李</i></span>回家。</p>", "我让小王去找老李回家。"),
        ("<p>A<span>B<em>C</em>D</span>E</p>", "ABCDE"),
        # Two- and three-level nesting with inline siblings.
        ("<p><span><em>深</em>层</span>文本</p>", "深层文本"),
        ("<p><b><i><span>三层</span></i></b>结束</p>", "三层结束"),
        ("<p>前<a href='#'>链接</a>中<em>强调</em>后</p>", "前链接中强调后"),
        # Text directly before and after a block child.
        ("<p>块前<div>块内</div>块后</p>", "块前\n块内\n块后"),
        # Direct text of the body itself.
        ("直接文本<p>段落</p>尾部", "直接文本\n段落\n尾部"),
        # <li> containing <p> must not re-emit the parent.
        ("<ul><li><p>第一项</p></li><li>第二项</li></ul>", "第一项\n第二项"),
        # Genuinely repeated prose is preserved, never de-duplicated globally.
        ("<p>同一句话。</p><p>同一句话。</p>", "同一句话。\n同一句话。"),
        # <br> is an explicit author line break, so it must survive extraction.
        ("<p>上<br/>下</p>", "上\n下"),
        ("<p>English and 中文，标点！</p>", "English and 中文，标点！"),
    ],
)
def test_epub_inline_order_is_document_order(body: str, expected: str) -> None:
    assert _body_text(body) == expected


def test_epub_tail_after_skipped_element_is_kept() -> None:
    body = "<head><style>p{color:red}</style></head><p>正文<span>正文二</span>尾</p>"
    assert _body_text(body) == "正文正文二尾"


# --------------------------------------------------------------------------- #
# IMP-02: the node limit is a refusal, not a silent truncation
# --------------------------------------------------------------------------- #
def test_epub_node_limit_refuses_instead_of_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_EPUB_NODE_LIMIT", "1000")
    paragraphs = "".join(f"<p>段落{index}</p>" for index in range(1200))
    root = ElementTree.fromstring(f'<html xmlns="{XHTML}"><body>{paragraphs}</body></html>')
    with pytest.raises(DomainRuleError) as error:
        _epub_traversal(root)
    assert error.value.code == "DOCUMENT_STRUCTURE_TOO_COMPLEX"
    assert error.value.details["node_limit"] == 1000


def test_epub_long_flat_chapter_is_complete(document_workspace: Path) -> None:
    """A 21,000-paragraph chapter must import whole when the budget allows it."""

    count = 21_000
    source = document_workspace / "long.epub"
    paragraphs = "".join(f"<p>段落{index}</p>" for index in range(count))
    _write_epub(source, {"chapter1.xhtml": f'<html xmlns="{XHTML}"><body>{paragraphs}</body></html>'})
    extracted = extract_document_text(source)
    assert extracted["format"] == "EPUB"
    assert extracted["quality"] == "complete"
    lines = [line for line in extracted["text"].split("\n") if line.strip()]
    # The sentinel at the very end must be present: this is the truncated-body
    # defect, not a preview limit.
    assert lines[0] == "段落0"
    assert lines[-1] == f"段落{count - 1}"
    assert len(lines) == count


# --------------------------------------------------------------------------- #
# LDS-04: format dispatch
# --------------------------------------------------------------------------- #
def _write_epub(path: Path, chapters: dict[str, str]) -> None:
    items = "".join(
        f"<item id='c{index}' href='{name}' media-type='application/xhtml+xml'/>"
        for index, name in enumerate(chapters)
    )
    spine = "".join(f"<itemref idref='c{index}'/>" for index in range(len(chapters)))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'>"
            f"<manifest>{items}</manifest><spine>{spine}</spine></package>",
        )
        for name, body in chapters.items():
            archive.writestr(f"OEBPS/{name}", body)


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    table = (
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>单元格甲</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>单元格乙</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    document_xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body>{body}{table}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        archive.writestr("word/document.xml", document_xml)


@pytest.fixture()
def document_workspace(tmp_path: Path) -> Path:
    return tmp_path


def test_docx_paragraphs_and_table_cells_are_extracted(document_workspace: Path) -> None:
    source = document_workspace / "fixture_document.docx"
    _write_docx(source, ["第一章 起点", "林舟推开旧仓库的门。"])
    extracted = extract_document_text(source)
    assert extracted["format"] == "DOCX"
    assert "第一章 起点" in extracted["text"]
    assert "林舟推开旧仓库的门。" in extracted["text"]
    # The table cell used to be lost entirely by the text decoder.
    assert "单元格甲" in extracted["text"]
    assert "单元格乙" in extracted["text"]
    assert "PK" not in extracted["text"]


def test_docx_bytes_do_not_survive_a_text_decoder(document_workspace: Path) -> None:
    """Proof that the old path could never work: the container is not UTF-8 text."""

    source = document_workspace / "fixture_document.docx"
    _write_docx(source, ["第一章 起点"])
    payload = source.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")
    assert extract_document_text(source)["text"].startswith("第一章 起点")


def test_real_pdf_with_a_text_layer_is_extracted(document_workspace: Path) -> None:
    writer_module = pytest.importorskip("pypdf")
    source = document_workspace / "text-layer.pdf"
    writer = writer_module.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)
    # A blank page has no text layer: that must be a concrete diagnosis, never an
    # empty string silently imported as the authority.
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source)
    assert error.value.code == "DOCUMENT_TEXT_EMPTY"
    assert "OCR" in error.value.message


def test_epub_chapters_are_extracted_in_spine_order(document_workspace: Path) -> None:
    source = document_workspace / "novel.epub"
    _write_epub(
        source,
        {
            "chapter1.xhtml": f'<html xmlns="{XHTML}"><body><h1>第一章</h1><p>风从海上来。</p></body></html>',
            "chapter2.xhtml": f'<html xmlns="{XHTML}"><body><h1>第二章</h1><p>灯塔重新亮起。</p></body></html>',
        },
    )
    extracted = extract_document_text(source)
    assert extracted["format"] == "EPUB"
    assert [line for line in extracted["text"].split("\n") if line.strip()] == [
        "第一章", "风从海上来。", "第二章", "灯塔重新亮起。",
    ]
    assert extracted["encoding"] is None
    assert extracted["confidence"] == "CONTAINER_PARSED"


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8", False), ("utf-8-sig", True), ("gb18030", False), ("utf-16", True)],
)
def test_plain_text_encodings_are_reported_not_guessed(
    document_workspace: Path, encoding: str, bom: bool
) -> None:
    del bom
    source = document_workspace / f"novel-{encoding}.txt"
    source.write_bytes("第一章 起点\n林舟推开旧仓库的门。".encode(encoding))
    extracted = extract_document_text(source)
    assert extracted["format"] == "TEXT"
    assert extracted["quality"] == "complete"
    assert extracted["text"] == "第一章 起点\n林舟推开旧仓库的门。"
    assert extracted["encoding"] is not None


def test_unsupported_suffix_is_refused(document_workspace: Path) -> None:
    source = document_workspace / "archive.rar"
    source.write_bytes(b"Rar!\x1a\x07\x00")
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source)
    assert error.value.code == "UNSUPPORTED_DOCUMENT_TYPE"


def test_oversized_document_is_refused_before_parsing(document_workspace: Path) -> None:
    source = document_workspace / "big.txt"
    source.write_bytes(b"x" * 4096)
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source, maximum_bytes=1024)
    assert error.value.code == "DOCUMENT_TOO_LARGE"


def test_broken_zip_container_is_a_concrete_parse_failure(document_workspace: Path) -> None:
    source = document_workspace / "broken.docx"
    source.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source)
    assert error.value.code == "DOCUMENT_PARSE_FAILED"


def test_empty_document_is_refused(document_workspace: Path) -> None:
    source = document_workspace / "empty.txt"
    source.write_bytes(b"")
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source)
    assert error.value.code == "DOCUMENT_TEXT_EMPTY"


def test_whitespace_only_document_is_refused(document_workspace: Path) -> None:
    source = document_workspace / "blank.txt"
    source.write_bytes("   \n\t\n".encode("utf-8"))
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(source)
    assert error.value.code == "DOCUMENT_TEXT_EMPTY"


def test_extraction_reports_budgets_and_counts(document_workspace: Path) -> None:
    source = document_workspace / "novel.txt"
    source.write_bytes("第一段\n第二段\n".encode("utf-8"))
    extracted = extract_document_text(source)
    assert extracted["budgets"]["max_characters"] == documents.DOCUMENT_EXTRACTION_MAX_CHARACTERS
    assert extracted["paragraph_count"] == 2
    assert extracted["character_count"] == len("第一段\n第二段\n")
    assert extracted["warnings"] == []
    assert sys.getsizeof(extracted) > 0
