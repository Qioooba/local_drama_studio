"""Run Alembic with a preflight backup for the configured SQLite database."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

Settings = import_module("local_drama.config").Settings
online_backup = import_module(
    "local_drama.infrastructure.database.backup"
).online_backup


def migrate(database: Path | None = None) -> None:
    settings = Settings.from_env()
    target = database or settings.database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        integrity = online_backup(
            target,
            settings.backups_root
            / f"pre_migration_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3",
        )
        if integrity != "ok":
            raise RuntimeError(f"migration preflight backup failed: {integrity}")
    os.environ["LOCAL_DRAMA_DATABASE_URL"] = f"sqlite:///{target.as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    # Resolve Alembic paths from the repository root so this entry point is
    # stable when invoked from the root, apps/api, or pytest.
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps" / "api"))
    command.upgrade(config, "head")
    print(f"migration complete database={target} integrity=ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    migrate(args.database)
