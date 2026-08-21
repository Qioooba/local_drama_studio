"""SQLite adapter for local generation duration evidence."""

from __future__ import annotations

import sqlite3
from typing import Any


class SqliteGenerationEstimateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.query_count = 0

    def _columns(self, table: str) -> set[str]:
        self.query_count += 1
        try:
            return {str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.DatabaseError:
            return set()

    def load_successful_attempts(self, *, profile_version_id: str, limit: int) -> dict[str, Any]:
        jobs = self._columns("jobs")
        attempts = self._columns("job_attempts")
        required_jobs = {"id", "execution_profile_version_id", "input_snapshot_json", "channel"}
        required_attempts = {"id", "job_id", "state", "started_at", "finished_at"}
        if not required_jobs <= jobs or not required_attempts <= attempts:
            return {"schema_available": False, "items": [], "query_count": self.query_count}

        leases = self._columns("job_resource_leases")
        lease_expr = "NULL"
        if {"attempt_id", "resource_key"} <= leases:
            lease_expr = "(SELECT rl.resource_key FROM job_resource_leases rl WHERE rl.attempt_id=a.id ORDER BY rl.acquired_at DESC, rl.id DESC LIMIT 1)"
        self.query_count += 1
        try:
            rows = self.connection.execute(
                f"""SELECT a.id AS attempt_id,a.started_at,a.finished_at,
                j.input_snapshot_json,j.channel,{lease_expr} AS resource_key
                FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE a.state='SUCCEEDED' AND a.started_at IS NOT NULL AND a.finished_at IS NOT NULL
                  AND j.execution_profile_version_id=?
                ORDER BY a.finished_at DESC,a.id DESC LIMIT ?""",
                (profile_version_id, limit),
            ).fetchall()
        except sqlite3.DatabaseError:
            return {"schema_available": False, "items": [], "query_count": self.query_count}
        return {
            "schema_available": True,
            "items": [dict(row) for row in rows],
            "query_count": self.query_count,
        }
