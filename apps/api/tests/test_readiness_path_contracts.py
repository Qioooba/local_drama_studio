"""Schema-readiness path handling (S1).

Reproduced defect on the audit snapshot: ``inspect_schema_readiness`` built its
read-only connection as ``f"file:{path.as_posix()}?mode=ro"``.  For a data
directory named ``work#draft`` SQLite parsed ``#draft/...?mode=ro`` as a URI
*fragment*, so it read the database at ``work`` (a different, non-existent file),
reported every core table as missing, made ``GET /api/v1/health/ready`` answer 503
on a perfectly healthy database — and, because the ``mode=ro`` parameter was lost
with the fragment, created a stray zero-byte ``work`` file next to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_drama.infrastructure.database.readiness import inspect_schema_readiness
from local_drama.infrastructure.database.sqlite import sqlite_readonly_uri


@pytest.mark.parametrize(
    "directory",
    ["plain", "with space", "中文目录", "work#draft", "literal%23", "a%b", "amp&ersand"],
)
def test_readiness_is_ready_for_every_legal_directory_name(
    tmp_path: Path, directory: str, database: object
) -> None:
    target = tmp_path / directory
    target.mkdir(parents=True)
    database_path = target / "local_drama.sqlite3"
    database_path.write_bytes(Path(str(database.path)).read_bytes())

    report = inspect_schema_readiness(database_path)
    assert report.state == "ready", (directory, report.state, report.missing_tables[:5])
    assert report.missing_tables == ()
    assert report.missing_columns == ()


@pytest.mark.parametrize("directory", ["work#draft", "with space", "中文目录"])
def test_readonly_inspection_never_creates_a_file(
    tmp_path: Path, directory: str, database: object
) -> None:
    target = tmp_path / directory
    target.mkdir(parents=True)
    database_path = target / "local_drama.sqlite3"
    source_bytes = Path(str(database.path)).read_bytes()
    database_path.write_bytes(source_bytes)
    before = sorted(item.name for item in target.iterdir())

    inspect_schema_readiness(database_path)

    after = sorted(item.name for item in target.iterdir())
    # WAL sidecars are legitimate; a *new database* or a truncated sibling is not.
    unexpected = [
        name
        for name in after
        if name not in before and not name.startswith("local_drama.sqlite3-")
    ]
    assert unexpected == [], after
    assert database_path.stat().st_size == len(source_bytes)


def test_readonly_uri_percent_encodes_special_characters(tmp_path: Path) -> None:
    uri = sqlite_readonly_uri(tmp_path / "work#draft" / "local_drama.sqlite3")
    assert uri.endswith("?mode=ro")
    assert "#" not in uri.split("?", 1)[0]
    assert "%23" in uri


def test_missing_database_is_still_reported_as_no_database(tmp_path: Path) -> None:
    report = inspect_schema_readiness(tmp_path / "work#draft" / "absent.sqlite3")
    assert report.state == "no_database"
    assert report.database_exists is False
    # The probe must not create the directory or the file it was asked about.
    assert not (tmp_path / "work#draft").exists()
