"""Document parsing matrix regressions for NP04, NP05, NP06, NP07 and NP13-era bugs.

Fixtures are minimal but genuinely valid DOCX/EPUB containers synthesised with
``zipfile`` so the real reader code paths are exercised (no mocks of the parser).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application import documents
from local_drama.application.documents import DocumentImportService, _epub_text_blocks, _normalise_epub_reference
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _project(workspace, database, code: str = "parsing_matrix") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Parsing matrix",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _docx(path: Path, body_xml: str) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body>{body_xml}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _run(text: str, *, bold: bool = False) -> str:
    properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{properties}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _epub(path: Path, *, package_path: str, manifest_hrefs: dict[str, str], documents: dict[str, str]) -> Path:
    manifest = "".join(f'<item id="{key}" href="{href}" media-type="application/xhtml+xml"/>' for key, href in manifest_hrefs.items())
    spine = "".join(f'<itemref idref="{key}"/>' for key in manifest_hrefs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            f"<rootfiles><rootfile full-path='{package_path}'/></rootfiles></container>",
        )
        archive.writestr(
            package_path,
            "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'>"
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        for name, body in documents.items():
            archive.writestr(name, f"<html xmlns='http://www.w3.org/1999/xhtml'><body>{body}</body></html>")
    return path


# --------------------------------------------------------------------------
# NP04: format-parsed text must be validated before anything is persisted.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("spaces.txt", " \n\n\t \n"),
        ("zero-byte.txt", ""),
        ("newlines-only.txt", "\n\n\n"),
        ("unicode-spaces.txt", "\u3000\u3000\n\u00a0\u00a0"),
        ("bom-only.txt", "\ufeff"),
    ],
)
def test_whitespace_only_text_upload_returns_422_without_any_records(
    workspace, database, filename: str, payload: str,
) -> None:
    project = _project(workspace, database, code=f"np04_{filename.replace('.', '_').replace('-', '_')}")

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project['id']}/imports:upload",
            content=payload.encode("utf-8"),
            headers={"Content-Type": "text/plain", "X-File-Name": filename},
        )

    assert response.status_code == 422, response.text
    # A zero-byte upload is rejected by the upload boundary; whitespace-only
    # content is rejected by the format-parsed text validation.
    assert response.json()["error"]["code"] in {"DOCUMENT_TEXT_EMPTY", "DOCUMENT_UPLOAD_EMPTY"}
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 0


def test_empty_docx_and_tag_only_epub_are_rejected_as_empty_text(workspace, database) -> None:
    project = _project(workspace, database, code="np04_empty_containers")
    empty_docx = _docx(workspace.work_root / "empty.docx", "<w:p/><w:p><w:r><w:t>   </w:t></w:r></w:p>")
    tag_only_epub = _epub(
        workspace.work_root / "tag-only.epub",
        package_path="OPS/package.opf",
        manifest_hrefs={"c1": "chapter1.xhtml"},
        documents={"OPS/chapter1.xhtml": "<div><span></span><em>  </em></div>"},
    )
    service = DocumentImportService(database, workspace)

    for source in (empty_docx, tag_only_epub):
        with pytest.raises(DomainRuleError) as caught:
            service.import_document(str(project["id"]), source)
        assert caught.value.code == "DOCUMENT_TEXT_EMPTY", source.name

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 0


def test_repeat_whitespace_upload_never_returns_500_or_a_ready_session(workspace, database) -> None:
    project = _project(workspace, database, code="np04_repeat")
    payload = " \n\n\t \n".encode()

    with TestClient(create_app(workspace)) as client:
        for _ in range(2):
            response = client.post(
                f"/api/v1/projects/{project['id']}/imports:upload",
                content=payload,
                headers={"Content-Type": "text/plain", "X-File-Name": "spaces.txt"},
            )
            assert response.status_code == 422, response.text

    assert DocumentImportService(database, workspace).latest_for_project(str(project["id"])) is None
    with database.connect() as connection:
        rows = connection.execute("SELECT status FROM import_sessions").fetchall()
    assert all(row["status"] != "PREVIEW_READY" for row in rows)


def test_valid_single_paragraph_still_imports_after_validation(workspace, database) -> None:
    project = _project(workspace, database, code="np04_valid")
    source = workspace.work_root / "valid.txt"
    source.write_text("林默推开门。", encoding="utf-8")

    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert result["status"] == "PREVIEW_READY"
    assert result["preview"]["paragraph_count"] == 1
    assert result["preview"]["source_structure_version"] == 3


def test_broken_legacy_preview_session_is_invalidated_and_not_restored(workspace, database) -> None:
    project = _project(workspace, database, code="np04_legacy_broken")
    source = workspace.work_root / "legacy.txt"
    source.write_text("第一章 雨夜\n\n正文。", encoding="utf-8")
    service = DocumentImportService(database, workspace)
    imported = service.import_document(str(project["id"]), source)
    broken_preview = dict(imported["preview"])
    broken_preview["paragraph_count"] = 0
    broken_preview["paragraphs"] = []
    with database.transaction() as connection:
        connection.execute(
            "UPDATE import_sessions SET preview_json=? WHERE id=?",
            (documents._json(broken_preview), imported["import_session_id"]),
        )

    assert service.latest_for_project(str(project["id"])) is None
    with database.connect() as connection:
        status = connection.execute(
            "SELECT status, error_summary FROM import_sessions WHERE id=?", (imported["import_session_id"],),
        ).fetchone()
    assert status["status"] == "INVALID"
    assert status["error_summary"]

    # The same content re-imports into a fresh, usable preview session.
    reimported = service.import_document(str(project["id"]), source)
    assert reimported["status"] == "PREVIEW_READY"
    assert reimported["preview"]["paragraph_count"] == 2
    assert reimported["import_session_id"] != imported["import_session_id"]


# --------------------------------------------------------------------------
# NP05: DOCX soft line breaks, tabs, tables and reading order.
# --------------------------------------------------------------------------


def test_docx_soft_break_and_tab_preserve_speaker_boundaries(workspace, database) -> None:
    source = _docx(
        workspace.work_root / "dialogue.docx",
        "<w:p>"
        + _run("林默：打开门。")
        + '<w:r><w:br/></w:r>'
        + _run("苏晚：等一下。")
        + '<w:r><w:tab/></w:r>'
        + _run("门外有危险。")
        + "</w:p>",
    )

    text = documents._read_docx(source)

    assert text == "林默：打开门。\n苏晚：等一下。\t门外有危险。"


def test_docx_reads_runs_in_order_and_keeps_page_breaks(workspace, database) -> None:
    source = _docx(
        workspace.work_root / "runs.docx",
        "<w:p>"
        + _run("前段")
        + _run("加粗", bold=True)
        + '<w:r><w:br w:type="page"/></w:r>'
        + _run("后段")
        + "</w:p>"
        + "<w:p>"
        + _run("第二段")
        + "</w:p>",
    )

    text = documents._read_docx(source)

    assert text == "前段加粗\n后段\n\n第二段"
    paragraphs = documents.source_paragraphs(text)
    assert [paragraph.text for paragraph in paragraphs] == ["前段加粗\n后段", "第二段"]


def test_docx_table_rows_and_cells_stay_separated(workspace, database) -> None:
    source = _docx(
        workspace.work_root / "table.docx",
        "<w:p>" + _run("场景表") + "</w:p>"
        "<w:tbl>"
        "<w:tr><w:tc><w:p>" + _run("角色") + "</w:p></w:tc><w:tc><w:p>" + _run("台词") + "</w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p>" + _run("林默") + "</w:p></w:tc><w:tc><w:p>" + _run("开门") + "</w:p></w:tc></w:tr>"
        "</w:tbl>",
    )

    text = documents._read_docx(source)

    assert "场景表" in text
    assert "林默" in text and "开门" in text
    assert text.index("角色") < text.index("台词") < text.index("林默") < text.index("开门")
    # Cells must be separated: the two cell texts never merge into one token.
    assert "角色台词" not in text
    assert "林默开门" not in text


def test_docx_nested_paragraph_container_is_not_extracted_twice(workspace, database) -> None:
    source = _docx(
        workspace.work_root / "textbox.docx",
        "<w:p>"
        + _run("外层正文。")
        + "<w:r><w:pict><w:txbxContent><w:p>"
        + _run("文本框内容。")
        + "</w:p></w:txbxContent></w:pict></w:r>"
        + "</w:p>",
    )

    text = documents._read_docx(source)

    assert text.count("文本框内容。") == 1
    assert text.count("外层正文。") == 1


# --------------------------------------------------------------------------
# NP06: EPUB nested blocks emit each text node exactly once.
# --------------------------------------------------------------------------


def test_epub_nested_blocks_emit_each_text_node_once(workspace, database) -> None:
    project = _project(workspace, database, code="np06_nested")
    source = _epub(
        workspace.work_root / "nested.epub",
        package_path="OEBPS/content.opf",
        manifest_hrefs={"c1": "chapter1.xhtml"},
        documents={
            "OEBPS/chapter1.xhtml": (
                "<blockquote><p>唯一线索：青灯在桥下。</p></blockquote>"
                "<ul><li><p>苏晚说：我知道路。</p></li></ul>"
                "<div><h1>第三章</h1><p>第三人开口。</p></div>"
                "<p>混合 <span>行内</span><em>强调</em> 文本。</p>"
            )
        },
    )

    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    paragraphs = result["preview"]["paragraphs"]

    assert paragraphs.count("唯一线索：青灯在桥下。") == 1
    assert paragraphs.count("苏晚说：我知道路。") == 1
    assert paragraphs.count("第三章") == 1
    assert paragraphs.count("第三人开口。") == 1
    inline = next(item for item in paragraphs if "行内" in item)
    assert "混合" in inline and "强调" in inline
    assert inline.count("行内") == 1 and inline.count("强调") == 1


def test_epub_genuinely_repeated_dialogue_is_preserved(workspace, database) -> None:
    project = _project(workspace, database, code="np06_repeat")
    source = _epub(
        workspace.work_root / "repeat.epub",
        package_path="OEBPS/content.opf",
        manifest_hrefs={"c1": "chapter1.xhtml"},
        documents={"OEBPS/chapter1.xhtml": "<p>我知道路。</p><p>我知道路。</p>"},
    )

    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert result["preview"]["paragraphs"] == ["我知道路。", "我知道路。"]


def test_epub_mixed_leading_and_trailing_text_around_nested_blocks(workspace) -> None:
    root = documents.ElementTree.fromstring(
        "<div xmlns='http://www.w3.org/1999/xhtml'>开头文字<blockquote><p>引用内容。</p></blockquote>结尾文字</div>"
    )

    blocks = _epub_text_blocks(root)

    assert "开头文字" in blocks
    assert "引用内容。" in blocks
    assert "结尾文字" in blocks
    assert blocks.count("引用内容。") == 1


# --------------------------------------------------------------------------
# NP07: in-package relative URI resolution.
# --------------------------------------------------------------------------


def test_normalised_relative_reference_accepts_legal_forms() -> None:
    assert _normalise_epub_reference("OPS", "../Text/chapter.xhtml") == "Text/chapter.xhtml"
    assert _normalise_epub_reference("OPS", "./chapter.xhtml") == "OPS/chapter.xhtml"
    assert _normalise_epub_reference("OPS", "chapter.xhtml#anchor") == "OPS/chapter.xhtml"
    assert _normalise_epub_reference("OPS", "chapter.xhtml?x=1#a") == "OPS/chapter.xhtml"
    assert _normalise_epub_reference("OPS/text", "chapter%20one.xhtml") == "OPS/text/chapter one.xhtml"
    assert _normalise_epub_reference("OPS", "子目录/章节.xhtml") == "OPS/子目录/章节.xhtml"
    assert _normalise_epub_reference("OPS/text/nested", "../../chapter.xhtml") == "OPS/chapter.xhtml"


@pytest.mark.parametrize(
    "href",
    [
        "../../secret.xhtml",
        "../../../etc/passwd",
        "http://example.com/chapter.xhtml",
        "https://example.com/chapter.xhtml",
        "file:///etc/passwd",
        "%2e%2e/%2e%2e/secret.xhtml",
        "../%2e%2e/secret.xhtml",
        "..\\windows\\path.xhtml",
        "C:\\windows\\path.xhtml",
        "/absolute/path.xhtml",
        "",
    ],
)
def test_unsafe_epub_references_are_rejected(href: str) -> None:
    """``base_dir='OPS'`` is one level deep, so climbing twice escapes the ZIP root."""
    with pytest.raises(DomainRuleError):
        _normalise_epub_reference("OPS", href)


def test_percent_decoded_dot_segments_cannot_be_smuggled_past_the_check() -> None:
    """``%2e%2e`` is decoded *before* the boundary check, not after."""
    assert _normalise_epub_reference("OPS/text", "%2e%2e/chapter.xhtml") == "OPS/chapter.xhtml"
    assert _normalise_epub_reference("OPS", "..%2fsecret.xhtml") == "secret.xhtml"
    with pytest.raises(DomainRuleError):
        _normalise_epub_reference("OPS", "%2e%2e/%2e%2e/secret.xhtml")


def test_epub_parent_directory_reference_imports_successfully(workspace, database) -> None:
    project = _project(workspace, database, code="np07_parent_ref")
    source = workspace.work_root / "parent-ref.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OPS/package.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "OPS/package.opf",
            "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'>"
            "<manifest><item id='c1' href='../Text/chapter.xhtml' media-type='application/xhtml+xml'/>"
            "<item id='c2' href='../Text/chapter%20two.xhtml#top' media-type='application/xhtml+xml'/></manifest>"
            "<spine><itemref idref='c1'/><itemref idref='c2'/></spine></package>",
        )
        archive.writestr(
            "Text/chapter.xhtml",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>第一章</h1><p>父目录正文。</p></body></html>",
        )
        archive.writestr(
            "Text/chapter two.xhtml",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>带空格文件名正文。</p></body></html>",
        )

    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert result["status"] == "PREVIEW_READY"
    assert result["preview"]["paragraphs"] == ["第一章", "父目录正文。", "带空格文件名正文。"]


def test_epub_with_escaping_reference_is_rejected_without_records(workspace, database) -> None:
    project = _project(workspace, database, code="np07_escape")
    source = workspace.work_root / "escaping.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OPS/package.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "OPS/package.opf",
            "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'>"
            "<manifest><item id='c1' href='../../secret.xhtml' media-type='application/xhtml+xml'/></manifest>"
            "<spine><itemref idref='c1'/></spine></package>",
        )
        archive.writestr("secret.xhtml", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>包外内容</p></body></html>")

    with pytest.raises(DomainRuleError) as caught:
        DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert caught.value.code == "DOCUMENT_PARSE_FAILED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 0


def test_epub_invalid_tags_only_returns_empty_text_error(workspace, database) -> None:
    project = _project(workspace, database, code="np06_invalid_tags")
    source = _epub(
        workspace.work_root / "invalid-tags.epub",
        package_path="OEBPS/content.opf",
        manifest_hrefs={"c1": "chapter1.xhtml"},
        documents={"OEBPS/chapter1.xhtml": "<img src='x.png'/><br/><hr/>"},
    )

    with pytest.raises(DomainRuleError) as caught:
        DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert caught.value.code == "DOCUMENT_TEXT_EMPTY"


# --------------------------------------------------------------------------
# NP09: dense single-newline TXT structure preview and layout choice.
# --------------------------------------------------------------------------


def test_dense_single_newline_novel_exposes_layout_options_and_chapter_list(workspace, database) -> None:
    project = _project(workspace, database, code="np09_dense_txt")
    source = workspace.work_root / "dense-novel.txt"
    source.write_text("第一章雨夜\n林默推开门。\n第二章清晨\n苏晚醒来。", encoding="utf-8")

    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    preview = result["preview"]

    assert preview["paragraph_layout"] == "NEWLINE"
    assert preview["paragraph_layout_recommended"] == "NEWLINE"
    assert preview["paragraph_layout_options"] == ["BLANK_LINE", "NEWLINE"]
    assert preview["paragraph_count"] == 4
    assert preview["chapters"] == [
        {"title": "第一章雨夜", "start_paragraph": 1, "end_paragraph": 2},
        {"title": "第二章清晨", "start_paragraph": 3, "end_paragraph": 4},
    ]


def test_explicit_blank_line_layout_creates_its_own_parse_generation(workspace, database) -> None:
    project = _project(workspace, database, code="np09_layout_switch")
    source = workspace.work_root / "layout-switch.txt"
    source.write_text("第一段。\n\n第二段。\n\n第三段。", encoding="utf-8")
    service = DocumentImportService(database, workspace)

    blank_line = service.import_document(str(project["id"]), source, paragraph_layout="BLANK_LINE")
    newline = service.import_document(str(project["id"]), source, paragraph_layout="NEWLINE")

    # The same immutable raw file supports both paragraph strategies as separate
    # parse versions, so the old index is never overwritten.
    assert blank_line["preview"]["paragraph_layout"] == "BLANK_LINE"
    assert newline["preview"]["paragraph_layout"] == "NEWLINE"
    assert blank_line["source_document_version_id"] != newline["source_document_version_id"]
    assert blank_line["preview"]["paragraph_count"] == 3
    assert newline["preview"]["paragraph_count"] == 3
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 2
