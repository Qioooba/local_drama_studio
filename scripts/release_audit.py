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
        and source.get("migration") == "0028_audio_binding_authority"
        and evidence.get("upgrade_copy", {}).get("to_migration") == "0029_user_supplied_model_policy"
        and safety.get("production_database_mutated") is False
        and safety.get("network_contacted") is False
    )


def _sbom_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"valid": False, "package_count": 0, "target_runtime_noassertion": None}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"valid": False, "package_count": 0, "target_runtime_noassertion": None}
    packages = document.get("packages")
    package_count = document.get("package_count")
    license_summary = document.get("license_summary")
    target_runtime_noassertion = license_summary.get("noassertion_target_runtime") if isinstance(license_summary, dict) else None
    total_noassertion = license_summary.get("noassertion_total") if isinstance(license_summary, dict) else None
    observed_noassertion = sum(1 for package in packages if isinstance(package, dict) and package.get("licenseConcluded") == "NOASSERTION") if isinstance(packages, list) else -1
    valid = (
        document.get("bomFormat") == "SPDX"
        and document.get("specVersion") == "2.3"
        and document.get("release_status") in {"DRAFT", "FINAL"}
        and isinstance(packages, list)
        and isinstance(package_count, int)
        and package_count == len(packages)
        and isinstance(document.get("lockfile_sha256"), str)
        and len(document["lockfile_sha256"]) == 64
        and isinstance(total_noassertion, int)
        and total_noassertion == observed_noassertion
        and target_runtime_noassertion == 0
    )
    return {
        "valid": valid,
        "package_count": package_count if isinstance(package_count, int) else 0,
        "target_runtime_noassertion": target_runtime_noassertion,
        "lock_only_noassertion": license_summary.get("noassertion_lock_only_non_target_platform") if isinstance(license_summary, dict) else None,
    }


def _local_uat_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    summary = evidence.get("summary", {})
    safety = evidence.get("safety", {})
    return (
        evidence.get("status") == "PASS"
        and summary.get("get_requests", 0) == summary.get("successful_gets", -1)
        and summary.get("safety_passed") is True
        and safety.get("runtime_contacted") is False
        and safety.get("network_contacted") is False
        and safety.get("mutated") is False
        and safety.get("jobs_created") is False
    )


def _metadata_scale_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    fixture = evidence.get("fixture", {})
    return (
        evidence.get("status") == "PASS"
        and fixture.get("episodes") == 60
        and fixture.get("shots") == 800
        and fixture.get("media_assets") == 10_000
        and fixture.get("media_versions") == 10_000
        and fixture.get("playable_media_claimed") is False
        and evidence.get("runtime_contacted") is False
        and evidence.get("network_contacted") is False
        and evidence.get("production_database_contacted") is False
    )


def _security_uat_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    required = {
        "MALICIOUS_ORIGIN_REJECTED",
        "CSRF_TOKEN_REQUIRED",
        "VALID_INSTANCE_TOKEN_ACCEPTED",
        "PROJECT_PATH_ESCAPE_REJECTED",
        "REMOTE_PROVIDER_DISABLED",
        "UNTRUSTED_CUSTOM_NODE_REJECTED",
        "ZERO_PUBLIC_NETWORK_E2E",
        "OPENAPI_HAS_NO_REMOTE_CREDENTIAL_FIELDS",
        "DATABASE_INTEGRITY",
    }
    passed = {str(item.get("code")) for item in evidence.get("checks", []) if item.get("passed") is True}
    return (
        evidence.get("status") == "PASS"
        and required <= passed
        and evidence.get("runtime_contacted") is False
        and evidence.get("public_network_contacted") is False
        and evidence.get("production_database_contacted") is False
        and evidence.get("jobs_created") is False
    )


def _recovery_restore_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    checks = {str(item.get("code")): item.get("passed") is True for item in evidence.get("checks", [])}
    return (
        evidence.get("status") == "PASS"
        and checks.get("ONLINE_BACKUP_INTEGRITY") is True
        and checks.get("RESTORED_DATABASE_INTEGRITY") is True
        and checks.get("ONE_HUNDRED_MEDIA_HASHES") is True
        and checks.get("RESTORED_API_READY") is True
        and checks.get("RESTORED_PROJECT_READ") is True
        and checks.get("RESTORED_REVIEW_ENTRY") is True
        and evidence.get("runtime_contacted") is False
        and evidence.get("network_contacted") is False
        and evidence.get("production_database_contacted") is False
    )


def _stale_job_maintenance_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    before = evidence.get("before", {})
    after = evidence.get("after", {})
    capacity = evidence.get("post_action_capacity", {})
    safety = evidence.get("safety", {})
    return (
        evidence.get("status") == "PASS"
        and before.get("state") == "QUEUED"
        and before.get("active_attempts") == 0
        and after.get("state") == "CANCELLED"
        and capacity.get("queued_count") == 0
        and capacity.get("active_attempt_count") == 0
        and safety.get("comfyui_job_submitted") is False
        and safety.get("comfyui_job_polled") is False
        and safety.get("production_media_changed") is False
    )


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
    g7_pass = g7["status"] == "PASS"
    g8_evidence_pass = g8["status"] == "PASS"
    g9_evidence_pass = g9["status"] == "PASS"
    ordered_g8_pass = g7_pass and g8_evidence_pass
    ordered_g9_pass = ordered_g8_pass and g9_evidence_pass

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
    rehearsal_path = ROOT / "docs" / "evidence" / "g10" / "upgrade-rollback-rehearsal-2026-08-15.json"
    sbom_path = ROOT / "docs" / "release" / "sbom.json"
    sbom_inventory = _sbom_inventory(sbom_path)
    local_uat_path = ROOT / "docs" / "evidence" / "g10" / "local-uat-readonly-2026-08-14.json"
    stale_job_path = ROOT / "docs" / "evidence" / "g10" / "stale-job-maintenance-2026-08-14.json"
    metadata_scale_path = ROOT / "docs" / "evidence" / "g10" / "metadata-scale-uat-2026-08-15.json"
    security_uat_path = ROOT / "docs" / "evidence" / "g10" / "security-uat-2026-08-15.json"
    recovery_restore_path = ROOT / "docs" / "evidence" / "g10" / "recovery-restore-uat-2026-08-15.json"
    artifact_state = {
        name: {"exists": path.is_file(), "final": _is_final(path) if requires_final else path.is_file(), "path": path.relative_to(ROOT).as_posix()}
        for name, (path, requires_final) in required_artifacts.items()
    }
    release_artifacts_ready = all(item["final"] for item in artifact_state.values())
    checks = [
        {"code": "DATABASE_INTEGRITY", "passed": _integrity(DB_PATH) == "ok", "observed": _integrity(DB_PATH)},
        {"code": "MIGRATION_HEAD", "passed": bool(migration and str(migration["version_num"]) == "0029_user_supplied_model_policy"), "observed": str(migration["version_num"]) if migration else None},
        {"code": "BACKUP_INTEGRITY", "passed": bool(backup_paths) and all(_integrity(path) == "ok" for path in backup_paths[:5]), "observed_count": min(len(backup_paths), 5)},
        {"code": "ORDERED_G7", "passed": g7_pass, "observed": g7["status"], "next_required_action": g7["next_required_action"]},
        {
            "code": "ORDERED_G8",
            "passed": ordered_g8_pass,
            "observed": g8["status"],
            "upstream_g7": g7["status"],
            "next_required_action": g8["next_required_action"] if g7_pass else f"WAIT_FOR_G7:{g7['next_required_action']}",
        },
        {
            "code": "ORDERED_G9",
            "passed": ordered_g9_pass,
            "observed": g9["status"],
            "upstream_g7": g7["status"],
            "upstream_g8": g8["status"],
            "next_required_action": (
                g9["next_required_action"]
                if ordered_g8_pass
                else f"WAIT_FOR_ORDERED_G8:{g7['next_required_action'] if not g7_pass else g8['next_required_action']}"
            ),
        },
        {"code": "UPGRADE_ROLLBACK_REHEARSAL", "passed": _rehearsal_passed(rehearsal_path), "evidence": rehearsal_path.relative_to(ROOT).as_posix()},
        {"code": "SBOM_INVENTORY", "passed": sbom_inventory["valid"], "package_count": sbom_inventory["package_count"], "target_runtime_noassertion": sbom_inventory["target_runtime_noassertion"], "lock_only_noassertion": sbom_inventory["lock_only_noassertion"], "evidence": sbom_path.relative_to(ROOT).as_posix()},
        {"code": "LOCAL_UAT_READONLY_BASELINE", "passed": _local_uat_passed(local_uat_path), "evidence": local_uat_path.relative_to(ROOT).as_posix()},
        {"code": "METADATA_SCALE_UAT", "passed": _metadata_scale_passed(metadata_scale_path), "evidence": metadata_scale_path.relative_to(ROOT).as_posix()},
        {"code": "SECURITY_UAT", "passed": _security_uat_passed(security_uat_path), "evidence": security_uat_path.relative_to(ROOT).as_posix()},
        {"code": "CLEAN_ROOT_RECOVERY_UAT", "passed": _recovery_restore_passed(recovery_restore_path), "evidence": recovery_restore_path.relative_to(ROOT).as_posix()},
        {"code": "STALE_JOB_MAINTENANCE", "passed": _stale_job_maintenance_passed(stale_job_path), "evidence": stale_job_path.relative_to(ROOT).as_posix()},
        {"code": "RELEASE_ARTIFACTS", "passed": release_artifacts_ready, "artifacts": artifact_state},
    ]
    all_passed = all(bool(check["passed"]) for check in checks)
    return {
        "schema_version": "g10.release_audit.read_only.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if all_passed else "IN_PROGRESS",
        "project_id": project_id,
        "checks": checks,
        "safety": {"runtime_contacted": False, "network_contacted": False, "mutated": False, "migrations_run": False, "jobs_created": False},
        "exit_decision": "GO for the Windows x64 LOCAL_ONLY source release; user-selected external models and media are not bundled" if all_passed else "G10 release go/no-go is pending; missing or blocked checks must not be bypassed",
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
