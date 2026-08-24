"""Persistent LOCAL_ONLY queue, lease protocol, outbox events and recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

QUEUED = "QUEUED"
CLAIMED = "CLAIMED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCELLED = "CANCELLED"
ORPHANED = "ORPHANED"
NEEDS_ATTENTION = "NEEDS_ATTENTION"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _parse_json(value: str) -> Any:
    return json.loads(value) if value else {}


def _resource_key(channel: str, worker_id: str | None = None) -> str:
    """Return the scheduler resource gate for a channel.

    GPU_H3 is deliberately a single heavy resource. Other channels have
    independent gates so CPU/text/audio work does not block a GPU slot (or
    each other). The key is persisted with every attempt for audit/recovery.
    """
    normalized = channel.strip().upper()
    if normalized in {"GPU_H3", "GPU", "VIDEO_GPU"}:
        return "GPU_H3_HEAVY"
    # Non-GPU channels are independent. A worker is still prevented from
    # claiming two jobs on the same channel, while separate workers may run
    # CPU/text/audio work concurrently.
    return f"CHANNEL:{normalized}:{worker_id or 'scheduler'}"


class JobService:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings
        self._last_automatic_reconcile_at: float | None = None

    def _emit(self, connection: Any, event_type: str, project_id: str, subject_type: str, subject_id: str, payload: dict[str, Any]) -> int:
        cursor = connection.execute(
            "INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json) VALUES (?, ?, ?, ?, ?)",
            (event_type, project_id, subject_type, subject_id, _json(payload)),
        )
        return int(cursor.lastrowid)

    def _job_response(self, row: Any, *, replay: bool = False) -> dict[str, Any]:
        progress = _parse_json(str(row["progress_json"] or "{}")) if "progress_json" in row.keys() else {}
        return {
            "id": row["id"],
            "type": row["type"],
            "project_id": row["project_id"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "state": row["state"],
            "channel": row["channel"],
            "idempotency_key": row["idempotency_key"],
            "input_snapshot": _parse_json(row["input_snapshot_json"]),
            "execution_profile_version_id": row["execution_profile_version_id"],
            "priority": row["priority"],
            "max_attempts": row["max_attempts"],
            "revision": row["revision"],
            "created_at": row["created_at"] if "created_at" in row.keys() else None,
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
            "progress": progress,
            "progress_updated_at": row["progress_updated_at"] if "progress_updated_at" in row.keys() else None,
            "started_at": row["started_at"] if "started_at" in row.keys() else None,
            "finished_at": row["finished_at"] if "finished_at" in row.keys() else None,
            "last_error_code": row["last_error_code"] if "last_error_code" in row.keys() else None,
            "last_error_detail_redacted": row["last_error_detail_redacted"] if "last_error_detail_redacted" in row.keys() else None,
            "idempotent_replay": replay,
        }

    def create_job(
        self,
        project_id: str,
        job_type: str,
        subject_type: str,
        subject_id: str,
        channel: str,
        input_snapshot: dict[str, Any],
        idempotency_key: str,
        *,
        execution_profile_version_id: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        depends_on_job_ids: list[str] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            return self.create_job_in_transaction(
                connection,
                project_id,
                job_type,
                subject_type,
                subject_id,
                channel,
                input_snapshot,
                idempotency_key,
                execution_profile_version_id=execution_profile_version_id,
                priority=priority,
                max_attempts=max_attempts,
                depends_on_job_ids=depends_on_job_ids,
                actor=actor,
            )

    def create_job_in_transaction(
        self,
        connection: Any,
        project_id: str,
        job_type: str,
        subject_type: str,
        subject_id: str,
        channel: str,
        input_snapshot: dict[str, Any],
        idempotency_key: str,
        *,
        execution_profile_version_id: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        depends_on_job_ids: list[str] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "Job command 必须提供 1—200 字符 Idempotency-Key")
        if max_attempts < 1 or max_attempts > 20:
            raise DomainRuleError("INVALID_MAX_ATTEMPTS", "max_attempts 必须在 1—20 之间")
        dependencies = depends_on_job_ids or []
        request_payload = {
            "project_id": project_id,
            "type": job_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "channel": channel,
            "input_snapshot": input_snapshot,
            "execution_profile_version_id": execution_profile_version_id,
            "priority": priority,
            "max_attempts": max_attempts,
            "depends_on_job_ids": dependencies,
        }
        payload_hash = _payload_hash(request_payload)
        scope = f"job:create:{project_id}"
        job_id = str(uuid.uuid4())
        now = _iso(_utc_now())
        existing = connection.execute(
            "SELECT payload_hash, response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
            (scope, idempotency_key),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(str(existing["payload_hash"]), payload_hash):
                raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 Idempotency-Key 的请求体不一致")
            result = dict(_parse_json(existing["response_json"]))
            result["idempotent_replay"] = True
            return result
        project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        if len(set(dependencies)) != len(dependencies) or job_id in dependencies:
            raise DomainRuleError("INVALID_JOB_DEPENDENCY", "Job dependency 不能重复或自引用")
        if dependencies:
            placeholders = ",".join("?" for _ in dependencies)
            rows = connection.execute(f"SELECT id, project_id FROM jobs WHERE id IN ({placeholders})", dependencies).fetchall()
            if len(rows) != len(dependencies) or any(row["project_id"] != project_id for row in rows):
                raise DomainRuleError("JOB_DEPENDENCY_NOT_FOUND", "Job dependency 必须存在于同一项目")
        connection.execute(
            """INSERT INTO jobs
                (id, type, project_id, subject_type, subject_id, state, channel, idempotency_key, input_snapshot_json,
                 execution_profile_version_id, priority, max_attempts, next_run_at, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
            (
                job_id,
                job_type,
                project_id,
                subject_type,
                subject_id,
                channel,
                idempotency_key,
                _json(input_snapshot),
                execution_profile_version_id,
                priority,
                max_attempts,
                now,
                now,
                now,
                actor,
            ),
        )
        for dependency in dependencies:
            connection.execute("INSERT INTO job_dependencies (job_id, depends_on_job_id) VALUES (?, ?)", (job_id, dependency))
        event_id = self._emit(connection, "JOB_QUEUED", project_id, "JOB", job_id, {"state": QUEUED, "channel": channel})
        connection.execute(
            "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, job_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'JOB_QUEUED', 'job', ?, ?, ?, ?)",
            (actor, job_id, job_id, "Job 入队", _json({"event_id": event_id, "channel": channel})),
        )
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        result = self._job_response(row)
        connection.execute(
            "INSERT INTO command_idempotencies (scope, idempotency_key, payload_hash, response_json) VALUES (?, ?, ?, ?)",
            (scope, idempotency_key, payload_hash, _json(result)),
        )
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在", {"job_id": job_id})
            attempts = connection.execute("SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt_no", (job_id,)).fetchall()
            dependencies = connection.execute("SELECT depends_on_job_id FROM job_dependencies WHERE job_id=?", (job_id,)).fetchall()
            artifacts = connection.execute(
                """SELECT a.*, mv.id AS promoted_media_version_id
                FROM artifacts a
                LEFT JOIN media_versions mv ON mv.source_artifact_id=a.id
                WHERE a.job_attempt_id IN (SELECT id FROM job_attempts WHERE job_id=?)
                ORDER BY a.created_at""",
                (job_id,),
            ).fetchall()
        artifacts_by_attempt: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            artifacts_by_attempt.setdefault(str(artifact["job_attempt_id"]), []).append(dict(artifact))
        attempt_items = [
            {
                **dict(attempt),
                "progress": _parse_json(str(attempt["progress_json"] or "{}")) if "progress_json" in attempt.keys() else {},
                "artifacts": artifacts_by_attempt.get(str(attempt["id"]), []),
            }
            for attempt in attempts
        ]
        return {
            **self._job_response(row),
            "attempts": attempt_items,
            "depends_on_job_ids": [item["depends_on_job_id"] for item in dependencies],
        }

    def list_jobs(self, project_id: str | None = None, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if states:
            where.append(f"state IN ({','.join('?' for _ in states)})")
            params.extend(states)
        params.append(max(1, min(limit, 500)))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM jobs {clause} ORDER BY created_at DESC LIMIT ?", params).fetchall()
        return [self._job_response(row) for row in rows]

    def list_jobs_page(self, project_id: str | None = None, states: list[str] | None = None, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return a bounded server-side window and a deterministic offset cursor."""
        where: list[str] = []
        params: list[Any] = []
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if states:
            where.append(f"state IN ({','.join('?' for _ in states)})")
            params.extend(states)
        normalized_cursor = max(0, int(cursor))
        normalized_limit = max(1, min(int(limit), 100))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([normalized_limit + 1, normalized_cursor])
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM jobs {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", params).fetchall()
        has_more = len(rows) > normalized_limit
        items = [self._job_response(row) for row in rows[:normalized_limit]]
        return {"items": items, "next_cursor": normalized_cursor + normalized_limit if has_more else None, "cursor": normalized_cursor, "limit": normalized_limit}

    def list_attempts(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            job = connection.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在", {"job_id": job_id})
            rows = connection.execute("SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt_no", (job_id,)).fetchall()
        return [
            {
                **dict(row),
                "progress": _parse_json(str(row["progress_json"] or "{}")) if "progress_json" in row.keys() else {},
                # Lease tokens are credentials and never belong in a read API.
                "lease_token": None,
            }
            for row in rows
        ]

    def attempt_events(self, attempt_id: str, *, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 200))
        with self.database.connect() as connection:
            exists = connection.execute("SELECT id FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
            if exists is None:
                raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
            rows = connection.execute(
                "SELECT * FROM outbox_events WHERE subject_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (attempt_id, max(0, int(cursor)), normalized_limit + 1),
            ).fetchall()
        has_more = len(rows) > normalized_limit
        items = [{**dict(row), "payload": _parse_json(row["payload_json"])} for row in rows[:normalized_limit]]
        next_cursor = int(items[-1]["event_id"]) if has_more and items else None
        return {"items": items, "cursor": max(0, int(cursor)), "next_cursor": next_cursor, "limit": normalized_limit}

    def claim(
        self,
        worker_id: str,
        channels: list[str] | None = None,
        lease_seconds: int = 60,
        actor: str = "worker",
        *,
        worker_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id:
            raise DomainRuleError("WORKER_ID_REQUIRED", "claim 必须提供 worker_id")
        if lease_seconds < 5 or lease_seconds > 3600:
            raise DomainRuleError("INVALID_LEASE", "lease_seconds 必须在 5—3600 之间")
        now = _utc_now()
        now_iso = _iso(now)
        expires = _iso(now + timedelta(seconds=lease_seconds))
        # A worker/API restart has no in-memory queue to restore. Reconcile
        # expired leases before selecting the next durable QUEUED item so an
        # orphaned attempt cannot strand the queue until a manual endpoint call.
        observed = monotonic()
        if self._last_automatic_reconcile_at is None or observed - self._last_automatic_reconcile_at >= 5.0:
            self.reconcile(now=now, actor="scheduler-restart")
            self._last_automatic_reconcile_at = observed
        with self.database.transaction() as connection:
            if worker_session_id is not None:
                session = connection.execute(
                    "SELECT worker_id,status,lease_expires_at FROM worker_sessions WHERE id=?",
                    (worker_session_id,),
                ).fetchone()
                if session is None:
                    raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
                if str(session["worker_id"]) != worker_id:
                    raise DomainRuleError("WORKER_SESSION_MISMATCH", "WorkerSession 与 worker_id 不匹配")
                if str(session["status"]) != "RUNNING" or datetime.fromisoformat(str(session["lease_expires_at"])) <= now:
                    raise DomainRuleError("WORKER_SESSION_NOT_ACTIVE", "WorkerSession 未运行或 heartbeat 已过期")
            params: list[Any] = [now_iso]
            channel_clause = ""
            if channels:
                channel_clause = f"AND j.channel IN ({','.join('?' for _ in channels)})"
                params.extend(channels)
            params.append(worker_id)
            row = connection.execute(
                f"""SELECT j.* FROM jobs j
                WHERE j.state='QUEUED' AND (j.next_run_at IS NULL OR j.next_run_at<=?) {channel_clause}
                AND NOT EXISTS (SELECT 1 FROM job_attempts active JOIN jobs aj ON aj.id=active.job_id
                                WHERE active.worker_id=? AND active.state IN ('CLAIMED','RUNNING') AND aj.channel=j.channel)
                AND NOT (j.channel IN ('GPU_H3','GPU','VIDEO_GPU') AND EXISTS (
                    SELECT 1 FROM jobs gpu_active
                    WHERE gpu_active.channel IN ('GPU_H3','GPU','VIDEO_GPU') AND gpu_active.state IN ('CLAIMED','RUNNING')
                ))
                AND NOT (j.channel IN ('GPU_H3','GPU','VIDEO_GPU') AND EXISTS (
                    SELECT 1 FROM job_resource_leases rl WHERE rl.resource_key='GPU_H3_HEAVY' AND rl.released_at IS NULL
                ))
                AND NOT EXISTS (SELECT 1 FROM job_dependencies d JOIN jobs dependency ON dependency.id=d.depends_on_job_id WHERE d.job_id=j.id AND dependency.state!='SUCCEEDED')
                ORDER BY j.priority ASC, j.created_at ASC LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            attempt_row = connection.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 AS attempt_no FROM job_attempts WHERE job_id=?", (row["id"],)).fetchone()
            attempt_no = int(attempt_row["attempt_no"])
            token = secrets_token()
            attempt_id = str(uuid.uuid4())
            connection.execute(
                "UPDATE jobs SET state='CLAIMED', next_run_at=NULL, started_at=COALESCE(started_at, ?), updated_at=?, revision=revision+1 WHERE id=? AND state='QUEUED'",
                (now_iso, now_iso, row["id"]),
            )
            self._sync_experiment_cell_status(connection, str(row["id"]), CLAIMED, now_iso)
            connection.execute(
                """INSERT INTO job_attempts
                (id, job_id, attempt_no, state, worker_id, worker_session_id, lease_token, lease_expires_at, heartbeat_at, started_at, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (attempt_id, row["id"], attempt_no, worker_id, worker_session_id, token, expires, now_iso, now_iso, now_iso, now_iso, actor),
            )
            connection.execute(
                "INSERT INTO job_resource_leases (id, job_id, attempt_id, channel, resource_key, acquired_at, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), row["id"], attempt_id, row["channel"], _resource_key(str(row["channel"]), worker_id), now_iso, now_iso, actor),
            )
            self._emit(
                connection,
                "JOB_CLAIMED",
                row["project_id"],
                "JOB_ATTEMPT",
                attempt_id,
                {"job_id": row["id"], "attempt_no": attempt_no, "worker_id": worker_id, "worker_session_id": worker_session_id},
            )
            return {
                "job": self._job_response({**dict(row), "state": CLAIMED, "revision": row["revision"] + 1}),
                "attempt": {
                    "id": attempt_id,
                    "job_id": row["id"],
                    "attempt_no": attempt_no,
                    "state": CLAIMED,
                    "worker_id": worker_id,
                    "worker_session_id": worker_session_id,
                    "lease_token": token,
                    "lease_expires_at": expires,
                },
            }

    def _leased_attempt(self, connection: Any, attempt_id: str, lease_token: str, worker_id: str) -> Any:
        row = connection.execute(
            "SELECT a.*, j.project_id, j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
        if row["worker_id"] != worker_id or not hmac.compare_digest(str(row["lease_token"] or ""), lease_token):
            raise DomainRuleError("LEASE_TOKEN_INVALID", "lease_token 或 worker_id 无效")
        if row["state"] not in {CLAIMED, RUNNING}:
            raise DomainRuleError("ATTEMPT_NOT_ACTIVE", "JobAttempt 不再接受 worker 写入")
        if row["lease_expires_at"] and datetime.fromisoformat(row["lease_expires_at"]) <= _utc_now():
            raise DomainRuleError("LEASE_EXPIRED", "JobAttempt lease 已过期")
        return row

    def heartbeat(
        self, attempt_id: str, lease_token: str, worker_id: str, *, progress: dict[str, Any] | None = None, lease_seconds: int = 60
    ) -> dict[str, Any]:
        progress = progress or {}
        if not isinstance(progress, dict):
            raise DomainRuleError("INVALID_JOB_PROGRESS", "progress 必须是对象")
        percent = progress.get("percent")
        if percent is not None:
            try:
                if float(percent) < 0 or float(percent) > 100:
                    raise ValueError
            except (TypeError, ValueError) as error:
                raise DomainRuleError("INVALID_JOB_PROGRESS", "progress.percent 必须在 0—100 之间") from error
        now = _utc_now()
        now_iso = _iso(now)
        expires = _iso(now + timedelta(seconds=lease_seconds))
        with self.database.transaction() as connection:
            row = self._leased_attempt(connection, attempt_id, lease_token, worker_id)
            progress_payload = progress
            connection.execute(
                "UPDATE job_attempts SET state='RUNNING', heartbeat_at=?, lease_expires_at=?, progress_json=?, updated_at=?, revision=revision+1 WHERE id=?",
                (now_iso, expires, _json(progress_payload), now_iso, attempt_id),
            )
            connection.execute(
                """UPDATE jobs
                SET state=CASE WHEN state='CANCEL_REQUESTED' THEN state ELSE 'RUNNING' END,
                    progress_json=?, progress_updated_at=?, updated_at=?, revision=revision+1
                WHERE id=?""",
                (_json(progress_payload), now_iso, now_iso, row["job_id"]),
            )
            projected_state = CANCEL_REQUESTED if str(row["job_state"]) == CANCEL_REQUESTED else RUNNING
            self._sync_experiment_cell_status(connection, str(row["job_id"]), projected_state, now_iso)
            self._emit(connection, "JOB_HEARTBEAT", row["project_id"], "JOB_ATTEMPT", attempt_id, {"job_id": row["job_id"], "progress": progress_payload})
        return {
            "attempt_id": attempt_id,
            "job_id": row["job_id"],
            "state": RUNNING,
            "heartbeat_at": now_iso,
            "lease_expires_at": expires,
            "progress": progress_payload,
            "cancel_requested": row["job_state"] == CANCEL_REQUESTED,
        }

    def attach_provider(
        self,
        attempt_id: str,
        lease_token: str,
        worker_id: str,
        provider_job_id: str,
        *,
        comfy_prompt_id: str | None = None,
        comfy_client_id: str | None = None,
        sandbox_rel_path: str | None = None,
    ) -> dict[str, Any]:
        if not provider_job_id:
            raise DomainRuleError("PROVIDER_JOB_ID_REQUIRED", "provider_job_id 不能为空")
        with self.database.transaction() as connection:
            row = self._leased_attempt(connection, attempt_id, lease_token, worker_id)
            connection.execute(
                "UPDATE job_attempts SET provider_job_id=?, comfy_prompt_id=?, comfy_client_id=?, sandbox_rel_path=?, updated_at=?, revision=revision+1 WHERE id=?",
                (provider_job_id, comfy_prompt_id, comfy_client_id, sandbox_rel_path, _iso(_utc_now()), attempt_id),
            )
            return {
                "attempt_id": attempt_id,
                "job_id": row["job_id"],
                "provider_job_id": provider_job_id,
                "comfy_prompt_id": comfy_prompt_id,
                "comfy_client_id": comfy_client_id,
                "sandbox_rel_path": sandbox_rel_path,
            }

    @staticmethod
    def _sync_experiment_cell_status(connection: Any, job_id: str, state: str, now: str) -> None:
        """Project a child generation Job state back to its matrix cell."""
        cell = connection.execute(
            "SELECT id, experiment_id FROM experiment_cells WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if cell is None:
            return
        connection.execute("UPDATE experiment_cells SET status=? WHERE id=?", (state, cell["id"]))
        if state != SUCCEEDED:
            return
        aggregate = connection.execute(
            """SELECT ge.cell_count,
            COUNT(ec.id) AS expanded_count,
            SUM(CASE WHEN ec.status='SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_count
            FROM generation_experiments ge
            LEFT JOIN experiment_cells ec ON ec.experiment_id=ge.id
            WHERE ge.id=? GROUP BY ge.id""",
            (cell["experiment_id"],),
        ).fetchone()
        if (
            aggregate is not None
            and int(aggregate["expanded_count"] or 0) == int(aggregate["cell_count"])
            and int(aggregate["succeeded_count"] or 0) == int(aggregate["cell_count"])
        ):
            connection.execute(
                """UPDATE generation_experiments SET status='COMPLETED', updated_at=?, revision=revision+1
                WHERE id=? AND status='CONFIRMED'""",
                (now, cell["experiment_id"]),
            )

    def complete(
        self,
        attempt_id: str,
        lease_token: str,
        worker_id: str,
        *,
        success: bool,
        error_code: str | None = None,
        error_detail_redacted: str | None = None,
        provider_job_id: str | None = None,
        retryable: bool = True,
    ) -> dict[str, Any]:
        now = _iso(_utc_now())
        with self.database.transaction() as connection:
            row = self._leased_attempt(connection, attempt_id, lease_token, worker_id)
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
            if job is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在")
            # A cancellation requested while a local runtime is finishing must
            # never be overwritten by a late success heartbeat/completion.
            if job["state"] == CANCEL_REQUESTED:
                attempt_state = CANCELLED
                job_state = CANCELLED
                next_run_at = None
            elif success:
                attempt_state = SUCCEEDED
                job_state = SUCCEEDED
                next_run_at = None
            elif retryable and int(row["attempt_no"]) < int(job["max_attempts"]):
                attempt_state = FAILED
                job_state = QUEUED
                next_run_at = _iso(_utc_now() + timedelta(seconds=min(300, 2 ** int(row["attempt_no"]))))
            else:
                attempt_state = FAILED
                job_state = FAILED
                next_run_at = None
            existing_progress = _parse_json(str(row["progress_json"] or "{}"))
            existing_phase = str(existing_progress.get("phase") or "")
            # Preserve a worker's meaningful completed business phase (for
            # example DRAFT_READY) instead of hiding it behind the transport
            # state SUCCEEDED. Generic jobs still receive SUCCEEDED.
            completed_phase = (
                existing_phase
                if attempt_state == SUCCEEDED
                and int(existing_progress.get("percent") or 0) >= 100
                and existing_phase not in {"", "QUEUED", "RUNNING", "RETRY_WAITING"}
                else attempt_state
            )
            terminal_progress = {
                **existing_progress,
                "phase": completed_phase if job_state != QUEUED else "RETRY_WAITING",
                **({"percent": 100, "eta_seconds": 0} if attempt_state == SUCCEEDED else {}),
            }
            connection.execute(
                "UPDATE job_attempts SET state=?, provider_job_id=?, error_code=?, error_detail_redacted=?, progress_json=?, lease_token=NULL, lease_expires_at=NULL, finished_at=?, updated_at=?, revision=revision+1 WHERE id=?",
                (attempt_state, provider_job_id, error_code, error_detail_redacted, _json(terminal_progress), now, now, attempt_id),
            )
            connection.execute(
                "UPDATE jobs SET state=?, next_run_at=?, progress_json=?, progress_updated_at=?, last_error_code=?, last_error_detail_redacted=?, finished_at=?, updated_at=?, revision=revision+1 WHERE id=?",
                (job_state, next_run_at, _json(terminal_progress), now, error_code, error_detail_redacted, now if job_state in {SUCCEEDED, FAILED, CANCELLED} else None, now, row["job_id"]),
            )
            self._sync_experiment_cell_status(connection, str(row["job_id"]), job_state, now)
            connection.execute("UPDATE job_resource_leases SET released_at=? WHERE attempt_id=? AND released_at IS NULL", (now, attempt_id))
            self._emit(
                connection,
                "JOB_FINISHED",
                row["project_id"],
                "JOB_ATTEMPT",
                attempt_id,
                {"job_id": row["job_id"], "attempt_state": attempt_state, "job_state": job_state, "error_code": error_code},
            )
            if job_state in {FAILED, CANCELLED, NEEDS_ATTENTION, ORPHANED}:
                # Dependency propagation must remain immediate even though
                # idle polling throttles full lease scans.
                self._last_automatic_reconcile_at = None
            return {"job_id": row["job_id"], "attempt_id": attempt_id, "attempt_state": attempt_state, "job_state": job_state, "next_run_at": next_run_at}

    def recover_provider_success(self, attempt_id: str, provider_job_id: str, actor: str = "reconciler") -> dict[str, Any]:
        now = _iso(_utc_now())
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT a.*, j.project_id, j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
            if row["state"] not in {ORPHANED, NEEDS_ATTENTION}:
                raise DomainRuleError("ATTEMPT_NOT_RECOVERABLE", "只有 ORPHANED 或 NEEDS_ATTENTION Attempt 可以恢复")
            if str(row["provider_job_id"] or "") != provider_job_id:
                raise DomainRuleError("PROVIDER_JOB_MISMATCH", "provider_job_id 与 Attempt 记录不一致")
            connection.execute(
                "UPDATE job_attempts SET state='SUCCEEDED', lease_token=NULL, lease_expires_at=NULL, error_code=NULL, finished_at=?, updated_at=?, revision=revision+1 WHERE id=?",
                (now, now, attempt_id),
            )
            connection.execute(
                "UPDATE jobs SET state='SUCCEEDED', next_run_at=NULL, last_error_code=NULL, last_error_detail_redacted=NULL, finished_at=?, updated_at=?, revision=revision+1 WHERE id=?", (now, now, row["job_id"])
            )
            self._sync_experiment_cell_status(connection, str(row["job_id"]), SUCCEEDED, now)
            connection.execute("UPDATE job_resource_leases SET released_at=? WHERE attempt_id=? AND released_at IS NULL", (now, attempt_id))
            self._emit(connection, "JOB_RECOVERED", row["project_id"], "JOB_ATTEMPT", attempt_id, {"job_id": row["job_id"], "provider_job_id": provider_job_id})
            return {"job_id": row["job_id"], "attempt_id": attempt_id, "attempt_state": SUCCEEDED, "job_state": SUCCEEDED, "recovered": True, "actor": actor}

    def cancel(self, job_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _iso(_utc_now())
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在")
            if row["state"] in {SUCCEEDED, FAILED, CANCELLED}:
                return self._job_response(row)
            target = CANCELLED if row["state"] == QUEUED else CANCEL_REQUESTED
            connection.execute("UPDATE jobs SET state=?, cancel_requested_at=?, updated_at=?, revision=revision+1 WHERE id=?", (target, now, now, job_id))
            self._sync_experiment_cell_status(connection, job_id, target, now)
            self._emit(connection, "JOB_CANCEL_REQUESTED", row["project_id"], "JOB", job_id, {"state": target})
            updated = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if target == CANCELLED:
                self._last_automatic_reconcile_at = None
            return self._job_response(updated)

    def retry(self, job_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _iso(_utc_now())
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在")
            if row["state"] not in {FAILED, NEEDS_ATTENTION, ORPHANED}:
                raise DomainRuleError("JOB_NOT_RETRYABLE", "只有失败、孤儿或需人工关注的 Job 可以 retry")
            connection.execute(
                "UPDATE jobs SET state='QUEUED', next_run_at=?, cancel_requested_at=NULL, progress_json='{}', progress_updated_at=NULL, last_error_code=NULL, last_error_detail_redacted=NULL, finished_at=NULL, updated_at=?, revision=revision+1 WHERE id=?", (now, now, job_id)
            )
            self._sync_experiment_cell_status(connection, job_id, QUEUED, now)
            self._emit(connection, "JOB_REQUEUED", row["project_id"], "JOB", job_id, {"reason": "explicit_retry"})
            updated = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._job_response(updated)

    def clone(self, job_id: str, idempotency_key: str, input_overrides: dict[str, Any] | None = None, actor: str = "local-user") -> dict[str, Any]:
        source = self.get_job(job_id)
        input_snapshot = {**source["input_snapshot"], **(input_overrides or {}), "clone_of_job_id": job_id}
        return self.create_job(
            str(source["project_id"]),
            str(source["type"]),
            str(source["subject_type"]),
            str(source["subject_id"]),
            str(source["channel"]),
            input_snapshot,
            idempotency_key,
            execution_profile_version_id=source["execution_profile_version_id"],
            priority=int(source["priority"]),
            max_attempts=int(source["max_attempts"]),
            actor=actor,
        )

    def reconcile(self, *, now: datetime | None = None, actor: str = "reconciler") -> dict[str, Any]:
        current = now or _utc_now()
        current_iso = _iso(current)
        recovered: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT a.*, j.project_id, j.max_attempts, j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.state IN ('CLAIMED','RUNNING') AND a.lease_expires_at IS NOT NULL AND a.lease_expires_at<?",
                (current_iso,),
            ).fetchall()
            for row in rows:
                uncertain = bool(row["provider_job_id"])
                next_job_state = NEEDS_ATTENTION if uncertain else (QUEUED if int(row["attempt_no"]) < int(row["max_attempts"]) else NEEDS_ATTENTION)
                connection.execute(
                    "UPDATE job_attempts SET state='ORPHANED', error_code='WORKER_LEASE_EXPIRED', error_detail_redacted='lease expired; reconciled locally', lease_token=NULL, updated_at=?, revision=revision+1 WHERE id=?",
                    (current_iso, row["id"]),
                )
                connection.execute(
                    "UPDATE jobs SET state=?, next_run_at=?, last_error_code='WORKER_LEASE_EXPIRED', updated_at=?, revision=revision+1 WHERE id=?",
                    (next_job_state, current_iso if next_job_state == QUEUED else None, current_iso, row["job_id"]),
                )
                connection.execute("UPDATE job_resource_leases SET released_at=? WHERE attempt_id=? AND released_at IS NULL", (current_iso, row["id"]))
                self._emit(
                    connection,
                    "JOB_RECONCILED",
                    row["project_id"],
                    "JOB_ATTEMPT",
                    row["id"],
                    {"job_id": row["job_id"], "attempt_state": ORPHANED, "job_state": next_job_state, "uncertain_side_effect": uncertain},
                )
                recovered.append({"attempt_id": row["id"], "job_id": row["job_id"], "job_state": next_job_state, "uncertain_side_effect": uncertain})
            blocked_rows = connection.execute(
                """SELECT j.id AS job_id, j.project_id, dependency.id AS dependency_id,
                dependency.state AS dependency_state
                FROM jobs j JOIN job_dependencies d ON d.job_id=j.id
                JOIN jobs dependency ON dependency.id=d.depends_on_job_id
                WHERE j.state='QUEUED' AND dependency.state IN ('FAILED','CANCELLED','NEEDS_ATTENTION','ORPHANED')
                ORDER BY j.created_at, dependency.created_at"""
            ).fetchall()
            propagated: set[str] = set()
            for row in blocked_rows:
                job_id = str(row["job_id"])
                if job_id in propagated:
                    continue
                propagated.add(job_id)
                dependency_id = str(row["dependency_id"])
                dependency_state = str(row["dependency_state"])
                connection.execute(
                    """UPDATE jobs SET state='NEEDS_ATTENTION', next_run_at=NULL,
                    last_error_code='JOB_DEPENDENCY_FAILED', last_error_detail_redacted=?,
                    updated_at=?, revision=revision+1 WHERE id=? AND state='QUEUED'""",
                    (f"上游任务 {dependency_id[:12]} 状态为 {dependency_state}", current_iso, job_id),
                )
                self._sync_experiment_cell_status(connection, job_id, NEEDS_ATTENTION, current_iso)
                self._emit(
                    connection,
                    "JOB_DEPENDENCY_BLOCKED",
                    str(row["project_id"]),
                    "JOB",
                    job_id,
                    {"job_id": job_id, "dependency_job_id": dependency_id, "dependency_state": dependency_state, "job_state": NEEDS_ATTENTION},
                )
                recovered.append({
                    "job_id": job_id,
                    "job_state": NEEDS_ATTENTION,
                    "dependency_job_id": dependency_id,
                    "dependency_state": dependency_state,
                })
        return {"reconciled": len(recovered), "items": recovered, "at": current_iso}

    def events(self, *, after_event_id: int = 0, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where = ["event_id>?"]
        params: list[Any] = [after_event_id]
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM outbox_events WHERE {' AND '.join(where)} ORDER BY event_id LIMIT ?", params).fetchall()
        return [{**dict(row), "payload": _parse_json(row["payload_json"])} for row in rows]

    def register_artifact(self, attempt_id: str, kind: str, sandbox_path: str, actor: str = "worker") -> dict[str, Any]:
        if self.settings is None:
            raise DomainRuleError("WORKSPACE_REQUIRED", "artifact 注册需要本地 workspace")
        relative = Path(sandbox_path)
        if relative.is_absolute() or ".." in relative.parts or relative.name.startswith(".partial"):
            raise DomainRuleError("INVALID_ARTIFACT_PATH", "artifact 必须是 work sandbox 内的完成文件")
        root = self.settings.work_root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise DomainRuleError("ARTIFACT_NOT_FOUND", "artifact 不存在、越界或为 symlink")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        now = _iso(_utc_now())
        with self.database.transaction() as connection:
            attempt = connection.execute(
                "SELECT a.id, a.job_id, j.project_id FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE job_attempt_id=? AND kind=? AND sandbox_rel_path=?", (attempt_id, kind, relative.as_posix())
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "idempotent_replay": True}
            artifact_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO artifacts (id, job_attempt_id, kind, sandbox_rel_path, sha256, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?, 1, 'v2')",
                (artifact_id, attempt_id, kind, relative.as_posix(), digest, now, now, actor),
            )
            self._emit(
                connection,
                "ARTIFACT_REGISTERED",
                attempt["project_id"],
                "ARTIFACT",
                artifact_id,
                {"attempt_id": attempt_id, "kind": kind, "sha256": digest, "byte_size": size},
            )
            return {
                "id": artifact_id,
                "job_attempt_id": attempt_id,
                "kind": kind,
                "sandbox_rel_path": relative.as_posix(),
                "sha256": digest,
                "byte_size": size,
                "status": "VERIFIED",
                "idempotent_replay": False,
            }


def secrets_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex
