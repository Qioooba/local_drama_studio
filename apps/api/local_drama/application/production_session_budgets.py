from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

DEFAULT_MAX_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_MAX_NEW_JOBS = 600
DEFAULT_MAX_ATTEMPTS_TOTAL = 1_200
DEFAULT_MAX_OUTPUT_BYTES = 100 * 1024 * 1024 * 1024
DEFAULT_MAX_QUEUED_GPU_JOBS = 8
DEFAULT_DISPATCH_SHOTS_PER_TICK = 4

GPU_ACTIVE_STATES = ("QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalized_budget_configuration(configuration: dict[str, Any]) -> dict[str, int]:
    """Read old and new sessions through one backwards-compatible contract."""

    return {
        "max_duration_seconds": max(60, int(configuration.get("max_duration_seconds") or DEFAULT_MAX_DURATION_SECONDS)),
        "max_new_jobs": max(1, int(configuration.get("max_new_jobs") or DEFAULT_MAX_NEW_JOBS)),
        "max_attempts_total": max(
            1,
            int(configuration.get("max_attempts_total") or DEFAULT_MAX_ATTEMPTS_TOTAL),
        ),
        "max_output_bytes": max(1, int(configuration.get("max_output_bytes") or DEFAULT_MAX_OUTPUT_BYTES)),
        "max_queued_gpu_jobs": max(
            1,
            int(configuration.get("max_queued_gpu_jobs") or DEFAULT_MAX_QUEUED_GPU_JOBS),
        ),
        "dispatch_shots_per_tick": max(
            1,
            int(configuration.get("dispatch_shots_per_tick") or DEFAULT_DISPATCH_SHOTS_PER_TICK),
        ),
    }


class ProductionSessionBudgetService:
    """Compute production usage from durable facts without reserving fake work."""

    @staticmethod
    def inspect_with_connection(
        connection: sqlite3.Connection,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        session = connection.execute(
            """SELECT configuration_json,status,started_at,finished_at,created_at
               FROM production_sessions WHERE id=?""",
            (session_id,),
        ).fetchone()
        if session is None:
            return {}
        try:
            raw_configuration = json.loads(str(session["configuration_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_configuration = {}
        configuration = raw_configuration if isinstance(raw_configuration, dict) else {}
        limits = normalized_budget_configuration(configuration)
        # A Job is owned when the session linked it as owned work or when the
        # immutable generation snapshot names the session. The latter closes
        # the short interval between child submission and runner reconciliation.
        owned_jobs_sql = """
            SELECT DISTINCT j.id
            FROM jobs j
            WHERE j.deleted_at IS NULL AND (
              EXISTS(
                SELECT 1 FROM production_session_job_links psl
                WHERE psl.job_id=j.id AND psl.session_id=?
                  AND psl.link_state='ACTIVE'
                  AND psl.role!='EPISODE_PREPARATION_REUSED'
              )
              OR json_extract(j.input_snapshot_json,'$.production_session_id')=?
            )
        """
        owned_job_count = int(connection.execute(f"SELECT COUNT(*) FROM ({owned_jobs_sql})", (session_id, session_id)).fetchone()[0])
        attempt_count = int(
            connection.execute(
                f"""SELECT COUNT(*) FROM job_attempts ja
                    WHERE ja.job_id IN ({owned_jobs_sql})""",
                (session_id, session_id),
            ).fetchone()[0]
        )
        output_bytes = int(
            connection.execute(
                f"""SELECT COALESCE(SUM(x.byte_size),0) FROM (
                      SELECT DISTINCT mv.id,mv.byte_size
                      FROM media_versions mv
                      JOIN artifacts a ON a.id=mv.source_artifact_id
                      JOIN job_attempts ja ON ja.id=a.job_attempt_id
                      WHERE ja.job_id IN ({owned_jobs_sql})
                        AND mv.integrity_status='VERIFIED'
                    ) x""",
                (session_id, session_id),
            ).fetchone()[0]
        )
        placeholders = ",".join("?" for _ in GPU_ACTIVE_STATES)
        gpu_active = int(
            connection.execute(
                f"""SELECT COUNT(*) FROM jobs
                    WHERE deleted_at IS NULL AND channel LIKE 'GPU%'
                      AND state IN ({placeholders})""",
                GPU_ACTIVE_STATES,
            ).fetchone()[0]
        )
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = _parse_time(session["started_at"])
        if started_at is None and str(session["status"]) != "READY":
            # Sessions created before started_at was populated still need a
            # conservative clock. A newly planned READY session, however,
            # must not spend its run budget while waiting for the user.
            started_at = _parse_time(session["created_at"])
        finished_at = _parse_time(session["finished_at"])
        elapsed_until = min(observed_at, finished_at) if finished_at is not None else observed_at
        elapsed_seconds = max(0, int((elapsed_until - started_at).total_seconds())) if started_at is not None else 0
        usage = {
            "elapsed_seconds": elapsed_seconds,
            "new_jobs": owned_job_count,
            "attempts_total": attempt_count,
            "output_bytes": output_bytes,
            "global_queued_gpu_jobs": gpu_active,
        }
        hard_blockers: list[dict[str, Any]] = []
        checks = (
            ("max_duration_seconds", "elapsed_seconds", "PRODUCTION_SESSION_DURATION_BUDGET_EXHAUSTED", "最长运行时间已用完"),
            ("max_new_jobs", "new_jobs", "PRODUCTION_SESSION_JOB_BUDGET_EXHAUSTED", "新建任务预算已用完"),
            ("max_attempts_total", "attempts_total", "PRODUCTION_SESSION_ATTEMPT_BUDGET_EXHAUSTED", "执行尝试预算已用完"),
            ("max_output_bytes", "output_bytes", "PRODUCTION_SESSION_OUTPUT_BUDGET_EXHAUSTED", "输出空间预算已用完"),
        )
        for limit_key, usage_key, code, message in checks:
            if usage[usage_key] >= limits[limit_key]:
                hard_blockers.append(
                    {
                        "code": code,
                        "message": message,
                        "usage": usage[usage_key],
                        "limit": limits[limit_key],
                        "limit_key": limit_key,
                    }
                )
        resource_wait = None
        if gpu_active >= limits["max_queued_gpu_jobs"]:
            resource_wait = {
                "code": "PRODUCTION_SESSION_GPU_QUEUE_WAIT",
                "message": "GPU 队列已达到容量上限，释放容量后会自动继续",
                "usage": gpu_active,
                "limit": limits["max_queued_gpu_jobs"],
                "limit_key": "max_queued_gpu_jobs",
            }
        return {
            "limits": limits,
            "usage": usage,
            "remaining": {
                "duration_seconds": max(0, limits["max_duration_seconds"] - elapsed_seconds),
                "new_jobs": max(0, limits["max_new_jobs"] - owned_job_count),
                "attempts_total": max(0, limits["max_attempts_total"] - attempt_count),
                "output_bytes": max(0, limits["max_output_bytes"] - output_bytes),
                "gpu_queue_slots": max(0, limits["max_queued_gpu_jobs"] - gpu_active),
            },
            "hard_blockers": hard_blockers,
            "resource_wait": resource_wait,
            "can_dispatch": not hard_blockers and resource_wait is None,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        }
