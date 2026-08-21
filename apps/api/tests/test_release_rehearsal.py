from __future__ import annotations

import hashlib
import os
from pathlib import Path

from scripts import release_rehearsal

from alembic import command


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _migrate_to(path: Path, revision: str) -> None:
    previous = os.environ.get("LOCAL_DRAMA_DATABASE_URL")
    try:
        os.environ["LOCAL_DRAMA_DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
        command.upgrade(release_rehearsal._alembic_config(), revision)
    finally:
        if previous is None:
            os.environ.pop("LOCAL_DRAMA_DATABASE_URL", None)
        else:
            os.environ["LOCAL_DRAMA_DATABASE_URL"] = previous


def test_release_rehearsal_uses_current_graph_and_never_mutates_source(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    _migrate_to(source, "0041_character_voice_bindings")
    before = _sha256(source)

    result = release_rehearsal.rehearse(source, tmp_path / "isolated-rehearsal")

    expected_heads, _ = release_rehearsal._migration_graph()
    assert result["status"] == "PASS"
    assert result["upgrade_copy"]["expected_heads"] == expected_heads
    assert result["upgrade_copy"]["migration_heads"] == expected_heads
    assert result["upgrade_copy"]["from_migration"] == "0041_character_voice_bindings"
    assert result["safety"]["production_database_mutated"] is False
    assert _sha256(source) == before


def test_release_rehearsal_rejects_unknown_source_revision(tmp_path: Path) -> None:
    source = tmp_path / "foreign.sqlite3"
    import sqlite3

    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version(version_num) VALUES ('foreign_revision')")

    result = release_rehearsal.rehearse(source, tmp_path / "foreign-rehearsal")

    assert result["status"] == "FAIL"
    assert result["source_backup"]["migration_heads"] == ["foreign_revision"]
