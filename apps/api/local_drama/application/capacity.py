"""Read-only queue capacity snapshot for G9-09.

This is an observation of persisted local queue state, not a synthetic
benchmark.  It never claims throughput beyond completed jobs already recorded
in SQLite and never creates jobs, claims leases, starts workers, or contacts a
webhook.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from local_drama.application.worker_sessions import ACTIVE_SESSION_STATES
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.manifest import load_manifest


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
    def __init__(self, database: Database, settings: Any | None = None) -> None:
        self.database = database
        self.settings = settings

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
                f"""SELECT COUNT(*) AS count
                FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE a.state IN ('CLAIMED','RUNNING'){(' AND j.project_id=?' if project_id else '')}""",
                (project_id,) if project_id else (),
            ).fetchone()
            active_workers = connection.execute(
                f"""SELECT COUNT(DISTINCT worker_id) AS count FROM worker_sessions
                WHERE status IN ({','.join('?' for _ in ACTIVE_SESSION_STATES)}) AND lease_expires_at>?""",
                (*ACTIVE_SESSION_STATES, _now().isoformat()),
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
            duration_rows = connection.execute(
                f"SELECT state, created_at, updated_at FROM jobs {job_where}", job_params
            ).fetchall()
            retry_rows = connection.execute(
                f"SELECT COUNT(*) AS count FROM job_attempts a JOIN jobs j ON j.id=a.job_id {('WHERE j.project_id=?' if project_id else '')} GROUP BY a.job_id HAVING COUNT(*) > 1",
                (project_id,) if project_id else (),
            ).fetchall()
            review_where = "WHERE ma.project_id=?" if project_id else ""
            review_rows = connection.execute(
                f"""SELECT r.decision, COUNT(*) AS count FROM review_decisions r
                LEFT JOIN media_versions mv ON r.subject_id=mv.id
                LEFT JOIN media_assets ma ON mv.media_asset_id=ma.id
                {review_where} GROUP BY r.decision""",
                (project_id,) if project_id else (),
            ).fetchall()

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
        durations: list[float] = []
        for row in duration_rows:
            if str(row["state"]) not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                continue
            created = _parse(str(row["created_at"]))
            updated = _parse(str(row["updated_at"]))
            if created is not None and updated is not None:
                durations.append(max(0.0, (updated - created).total_seconds()))
        succeeded = by_state.get("SUCCEEDED", 0)
        failed = by_state.get("FAILED", 0)
        terminal = succeeded + failed
        review_counts = {str(row["decision"]): int(row["count"]) for row in review_rows}
        review_total = sum(review_counts.values())
        total_jobs = sum(by_state.values())
        gpu: dict[str, Any] = {"name": None, "total_bytes": None, "driver": None, "source": "UNAVAILABLE"}
        if self.settings is not None:
            try:
                runtime_gpu = dict(load_manifest(self.settings.manifest_path).runtime.get("gpu", {}))
                gpu = {"name": runtime_gpu.get("name"), "total_bytes": runtime_gpu.get("total_bytes"), "driver": runtime_gpu.get("driver"), "source": "LOCAL_MANIFEST"}
            except Exception:  # diagnostics itself reports manifest problems; capacity remains read-only
                pass
        disk: dict[str, Any] = {"free_bytes": None, "total_bytes": None, "used_bytes": None, "source": "UNAVAILABLE"}
        if self.settings is not None:
            usage = shutil.disk_usage(self.settings.data_root)
            disk = {"free_bytes": usage.free, "total_bytes": usage.total, "used_bytes": usage.used, "source": "LOCAL_FILESYSTEM"}
        return {
            "scope": {"project_id": project_id},
            "observed_at": _now().isoformat(),
            "jobs_by_state": by_state,
            "jobs_by_channel": by_channel,
            "queued_count": int(queued["count"]),
            "oldest_queued_age_seconds": queued_age,
            "active_attempt_count": int(active_attempts["count"]),
            "active_worker_count": int(active_workers["count"]),
            "gpu_active_count": int(gpu_active["count"]),
            "gpu_concurrency_limit": 1,
            "completed_last_24h": int(completed_24h["count"]),
            "gpu": gpu,
            "disk": disk,
            "duration_seconds": {"completed_count": len(durations), "average": round(sum(durations) / len(durations), 3) if durations else None, "max": round(max(durations), 3) if durations else None},
            "failure_rate": round(failed / terminal, 4) if terminal else None,
            "retry_rate": round(len(retry_rows) / total_jobs, 4) if total_jobs else None,
            "review": {"decision_counts": review_counts, "approval_rate": round(review_counts.get("APPROVED", 0) / review_total, 4) if review_total else None, "total": review_total},
            "observation_status": "OBSERVED_NOT_BENCHMARKED",
            "webhook_status": "LOOPBACK_EXPLICIT_BOUNDED",
            "would_create_jobs": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
