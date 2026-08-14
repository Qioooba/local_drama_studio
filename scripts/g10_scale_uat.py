"""Run the isolated G10 60-episode/800-shot/10k-media metadata-scale UAT.

The fixture uses the real migrated SQLite schema, FastAPI application, and read
models. Synthetic media rows intentionally remain ``integrity_status=UNKNOWN``
and never stand in for playable media or release evidence. No runtime or network
adapter is contacted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate

EPISODE_COUNT = 60
SHOT_COUNT = 800
MEDIA_COUNT = 10_000


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _stable_id(kind: str, number: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:g10-scale:{kind}:{number}"))


def build_fixture(root: Path) -> tuple[Settings, str, list[str], list[str], float]:
    started = time.perf_counter()
    settings = _settings(root)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="g10_scale_60ep",
        title="G10 isolated metadata scale UAT",
        episode_count=EPISODE_COUNT,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    with database.connect() as connection:
        episodes = connection.execute(
            "SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY e.number",
            (project["id"],),
        ).fetchall()
    episode_ids = [str(row["id"]) for row in episodes]
    now = datetime.now(UTC).isoformat()
    shot_ids: list[str] = []
    shot_rows: list[tuple[Any, ...]] = []
    revision_rows: list[tuple[Any, ...]] = []
    for number in range(SHOT_COUNT):
        shot_id = _stable_id("shot", number)
        revision_id = _stable_id("shot-revision", number)
        episode_id = episode_ids[number % EPISODE_COUNT]
        order_key = str(number // EPISODE_COUNT + 1)
        shot_ids.append(shot_id)
        shot_rows.append(
            (shot_id, episode_id, f"S{number + 1:04d}", order_key, 4_500, "SCALE_UAT", revision_id, now, now)
        )
        revision_rows.append(
            (revision_id, shot_id, json.dumps({"keywords": ["g10", "scale"], "prompt_snapshot": f"scale shot {number + 1}"}), now, now)
        )
    asset_rows: list[tuple[Any, ...]] = []
    version_rows: list[tuple[Any, ...]] = []
    for number in range(MEDIA_COUNT):
        asset_id = _stable_id("asset", number)
        version_id = _stable_id("media-version", number)
        owner_id = shot_ids[number % SHOT_COUNT]
        digest = hashlib.sha256(version_id.encode("utf-8")).hexdigest()
        asset_rows.append((asset_id, project["id"], owner_id, now, now))
        version_rows.append(
            (version_id, asset_id, f"99_g10_fixture/media-{number + 1:05d}.mp4", digest, now, now)
        )
    with database.transaction() as connection:
        connection.executemany(
            """INSERT INTO shots
            (id, episode_id, code, order_key, target_duration_ms, shot_type, status, current_revision_id,
             created_at, updated_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, 'g10-scale-uat')""",
            shot_rows,
        )
        connection.executemany(
            """INSERT INTO shot_revisions
            (id, shot_id, revision_no, fields_json, is_frozen, created_at, updated_at, created_by)
            VALUES (?, ?, 1, ?, 0, ?, ?, 'g10-scale-uat')""",
            revision_rows,
        )
        connection.executemany(
            """INSERT INTO media_assets
            (id, project_id, owner_type, owner_id, purpose, media_kind, version_counter,
             created_at, updated_at, created_by)
            VALUES (?, ?, 'SHOT', ?, 'SCALE_METADATA_ONLY', 'VIDEO', 1, ?, ?, 'g10-scale-uat')""",
            asset_rows,
        )
        connection.executemany(
            """INSERT INTO media_versions
            (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
             integrity_status, probe_json, created_at, updated_at, created_by)
            VALUES (?, ?, 1, 1, 'PROXY', ?, 'video/mp4', 0, ?, 'UNKNOWN', '{}', ?, ?, 'g10-scale-uat')""",
            version_rows,
        )
    return settings, str(project["id"]), episode_ids, shot_ids, time.perf_counter() - started


def _percentile_95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def run_uat(root: Path) -> dict[str, Any]:
    settings, project_id, episode_ids, shot_ids, build_seconds = build_fixture(root)
    database = Database(settings.database_path)
    production_ms: list[float] = []
    timeline_ms: list[float] = []
    entry_failures: list[dict[str, Any]] = []
    app = create_app(settings)
    with TestClient(app) as client:
        for episode_id in episode_ids:
            started = time.perf_counter()
            production = client.get(f"/api/v1/episodes/{episode_id}/production")
            production_ms.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            timeline = client.get(f"/api/v1/episodes/{episode_id}/timeline-status")
            timeline_ms.append((time.perf_counter() - started) * 1000)
            expected_shots = SHOT_COUNT // EPISODE_COUNT + (1 if episode_ids.index(episode_id) < SHOT_COUNT % EPISODE_COUNT else 0)
            if production.status_code != 200 or len(production.json().get("items", [])) != expected_shots:
                entry_failures.append({"episode_id": episode_id, "entry": "production", "status": production.status_code})
            if timeline.status_code != 200 or timeline.json().get("status", {}).get("episode", {}).get("id") != episode_id:
                entry_failures.append({"episode_id": episode_id, "entry": "timeline_delivery", "status": timeline.status_code})
        started = time.perf_counter()
        inbox = client.get(f"/api/v1/reviews/inbox?project_id={project_id}")
        review_inbox_ms = (time.perf_counter() - started) * 1000

    with database.connect() as connection:
        counts = {
            "episodes": int(connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]),
            "shots": int(connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0]),
            "media_assets": int(connection.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]),
            "media_versions": int(connection.execute("SELECT COUNT(*) FROM media_versions").fetchone()[0]),
        }
        started = time.perf_counter()
        owner_media_count = int(connection.execute("SELECT COUNT(*) FROM media_assets WHERE owner_id=?", (shot_ids[0],)).fetchone()[0])
        owner_lookup_ms = (time.perf_counter() - started) * 1000
        query_plans = {
            "media_owner": [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM media_assets WHERE owner_id=?", (shot_ids[0],))],
            "media_versions": [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN SELECT * FROM media_versions WHERE media_asset_id=? ORDER BY version_no", (_stable_id("asset", 0),))],
            "review_subject": [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN SELECT * FROM review_decisions WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC", (_stable_id("media-version", 0),))],
        }
        schema_version = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
    integrity = database.integrity_check()
    checks = [
        {"code": "SCALE_COUNTS", "passed": counts == {"episodes": 60, "shots": 800, "media_assets": 10_000, "media_versions": 10_000}},
        {"code": "ALL_EPISODE_ENTRIES", "passed": not entry_failures, "checked_episode_count": len(episode_ids)},
        {"code": "PRODUCTION_READ_P95", "passed": _percentile_95(production_ms) < 500, "p95_ms": round(_percentile_95(production_ms), 3), "max_ms": round(max(production_ms), 3)},
        {"code": "TIMELINE_DELIVERY_READ_P95", "passed": _percentile_95(timeline_ms) < 500, "p95_ms": round(_percentile_95(timeline_ms), 3), "max_ms": round(max(timeline_ms), 3)},
        {"code": "REVIEW_INBOX_READ", "passed": inbox.status_code == 200 and review_inbox_ms < 1_000, "elapsed_ms": round(review_inbox_ms, 3), "returned": len(inbox.json().get("items", [])) if inbox.status_code == 200 else 0},
        {"code": "MEDIA_OWNER_INDEX", "passed": owner_lookup_ms < 100 and owner_media_count > 0 and any("ix_media_assets_owner" in plan for plan in query_plans["media_owner"]), "elapsed_ms": round(owner_lookup_ms, 3)},
        {"code": "DATABASE_INTEGRITY", "passed": integrity == "ok", "integrity": integrity},
    ]
    return {
        "schema_version": "g10.metadata_scale_uat.v1",
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "scope": "isolated migrated SQLite + real FastAPI read paths",
        "fixture": {**counts, "synthetic_media_integrity": "UNKNOWN", "playable_media_claimed": False},
        "migration_head": schema_version,
        "build_seconds": round(build_seconds, 3),
        "checks": checks,
        "entry_failures": entry_failures,
        "query_plans": query_plans,
        "runtime_contacted": False,
        "network_contacted": False,
        "production_database_contacted": False,
        "interpretation": "G10-01/G10-02 metadata-scale evidence only. It does not replace playable-media regression, ordered G7-G9 exits, final UAT, or go/no-go.",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"g10-scale-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "metadata-scale-uat-2026-08-15.json")
    args = parser.parse_args()
    result = run_uat(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "fixture": result["fixture"], "output": str(args.output)}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
