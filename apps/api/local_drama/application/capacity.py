"""Read-only queue capacity snapshot for G9-09.

This is an observation of persisted local queue state, not a synthetic
benchmark.  It never claims throughput beyond completed jobs already recorded
in SQLite and never creates jobs, claims leases, starts workers, or contacts a
webhook.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CapacitySnapshotService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, project_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            if project_id and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            job_where = "WHERE project_id=?" if project_id else ""
            job_params: tuple[Any, ...] = (project_id,) if project_id else ()
            counts = connection.execute(
                f"SELECT state, channel, COUNT(*) AS count FROM jobs {job_where} GROUP BY state, channel ORDER BY state, channel",
                job_params,
            ).fetchall()
            queued = connection.execute(
                f"SELECT COUNT(*) AS count, MIN(created_at) AS oldest FROM jobs {job_where + (' AND ' if job_where else 'WHERE ')}state='QUEUED'",
                job_params,
            ).fetchone()
            active_attempts = connection.execute(
                f"""SELECT COUNT(*) AS count, COUNT(DISTINCT worker_id) AS workers
                FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE a.state IN ('CLAIMED','RUNNING'){(' AND j.project_id=?' if project_id else '')}""",
                (project_id,) if project_id else (),
            ).fetchone()
            gpu_active = connection.execute(
                f"""SELECT COUNT(*) AS count FROM jobs
                {job_where + (' AND ' if job_where else 'WHERE ')}channel='GPU_H3' AND state IN ('CLAIMED','RUNNING')""",
                job_params,
            ).fetchone()
            cutoff = (_now() - timedelta(hours=24)).isoformat()
            completed_24h = connection.execute(
                f"SELECT COUNT(*) AS count FROM jobs {job_where + (' AND ' if job_where else 'WHERE ')}state='SUCCEEDED' AND updated_at>=?",
                (*job_params, cutoff),
            ).fetchone()

        by_state: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        for row in counts:
            state = str(row["state"])
            channel = str(row["channel"])
            amount = int(row["count"])
            by_state[state] = by_state.get(state, 0) + amount
            by_channel[channel] = by_channel.get(channel, 0) + amount
        oldest = _parse(queued["oldest"])
        queued_age = max(0, round((_now() - oldest).total_seconds())) if oldest else None
        return {
            "scope": {"project_id": project_id},
            "observed_at": _now().isoformat(),
            "jobs_by_state": by_state,
            "jobs_by_channel": by_channel,
            "queued_count": int(queued["count"]),
            "oldest_queued_age_seconds": queued_age,
            "active_attempt_count": int(active_attempts["count"]),
            "active_worker_count": int(active_attempts["workers"]),
            "gpu_active_count": int(gpu_active["count"]),
            "gpu_concurrency_limit": 1,
            "completed_last_24h": int(completed_24h["count"]),
            "observation_status": "OBSERVED_NOT_BENCHMARKED",
            "webhook_status": "NOT_IMPLEMENTED",
            "would_create_jobs": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
