"""Run an isolated clean-root backup/restore UAT with 100 real WAV files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _write_wav(path: Path, sample_value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = int(sample_value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48_000)
        stream.writeframes(sample * 480)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(root: Path) -> dict[str, Any]:
    source_root = root / "source"
    restore_root = root / "restored"
    source = _settings(source_root)
    source.ensure_roots()
    migrate(source.database_path)
    source_database = Database(source.database_path)
    project = ProjectService(source_database, source.projects_root).create_project(
        code="g10_recovery_100_media",
        title="G10 recovery UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    media_service = MediaService(source_database, source)
    for number in range(100):
        wav = source.work_root / "fixtures" / f"recovery-{number + 1:03d}.wav"
        _write_wav(wav, number - 50)
        registered = media_service.import_file(str(project["id"]), wav, purpose="G10_RECOVERY_UAT", media_kind="AUDIO")
        if registered["probe"].get("probe_status") != "PASS":
            raise RuntimeError(f"real WAV probe failed at sample {number + 1}")

    backup_path = source.backups_root / "recovery-source.sqlite3"
    backup_started = time.perf_counter()
    backup_integrity = online_backup(source.database_path, backup_path)
    backup_seconds = time.perf_counter() - backup_started
    restore_started = time.perf_counter()
    restore = _settings(restore_root)
    restore.ensure_roots()
    shutil.copy2(backup_path, restore.database_path)
    shutil.copytree(source.projects_root, restore.projects_root, dirs_exist_ok=True)
    restore_seconds = time.perf_counter() - restore_started

    restored_database = Database(restore.database_path)
    # Rebuild the restored SQLite indexes before exercising API reads.  This
    # is intentionally performed on the isolated restore copy and records a
    # query-plan row so the recovery evidence covers index reconstruction,
    # rather than only copying an already-indexed database file.
    reindex_started = time.perf_counter()
    with restored_database.transaction() as connection:
        index_count_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchone()[0]
        )
        connection.execute("REINDEX")
        index_count_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchone()[0]
        )
        restored_index_query_plan = connection.execute(
            """EXPLAIN QUERY PLAN SELECT mv.id FROM media_versions mv
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            WHERE ma.project_id=? ORDER BY mv.id LIMIT 1""",
            (project["id"],),
        ).fetchall()
    reindex_seconds = time.perf_counter() - reindex_started
    hash_failures: list[dict[str, str]] = []
    with restored_database.connect() as connection:
        rows = connection.execute(
            """SELECT mv.id, mv.rel_path, mv.sha256, p.root_rel FROM media_versions mv
            JOIN media_assets ma ON ma.id=mv.media_asset_id JOIN projects p ON p.id=ma.project_id
            WHERE ma.project_id=? ORDER BY mv.id""",
            (project["id"],),
        ).fetchall()
    hash_started = time.perf_counter()
    for row in rows:
        path = restore.projects_root / str(row["root_rel"]) / str(row["rel_path"])
        observed = _sha256(path) if path.is_file() and not path.is_symlink() else "MISSING"
        if observed != str(row["sha256"]):
            hash_failures.append({"media_version_id": str(row["id"]), "observed": observed, "expected": str(row["sha256"])})
    hash_seconds = time.perf_counter() - hash_started

    api_started = time.perf_counter()
    with TestClient(create_app(restore)) as client:
        health = client.get("/api/v1/health/ready")
        projects = client.get("/api/v1/projects")
        reviews = client.get(f"/api/v1/reviews/inbox?project_id={project['id']}")
    api_ready_seconds = time.perf_counter() - api_started
    checks = [
        {"code": "ONLINE_BACKUP_INTEGRITY", "passed": backup_integrity == "ok"},
        {"code": "RESTORED_INDEX_REBUILD", "passed": index_count_before > 0 and index_count_after == index_count_before and bool(restored_index_query_plan), "index_count": index_count_after, "query_plan_rows": len(restored_index_query_plan)},
        {"code": "RESTORED_DATABASE_INTEGRITY", "passed": restored_database.integrity_check() == "ok"},
        {"code": "ONE_HUNDRED_MEDIA_HASHES", "passed": len(rows) == 100 and not hash_failures, "checked": len(rows), "failures": len(hash_failures)},
        {"code": "RESTORED_API_READY", "passed": health.status_code == 200 and health.json()["status"] == "HEALTHY"},
        {"code": "RESTORED_PROJECT_READ", "passed": projects.status_code == 200 and any(item["id"] == project["id"] for item in projects.json()["items"])},
        {"code": "RESTORED_REVIEW_ENTRY", "passed": reviews.status_code == 200 and len(reviews.json()["items"]) == 100},
    ]
    return {
        "schema_version": "g10.recovery_restore_uat.v1",
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "scope": "isolated clean-root restore with real locally probed WAV media",
        "checks": checks,
        "rto_seconds": round(restore_seconds + hash_seconds + api_ready_seconds, 3),
        "rpo": "zero fixture records lost from captured online backup",
        "timings": {"online_backup_seconds": round(backup_seconds, 3), "restore_copy_seconds": round(restore_seconds, 3), "index_rebuild_seconds": round(reindex_seconds, 3), "hash_verify_seconds": round(hash_seconds, 3), "api_ready_seconds": round(api_ready_seconds, 3)},
        "hash_failures": hash_failures,
        "runtime_contacted": False,
        "network_contacted": False,
        "production_database_contacted": False,
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"g10-recovery-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "recovery-restore-uat-2026-08-15.json")
    args = parser.parse_args()
    result = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "rto_seconds": result["rto_seconds"], "output": str(args.output)}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
