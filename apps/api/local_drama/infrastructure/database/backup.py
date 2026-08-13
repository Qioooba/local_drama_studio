from __future__ import annotations

import sqlite3
from pathlib import Path


def online_backup(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    temporary.replace(destination)
    check = sqlite3.connect(destination)
    try:
        result = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"backup integrity_check failed: {result}")
    return result
