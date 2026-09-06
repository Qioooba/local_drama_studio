from __future__ import annotations

import zipfile

from local_drama.application import documents
from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService


def test_pdf_reader_text_enters_normal_document_pipeline(workspace, database, monkeypatch) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        def __init__(self, _path: object) -> None:
            self.pages = [Page("第一章 雨夜"), Page("林舟推开旧仓库的门。")]

    monkeypatch.setattr(documents, "PdfReader", Reader)
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pdf_novel", title="PDF 小说", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "novel.pdf"
    source.write_bytes(b"%PDF-1.7 test fixture")
    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    assert result["status"] == "PREVIEW_READY"
    assert result["preview"]["character_count"] == len("第一章 雨夜\n\n林舟推开旧仓库的门。")


def test_epub_spine_order_enters_normal_document_pipeline(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="epub_novel", title="EPUB 小说", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "novel.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("META-INF/container.xml", """<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>""")
        archive.writestr("OEBPS/content.opf", """<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'><manifest><item id='c1' href='chapter1.xhtml' media-type='application/xhtml+xml'/><item id='c2' href='chapter2.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='c1'/><itemref idref='c2'/></spine></package>""")
        archive.writestr("OEBPS/chapter1.xhtml", "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>第一章</h1><p>风从海上来。</p></body></html>")
        archive.writestr("OEBPS/chapter2.xhtml", "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>第二章</h1><p>灯塔重新亮起。</p></body></html>")
    result = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    assert result["status"] == "PREVIEW_READY"
    assert result["preview"]["paragraphs"] == ["第一章", "风从海上来。", "第二章", "灯塔重新亮起。"]
