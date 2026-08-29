from __future__ import annotations

import json
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.application.source_text import source_paragraphs
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
    original_filename = "照骨灯_凡人修仙原创长篇_约200分钟.txt"
    with TestClient(create_app(workspace)) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project['id']}/imports:upload",
            content="第一场\n人物进入房间".encode(),
            headers={"Content-Type": "text/plain", "X-File-Name": quote(original_filename)},
        )
        empty = client.post(
            f"/api/v1/projects/{project['id']}/imports:upload",
            content=b"",
            headers={"Content-Type": "text/plain", "X-File-Name": "empty.txt"},
        )
    assert uploaded.status_code == 201, uploaded.text
    imported = uploaded.json()["import"]
    assert imported["status"] == "PREVIEW_READY"
    stored_source = imported["stored_source"]
    assert stored_source["scope"] == "PROJECT"
    assert stored_source["kind"] == "FILE"
    assert stored_source["display_name"] == original_filename
    assert stored_source["download_filename"] == original_filename
    assert original_filename in stored_source["rel_path"]
    assert not Path(stored_source["rel_path"]).is_absolute()
    expected_stored_path = workspace.projects_root / project["root_rel"] / stored_source["rel_path"]
    assert expected_stored_path.is_file()
    assert Path(stored_source["server_absolute_path"]) == expected_stored_path.resolve()
    assert stored_source["download_url"] == f"/api/v1/media-versions/{imported['media_version_id']}/content"
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "DOCUMENT_UPLOAD_EMPTY"


def test_document_import_routes_publish_one_typed_response_contract(workspace) -> None:
    spec = create_app(workspace).openapi()
    direct = spec["paths"]["/api/v1/projects/{project_id}/imports"]["post"]["responses"]["201"]
    upload = spec["paths"]["/api/v1/projects/{project_id}/imports:upload"]["post"]["responses"]["201"]
    expected = {"$ref": "#/components/schemas/DocumentImportResponse"}
    assert direct["content"]["application/json"]["schema"] == expected
    assert upload["content"]["application/json"]["schema"] == expected
    properties = spec["components"]["schemas"]["DocumentImportResult"]["properties"]
    assert "stored_source" in properties
    assert "stored_source_path" not in properties


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


def test_import_preview_indexes_chapter_headings_missing_a_leading_blank_line(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "chapter-heading-without-blank-line.txt"
    text = "第一章 雨夜\n\n第一段。\n第二章 清晨\n\n第二段。\n\n第三章 归途\n第三段。"
    source.write_text(text, encoding="utf-8")

    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    assert imported["preview"]["paragraphs"] == [
        "第一章 雨夜",
        "第一段。\n第二章 清晨",
        "第二段。",
        "第三章 归途\n第三段。",
    ]
    assert imported["preview"]["chapters"] == [
        {"title": "第一章 雨夜", "start_paragraph": 1, "end_paragraph": 2},
        {"title": "第二章 清晨", "start_paragraph": 3, "end_paragraph": 3},
        {"title": "第三章 归途", "start_paragraph": 4, "end_paragraph": 4},
    ]
    extracted = source.read_text(encoding="utf-8")
    for paragraph in source_paragraphs(extracted):
        assert extracted[paragraph.start:paragraph.end] == paragraph.text


def test_reimport_refreshes_a_preview_from_an_older_structure_index(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "stale-structure-preview.txt"
    source.write_text("第一章 雨夜\n\n第一段。\n第二章 清晨\n\n第二段。", encoding="utf-8")
    service = DocumentImportService(database, workspace)

    first = service.import_document(str(project["id"]), source)
    with database.transaction() as connection:
        preview = json.loads(
            connection.execute(
                "SELECT preview_json FROM import_sessions WHERE id=?",
                (first["import_session_id"],),
            ).fetchone()["preview_json"]
        )
        preview.pop("source_structure_version")
        connection.execute(
            "UPDATE import_sessions SET preview_json=? WHERE id=?",
            (json.dumps(preview, ensure_ascii=False), first["import_session_id"]),
        )

    refreshed = service.import_document(str(project["id"]), source)

    assert refreshed["source_document_version_id"] == first["source_document_version_id"]
    assert refreshed["import_session_id"] != first["import_session_id"]
    assert refreshed["preview"]["source_structure_version"] == 2
    assert [chapter["title"] for chapter in refreshed["preview"]["chapters"]] == ["第一章 雨夜", "第二章 清晨"]


def test_paragraph_api_pages_full_source_and_commit_freezes_selected_body_range(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "long-novel.md"
    source.write_text(
        "# 第一章 雨夜\n\n第一段。\n\n第二段。\n\n# 第二章 清晨\n\n第三段。\n\n第四段。",
        encoding="utf-8",
    )

    with TestClient(create_app(workspace)) as client:
        imported_response = client.post(
            f"/api/v1/projects/{project['id']}/imports",
            json={"source_path": str(source)},
        )
        assert imported_response.status_code == 201, imported_response.text
        imported = imported_response.json()["import"]
        session_id = imported["import_session_id"]

        first_page_response = client.get(f"/api/v1/import-sessions/{session_id}/paragraphs?start=1&limit=3")
        assert first_page_response.status_code == 200, first_page_response.text
        first_page = first_page_response.json()
        assert first_page["start_paragraph"] == 1
        assert first_page["end_paragraph"] == 3
        assert first_page["total_paragraph_count"] == 6
        assert first_page["has_previous"] is False
        assert first_page["has_more"] is True
        assert first_page["items"][0]["is_heading"] is True
        assert first_page["items"][1]["text"] == "第一段。"

        second_page = client.get(f"/api/v1/import-sessions/{session_id}/paragraphs?start=4&limit=3").json()
        assert second_page["has_previous"] is True
        assert second_page["has_more"] is False
        assert second_page["items"][0]["number"] == 4

        committed_response = client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={
                "expected_preview_hash": imported["preview_hash"],
                "source_paragraph_start": 2,
                "source_paragraph_end": 5,
            },
        )
        assert committed_response.status_code == 200, committed_response.text
        committed = committed_response.json()["commit"]
        assert committed["commit_snapshot"]["body_range"] == {
            "source_paragraph_start": 2,
            "source_paragraph_end": 5,
            "source_paragraph_count": 4,
            "selection_mode": "EXPLICIT",
        }
        selected = next(item for item in committed["items"] if item["item_type"] == "SOURCE_BODY_RANGE")
        assert selected["payload"] == committed["commit_snapshot"]["body_range"]

        repeated = client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={
                "expected_preview_hash": imported["preview_hash"],
                "source_paragraph_start": 2,
                "source_paragraph_end": 5,
            },
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["commit"]["idempotent"] is True
