"""Offline Windows Server V2 Model Platform release gate.

This check reads only machine configuration and SQLite metadata. It never
scans model files, contacts runtimes, creates Jobs, or exposes absolute paths
in its JSON evidence.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from local_drama.bootstrap.config_loader import load_machine_config
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import is_reparse_point
from local_drama.model_platform.application.production_execution_registry import (
    production_execution_handlers,
)

ROOT = Path(__file__).resolve().parents[1]


def expected_heads() -> list[str]:
    contract = json.loads(_migration_contract_path().read_text(encoding="utf-8"))
    return sorted(str(item) for item in contract["expected_heads"])


def _migration_contract_path() -> Path:
    """Locate the contract in source checkout and immutable release payloads.

    The release builder deliberately ships the gate in ``payload/app`` and
    the verified migration contract in ``payload/``.  Source execution keeps
    the authored contract under ``docs/release``.  Accept only those two
    release-owned locations; the instance directory must never supply this
    authority.
    """
    candidates = (
        ROOT / "docs" / "release" / "migration-contract.json",
        ROOT / "migration-contract.json",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def verify(*, config_path: Path, release_root: Path, instance_root: Path, database_path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        config = load_machine_config(config_path, release_root=release_root, instance_root=instance_root)
    except ValueError as error:
        return _result(checks + [_check("MACHINE_CONFIG", False, str(error))])
    if config is None:
        return _result(checks + [_check("MACHINE_CONFIG", False, "配置文件不存在")])
    root = config.runtime.model_root
    libraries = config.runtime.model_library_roots
    root_ok = root is not None and root.is_absolute() and not str(root).startswith("\\\\") and root.is_dir() and not is_reparse_point(root)
    checks.append(_check("MODEL_ROOT", root_ok, "configured local directory" if root_ok else "模型根目录未配置、不可用、UNC 或 reparse"))
    expected_libraries = {"comfyui", "pytorch", "ollama", "audio"}
    library_names = {path.name.casefold() for path in libraries}
    libraries_ok = root is not None and expected_libraries <= library_names and all(path.is_dir() and not is_reparse_point(path) for path in libraries)
    checks.append(_check("MODEL_LIBRARIES", libraries_ok, "four managed libraries present" if libraries_ok else "四类受管模型库不完整或不安全"))
    operational = (root / "downloads", root / "staging", root / "quarantine") if root else ()
    isolation_ok = root is not None and all(path.is_dir() and path not in libraries for path in operational)
    checks.append(_check("MODEL_OPERATIONAL_ISOLATION", isolation_ok, "operational areas are not scanner roots" if isolation_ok else "下载/暂存/隔离目录不安全"))
    checks.extend(_database_checks(database_path))
    return _result(checks)


def _database_checks(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return [_check("DATABASE", False, "数据库不存在")]
    try:
        with sqlite3.connect(path) as connection:
            heads = sorted(str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version"))
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            handler_coverage = _published_profile_handler_coverage(connection, tables)
    except sqlite3.Error as error:
        return [_check("DATABASE", False, f"SQLite 不可读取：{type(error).__name__}")]
    return [
        _check("DATABASE_INTEGRITY", integrity == "ok", integrity),
        _check("MIGRATION_HEAD", heads == expected_heads(), "migration head matches contract" if heads == expected_heads() else "migration head does not match contract"),
        _check("PROJECT_KNOWLEDGE_V2_SCHEMA", {"mp_project_knowledge_index_runs", "mp_project_knowledge_index_batches", "mp_project_knowledge_vectors"} <= tables, "V2 knowledge tables present"),
        handler_coverage,
    ]


def _published_profile_handler_coverage(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    """Fail a release before a published V2 Profile reaches a handlerless Worker.

    This is an offline structural check. It reads only the published Profile's
    capability and immutable adapter contract, then compares that pair with
    the code-owned production registry. No runtime is contacted and adapter
    names are intentionally not emitted in the gate result.
    """
    required = {
        "mp_profile_publications",
        "mp_execution_profile_versions",
        "mp_capability_definitions",
        "mp_adapter_binding_contract_versions",
    }
    if not required <= tables:
        return _check("PUBLISHED_PROFILE_HANDLER_COVERAGE", False, "profile handler tables missing")
    rows = connection.execute(
        """SELECT capability.code AS capability_code, binding.adapter_code AS adapter_code
           FROM mp_profile_publications publication
           JOIN mp_execution_profile_versions profile ON profile.id=publication.execution_profile_version_id
           JOIN mp_capability_definitions capability ON capability.id=profile.capability_definition_id
           JOIN mp_adapter_binding_contract_versions binding ON binding.id=profile.adapter_binding_contract_version_id
           WHERE publication.status='PUBLISHED'"""
    ).fetchall()
    registry = production_execution_handlers()
    unsupported = 0
    for capability_code, adapter_code in rows:
        try:
            registry.resolve(str(capability_code), str(adapter_code))
        except DomainRuleError:
            unsupported += 1
    return _check(
        "PUBLISHED_PROFILE_HANDLER_COVERAGE",
        unsupported == 0,
        f"{len(rows)} published Profiles have declared production handlers" if unsupported == 0 else f"{unsupported} published Profile(s) lack a declared production handler",
    )


def _check(code: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"code": code, "status": "PASS" if passed else "FAIL", "detail": detail}


def _result(checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": "localdramastudio.model-platform-release-gate.v1", "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL", "checks": checks, "runtime_contacted": False, "network_contacted": False, "mutated": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--instance-root", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    result = verify(config_path=args.config, release_root=args.release_root, instance_root=args.instance_root, database_path=args.database)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
