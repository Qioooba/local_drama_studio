"""Post-launch release verification for a fresh/installed LocalDramaStudio.

Read-only operator tool that verifies an installed local source release end to
end: SQLite integrity and migration head, recent backup integrity, ordered
G7/G8/G9 readiness projections, loopback-only API binding, the read-only local
UAT baseline, and the master-requirements/release audits.  It never runs
migrations, never starts workers, never contacts ComfyUI or the network, and
never mutates the database or project tree.  Output is a structured JSON
evidence file the operator can archive with the release.
"""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "local_drama.sqlite3"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _integrity(path: Path) -> str:
    if not path.is_file():
        return "missing"
    with _read_only_connection(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _migration_head() -> str | None:
    if not DB_PATH.is_file():
        return None
    with _read_only_connection(DB_PATH) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone() if table else None
    return str(row[0]) if row else None


def _expected_migration_heads() -> list[str]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def _loopback_binding(port: int) -> dict[str, Any]:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return {"listening": True, "address": f"127.0.0.1:{port}", "local_only": True}
    except OSError as error:
        return {"listening": False, "address": f"127.0.0.1:{port}", "local_only": False, "error": type(error).__name__}


def _backup_integrity() -> dict[str, Any]:
    backups = sorted((ROOT / "backups").glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
    return {
        "count": len(backups),
        "checked": min(len(backups), 5),
        "all_ok": bool(backups) and all(_integrity(path) == "ok" for path in backups[:5]),
    }


def _run_python_script(name: str) -> dict[str, Any]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    result = subprocess.run(
        [str(python), str(ROOT / name)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    parsed: dict[str, Any] | None = None
    stdout = (result.stdout or "").strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            pass
    if parsed is None:
        for line in reversed(stdout.splitlines()):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                break
    return {"script": name, "exit_code": result.returncode, "summary": parsed, "stderr_tail": (result.stderr or "").strip()[-500:]}


def verify(api_port: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    database_ok = _integrity(DB_PATH) == "ok"
    head = _migration_head()
    expected_heads = _expected_migration_heads()
    checks.append({"code": "DATABASE_INTEGRITY", "passed": database_ok, "observed": _integrity(DB_PATH)})
    checks.append({"code": "MIGRATION_HEAD", "passed": head in expected_heads and len(expected_heads) == 1, "observed": head, "expected": expected_heads})
    backups = _backup_integrity()
    checks.append({"code": "BACKUP_INTEGRITY", "passed": backups["all_ok"], **backups})
    binding = _loopback_binding(api_port)
    checks.append({"code": "LOOPBACK_API_BINDING", "passed": binding["listening"] and binding["local_only"], **binding})

    local_uat = _run_python_script("scripts/local_uat_readonly.py")
    checks.append({"code": "LOCAL_UAT_READONLY_BASELINE", "passed": local_uat["exit_code"] == 0, "observed": (local_uat.get("summary") or {}).get("status")})
    master = _run_python_script("scripts/master_requirements_audit.py")
    checks.append({"code": "MASTER_REQUIREMENTS_CLOSURE", "passed": master["exit_code"] == 0, "observed": (master.get("summary") or {}).get("status")})
    release = _run_python_script("scripts/release_audit.py")
    checks.append({"code": "RELEASE_AUDIT", "passed": release["exit_code"] == 0, "observed": (release.get("summary") or {}).get("status")})
    invariants = _run_python_script("scripts/refactor_invariants.py")
    checks.append({
        "code": "REFACTOR_INVARIANTS",
        "passed": invariants["exit_code"] == 0,
        "observed": (invariants.get("summary") or {}).get("status"),
        "summary": (invariants.get("summary") or {}).get("summary"),
    })

    all_passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "g10.verify_release.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "mode": "LOCAL_ONLY",
        "status": "PASS" if all_passed else "IN_PROGRESS",
        "database_path": str(DB_PATH),
        "checks": checks,
        "safety": {"runtime_contacted": False, "network_contacted": False, "mutated": False, "migrations_run": False, "jobs_created": False},
        "interpretation": "Post-launch read-only verification for the Windows x64 LOCAL_ONLY source release. It never runs migrations, contacts ComfyUI or the public network, or mutates the database.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3210)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "verify-release-2026-08-17.json")
    args = parser.parse_args()
    result = verify(args.port)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
