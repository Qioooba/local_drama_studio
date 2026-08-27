from __future__ import annotations

from pathlib import Path

import pytest

from local_drama.config import Settings
from local_drama.entrypoints.maintenance import _maintenance_lock


def test_database_maintenance_lock_is_scoped_to_the_database(tmp_path: Path) -> None:
    settings = Settings(instance_root=tmp_path)
    first = tmp_path / "first" / "database.sqlite3"
    second = tmp_path / "second" / "database.sqlite3"

    with _maintenance_lock(settings, database_path=first) as first_lock:
        assert first_lock == first.parent / "database.sqlite3.maintenance.lock"
        with _maintenance_lock(settings, database_path=second) as second_lock:
            assert second_lock == second.parent / "database.sqlite3.maintenance.lock"

        with pytest.raises(RuntimeError, match="maintenance lock already exists"):
            with _maintenance_lock(settings, database_path=first):
                pass

    assert not first_lock.exists()
