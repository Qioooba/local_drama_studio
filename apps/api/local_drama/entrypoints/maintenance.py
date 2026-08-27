from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from local_drama.bootstrap.config_migrations import migrate_config, restore_config
from local_drama.bootstrap.resource_locator import ResourceLocator
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.platform import create_platform_services

SCHEMA = "localdrama.maintenance-result.v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _result(operation: str, status: str, *, mutated: bool, details: dict[str, Any], error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "operation": operation,
        "status": status,
        "observed_at": _now(),
        "mutated": mutated,
        "details": details,
        "error": error,
    }


def _alembic_config(locator: ResourceLocator) -> Config:
    config = Config()
    config.set_main_option("script_location", str(locator.migrations_root))
    config.set_main_option("prepend_sys_path", str(locator.source_repo_root / "apps" / "api"))
    return config


def _expected_heads(locator: ResourceLocator) -> list[str]:
    return sorted(ScriptDirectory.from_config(_alembic_config(locator)).get_heads())


def _database_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path)}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        table = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'").fetchone()
        heads = sorted(str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version")) if table else []
    return {
        "exists": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "integrity": integrity,
        "migration_heads": heads,
    }


@contextmanager
def _maintenance_lock(settings: Settings, *, database_path: Path | None = None) -> Iterator[Path]:
    """Serialize mutations that share authority without blocking unrelated databases.

    Database maintenance is scoped to the database file. This keeps production
    upgrades mutually exclusive while allowing rehearsals, tests, and isolated
    instances with their own database to run independently. Recovery-set
    creation still uses the instance-wide lock because it snapshots database,
    config, and project files as one unit.
    """
    if database_path is None:
        lock = settings.instance_root / "runtime" / "maintenance.lock"
    else:
        target = database_path.resolve()
        lock = target.parent / f"{target.name}.maintenance.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(f"maintenance lock already exists: {lock}") from error
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "started_at": _now()}).encode("utf-8"))
        os.close(descriptor)
        yield lock
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


def _upgrade_database(path: Path, locator: ResourceLocator) -> None:
    previous = os.environ.get("LOCAL_DRAMA_DATABASE_URL")
    try:
        os.environ["LOCAL_DRAMA_DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
        command.upgrade(_alembic_config(locator), "heads")
    finally:
        if previous is None:
            os.environ.pop("LOCAL_DRAMA_DATABASE_URL", None)
        else:
            os.environ["LOCAL_DRAMA_DATABASE_URL"] = previous


def _restore_database_file(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".rollback.partial")
    shutil.copy2(source, temporary)
    if _database_state(temporary).get("integrity") != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("rollback database copy failed integrity check")
    temporary.replace(destination)


def inspect_database(settings: Settings, locator: ResourceLocator) -> dict[str, Any]:
    state = _database_state(settings.database_path)
    state["expected_heads"] = _expected_heads(locator)
    compatible = not state["exists"] or (
        state.get("integrity") == "ok" and state.get("migration_heads") == state["expected_heads"]
    )
    return _result("db.inspect", "PASS" if compatible else "BLOCKED", mutated=False, details=state)


def backup_database(settings: Settings, destination: Path | None = None) -> dict[str, Any]:
    source = settings.database_path
    if not source.is_file():
        return _result("db.backup", "BLOCKED", mutated=False, details={"database": str(source)}, error={"code": "DATABASE_NOT_FOUND"})
    target = destination or settings.backups_root / f"manual_{_stamp()}.sqlite3"
    integrity = online_backup(source, target)
    return _result(
        "db.backup",
        "PASS",
        mutated=True,
        details={"source": str(source), "backup": {**_database_state(target), "integrity": integrity}},
    )


def rehearse_upgrade(settings: Settings, locator: ResourceLocator, source: Path | None = None) -> dict[str, Any]:
    database = source or settings.database_path
    if not database.is_file():
        return _result("db.rehearse-upgrade", "BLOCKED", mutated=False, details={"database": str(database)}, error={"code": "DATABASE_NOT_FOUND"})
    root = settings.work_root / "maintenance" / f"rehearsal-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    copy = root / "database.sqlite3"
    online_backup(database, copy)
    before = _database_state(copy)
    _upgrade_database(copy, locator)
    after = _database_state(copy)
    expected = _expected_heads(locator)
    passed = after.get("integrity") == "ok" and after.get("migration_heads") == expected
    return _result(
        "db.rehearse-upgrade",
        "PASS" if passed else "FAIL",
        mutated=True,
        details={"source": _database_state(database), "before": before, "after": after, "expected_heads": expected, "rehearsal_root": str(root)},
    )


def upgrade_database(
    settings: Settings,
    locator: ResourceLocator,
    *,
    database_path: Path | None = None,
) -> dict[str, Any]:
    target_database = database_path.resolve() if database_path is not None else settings.database_path
    settings.ensure_roots()
    with _maintenance_lock(settings, database_path=target_database):
        source_exists = target_database.is_file()
        expected = _expected_heads(locator)
        current = _database_state(target_database)
        if (
            source_exists
            and current.get("integrity") == "ok"
            and current.get("migration_heads") == expected
        ):
            return _result(
                "db.upgrade",
                "PASS",
                mutated=False,
                details={"database": current, "expected_heads": expected, "already_current": True},
            )
        backup_path: Path | None = None
        rehearsal: dict[str, Any] | None = None
        if source_exists:
            backup_path = settings.backups_root / f"pre_migration_{_stamp()}.sqlite3"
            online_backup(target_database, backup_path)
            rehearsal = rehearse_upgrade(settings, locator, backup_path)
            if rehearsal["status"] != "PASS":
                return _result(
                    "db.upgrade",
                    "FAIL",
                    mutated=True,
                    details={"backup": str(backup_path), "rehearsal": rehearsal},
                    error={"code": "MIGRATION_REHEARSAL_FAILED"},
                )
        try:
            _upgrade_database(target_database, locator)
            state = _database_state(target_database)
            passed = state.get("integrity") == "ok" and state.get("migration_heads") == expected
            if not passed:
                raise RuntimeError("post-migration validation failed")
        except Exception as error:
            if backup_path is not None:
                _restore_database_file(backup_path, target_database)
            else:
                target_database.unlink(missing_ok=True)
            return _result(
                "db.upgrade",
                "FAIL",
                mutated=True,
                details={
                    "database": _database_state(target_database),
                    "expected_heads": expected,
                    "backup": str(backup_path) if backup_path else None,
                    "rehearsal": rehearsal,
                    "rolled_back": True,
                },
                error={"code": "MIGRATION_FAILED", "message": str(error)[:500]},
            )
        return _result(
            "db.upgrade",
            "PASS" if passed else "FAIL",
            mutated=True,
            details={"database": state, "expected_heads": expected, "backup": str(backup_path) if backup_path else None, "rehearsal": rehearsal},
        )


def restore_database(settings: Settings, source: Path) -> dict[str, Any]:
    source = source.resolve()
    source_state = _database_state(source)
    if not source_state.get("exists") or source_state.get("integrity") != "ok":
        return _result("db.restore", "BLOCKED", mutated=False, details={"source": source_state}, error={"code": "RESTORE_SOURCE_INVALID"})
    settings.ensure_roots()
    with _maintenance_lock(settings, database_path=settings.database_path):
        preserved = None
        if settings.database_path.is_file():
            preserved = settings.backups_root / f"pre_restore_{_stamp()}.sqlite3"
            online_backup(settings.database_path, preserved)
        temporary = settings.database_path.with_suffix(".restore.partial")
        shutil.copy2(source, temporary)
        if _database_state(temporary).get("integrity") != "ok":
            temporary.unlink(missing_ok=True)
            raise RuntimeError("restored database copy failed integrity check")
        temporary.replace(settings.database_path)
        restored = _database_state(settings.database_path)
    return _result(
        "db.restore",
        "PASS",
        mutated=True,
        details={"source": source_state, "restored": restored, "preserved_current": str(preserved) if preserved else None},
    )


def create_recovery_set(settings: Settings, locator: ResourceLocator) -> dict[str, Any]:
    settings.ensure_roots()
    recovery_id = _stamp()
    root = settings.backups_root / "recovery-sets" / recovery_id
    root.mkdir(parents=True, exist_ok=False)
    manifest_path = root / "recovery-manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "localdrama.recovery-set.v1",
        "recovery_id": recovery_id,
        "status": "BUILDING",
        "created_at": _now(),
        "database": None,
        "config": None,
        "projects_manifest": None,
        "release_version": settings.app_version,
        "expected_heads": _expected_heads(locator),
    }
    _atomic_json(manifest_path, manifest)
    with _maintenance_lock(settings):
        if settings.database_path.is_file():
            database_target = root / "database" / "local_drama.sqlite3"
            online_backup(settings.database_path, database_target)
            manifest["database"] = _database_state(database_target)
        if settings.config_path and settings.config_path.is_file():
            config_target = root / "config" / "config.json"
            config_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(settings.config_path, config_target)
            manifest["config"] = {"path": str(config_target.relative_to(root)), "sha256": _sha256(config_target)}
        projects_manifest = root / "projects-manifest.jsonl"
        count = 0
        projects_manifest.parent.mkdir(parents=True, exist_ok=True)
        with projects_manifest.open("w", encoding="utf-8", newline="\n") as stream:
            if settings.projects_root.is_dir():
                for path in sorted(settings.projects_root.rglob("*")):
                    if not path.is_file() or path.is_symlink():
                        continue
                    relative = path.relative_to(settings.projects_root).as_posix()
                    stream.write(json.dumps({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}, ensure_ascii=False) + "\n")
                    count += 1
            stream.flush()
            os.fsync(stream.fileno())
        manifest["projects_manifest"] = {"path": projects_manifest.name, "items": count, "sha256": _sha256(projects_manifest)}
        manifest["status"] = "COMPLETE"
        manifest["completed_at"] = _now()
        _atomic_json(manifest_path, manifest)
    return _result("recovery.create", "PASS", mutated=True, details={"root": str(root), "manifest": manifest})


def restore_recovery_set(settings: Settings, root: Path) -> dict[str, Any]:
    recovery_root = root.resolve()
    manifest_path = recovery_root / "recovery-manifest.json"
    if not manifest_path.is_file():
        return _result(
            "recovery.restore",
            "BLOCKED",
            mutated=False,
            details={"root": str(recovery_root)},
            error={"code": "RECOVERY_MANIFEST_NOT_FOUND"},
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE":
        return _result(
            "recovery.restore",
            "BLOCKED",
            mutated=False,
            details={"root": str(recovery_root)},
            error={"code": "RECOVERY_SET_INCOMPLETE"},
        )
    database = manifest.get("database")
    if isinstance(database, dict):
        source = recovery_root / "database" / "local_drama.sqlite3"
        if _sha256(source) != database.get("sha256"):
            raise RuntimeError("recovery database checksum mismatch")
        database_result = restore_database(settings, source)
    else:
        preserved = None
        with _maintenance_lock(settings, database_path=settings.database_path):
            if settings.database_path.is_file():
                preserved = settings.backups_root / f"failed_fresh_upgrade_{_stamp()}.sqlite3"
                online_backup(settings.database_path, preserved)
                settings.database_path.unlink()
        database_result = {"status": "PASS", "removed_new_database": True, "preserved": str(preserved) if preserved else None}
    return _result(
        "recovery.restore",
        "PASS",
        mutated=True,
        details={"root": str(recovery_root), "database": database_result},
    )


def _redact(value: Any, key: str = "") -> Any:
    sensitive = {"api_key", "secret", "password", "token", "credential"}
    if any(part in key.casefold() for part in sensitive):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def create_diagnostics_bundle(
    settings: Settings,
    locator: ResourceLocator,
    destination: Path | None = None,
) -> dict[str, Any]:
    settings.ensure_roots()
    target = destination or settings.work_root / "diagnostics" / f"diagnostics-{_stamp()}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    log_inventory = []
    if settings.logs_root.is_dir():
        for path in sorted(settings.logs_root.glob("*")):
            if path.is_file() and not path.is_symlink():
                log_inventory.append(
                    {"name": path.name, "bytes": path.stat().st_size, "modified_ns": path.stat().st_mtime_ns}
                )
    config: dict[str, Any] = {}
    if settings.config_path and settings.config_path.is_file():
        try:
            loaded = json.loads(settings.config_path.read_text(encoding="utf-8"))
            config = _redact(loaded)
        except (OSError, json.JSONDecodeError):
            config = {"status": "unreadable"}
    facts = {
        "schema_version": "localdrama.diagnostics.v1",
        "created_at": _now(),
        "release": {
            "version": settings.app_version,
            "release_root_name": settings.release_root.name,
            "expected_heads": _expected_heads(locator),
        },
        "database": _database_state(settings.database_path),
        "platform": create_platform_services(settings).public_capabilities(),
        "storage": {
            "data_free_bytes": shutil.disk_usage(settings.data_root).free,
            "projects_free_bytes": shutil.disk_usage(settings.projects_root).free,
        },
        "logs": log_inventory,
    }
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("facts.json", json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        archive.writestr("config.redacted.json", json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(target)
    return _result(
        "diagnostics.create",
        "PASS",
        mutated=True,
        details={"bundle": str(target), "bytes": target.stat().st_size, "sha256": _sha256(target)},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-drama-maintenance")
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config-upgrade")
    config_restore = commands.add_parser("config-restore")
    config_restore.add_argument("source", type=Path)
    commands.add_parser("inspect")
    backup = commands.add_parser("backup")
    backup.add_argument("--destination", type=Path)
    rehearse = commands.add_parser("rehearse-upgrade")
    rehearse.add_argument("--source", type=Path)
    commands.add_parser("upgrade")
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    commands.add_parser("recovery-create")
    recovery_restore = commands.add_parser("recovery-restore")
    recovery_restore.add_argument("source", type=Path)
    diagnostics = commands.add_parser("diagnostics-create")
    diagnostics.add_argument("--destination", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.config:
        os.environ["LOCAL_DRAMA_CONFIG"] = args.config
    locator = ResourceLocator.discover()
    try:
        if args.command in {"config-upgrade", "config-restore"}:
            if args.command == "config-upgrade":
                config_result = migrate_config(
                    locator.config_path,
                    backups_root=locator.instance_root / "backups" / "config",
                )
            else:
                config_result = restore_config(
                    locator.config_path,
                    args.source,
                    backups_root=locator.instance_root / "backups" / "config",
                )
            result = _result(
                f"config.{args.command.removeprefix('config-')}",
                str(config_result["status"]),
                mutated=bool(config_result["mutated"]),
                details=config_result,
            )
        else:
            settings = Settings.from_env()
            if args.command == "inspect":
                result = inspect_database(settings, locator)
            elif args.command == "backup":
                result = backup_database(settings, args.destination)
            elif args.command == "rehearse-upgrade":
                result = rehearse_upgrade(settings, locator, args.source)
            elif args.command == "upgrade":
                result = upgrade_database(settings, locator)
            elif args.command == "restore":
                result = restore_database(settings, args.source)
            elif args.command == "recovery-create":
                result = create_recovery_set(settings, locator)
            elif args.command == "recovery-restore":
                result = restore_recovery_set(settings, args.source)
            else:
                result = create_diagnostics_bundle(settings, locator, args.destination)
    except Exception as error:
        result = _result(
            f"maintenance.{args.command}",
            "FAIL",
            mutated=args.command not in {"inspect"},
            details={},
            error={"code": type(error).__name__, "message": str(error)[:500]},
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
