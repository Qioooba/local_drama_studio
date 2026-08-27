"""Compatibility wrapper for the structured Maintenance database upgrade."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.bootstrap.resource_locator import ResourceLocator
from local_drama.config import Settings
from local_drama.entrypoints.maintenance import upgrade_database


def migrate(database: Path | None = None) -> None:
    settings = Settings.from_env()
    target = database.resolve() if database is not None else None
    if database is not None:
        data_root = target.parent
        # An explicit database is an isolated maintenance target (pytest,
        # rehearsal, recovery). Its lock and backup roots must not leak back
        # to the configured desktop instance.
        settings = settings.model_copy(
            update={
                "data_root": data_root,
                "instance_root": data_root,
                "work_root": data_root / "work",
                "backups_root": data_root / "backups",
                "cache_root": data_root / "cache",
                "logs_root": data_root / "logs",
            }
        )
    result = upgrade_database(settings, ResourceLocator.discover(), database_path=target)
    if result["status"] != "PASS":
        raise RuntimeError(str(result.get("error") or result["details"]))
    print(f"migration complete database={target or settings.database_path} integrity=ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    migrate(args.database)
