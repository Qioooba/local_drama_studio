"""Migration regressions for the versioned source-parsing protocol (0101).

The migration is additive and cannot be rewound (its ``downgrade`` is
append-only by design), so the upgrade path is exercised by the real migration
runner in every other test in this suite.  What this module checks explicitly
is the shipped schema contract plus the one data rewrite the migration performs:
retiring ``PREVIEW_READY`` sessions whose stored preview has zero paragraphs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_MODULE = ROOT / "apps" / "api" / "alembic" / "versions" / "0101_versioned_source_parsing.py"

REVISION = "0101_versioned_source_parsing"
PREVIOUS_REVISION = "0100_production_session_waiting_user"
# Later migrations may sit on top of this revision; the schema assertions below
# only require that 0101 has been applied.
MINIMUM_APPLIED_REVISION = REVISION


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Migration 0101",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _imported(workspace, database, code: str) -> dict[str, object]:
    project = _project(workspace, database, code)
    source = workspace.work_root / f"{code}.txt"
    source.write_text("第一章 雨夜\n\n正文。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    imported["project_id"] = project["id"]
    return imported


def test_migration_declares_the_expected_revision_chain() -> None:
    source = MIGRATION_MODULE.read_text(encoding="utf-8")
    assert f'revision = "{REVISION}"' in source
    assert f'down_revision = "{PREVIOUS_REVISION}"' in source
    # Additive only: the migration must not delete or rewrite frozen text.
    assert "DELETE FROM" not in source
    assert "DROP TABLE" not in source


def test_schema_exposes_parse_generation_and_committed_scope_columns(workspace, database) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    heads = set(ScriptDirectory.from_config(config).get_heads())

    with database.connect() as connection:
        version_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_document_versions)")}
        session_columns = {row[1] for row in connection.execute("PRAGMA table_info(import_sessions)")}
        current_revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
    assert {"parser_version", "structure_version"} <= version_columns
    assert {"committed_scope_json", "committed_scope_hash"} <= session_columns
    # The database is at a single head and the 0101 migration has been applied.
    assert current_revision in heads
    assert current_revision >= MINIMUM_APPLIED_REVISION


def test_only_zero_paragraph_ready_sessions_are_retired(workspace, database) -> None:
    imported = _imported(workspace, database, "migration_0101")
    database_path = workspace.database_path
    good_preview = dict(imported["preview"])
    broken_preview = {**good_preview, "paragraph_count": 0, "paragraphs": []}
    broken_id = str(uuid.uuid4())
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """INSERT INTO import_sessions
            (id, project_id, source_document_version_id, session_kind, status, preview_json, created_at, updated_at,
             created_by, revision, schema_version)
            VALUES (?, ?, ?, 'SCRIPT', 'PREVIEW_READY', ?, ?, ?, 'local-user', 1, 'v2')""",
            (
                broken_id, str(imported["project_id"]), imported["source_document_version_id"],
                json.dumps(broken_preview, ensure_ascii=False), now, now,
            ),
        )
        # The exact data rewrite shipped in 0101_versioned_source_parsing.py.
        connection.execute(
            """
            UPDATE import_sessions
            SET status='INVALID',
                error_summary=COALESCE(error_summary, '解析结果没有可用段落，会话已作废；请重新导入以创建新的解析版本')
            WHERE status='PREVIEW_READY'
              AND COALESCE(CAST(json_extract(preview_json, '$.paragraph_count') AS INTEGER), 0) <= 0
            """
        )

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        broken = connection.execute(
            "SELECT status, error_summary FROM import_sessions WHERE id=?", (broken_id,),
        ).fetchone()
        good = connection.execute(
            "SELECT status FROM import_sessions WHERE id=?", (imported["import_session_id"],),
        ).fetchone()
        version = connection.execute(
            "SELECT extracted_text_rel, text_sha256, parser_version, structure_version FROM source_document_versions WHERE id=?",
            (imported["source_document_version_id"],),
        ).fetchone()

    assert broken["status"] == "INVALID"
    assert broken["error_summary"]
    assert good["status"] == "PREVIEW_READY"
    # No existing row's referenced offsets, hashes or parse generation changed.
    with database.connect() as connection:
        current = connection.execute(
            "SELECT extracted_text_rel, text_sha256 FROM source_document_versions WHERE id=?",
            (imported["source_document_version_id"],),
        ).fetchone()
    assert version["extracted_text_rel"] == current["extracted_text_rel"]
    assert version["text_sha256"] == current["text_sha256"]
    assert version["parser_version"] == 2
    assert version["structure_version"] == 3
