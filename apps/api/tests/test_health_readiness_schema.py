"""HTTP-04 regression: `/health/ready` must not report HEALTHY for a database that cannot serve requests.

The old probe only proved it could read any row from ``alembic_version``.  A
database holding nothing but that table and the ``0001_bootstrap`` revision was
reported as ``HEALTHY`` with ``database=ok``, and the first ``GET /projects``
returned HTTP 500.

The five acceptance cases are covered below:

1. no database
2. only the version table
3. an old head (one revision behind the release chain)
4. multiple heads
5. a complete, current, migrated database

Only case 5 may be ``HEALTHY``, and its ``/projects`` must be readable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from scripts.migrate import migrate

from local_drama.config import Settings
from local_drama.infrastructure.database.readiness import (
    CORE_COLUMNS,
    CORE_TABLES,
    inspect_schema_readiness,
    release_migration_heads,
)
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

EXPECTED_HEADS = release_migration_heads()


def _workspace(tmp_path: Path) -> Settings:
    workspace = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    workspace.ensure_roots()
    return workspace


def _migrated_workspace(tmp_path: Path) -> Settings:
    workspace = _workspace(tmp_path)
    migrate(workspace.database_path)
    return workspace


def _overwrite_revisions(database_path: Path, revisions: list[str]) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.executemany("INSERT INTO alembic_version (version_num) VALUES (?)", [(revision,) for revision in revisions])


def _version_only_database(database_path: Path) -> None:
    """Reduce a migrated database to only ``alembic_version`` + ``0001_bootstrap``.

    The file is rewritten in place (never deleted) so the check does not race
    with any handle another process may still hold on Windows.
    """
    with sqlite3.connect(database_path) as connection:
        tables = [
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if str(row[0]) not in {"alembic_version", "sqlite_sequence"}
        ]
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            connection.execute(f'DROP TABLE IF EXISTS "{table}"')
        connection.execute("DELETE FROM alembic_version")
        connection.execute("INSERT INTO alembic_version (version_num) VALUES ('0001_bootstrap')")


def test_acceptance_case_one_no_database_is_not_ready(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert not workspace.database_path.exists()

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/health/ready")
        live = client.get("/api/v1/health/live")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "NOT_READY"
    assert payload["checks"]["database"] == "not_configured_until_g2"
    assert "DATABASE_NOT_CONFIGURED" in payload["reasons"]
    # `live` keeps meaning only "the process is alive".
    assert live.status_code == 200
    assert live.json()["status"] == "HEALTHY"


def test_acceptance_case_two_version_table_only_is_not_ready(tmp_path: Path) -> None:
    workspace = _migrated_workspace(tmp_path)
    _version_only_database(workspace.database_path)

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "NOT_READY"
    assert payload["checks"]["database"] == "schema_incomplete"
    assert "DATABASE_SCHEMA_INCOMPLETE" in payload["reasons"]


def test_acceptance_case_three_old_head_is_not_ready(tmp_path: Path) -> None:
    workspace = _migrated_workspace(tmp_path)
    _overwrite_revisions(workspace.database_path, ["0001_g2_core"])

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "NOT_READY"
    assert payload["checks"]["database"] == "migration_pending"
    assert "DATABASE_MIGRATION_HEAD_MISMATCH" in payload["reasons"]


def test_acceptance_case_four_multiple_heads_is_not_ready(tmp_path: Path) -> None:
    workspace = _migrated_workspace(tmp_path)
    _overwrite_revisions(workspace.database_path, [EXPECTED_HEADS[0], "0001_g2_core"])

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "NOT_READY"
    assert "DATABASE_MIGRATION_HEAD_MISMATCH" in payload["reasons"]


def test_acceptance_case_five_complete_database_is_healthy_and_projects_readable(tmp_path: Path) -> None:
    workspace = _migrated_workspace(tmp_path)

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/health/ready")
        projects = client.get("/api/v1/projects")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "HEALTHY"
    assert payload["checks"]["database"] == "ok"
    assert payload["reasons"] == []
    assert all(value == "ok" for value in payload["checks"].values())
    assert projects.status_code == 200


def test_schema_readiness_reports_each_incompatibility_kind(tmp_path: Path) -> None:
    workspace = _migrated_workspace(tmp_path)

    complete = inspect_schema_readiness(workspace.database_path)
    assert complete.state == "ready"
    assert complete.reason == ""
    assert complete.current_revisions == tuple(sorted(EXPECTED_HEADS))
    assert complete.expected_heads == tuple(sorted(EXPECTED_HEADS))

    missing = inspect_schema_readiness(workspace.database_path, core_tables=(*CORE_TABLES, "definitely_absent_table"))
    assert missing.state == "schema_incomplete"
    assert missing.reason == "DATABASE_SCHEMA_INCOMPLETE"
    assert missing.missing_tables == ("definitely_absent_table",)

    narrowed = dict(CORE_COLUMNS)
    narrowed["projects"] = (*CORE_COLUMNS["projects"], "definitely_absent_column")
    missing_column = inspect_schema_readiness(workspace.database_path, core_columns=narrowed)
    assert missing_column.state == "schema_incomplete"
    assert missing_column.missing_columns == ("projects.definitely_absent_column",)

    unreadable = inspect_schema_readiness(workspace.data_root / "not-a-database.sqlite3")
    assert unreadable.state == "no_database"
    assert unreadable.reason == "DATABASE_NOT_CONFIGURED"


def test_release_migration_heads_are_derived_from_the_repo_chain() -> None:
    assert EXPECTED_HEADS, "the release migration chain must declare at least one head"
    versions_root = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(versions_root.glob("*.py")))
    for head in EXPECTED_HEADS:
        assert head in source, f"derived head {head} must exist in the repo migration chain"


def test_ready_reuses_the_cached_startup_verdict(monkeypatch, tmp_path: Path) -> None:
    """`ready` must not re-inspect the database on every request."""
    workspace = _migrated_workspace(tmp_path)
    inspections: list[Path] = []
    original = inspect_schema_readiness

    def counting_inspection(path: Path, **kwargs) -> object:  # noqa: ANN003
        inspections.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr("local_drama.main.inspect_schema_readiness", counting_inspection)
    with TestClient(create_app(workspace)) as client:
        for _ in range(3):
            assert client.get("/api/v1/health/ready").status_code == 200

    # One inspection during startup; the route serves the cached report.
    assert inspections == [workspace.database_path]


def test_database_facade_integrity_check_is_not_used_by_readiness(monkeypatch, tmp_path: Path) -> None:
    """The expensive full-database integrity check must stay out of readiness."""
    workspace = _migrated_workspace(tmp_path)

    def unexpected_integrity_check(self) -> str:  # noqa: ANN001
        raise AssertionError("readiness must not run PRAGMA integrity_check")

    monkeypatch.setattr(Database, "integrity_check", unexpected_integrity_check)
    with TestClient(create_app(workspace)) as client:
        assert client.get("/api/v1/health/ready").status_code == 200
