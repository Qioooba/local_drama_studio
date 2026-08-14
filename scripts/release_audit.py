"""Generate a truthful, read-only G10 release-readiness audit.

The audit does not run migrations, start workers, contact ComfyUI, or alter
the database.  It is intentionally allowed to report IN_PROGRESS while the
ordered G7-G9 gates and release artifacts are incomplete.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.g7_readiness import (  # type: ignore[import-not-found]
    G7ReadinessService,
)
from local_drama.application.g8_readiness import (  # type: ignore[import-not-found]
    G8ReadinessService,
)
from local_drama.application.g9_readiness import (  # type: ignore[import-not-found]
    G9ReadinessService,
)
from local_drama.infrastructure.database.sqlite import (  # type: ignore[import-not-found]
    Database,
)

DB_PATH = ROOT / "data" / "local_drama.sqlite3"


def _integrity(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _is_final(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text).get("release_status") == "FINAL"
        except json.JSONDecodeError:
            return False
    return "release_status: FINAL" in text


def _rehearsal_passed(path: Path) -> bool:
    """Return true only for a captured, isolated upgrade/restore rehearsal."""
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    source = evidence.get("source_backup", {})
    restore = evidence.get("restore_copy", {})
    safety = evidence.get("safety", {})
    return (
        evidence.get("status") == "PASS"
        and source.get("integrity") == "ok"
        and restore.get("integrity") == "ok"
        and restore.get("matches_source_sha256") is True
        and safety.get("production_database_mutated") is False
        and safety.get("network_contacted") is False
    )


def _sbom_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"valid": False, "package_count": 0}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"valid": False, "package_count": 0}
    packages = document.get("packages")
    package_count = document.get("package_count")
    valid = (
        document.get("bomFormat") == "SPDX"
        and document.get("specVersion") == "2.3"
        and document.get("release_status") == "DRAFT"
        and isinstance(packages, list)
        and isinstance(package_count, int)
        and package_count == len(packages)
        and isinstance(document.get("lockfile_sha256"), str)
        and len(document["lockfile_sha256"]) == 64
    )
    return {"valid": valid, "package_count": package_count if isinstance(package_count, int) else 0}


def audit() -> dict[str, Any]:
    database = Database(DB_PATH)
    with database.connect() as connection:
        project = connection.execute("SELECT id FROM projects ORDER BY created_at DESC LIMIT 1").fetchone()
        migration = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    if project is None:
        raise RuntimeError("no project exists in the production database")
    project_id = str(project["id"])
    g7 = G7ReadinessService(database).inspect(project_id)
    g8 = G8ReadinessService(database).inspect(project_id)
    g9 = G9ReadinessService(database).inspect(project_id)

    backup_paths = sorted((ROOT / "backups").glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
    required_artifacts = {
        "api_lock": (ROOT / "apps" / "api" / "requirements.lock", False),
        "web_lock": (ROOT / "pnpm-lock.yaml", False),
        "start_script": (ROOT / "scripts" / "start.ps1", False),
        "migration_script": (ROOT / "scripts" / "migrate.py", False),
        "install_upgrade_rollback": (ROOT / "docs" / "release" / "install-upgrade-rollback.md", True),
        "sbom": (ROOT / "docs" / "release" / "sbom.json", True),
        "go_no_go": (ROOT / "docs" / "release" / "go-no-go.md", True),
    }
    rehearsal_path = ROOT / "docs" / "evidence" / "g10" / "upgrade-rollback-rehearsal-2026-08-14.json"
    sbom_path = ROOT / "docs" / "release" / "sbom.json"
    sbom_inventory = _sbom_inventory(sbom_path)
    artifact_state = {
        name: {"exists": path.is_file(), "final": _is_final(path) if requires_final else path.is_file(), "path": path.relative_to(ROOT).as_posix()}
        for name, (path, requires_final) in required_artifacts.items()
    }
    release_artifacts_ready = all(item["final"] for item in artifact_state.values())
    checks = [
        {"code": "DATABASE_INTEGRITY", "passed": _integrity(DB_PATH) == "ok", "observed": _integrity(DB_PATH)},
        {"code": "MIGRATION_HEAD", "passed": bool(migration and str(migration["version_num"]) == "0020_g7_model_license_evidence"), "observed": str(migration["version_num"]) if migration else None},
        {"code": "BACKUP_INTEGRITY", "passed": bool(backup_paths) and all(_integrity(path) == "ok" for path in backup_paths[:5]), "observed_count": min(len(backup_paths), 5)},
        {"code": "ORDERED_G7", "passed": g7["status"] == "PASS", "observed": g7["status"], "next_required_action": g7["next_required_action"]},
        {"code": "ORDERED_G8", "passed": g8["status"] == "PASS", "observed": g8["status"], "next_required_action": g8["next_required_action"]},
        {"code": "ORDERED_G9", "passed": g9["status"] == "PASS", "observed": g9["status"], "next_required_action": g9["next_required_action"]},
        {"code": "UPGRADE_ROLLBACK_REHEARSAL", "passed": _rehearsal_passed(rehearsal_path), "evidence": rehearsal_path.relative_to(ROOT).as_posix()},
        {"code": "SBOM_INVENTORY", "passed": sbom_inventory["valid"], "package_count": sbom_inventory["package_count"], "evidence": sbom_path.relative_to(ROOT).as_posix()},
        {"code": "RELEASE_ARTIFACTS", "passed": release_artifacts_ready, "artifacts": artifact_state},
    ]
    return {
        "schema_version": "g10.release_audit.read_only.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if all(bool(check["passed"]) for check in checks) else "IN_PROGRESS",
        "project_id": project_id,
        "checks": checks,
        "safety": {"runtime_contacted": False, "network_contacted": False, "mutated": False, "migrations_run": False, "jobs_created": False},
        "exit_decision": "G10 release go/no-go is pending; missing or blocked checks must not be bypassed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "release-readiness-2026-08-14.json")
    args = parser.parse_args()
    result = audit()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
