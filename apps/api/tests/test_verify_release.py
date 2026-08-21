"""Safety contracts for the read-only release verifier."""

from __future__ import annotations

from pathlib import Path

from scripts import verify_release
from scripts.migrate import migrate


def test_release_database_probes_never_create_missing_database(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(verify_release, "DB_PATH", missing)

    assert verify_release._integrity(missing) == "missing"
    assert verify_release._migration_head() is None
    assert not missing.exists()


def test_release_database_probes_match_dynamic_alembic_head(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "head.sqlite3"
    migrate(database)
    monkeypatch.setattr(verify_release, "DB_PATH", database)

    expected = verify_release._expected_migration_heads()
    assert expected == ["0054_character_identity_pack_hardening"]
    assert verify_release._migration_head() == expected[0]
    assert verify_release._integrity(database) == "ok"
