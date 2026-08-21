from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.application.read_models import SearchService
from local_drama.main import create_app


def _project(workspace, database) -> tuple[ProjectService, dict[str, object]]:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(
        code="source_200k", title="20万字剧本", episode_count=50, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return service, project


def test_200k_chinese_source_is_preserved_and_all_read_responses_are_bounded(workspace, database, monkeypatch) -> None:
    projects, project = _project(workspace, database)
    project_id = str(project["id"])
    marker = "罕见剧情标记甲乙丙丁"
    text = ("第一章\n\n" + "山河故人归" * 40_000)[:200_000]
    marker_start = 150_123
    text = text[:marker_start] + marker + text[marker_start + len(marker):]
    assert len(text) == 200_000
    source = workspace.work_root / "long-script.txt"
    source.write_text(text, encoding="utf-8", newline="")
    original_bytes = source.read_bytes()

    service = DocumentImportService(database, workspace)
    original_index = service._replace_search_index
    authoritative_rows_visible_before_index: list[tuple[int, int]] = []

    def observed_index(*args, **kwargs):
        with database.connect() as connection:
            authoritative_rows_visible_before_index.append((
                int(connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0]),
            ))
        return original_index(*args, **kwargs)

    monkeypatch.setattr(service, "_replace_search_index", observed_index)
    imported = service.import_document(project_id, source)
    assert authoritative_rows_visible_before_index == [(1, 1)]
    assert imported["index_status"] == "READY"
    assert imported["preview"]["character_count"] == 200_000
    assert imported["preview"]["preview_truncated"] is True
    assert sum(len(item) for item in imported["preview"]["paragraphs"]) <= 12_000
    assert max(map(len, imported["preview"]["paragraphs"])) <= 1_000
    assert len(json.dumps(imported, ensure_ascii=False).encode("utf-8")) < 20_000
    assert source.read_bytes() == original_bytes

    committed = service.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))
    assert committed["source_preserved"] is True
    with database.connect() as connection:
        version = connection.execute(
            "SELECT extracted_text_rel,text_sha256,metadata_json FROM source_document_versions WHERE id=?",
            (imported["source_document_version_id"],),
        ).fetchone()
        assert json.loads(version["metadata_json"])["offset_unit"] == "UNICODE_CODEPOINT"
        assert connection.execute("SELECT COUNT(*) FROM script_breakdown_drafts").fetchone()[0] == 0
    extracted = service.media._project_root(project_id) / str(version["extracted_text_rel"])
    assert extracted.read_text(encoding="utf-8") == text
    assert hashlib.sha256(extracted.read_bytes()).hexdigest() == version["text_sha256"]

    passage_start = marker_start - 11
    passage_end = marker_start + len(marker) + 13
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/source-document-versions/{imported['source_document_version_id']}/passage",
            params={"start": passage_start, "end": passage_end},
        )
        assert response.status_code == 200, response.text
        passage = response.json()
        assert passage["offset_unit"] == "UNICODE_CODEPOINT"
        assert passage["text"] == text[passage_start:passage_end]
        assert passage["source_start"] == passage_start
        assert passage["source_end"] == passage_end
        assert passage["total_character_count"] == 200_000
        assert len(response.content) < 12_000
        too_large = client.get(
            f"/api/v1/source-document-versions/{imported['source_document_version_id']}/passage",
            params={"start": 0, "end": 8_001},
        )
        assert too_large.status_code == 422
        assert too_large.json()["error"]["code"] == "SOURCE_PASSAGE_TOO_LARGE"

    results = SearchService(database).search(marker, project_id=project_id, limit=5)
    assert len(results) == 1
    assert results[0]["subject_type"] == "SOURCE_DOCUMENT"
    assert marker not in results[0]["snippet"]  # no source passage leaks into navigation results
    assert len(json.dumps(results, ensure_ascii=False).encode("utf-8")) < 4_000

    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    scene = projects.create_scene(project_id, "SC-LONG", "长文本中段")
    bound = projects.bind_episode_scene_range(
        str(episode["id"]), str(scene["id"]), 1, passage_start, passage_end, "Unicode character offsets",
    )
    assert (bound["source_start"], bound["source_end"]) == (passage_start, passage_end)
    assert bound["offset_unit"] == "UNICODE_CODEPOINT"


def test_search_index_failure_is_explicit_and_reimport_retries_without_losing_source(workspace, database) -> None:
    _, project = _project(workspace, database)
    project_id = str(project["id"])
    source = workspace.work_root / "retry-index.txt"
    source.write_text("第一场\n\n可恢复搜索索引", encoding="utf-8")
    with database.transaction() as connection:
        connection.execute("DROP TABLE fts_search")

    service = DocumentImportService(database, workspace)
    failed = service.import_document(project_id, source)
    assert failed["index_status"] == "FAILED_RETRYABLE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='SOURCE_SEARCH_INDEX_FAILED'").fetchone()[0] == 1

    with database.transaction() as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE fts_search USING fts5(project_id UNINDEXED,subject_type UNINDEXED,subject_id UNINDEXED,content)"
        )
    retried = service.import_document(project_id, source)
    assert retried["reused"] is True
    assert retried["index_status"] == "READY"
    assert SearchService(database).search("可恢复搜索索引", project_id=project_id, limit=5)[0]["subject_type"] == "SOURCE_DOCUMENT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 1
