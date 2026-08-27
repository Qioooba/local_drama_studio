"""Prepare real persisted shot entities for the Production Cockpit scale UAT.

The script exercises the paginated production state matrix, not a graph. These
are ordinary persisted SHOT rows explicitly marked ``SCALE_UAT``; no media,
job, runtime or network is created.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.database.episode_production_repository import SqliteEpisodeProductionReadRepository

PROJECT_ID = "e5eaa01d-d39a-4a63-acbf-026da30b46e7"
EPISODE_ID = "d4db1033-9517-4bd0-958d-4228e0abead1"
TARGET_SHOTS = 500


def prepare() -> dict[str, Any]:
    settings = Settings.from_env()
    settings.ensure_roots()
    backup_path = settings.backups_root / f"g9_preflight_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    backup_integrity = online_backup(settings.database_path, backup_path)
    database = Database(settings.database_path)
    service = ProjectService(database, settings.projects_root)
    existing = service.list_shots(EPISODE_ID)
    if len(existing) > TARGET_SHOTS:
        raise RuntimeError(f"production episode already has {len(existing)} shots; refusing to remove or reorder data")
    by_code = {str(shot["code"]): shot for shot in existing}
    created: list[dict[str, Any]] = []
    number = 1
    while len(existing) + len(created) < TARGET_SHOTS:
        code = f"G9_SCALE_UAT_{number:03d}"
        shot = by_code.get(code)
        if shot is None:
            shot = service.create_shot(EPISODE_ID, code, 1000, "SCALE_UAT")
            created.append({"id": shot["id"], "code": shot["code"], "shot_type": shot["shot_type"]})
        number += 1
    grid = SqliteEpisodeProductionReadRepository(database).shot_facts(EPISODE_ID, cursor=0, limit=75, states=set())
    visible_rows = len(grid["items"])
    total_shots = int(grid["total"])
    if visible_rows != 75 or total_shots < TARGET_SHOTS or grid["next_cursor"] != 75:
        raise RuntimeError(f"Production Cockpit scale precondition failed: shots={total_shots}, rows={visible_rows}")
    evidence = {
        "schema_version": "production-cockpit-scale-uat.v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "LOCAL_ONLY",
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "persisted_scale_entities": True,
        "fixture_mode": False,
        "created_shots": created,
        "total_shots": total_shots,
        "visible_rows": visible_rows,
        "page_limit": grid["limit"],
        "next_cursor": grid["next_cursor"],
        "backup": {"path": str(backup_path), "integrity_check": backup_integrity},
        "jobs_created": False,
        "runtime_contacted": False,
        "network_contacted": False,
        "media_created": False,
        "note": "SCALE_UAT shot rows are real persisted production-domain entities used only to exercise the Production Cockpit state matrix; no mock nodes or fake media are injected.",
    }
    output = ROOT / "docs/evidence/g9/g9-production-scale-uat-2026-08-15.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
