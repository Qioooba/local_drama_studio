from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CURRENT_CONFIG_SCHEMA = 1
ConfigMigration = Callable[[dict[str, Any]], dict[str, Any]]
MIGRATIONS: dict[int, ConfigMigration] = {}


def migrate_config(path: Path, *, backups_root: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "PASS", "mutated": False, "schema_version": CURRENT_CONFIG_SCHEMA, "missing": True}
    raw = json.loads(path.read_text(encoding="utf-8"))
    version = int(raw.get("schema_version", 0))
    if version > CURRENT_CONFIG_SCHEMA:
        raise ValueError(
            f"config schema {version} is newer than supported schema {CURRENT_CONFIG_SCHEMA}"
        )
    if version == CURRENT_CONFIG_SCHEMA:
        return {"status": "PASS", "mutated": False, "schema_version": version}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backups_root.mkdir(parents=True, exist_ok=True)
    backup = backups_root / f"config-before-v{CURRENT_CONFIG_SCHEMA}-{stamp}.json"
    shutil.copy2(path, backup)
    while version < CURRENT_CONFIG_SCHEMA:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(f"no config migration registered from schema {version}")
        raw = migration(raw)
        next_version = int(raw.get("schema_version", version))
        if next_version <= version:
            raise RuntimeError(f"config migration from schema {version} did not advance")
        version = next_version
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(raw, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    return {
        "status": "PASS",
        "mutated": True,
        "schema_version": version,
        "backup": str(backup),
    }


def restore_config(path: Path, source: Path, *, backups_root: Path) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("config restore source is not a regular file")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "schema_version" not in payload:
        raise ValueError("config restore source has no schema version")
    backups_root.mkdir(parents=True, exist_ok=True)
    preserved = None
    if path.is_file():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        preserved = backups_root / f"config-before-restore-{stamp}.json"
        shutil.copy2(path, preserved)
    temporary = path.with_suffix(path.suffix + ".restore.partial")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, temporary)
    temporary.replace(path)
    return {
        "status": "PASS",
        "mutated": True,
        "schema_version": int(payload["schema_version"]),
        "source": str(source),
        "preserved": str(preserved) if preserved else None,
    }
