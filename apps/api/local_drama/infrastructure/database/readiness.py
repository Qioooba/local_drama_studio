"""Database readiness truthfulness for the API process.

The API must never claim to be ready when its database cannot serve business
requests.  Before this module existed, ``/health/ready`` only proved that it
could read one row from ``alembic_version``, so a database holding nothing but
that table and the ``0001_bootstrap`` revision was reported as ``HEALTHY`` and
the first ``GET /projects`` returned HTTP 500.

Two independent facts are checked here:

* the revision set stored in ``alembic_version`` equals the migration head of
  the release migration chain (read from Alembic's own script directory, never
  hardcoded), and
* the small set of core tables and columns the API truly needs exists.

Both checks are cheap single-table reads against SQLite, which is why the
result can be cached at startup and reused by every health request instead of
running a full-database ``PRAGMA integrity_check``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory

from local_drama.bootstrap.resource_locator import ResourceLocator

SchemaReadinessState = Literal[
    "no_database",
    "unreadable",
    "schema_incomplete",
    "revision_mismatch",
    "not_applicable",
    "ready",
]

StepStatus = Literal["completed", "skipped", "failed"]

ApplicationReadiness = Literal["READY", "NOT_READY"]

#: Explicit, deliberately small list of the tables the API needs to serve
#: business requests.  This is a capability contract, not a dump of the 230+
#: migrated tables: every entry must be justified by an API request path that
#: fails without it.
CORE_TABLES: tuple[str, ...] = (
    "alembic_version",
    "projects",
    "media_assets",
    "media_versions",
    "selections",
    "review_templates",
    "review_decisions",
    "audit_events",
)

#: Columns inside those tables whose absence breaks an existing request path.
CORE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "projects": ("id", "code", "title", "status", "root_rel", "revision"),
    "media_assets": ("id", "project_id", "media_kind", "selected_version_id", "revision"),
    "media_versions": ("id", "media_asset_id", "stage", "rel_path", "sha256", "integrity_status"),
    "selections": ("id", "media_asset_id", "media_version_id", "selection_type"),
    "review_templates": ("id", "code", "version_no", "subject_type", "items_json"),
    "review_decisions": ("id", "review_template_version_id", "decision", "subject_type", "subject_id"),
    "audit_events": ("event_id", "actor", "action", "subject_type", "subject_id"),
}

_HEAD_CACHE: dict[str, tuple[str, ...]] = {}


class MigrationChainUnavailable(RuntimeError):
    """Raised when the release migration chain itself cannot be read."""


def release_migration_heads(locator: ResourceLocator | None = None) -> tuple[str, ...]:
    """Return the release migration head revisions from the Alembic chain.

    The head is derived from the repository's actual ``alembic/versions``
    directory so it stays correct when migrations are added.  The chain is
    immutable for a running process, so it is cached per migrations root.
    """
    resolved = locator or ResourceLocator.discover()
    migrations_root = resolved.migrations_root
    key = str(migrations_root)
    cached = _HEAD_CACHE.get(key)
    if cached is not None:
        return cached
    config = Config()
    config.set_main_option("script_location", str(migrations_root))
    config.set_main_option("path_separator", "os")
    config.set_main_option("prepend_sys_path", str(resolved.source_repo_root / "apps" / "api"))
    try:
        heads = tuple(sorted(str(head) for head in ScriptDirectory.from_config(config).get_heads()))
    except Exception as error:  # alembic surfaces its own error hierarchy
        raise MigrationChainUnavailable(f"cannot read migration chain at {migrations_root}: {type(error).__name__}") from error
    if not heads:
        raise MigrationChainUnavailable(f"migration chain at {migrations_root} declares no head revision")
    _HEAD_CACHE[key] = heads
    return heads


@dataclass(frozen=True)
class SchemaReadiness:
    """Named outcome of the database schema check.

    ``reason`` is a stable, machine-readable code and is empty exactly when
    ``state`` is ``ready``.
    """

    state: SchemaReadinessState
    database_path: Path
    database_exists: bool
    current_revisions: tuple[str, ...]
    expected_heads: tuple[str, ...]
    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state in {"ready", "not_applicable"}

    @property
    def reason(self) -> str:
        if self.state == "ready":
            return ""
        if self.state == "not_applicable":
            return "DATABASE_NOT_INITIALIZED"
        if self.state == "no_database":
            return "DATABASE_NOT_CONFIGURED"
        if self.state == "unreadable":
            return "DATABASE_UNREADABLE"
        if self.state == "schema_incomplete":
            return "DATABASE_SCHEMA_INCOMPLETE"
        return "DATABASE_MIGRATION_HEAD_MISMATCH"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable structured readiness report."""
        payload: dict[str, object] = {
            "state": self.state,
            "ready": self.ready,
            "reason": self.reason,
            "database_path": str(self.database_path),
            "database_exists": self.database_exists,
            "current_revisions": list(self.current_revisions),
            "expected_heads": list(self.expected_heads),
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.missing_tables:
            payload["missing_tables"] = list(self.missing_tables)
        if self.missing_columns:
            payload["missing_columns"] = list(self.missing_columns)
        return payload


def _is_unsupported_path(path: Path) -> bool:
    """Return True for database targets this process does not own on disk.

    An in-memory target is recreated empty by every connection, so it has no
    persistent schema to validate.  Such targets are reported as
    ``not_applicable`` rather than ``not_ready`` so isolated in-memory tests
    that never migrate do not look like a broken release database.
    """
    return str(path) in {":memory:", ""} or str(path).startswith("file::memory:")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]).casefold() for row in rows}


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]).casefold() for row in rows}


def _revisions(connection: sqlite3.Connection, tables: set[str]) -> tuple[str, ...]:
    if "alembic_version" not in tables:
        return ()
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return tuple(sorted(str(row[0]) for row in rows if row[0] is not None))


def inspect_schema_readiness(
    database_path: Path,
    *,
    expected_heads: Sequence[str] | None = None,
    core_tables: Sequence[str] | None = None,
    core_columns: Mapping[str, Sequence[str]] | None = None,
) -> SchemaReadiness:
    """Inspect one SQLite database without mutating it.

    The result is a pure value object so the caller can cache it at startup and
    refresh it after migration or maintenance.
    """
    tables_contract = tuple(core_tables) if core_tables is not None else CORE_TABLES
    columns_contract = dict(core_columns) if core_columns is not None else dict(CORE_COLUMNS)
    if _is_unsupported_path(database_path):
        return SchemaReadiness(
            state="not_applicable",
            database_path=database_path,
            database_exists=False,
            current_revisions=(),
            expected_heads=(),
            missing_tables=(),
            missing_columns=(),
        )
    if not database_path.is_file():
        return SchemaReadiness(
            state="no_database",
            database_path=database_path,
            database_exists=False,
            current_revisions=(),
            expected_heads=(),
            missing_tables=(),
            missing_columns=(),
        )
    expected = tuple(sorted(expected_heads)) if expected_heads is not None else release_migration_heads()
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return SchemaReadiness(
            state="unreadable",
            database_path=database_path,
            database_exists=True,
            current_revisions=(),
            expected_heads=expected,
            missing_tables=(),
            missing_columns=(),
            detail=type(error).__name__,
        )
    try:
        existing_tables = _table_names(connection)
        missing_tables = tuple(name for name in tables_contract if name.casefold() not in existing_tables)
        missing_columns: list[str] = []
        for table, columns in columns_contract.items():
            if table.casefold() not in existing_tables:
                continue
            present = _column_names(connection, table)
            for column in columns:
                if column.casefold() not in present:
                    missing_columns.append(f"{table}.{column}")
        revisions = _revisions(connection, existing_tables)
    except sqlite3.Error as error:
        return SchemaReadiness(
            state="unreadable",
            database_path=database_path,
            database_exists=True,
            current_revisions=(),
            expected_heads=expected,
            missing_tables=(),
            missing_columns=(),
            detail=type(error).__name__,
        )
    finally:
        connection.close()

    if missing_tables or missing_columns:
        detail_parts = []
        if missing_tables:
            detail_parts.append("missing_tables=" + ",".join(missing_tables))
        if missing_columns:
            detail_parts.append("missing_columns=" + ",".join(missing_columns))
        return SchemaReadiness(
            state="schema_incomplete",
            database_path=database_path,
            database_exists=True,
            current_revisions=revisions,
            expected_heads=expected,
            missing_tables=missing_tables,
            missing_columns=tuple(missing_columns),
            detail=";".join(detail_parts),
        )
    if revisions != expected:
        return SchemaReadiness(
            state="revision_mismatch",
            database_path=database_path,
            database_exists=True,
            current_revisions=revisions,
            expected_heads=expected,
            missing_tables=(),
            missing_columns=(),
            detail=f"database_heads={','.join(revisions) or '<none>'};expected_heads={','.join(expected)}",
        )
    return SchemaReadiness(
        state="ready",
        database_path=database_path,
        database_exists=True,
        current_revisions=revisions,
        expected_heads=expected,
        missing_tables=(),
        missing_columns=(),
    )


@dataclass(frozen=True)
class InitializationStep:
    """One independently recorded application initialization step.

    ``required`` distinguishes built-in bootstrap the API cannot serve without
    (``database_schema``, ``review_templates``) from optional configuration
    such as the local model manifest.  A failed optional step must never stop a
    later required step, and a failed required step is what turns the whole
    application into an explicit not-servable readiness state.
    """

    name: str
    required: bool
    status: StepStatus
    detail: str
    error_type: str = ""
    error_code: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "required": self.required,
            "status": self.status,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.error_type:
            payload["error_type"] = self.error_type
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class InitializationReport:
    """Per-step startup report backing the readiness surface."""

    steps: tuple[InitializationStep, ...]
    schema: SchemaReadiness

    @property
    def ready(self) -> bool:
        return all(step.ok for step in self.steps if step.required)

    @property
    def blocked_by(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps if step.required and not step.ok)

    def step(self, name: str) -> InitializationStep | None:
        return next((step for step in self.steps if step.name == name), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "READY" if self.ready else "NOT_READY",
            "blocked_by": list(self.blocked_by),
            "steps": [step.as_dict() for step in self.steps],
            "schema": self.schema.as_dict(),
        }
