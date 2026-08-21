"""Rehearse upgrade and backup restore on isolated database copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps" / "api"))
    return config


def _migration_graph() -> tuple[list[str], set[str]]:
    scripts = ScriptDirectory.from_config(_alembic_config())
    return sorted(scripts.get_heads()), {revision.revision for revision in scripts.walk_revisions()}


def _default_evidence_path() -> Path:
    heads, _ = _migration_graph()
    head_label = "-".join(heads) if heads else "no-head"
    date_label = datetime.now(UTC).strftime("%Y-%m-%d")
    return ROOT / "docs" / "evidence" / "g10" / f"upgrade-rollback-rehearsal-{head_label}-{date_label}.json"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_state(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        migrations = sorted(str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version"))
        return {
            "migration": migrations[0] if len(migrations) == 1 else None,
            "migration_heads": migrations,
            "integrity": str(connection.execute("PRAGMA integrity_check").fetchone()[0]),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }


def _upgrade(path: Path) -> None:
    previous = os.environ.get("LOCAL_DRAMA_DATABASE_URL")
    try:
        os.environ["LOCAL_DRAMA_DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
        command.upgrade(_alembic_config(), "heads")
    finally:
        if previous is None:
            os.environ.pop("LOCAL_DRAMA_DATABASE_URL", None)
        else:
            os.environ["LOCAL_DRAMA_DATABASE_URL"] = previous


def rehearse(source: Path, rehearsal_root: Path) -> dict[str, object]:
    rehearsal_root.mkdir(parents=True, exist_ok=False)
    upgrade_copy = rehearsal_root / "upgrade.sqlite3"
    restore_copy = rehearsal_root / "restore.sqlite3"
    shutil.copy2(source, upgrade_copy)
    shutil.copy2(source, restore_copy)
    source_state = _database_state(source)
    expected_heads, known_revisions = _migration_graph()
    source_revision_known = bool(source_state["migration_heads"]) and set(source_state["migration_heads"]).issubset(known_revisions)
    if source_revision_known:
        _upgrade(upgrade_copy)
    upgrade_state = _database_state(upgrade_copy)
    restore_state = _database_state(restore_copy)
    status = "PASS" if (
        source_state["integrity"] == "ok"
        and upgrade_state["integrity"] == "ok"
        and source_revision_known
        and upgrade_state["migration_heads"] == expected_heads
        and restore_state["integrity"] == "ok"
        and restore_state["sha256"] == source_state["sha256"]
    ) else "FAIL"
    return {
        "schema_version": "g10.upgrade_rollback_rehearsal.v3",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": status,
        "scope": "isolated copy of a production pre-migration backup",
        "source_backup": {"path": _display_path(source), **source_state},
        "upgrade_copy": {
            "operation": "alembic upgrade heads on isolated copy" if source_revision_known else "skipped: source revision is outside the migration graph",
            "from_migration": source_state["migration"],
            "to_migration": upgrade_state["migration"],
            "expected_heads": expected_heads,
            **upgrade_state,
        },
        "restore_copy": {
            "operation": "restore source backup to a separate copy",
            **restore_state,
            "matches_source_sha256": restore_state["sha256"] == source_state["sha256"],
        },
        "safety": {
            "production_database_mutated": False,
            "api_contacted": False,
            "comfyui_contacted": False,
            "network_contacted": False,
            "jobs_created": False,
            "temporary_directory_retained": _display_path(rehearsal_root),
        },
        "interpretation": "Current migration upgrade and exact backup restore are verified on isolated copies; this does not authorize final release.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"release-rehearsal-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=_default_evidence_path())
    args = parser.parse_args()
    source = args.source or max((ROOT / "backups").glob("pre_migration_*.sqlite3"), key=lambda path: path.stat().st_mtime)
    result = rehearse(source.resolve(), args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "source": str(source), "output": str(args.output)}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
