"""Query helper: recent jobs, candidates and media versions for the audit instance."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "local_drama.sqlite3"


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    print("--- recent jobs")
    for row in connection.execute(
        "SELECT id, type, state, stage_code, revision, execution_profile_version_id, created_at, "
        "started_at, finished_at, last_error_code, progress_json "
        "FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ):
        print(dict(row))
    print("--- recent explainer candidates")
    for row in connection.execute(
        "SELECT c.id, c.status, c.candidate_kind, c.job_id, c.created_at, c.adopted, c.media_version_id, "
        "json_extract(c.lineage_json,'$.seed') AS seed, json_extract(c.lineage_json,'$.operation_id') AS op "
        "FROM explainer_media_candidates c ORDER BY c.created_at DESC LIMIT ?",
        (limit,),
    ):
        print(dict(row))
    print("--- recent media versions")
    columns = [row[1] for row in connection.execute("PRAGMA table_info(media_versions)")]
    print(f"(columns: {columns})")
    for row in connection.execute("SELECT * FROM media_versions ORDER BY rowid DESC LIMIT ?", (limit,)):
        print(dict(row))
    newest = connection.execute("SELECT id, input_snapshot_json FROM jobs ORDER BY created_at DESC LIMIT 1").fetchone()
    if newest:
        print("--- newest job input snapshot")
        print(json.dumps(json.loads(newest["input_snapshot_json"]), ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
