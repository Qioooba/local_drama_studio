from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.migrate import migrate

from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


@pytest.fixture()
def workspace(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )


@pytest.fixture()
def database(workspace: Settings) -> Database:
    workspace.ensure_roots()
    migrate(workspace.database_path)
    return Database(workspace.database_path)
