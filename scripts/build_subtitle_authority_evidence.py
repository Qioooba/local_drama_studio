"""Create one real script-authoritative subtitle revision for formal G8 UAT."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.timeline import TimelineService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database

EPISODE_ID = "d4db1033-9517-4bd0-958d-4228e0abead1"
SOURCE_DOCUMENT_VERSION_ID = "6bdf1995-bbc2-4df8-a776-0779a4edfa19"


def main() -> None:
    settings = Settings.from_env()
    settings.ensure_roots()
    database = Database(settings.database_path)
    with database.connect() as connection:
        existing = connection.execute(
            "SELECT id,input_snapshot_json FROM subtitle_revisions WHERE episode_id=? ORDER BY revision_no",
            (EPISODE_ID,),
        ).fetchall()
    for row in existing:
        snapshot = json.loads(str(row["input_snapshot_json"]))
        if snapshot.get("schema_version") == "localdrama.subtitle-authority.v1":
            raise RuntimeError(f"verified script-authoritative subtitle already exists: {row['id']}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = settings.backups_root / f"subtitle_authority_preflight_{stamp}.sqlite3"
    backup_integrity = online_backup(settings.database_path, backup_path)
    subtitle = TimelineService(database, settings).create_subtitle_revision(
        EPISODE_ID,
        [
            {"start_us": 0, "end_us": 3_000_000, "text": "谁在里面？"},
            {"start_us": 5_000_000, "end_us": 9_000_000, "text": "先喝口热水，天亮以前我们一起想办法。"},
        ],
        format="SRT",
        authority={"text_authority": "SCRIPT", "source_document_version_id": SOURCE_DOCUMENT_VERSION_ID},
        actor="subtitle-authority-uat",
    )
    evidence = {
        "schema_version": "localdrama.subtitle-authority-evidence.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "episode_id": EPISODE_ID,
        "subtitle_revision_id": subtitle["id"],
        "revision_no": subtitle["revision_no"],
        "format": subtitle["format"],
        "cue_count": len(subtitle["cues"]),
        "content_hash": subtitle["content_hash"],
        "authority_status": subtitle["authority_status"],
        "input_snapshot": subtitle["input_snapshot"],
        "backup": {"path": str(backup_path), "integrity": backup_integrity},
        "runtime_contacted": False,
        "network_contacted": False,
    }
    output = ROOT / "docs" / "evidence" / "g8" / "subtitle-authority-production-2026-08-15.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
