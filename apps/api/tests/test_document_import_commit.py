from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="document_import_commit",
        title="Document import commit",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _docx(path: Path, text: str) -> Path:
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def test_txt_markdown_and_docx_preview_then_idempotent_commit_preserve_source(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    sources = [
        workspace.work_root / "script.txt",
        workspace.work_root / "script.md",
        workspace.work_root / "script.docx",
    ]
    sources[0].write_text("第一场\n\n人物进入房间", encoding="utf-8")
    sources[1].write_text("# 第二场\n\n人物离开房间", encoding="utf-8")
    _docx(sources[2], "第三场：人物回到房间")
    original_bytes = {path: path.read_bytes() for path in sources}

    with TestClient(create_app(workspace)) as client:
        for source in sources:
            imported = client.post(f"/api/v1/projects/{project_id}/imports", json={"source_path": str(source)})
            assert imported.status_code == 201, imported.text
            result = imported.json()["import"]
            assert result["status"] == "PREVIEW_READY"
            assert len(result["preview_hash"]) == 64
            session = client.get(f"/api/v1/import-sessions/{result['import_session_id']}").json()["session"]
            assert session["validation"] == {"valid": True, "issue_count": 0}
            assert session["source_document_version"]["parse_status"] == "PARSED"
            assert client.get(f"/api/v1/import-sessions/{result['import_session_id']}/issues").json()["issues"] == []

            stale = client.post(f"/api/v1/import-sessions/{result['import_session_id']}:commit", json={"expected_preview_hash": "0" * 64})
            assert stale.status_code == 422
            assert stale.json()["error"]["code"] == "IMPORT_PREVIEW_STALE"
            committed = client.post(
                f"/api/v1/import-sessions/{result['import_session_id']}:commit",
                json={"expected_preview_hash": result["preview_hash"]},
            )
            assert committed.status_code == 200, committed.text
            assert committed.json()["commit"]["status"] == "COMMITTED"
            assert committed.json()["commit"]["idempotent"] is False
            assert committed.json()["commit"]["source_preserved"] is True
            repeated = client.post(
                f"/api/v1/import-sessions/{result['import_session_id']}:commit",
                json={"expected_preview_hash": result["preview_hash"]},
            )
            assert repeated.status_code == 200
            assert repeated.json()["commit"]["idempotent"] is True
            assert source.read_bytes() == original_bytes[source]

        duplicate_source = client.post(f"/api/v1/projects/{project_id}/imports", json={"source_path": str(sources[0])})
        assert duplicate_source.status_code == 201, duplicate_source.text
        duplicate_import = duplicate_source.json()["import"]
        assert duplicate_import["status"] == "COMMITTED"
        duplicate_commit = client.post(
            f"/api/v1/import-sessions/{duplicate_import['import_session_id']}:commit",
            json={"expected_preview_hash": duplicate_import["preview_hash"]},
        )
        assert duplicate_commit.status_code == 200
        assert duplicate_commit.json()["commit"]["idempotent"] is True

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM import_sessions WHERE status='COMMITTED'").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='IMPORT_SESSION_COMMITTED'").fetchone()[0] == 3


def test_unsupported_document_does_not_create_source_or_session(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "script.pdf"
    source.write_bytes(b"not a supported document")
    service = DocumentImportService(database, workspace)

    try:
        service.import_document(str(project["id"]), source)
    except Exception as error:
        assert getattr(error, "code", None) == "UNSUPPORTED_DOCUMENT_TYPE"
    else:
        raise AssertionError("unsupported document import must fail")

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 0


def test_browser_document_upload_streams_into_the_same_preview_contract(workspace, database) -> None:
    project = _project(workspace, database)
    with TestClient(create_app(workspace)) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project['id']}/imports:upload",
            content="第一场\n人物进入房间".encode(),
            headers={"Content-Type": "text/plain", "X-File-Name": "browser-script.txt"},
        )
        empty = client.post(
            f"/api/v1/projects/{project['id']}/imports:upload",
            content=b"",
            headers={"Content-Type": "text/plain", "X-File-Name": "empty.txt"},
        )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["import"]["status"] == "PREVIEW_READY"
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "DOCUMENT_UPLOAD_EMPTY"


def test_import_preview_exposes_deterministic_chapter_shortcuts(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "chaptered-script.md"
    source.write_text(
        "# 第一章 雨夜\n\n林默进入剧院。\n他打开手电。\n\n第二段正文。\n\n第二章 清晨\n\n苏晚来到门口。",
        encoding="utf-8",
    )

    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert imported["preview"]["paragraph_count"] == 5
    assert imported["preview"]["paragraphs"][1] == "林默进入剧院。\n他打开手电。"
    assert imported["preview"]["chapters"] == [
        {"title": "第一章 雨夜", "start_paragraph": 1, "end_paragraph": 3},
        {"title": "第二章 清晨", "start_paragraph": 4, "end_paragraph": 5},
    ]
