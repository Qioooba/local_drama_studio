"""SS-05: a project-package copy must carry the manuscript business records.

The versioned package protocol carries the source document, its parse version
(including the parser generation identity), the committed import session with
its authorised text range, and the project default duration.  Identity is
rewritten uniformly and every reference is verified against the copy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import zipfile

from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.duration import DEFAULT_PROJECT_TARGET_DURATION_MS


def _project(workspace, database, code: str, duration_ms: int = 180_000) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="原稿往返",
        episode_count=2,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=duration_ms,
        allow_unconfigured_capabilities=True,
    )


def _seed_manuscript(workspace, database, project_id: str, code: str) -> dict[str, str]:
    """Insert one imported+committed manuscript with an authorised body range."""
    root = workspace.projects_root / code
    raw_relative = "01_story/source_documents/novel.txt"
    raw_text = "第一段。\n第二段。\n第三段。\n第四段。\n"
    raw_path = root / raw_relative
    raw_path.write_text(raw_text, encoding="utf-8")
    extracted_relative = "00_admin/imports/novel.extracted.txt"
    extracted_path = root / extracted_relative
    extracted_path.write_text(raw_text, encoding="utf-8")
    raw_bytes = raw_path.read_bytes()
    extracted_bytes = extracted_path.read_bytes()

    document_id, version_id, session_id, preview_item_id, range_item_id = (str(uuid.uuid4()) for _ in range(5))
    now = "2026-08-20T00:00:00Z"
    committed_scope = {"source_paragraph_start": 2, "source_paragraph_end": 4, "selection_mode": "EXPLICIT"}
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO source_documents (id,project_id,code,title,source_kind,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?, 'SCRIPT', ?,?, 'author', 1, 'v2')""",
            (document_id, project_id, "SRC_001", "测试原稿", now, now),
        )
        connection.execute(
            """INSERT INTO source_document_versions
            (id,source_document_id,version_no,rel_path,source_name,mime_type,byte_size,sha256,text_sha256,extracted_text_rel,
             parse_status,parser_version,structure_version,metadata_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,?,?,'text/plain',?,?,?,?, 'PARSED', 3, 4, ?, ?,?, 'author', 1, 'v2')""",
            (
                version_id,
                document_id,
                raw_relative,
                "novel.txt",
                len(raw_bytes),
                hashlib.sha256(raw_bytes).hexdigest(),
                hashlib.sha256(extracted_bytes).hexdigest(),
                extracted_relative,
                json.dumps({"paragraph_count": 4, "offset_unit": "UNICODE_CODEPOINT"}, sort_keys=True),
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO import_sessions
            (id,project_id,source_document_version_id,session_kind,status,preview_json,error_summary,
             committed_scope_json,committed_scope_hash,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?, 'SCRIPT', 'COMMITTED', ?, NULL, ?, ?, ?,?, 'author', 1, 'v2')""",
            (
                session_id,
                project_id,
                version_id,
                json.dumps({"paragraph_count": 4}, sort_keys=True),
                json.dumps(committed_scope, sort_keys=True),
                hashlib.sha256(json.dumps(committed_scope, sort_keys=True).encode()).hexdigest(),
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO import_session_items
            (id,session_id,item_type,source_start,source_end,payload_json,validation_status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?, 'DOCUMENT_PREVIEW', 0, ?, ?, 'VALID', ?,?, 'author', 1, 'v2')""",
            (preview_item_id, session_id, len(raw_text), json.dumps({"paragraph_count": 4}, sort_keys=True), now, now),
        )
        connection.execute(
            """INSERT INTO import_session_items
            (id,session_id,item_type,source_start,source_end,payload_json,validation_status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?, 'SOURCE_BODY_RANGE', 5, 20, ?, 'VALID', ?,?, 'author', 1, 'v2')""",
            (range_item_id, session_id, json.dumps(committed_scope, sort_keys=True), now, now),
        )
    return {
        "document_id": document_id,
        "version_id": version_id,
        "session_id": session_id,
        "raw_relative": raw_relative,
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "extracted_relative": extracted_relative,
        "extracted_sha256": hashlib.sha256(extracted_bytes).hexdigest(),
        "committed_scope": json.dumps(committed_scope, sort_keys=True),
    }


def _stage_and_copy(workspace, database, service: ProjectPackageService, project_id: str, source_code: str, target_code: str) -> dict[str, object]:
    exported = service.export(project_id)
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workspace.projects_root / source_code / str(exported["rel_path"]), inbox / f"{target_code}.ldspkg")
    token = str(service.stage_from_inbox(f"{target_code}.ldspkg")["stage_token"])
    return service.import_as_copy(token, code=target_code, title="副本")


def test_copy_round_trips_manuscript_records_ranges_and_default_duration(workspace, database) -> None:
    source = _project(workspace, database, "manuscript_source")
    seeded = _seed_manuscript(workspace, database, str(source["id"]), "manuscript_source")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)

    imported = _stage_and_copy(workspace, database, service, str(source["id"]), "manuscript_source", "manuscript_copy")
    copied_id = str(imported["project_id"])

    assert imported["status"] == "IMPORTED"
    assert imported["missing_fields"] == []
    assert imported["target_duration_ms"] == 180_000
    assert imported["target_duration_source"] == "SOURCE_PROJECT_DEFAULT"
    assert imported["counts"]["source_documents"] == 1
    assert imported["counts"]["source_document_versions"] == 1
    assert imported["counts"]["import_sessions"] == 1
    assert imported["counts"]["import_session_items"] == 2

    with database.connect() as connection:
        project = connection.execute("SELECT target_duration_ms FROM projects WHERE id=?", (copied_id,)).fetchone()
        assert project["target_duration_ms"] == 180_000
        document = connection.execute("SELECT * FROM source_documents WHERE project_id=?", (copied_id,)).fetchone()
        version = connection.execute(
            "SELECT * FROM source_document_versions WHERE source_document_id=?", (document["id"],)
        ).fetchone()
        session = connection.execute("SELECT * FROM import_sessions WHERE project_id=?", (copied_id,)).fetchone()
        items = connection.execute("SELECT * FROM import_session_items WHERE session_id=?", (session["id"],)).fetchall()
        episode_count = connection.execute(
            "SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?", (copied_id,)
        ).fetchone()[0]

    # All identities belong to the copy, never to the source project.
    assert document["id"] != seeded["document_id"]
    assert version["id"] != seeded["version_id"]
    assert session["id"] != seeded["session_id"]
    assert session["source_document_version_id"] == version["id"]
    assert version["source_document_id"] == document["id"]
    assert session["status"] == "COMMITTED"
    assert episode_count == 2

    # The manuscript file and the parse generation identity travel with the copy.
    copied_root = workspace.projects_root / "manuscript_copy"
    assert (copied_root / str(version["rel_path"])).read_bytes() == (workspace.projects_root / "manuscript_source" / seeded["raw_relative"]).read_bytes()
    assert version["sha256"] == seeded["raw_sha256"]
    assert version["parser_version"] == 3 and version["structure_version"] == 4
    assert (copied_root / str(version["extracted_text_rel"])).is_file()

    # The authorised body range is preserved verbatim.
    ranges = [item for item in items if item["item_type"] == "SOURCE_BODY_RANGE"]
    assert len(ranges) == 1
    assert ranges[0]["source_start"] == 5 and ranges[0]["source_end"] == 20
    assert json.dumps(json.loads(str(ranges[0]["payload_json"])), sort_keys=True) == seeded["committed_scope"]
    assert json.dumps(json.loads(str(session["committed_scope_json"])), sort_keys=True) == seeded["committed_scope"]

    # A pending PREVIEW_READY session is never replayed as executable state, and
    # the omission is visible instead of silent.
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO import_sessions
            (id,project_id,source_document_version_id,session_kind,status,preview_json,error_summary,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?, 'SCRIPT', 'PREVIEW_READY', '{}', NULL, ?,?, 'author', 1, 'v2')""",
            (str(uuid.uuid4()), source["id"], seeded["version_id"], "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z"),
        )
    pending_import = _stage_and_copy(workspace, database, service, str(source["id"]), "manuscript_source", "manuscript_copy_pending")
    assert pending_import["counts"]["import_sessions"] == 1
    assert pending_import["counts"]["import_sessions_excluded_pending"] == 1
    assert pending_import["excluded_state"][0]["rule"] == "PENDING_IMPORT_SESSION_NOT_REPLAYED"
    assert len(pending_import["excluded_state"][0]["session_ids"]) == 1


def test_legacy_v2_package_reports_the_missing_manuscript_fields(workspace, database) -> None:
    source = _project(workspace, database, "manuscript_legacy", duration_ms=DEFAULT_PROJECT_TARGET_DURATION_MS)
    _seed_manuscript(workspace, database, str(source["id"]), "manuscript_legacy")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(source["id"]))
    source_package = workspace.projects_root / "manuscript_legacy" / str(exported["rel_path"])

    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    legacy = inbox / "manuscript-legacy-v2.ldspkg"
    with zipfile.ZipFile(source_package) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    state = json.loads(contents["project-state.json"])
    state["schema_version"] = "localdrama.project-state.v2"
    for key in ("source_documents", "source_document_versions", "import_sessions", "import_session_items"):
        state.pop(key, None)
    state["project"].pop("target_duration_ms", None)
    state_bytes = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest = json.loads(contents["package-manifest.json"])
    entry = next(item for item in manifest["entries"] if item["path"] == "project-state.json")
    entry.update(byte_size=len(state_bytes), sha256=hashlib.sha256(state_bytes).hexdigest())
    manifest["state_sha256"] = entry["sha256"]
    manifest["expanded_bytes"] = sum(item["byte_size"] for item in manifest["entries"])
    with zipfile.ZipFile(legacy, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            if name not in {"project-state.json", "package-manifest.json"}:
                archive.writestr(name, content)
        archive.writestr("project-state.json", state_bytes)
        archive.writestr("package-manifest.json", (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())

    inspected = service.inspect_path(legacy)
    assert inspected["state_schema_version"] == "localdrama.project-state.v2"
    assert set(inspected["missing_fields"]) == {
        "source_documents",
        "source_document_versions",
        "import_sessions",
        "import_session_items",
        "project.target_duration_ms",
    }
    token = str(service.stage_from_inbox(legacy.name)["stage_token"])
    imported = service.import_as_copy(token, code="manuscript_legacy_copy", title="旧包副本")

    assert imported["status"] == "IMPORTED"
    assert imported["counts"]["source_documents"] == 0
    assert set(imported["missing_fields"]) == set(inspected["missing_fields"])
    with database.connect() as connection:
        project = connection.execute("SELECT target_duration_ms FROM projects WHERE id=?", (imported["project_id"],)).fetchone()
        audit = connection.execute(
            "SELECT metadata_redacted_json FROM audit_events WHERE subject_id=? AND action='PROJECT_PACKAGE_IMPORTED'",
            (imported["project_id"],),
        ).fetchone()
    assert project["target_duration_ms"] is None
    assert set(json.loads(str(audit["metadata_redacted_json"]))["missing_fields"]) == set(inspected["missing_fields"])


def test_copy_does_not_carry_source_execution_state(workspace, database) -> None:
    source = _project(workspace, database, "manuscript_tokens")
    seeded = _seed_manuscript(workspace, database, str(source["id"]), "manuscript_tokens")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    imported = _stage_and_copy(workspace, database, service, str(source["id"]), "manuscript_tokens", "manuscript_tokens_copy")
    copied_id = str(imported["project_id"])

    with database.connect() as connection:
        copied_sessions = {
            str(row["id"]) for row in connection.execute("SELECT id FROM import_sessions WHERE project_id=?", (copied_id,)).fetchall()
        }
        copied_documents = {
            str(row["id"]) for row in connection.execute("SELECT id FROM source_documents WHERE project_id=?", (copied_id,)).fetchall()
        }
        jobs = connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (copied_id,)).fetchone()[0]
        outbox_payloads = [
            str(row["payload_json"]) for row in connection.execute("SELECT payload_json FROM outbox_events WHERE project_id=?", (copied_id,)).fetchall()
        ]
    assert seeded["session_id"] not in copied_sessions
    assert seeded["document_id"] not in copied_documents
    # A copy starts with no runnable job and never reintroduces the source's
    # worker/lease credentials through its outbox payloads.
    assert jobs == 0
    assert not any(seeded["session_id"] in payload for payload in outbox_payloads)
    assert not any(seeded["document_id"] in payload for payload in outbox_payloads)
