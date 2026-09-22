"""NP04/NP08/NP10 regressions: scope-aware commits and immutable source reads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Scope idempotency",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _five_paragraph_source(workspace) -> Path:
    source = workspace.work_root / "five-paragraphs.txt"
    source.write_text("第一段。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。", encoding="utf-8")
    return source


def _extracted_rel(database, source_document_version_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT extracted_text_rel FROM source_document_versions WHERE id=?",
            (source_document_version_id,),
        ).fetchone()
    return str(row["extracted_text_rel"])


def _project_root(workspace, project) -> Path:
    return workspace.projects_root / str(project["root_rel"])


def test_same_range_commit_replays_the_original_receipt(workspace, database) -> None:
    project = _project(workspace, database, code="scope_idempotency")
    source = _five_paragraph_source(workspace)

    with TestClient(create_app(workspace)) as client:
        imported = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(source)}).json()["import"]
        session_id = imported["import_session_id"]
        body = {
            "expected_preview_hash": imported["preview_hash"],
            "source_paragraph_start": 1,
            "source_paragraph_end": 3,
        }
        first = client.post(f"/api/v1/import-sessions/{session_id}:commit", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["commit"]["idempotent"] is False
        assert first.json()["commit"]["commit_snapshot"]["body_range"]["source_paragraph_end"] == 3

        replay = client.post(f"/api/v1/import-sessions/{session_id}:commit", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["commit"]["idempotent"] is True
        assert replay.json()["commit"]["commit_snapshot"]["body_range"] == first.json()["commit"]["commit_snapshot"]["body_range"]


def test_changing_the_committed_range_is_a_409_and_never_a_silent_no_op(workspace, database) -> None:
    project = _project(workspace, database, code="scope_conflict")
    source = _five_paragraph_source(workspace)

    with TestClient(create_app(workspace)) as client:
        imported = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(source)}).json()["import"]
        session_id = imported["import_session_id"]
        committed = client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={"expected_preview_hash": imported["preview_hash"], "source_paragraph_start": 1, "source_paragraph_end": 3},
        )
        assert committed.status_code == 200, committed.text

        conflicting = client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={"expected_preview_hash": imported["preview_hash"], "source_paragraph_start": 4, "source_paragraph_end": 5},
        )
        assert conflicting.status_code == 409, conflicting.text
        error = conflicting.json()["error"]
        assert error["code"] == "IMPORT_COMMIT_SCOPE_CONFLICT"
        assert error["details"]["existing_scope"]["source_paragraph_start"] == 1
        assert error["details"]["existing_scope"]["source_paragraph_end"] == 3
        assert error["details"]["requested_scope"]["source_paragraph_start"] == 4

    # The stored scope really is still 1-3: nothing silently changed.
    with database.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM import_session_items WHERE session_id=? AND item_type='SOURCE_BODY_RANGE'",
                (session_id,),
            ).fetchone()["payload_json"]
        )
    assert payload["source_paragraph_start"] == 1
    assert payload["source_paragraph_end"] == 3


def test_duplicate_import_can_commit_the_same_range_but_conflicts_on_a_new_one(workspace, database) -> None:
    project = _project(workspace, database, code="scope_duplicate_import")
    source = _five_paragraph_source(workspace)
    renamed = workspace.work_root / "renamed-copy.txt"
    renamed.write_bytes(source.read_bytes())

    with TestClient(create_app(workspace)) as client:
        first = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(source)}).json()["import"]
        commit_body = {
            "expected_preview_hash": first["preview_hash"],
            "source_paragraph_start": 1,
            "source_paragraph_end": 2,
        }
        assert client.post(f"/api/v1/import-sessions/{first['import_session_id']}:commit", json=commit_body).status_code == 200

        # Renaming and re-importing the same bytes reuses the same committed session.
        duplicate = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(renamed)}).json()["import"]
        assert duplicate["import_session_id"] == first["import_session_id"]
        assert duplicate["source_document_version_id"] == first["source_document_version_id"]
        assert duplicate["status"] == "COMMITTED"

        same_range = client.post(
            f"/api/v1/import-sessions/{duplicate['import_session_id']}:commit",
            json={**commit_body, "expected_preview_hash": duplicate["preview_hash"]},
        )
        assert same_range.status_code == 200, same_range.text
        assert same_range.json()["commit"]["idempotent"] is True

        different_range = client.post(
            f"/api/v1/import-sessions/{duplicate['import_session_id']}:commit",
            json={**commit_body, "expected_preview_hash": duplicate["preview_hash"], "source_paragraph_end": 4},
        )
        assert different_range.status_code == 409, different_range.text
        assert different_range.json()["error"]["code"] == "IMPORT_COMMIT_SCOPE_CONFLICT"

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_sessions").fetchone()[0] == 1


def test_explicit_new_selection_reuses_the_source_version_with_a_new_range(workspace, database) -> None:
    project = _project(workspace, database, code="scope_new_selection")
    source = _five_paragraph_source(workspace)

    with TestClient(create_app(workspace)) as client:
        imported = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(source)}).json()["import"]
        session_id = imported["import_session_id"]
        assert client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={"expected_preview_hash": imported["preview_hash"], "source_paragraph_start": 1, "source_paragraph_end": 3},
        ).status_code == 200

        selection_response = client.post(
            f"/api/v1/import-sessions/{session_id}:select-range",
            json={"source_paragraph_start": 4, "source_paragraph_end": 5},
        )
        assert selection_response.status_code == 201, selection_response.text
        selection = selection_response.json()["selection"]
        assert selection["reused"] is False
        assert selection["source_preserved"] is True
        assert selection["selected_range"] == {
            "source_paragraph_start": 4,
            "source_paragraph_end": 5,
            "source_paragraph_count": 2,
            "selection_mode": "EXPLICIT",
        }
        new_session_id = selection["import_session_id"]
        assert new_session_id != session_id
        assert selection["source_document_version_id"] == imported["source_document_version_id"]

        # The derived session is already authorised and reports the new scope.
        derived = client.get(f"/api/v1/import-sessions/{new_session_id}").json()["session"]
        assert derived["status"] == "COMMITTED"
        assert derived["source_document_version_id"] == imported["source_document_version_id"]
        derived_scope = next(item for item in derived["items"] if item["item_type"] == "SOURCE_BODY_RANGE")
        assert derived_scope["payload"]["source_paragraph_start"] == 4

    original = DocumentImportService(database, workspace).get_session(session_id)
    original_scope = next(item for item in original["items"] if item["item_type"] == "SOURCE_BODY_RANGE")
    assert original_scope["payload"]["source_paragraph_start"] == 1
    assert original_scope["payload"]["source_paragraph_end"] == 3
    with database.connect() as connection:
        # One immutable source version, two committed sessions, no source rewrite.
        assert connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_sessions WHERE status='COMMITTED'").fetchone()[0] == 2


def test_commit_create_new_selection_flag_derives_a_session(workspace, database) -> None:
    project = _project(workspace, database, code="scope_commit_flag")
    source = _five_paragraph_source(workspace)

    with TestClient(create_app(workspace)) as client:
        imported = client.post(f"/api/v1/projects/{project['id']}/imports", json={"source_path": str(source)}).json()["import"]
        session_id = imported["import_session_id"]
        assert client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={"expected_preview_hash": imported["preview_hash"], "source_paragraph_start": 1, "source_paragraph_end": 2},
        ).status_code == 200

        derived = client.post(
            f"/api/v1/import-sessions/{session_id}:commit",
            json={
                "expected_preview_hash": imported["preview_hash"],
                "source_paragraph_start": 3,
                "source_paragraph_end": 5,
                "create_new_selection": True,
            },
        )
        assert derived.status_code == 200, derived.text
        payload = derived.json()["commit"]
        assert payload["import_session_id"] != session_id
        assert payload["selected_range"]["source_paragraph_start"] == 3
        assert payload["source_preserved"] is True


# --------------------------------------------------------------------------
# NP10: passage and paragraph reads share one hash/version verification rule.
# --------------------------------------------------------------------------


def test_passage_and_paragraph_endpoints_agree_on_an_intact_source(workspace, database) -> None:
    project = _project(workspace, database, code="np10_intact")
    source = _five_paragraph_source(workspace)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    with TestClient(create_app(workspace)) as client:
        passage = client.get(
            f"/api/v1/source-document-versions/{imported['source_document_version_id']}/passage",
            params={"start": 0, "end": 4},
        )
        assert passage.status_code == 200, passage.text
        assert passage.json()["text"] == "第一段。"
        assert passage.json()["offset_unit"] == "UNICODE_CODEPOINT"

        paragraphs = client.get(f"/api/v1/import-sessions/{imported['import_session_id']}/paragraphs?start=1&limit=2")
        assert paragraphs.status_code == 200, paragraphs.text
        assert paragraphs.json()["items"][0]["text"] == "第一段。"

    with database.connect() as connection:
        recorded = connection.execute(
            "SELECT text_sha256 FROM source_document_versions WHERE id=?",
            (imported["source_document_version_id"],),
        ).fetchone()["text_sha256"]
    assert passage.json()["source_text_sha256"] == recorded


def test_tampered_extracted_text_is_rejected_by_passage_and_paragraphs(workspace, database) -> None:
    project = _project(workspace, database, code="np10_tamper")
    source = _five_paragraph_source(workspace)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    extracted = _project_root(workspace, project) / _extracted_rel(database, str(imported["source_document_version_id"]))
    original = extracted.read_text(encoding="utf-8")

    def _expect_tamper_rejected(label: str) -> None:
        with TestClient(create_app(workspace)) as client:
            passage = client.get(
                f"/api/v1/source-document-versions/{imported['source_document_version_id']}/passage",
                params={"start": 0, "end": 4},
            )
            paragraphs = client.get(f"/api/v1/import-sessions/{imported['import_session_id']}/paragraphs?start=1&limit=2")
        assert passage.status_code == 422, (label, passage.text)
        assert passage.json()["error"]["code"] == "IMPORT_EXTRACTED_TEXT_CHANGED", label
        assert paragraphs.status_code == 422, (label, paragraphs.text)
        assert paragraphs.json()["error"]["code"] == "IMPORT_EXTRACTED_TEXT_CHANGED", label

    # Same length, rewritten content (stale hash with an equal byte count).
    extracted.write_text(original.replace("第一段。", "第一段？", 1), encoding="utf-8")
    assert len(extracted.read_text(encoding="utf-8")) == len(original)
    _expect_tamper_rejected("same-length rewrite")

    # Truncated file.
    extracted.write_text(original[: len(original) // 2], encoding="utf-8")
    _expect_tamper_rejected("truncation")

    # Appended content.
    extracted.write_text(original + "\n\n外部追加。", encoding="utf-8")
    _expect_tamper_rejected("append")

    # Missing file.
    extracted.unlink()
    _expect_tamper_rejected("missing")

    # Restoring the original bytes makes both endpoints work again.
    extracted.write_text(original, encoding="utf-8", newline="")
    with TestClient(create_app(workspace)) as client:
        restored = client.get(
            f"/api/v1/source-document-versions/{imported['source_document_version_id']}/passage",
            params={"start": 0, "end": 4},
        )
    assert restored.status_code == 200, restored.text
    assert restored.json()["text"] == "第一段。"


def test_commit_refuses_a_tampered_extracted_text(workspace, database) -> None:
    project = _project(workspace, database, code="np10_commit_tamper")
    source = _five_paragraph_source(workspace)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    extracted = _project_root(workspace, project) / _extracted_rel(database, str(imported["source_document_version_id"]))
    extracted.write_text("被改写的正文。", encoding="utf-8")
    service = DocumentImportService(database, workspace)

    try:
        service.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))
    except DomainRuleError as error:
        assert error.code == "IMPORT_EXTRACTED_TEXT_CHANGED"
    else:
        raise AssertionError("commit must refuse a tampered extracted text")


# --------------------------------------------------------------------------
# Versioned parsing: parse generations are explicit and reusable.
# --------------------------------------------------------------------------


def test_parse_version_columns_and_extracted_identity_are_recorded(workspace, database) -> None:
    project = _project(workspace, database, code="np12_versions")
    source = _five_paragraph_source(workspace)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    with database.connect() as connection:
        row = connection.execute(
            "SELECT parser_version, structure_version, metadata_json, extracted_text_rel FROM source_document_versions WHERE id=?",
            (imported["source_document_version_id"],),
        ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert row["parser_version"] == 2
    assert row["structure_version"] == 3
    assert metadata["parser_version"] == 2
    assert metadata["structure_version"] == 3
    assert metadata["paragraph_layout"] == "BLANK_LINE"
    identity = hashlib.sha256(b"2:3:AUTO").hexdigest()[:12]
    assert str(row["extracted_text_rel"]).endswith(f"{identity}.extracted.txt")
    assert (_project_root(workspace, project) / str(row["extracted_text_rel"])).is_file()
    assert imported["preview"]["source_parser_version"] == 2
    assert imported["preview"]["paragraph_layout"] == "BLANK_LINE"
    assert imported["preview"]["source_structure_version"] == 3


def test_reimport_after_a_legacy_preview_rebuilds_the_preview_without_a_new_version(workspace, database) -> None:
    project = _project(workspace, database, code="np12_legacy_preview")
    source = _five_paragraph_source(workspace)
    service = DocumentImportService(database, workspace)
    first = service.import_document(str(project["id"]), source)
    current_rel = _extracted_rel(database, str(first["source_document_version_id"]))
    legacy_rel = f"00_admin/imports/{hashlib.sha256(source.read_bytes()).hexdigest()}.extracted.txt"
    project_root = _project_root(workspace, project)
    (project_root / legacy_rel).parent.mkdir(parents=True, exist_ok=True)
    (project_root / legacy_rel).write_bytes((project_root / current_rel).read_bytes())

    # Simulate a row written by the previous structure grammar: legacy parse
    # generation, no parse-version metadata, and a preview without a structure
    # index. The frozen text itself is unchanged.
    with database.transaction() as connection:
        metadata = json.loads(
            connection.execute(
                "SELECT metadata_json FROM source_document_versions WHERE id=?",
                (first["source_document_version_id"],),
            ).fetchone()["metadata_json"]
        )
        metadata.pop("parser_version")
        metadata.pop("structure_version")
        connection.execute(
            "UPDATE source_document_versions SET parser_version=1, structure_version=2, extracted_text_rel=?, metadata_json=? WHERE id=?",
            (legacy_rel, json.dumps(metadata, ensure_ascii=False), first["source_document_version_id"]),
        )
        preview = json.loads(
            connection.execute(
                "SELECT preview_json FROM import_sessions WHERE id=?", (first["import_session_id"],),
            ).fetchone()["preview_json"]
        )
        preview.pop("source_structure_version")
        preview.pop("source_parser_version")
        connection.execute(
            "UPDATE import_sessions SET preview_json=? WHERE id=?",
            (json.dumps(preview, ensure_ascii=False), first["import_session_id"]),
        )

    refreshed = service.import_document(str(project["id"]), source)

    # Same raw bytes and identical frozen text: reuse the version, refresh the preview.
    with database.connect() as connection:
        versions = connection.execute("SELECT COUNT(*) FROM source_document_versions").fetchone()[0]
    assert versions == 1
    assert refreshed["source_document_version_id"] == first["source_document_version_id"]
    assert refreshed["import_session_id"] != first["import_session_id"]
    assert refreshed["preview"]["source_structure_version"] == 3
    assert refreshed["preview"]["paragraph_count"] == 5
