"""Durable worker session, heartbeat and version-handshake supervision facts."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

WORKER_PROTOCOL_VERSION = "localdrama.worker-session.v1"
ACTIVE_SESSION_STATES = ("STARTING", "RUNNING", "BACKING_OFF", "DRAINING")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkerSessionService:
    """Owns session truth independently of an API process or browser window."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def start_session(
        self,
        worker_id: str,
        *,
        worker_version: str,
        api_version: str,
        protocol_version: str = WORKER_PROTOCOL_VERSION,
        channels: list[str] | None = None,
        process_id: int | None = None,
        lease_seconds: int = 30,
        actor: str = "supervisor",
    ) -> dict[str, Any]:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id or len(normalized_worker_id) > 100:
            raise DomainRuleError("WORKER_ID_INVALID", "worker_id 必须为 1—100 字符")
        if lease_seconds < 5 or lease_seconds > 3600:
            raise DomainRuleError("WORKER_SESSION_LEASE_INVALID", "WorkerSession lease 必须为 5—3600 秒")
        normalized_channels = sorted({str(item).strip().upper() for item in (channels or ["CPU"]) if str(item).strip()})
        if not normalized_channels:
            raise DomainRuleError("WORKER_CHANNELS_REQUIRED", "WorkerSession 至少声明一个 channel")
        current = _now()
        compatible = (
            worker_version == self.settings.app_version
            and api_version == self.settings.app_version
            and protocol_version == WORKER_PROTOCOL_VERSION
        )
        status = "RUNNING" if compatible else "INCOMPATIBLE"
        session_id = str(uuid.uuid4())
        expires = current + timedelta(seconds=lease_seconds)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """SELECT id FROM worker_sessions
                WHERE worker_id=? AND status IN ('STARTING','RUNNING','BACKING_OFF','DRAINING')
                AND lease_expires_at>? ORDER BY started_at DESC LIMIT 1""",
                (normalized_worker_id, _iso(current)),
            ).fetchone()
            if existing is not None:
                raise DomainRuleError(
                    "WORKER_SESSION_ALREADY_ACTIVE",
                    "同一 worker_id 已有有效 WorkerSession",
                    {"worker_id": normalized_worker_id, "session_id": str(existing["id"])},
                )
            error_detail = None if compatible else _json({
                "expected_api_version": self.settings.app_version,
                "expected_worker_version": self.settings.app_version,
                "expected_protocol_version": WORKER_PROTOCOL_VERSION,
                "actual_api_version": api_version,
                "actual_worker_version": worker_version,
                "actual_protocol_version": protocol_version,
            })
            connection.execute(
                """INSERT INTO worker_sessions
                (id,worker_id,process_id,api_version,worker_version,protocol_version,supported_channels_json,
                 status,started_at,heartbeat_at,lease_expires_at,last_error_redacted,created_at,updated_at,
                 created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?,?, ?,1,'v1')""",
                (
                    session_id,
                    normalized_worker_id,
                    process_id if process_id is not None else os.getpid(),
                    api_version,
                    worker_version,
                    protocol_version,
                    _json(normalized_channels),
                    status,
                    _iso(current),
                    _iso(current),
                    _iso(expires),
                    error_detail,
                    _iso(current),
                    _iso(current),
                    actor,
                ),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM worker_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在", {"session_id": session_id})
        item = dict(row)
        item["supported_channels"] = json.loads(str(item.pop("supported_channels_json") or "[]"))
        item["compatible"] = item["status"] != "INCOMPATIBLE"
        return item

    def list_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM worker_sessions ORDER BY started_at DESC,id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        current = _now()
        for row in rows:
            item = dict(row)
            item["supported_channels"] = json.loads(str(item.pop("supported_channels_json") or "[]"))
            persisted_status = str(item["status"])
            item["effective_status"] = (
                "STALE"
                if persisted_status in ACTIVE_SESSION_STATES and datetime.fromisoformat(str(item["lease_expires_at"])) <= current
                else persisted_status
            )
            item["compatible"] = persisted_status != "INCOMPATIBLE"
            result.append(item)
        return result

    def heartbeat(self, session_id: str, *, lease_seconds: int = 30) -> dict[str, Any]:
        if lease_seconds < 5 or lease_seconds > 3600:
            raise DomainRuleError("WORKER_SESSION_LEASE_INVALID", "WorkerSession lease 必须为 5—3600 秒")
        current = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM worker_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
            if str(row["status"]) not in ACTIVE_SESSION_STATES:
                raise DomainRuleError("WORKER_SESSION_NOT_ACTIVE", "WorkerSession 当前不接受 heartbeat", {"status": row["status"]})
            if datetime.fromisoformat(str(row["lease_expires_at"])) <= current:
                connection.execute(
                    "UPDATE worker_sessions SET status='STALE',updated_at=?,revision=revision+1 WHERE id=?",
                    (_iso(current), session_id),
                )
                raise DomainRuleError("WORKER_SESSION_EXPIRED", "WorkerSession heartbeat 已过期")
            if str(row["status"]) == "BACKING_OFF":
                connection.execute(
                    """UPDATE worker_sessions SET heartbeat_at=?,lease_expires_at=?,
                    updated_at=?,revision=revision+1 WHERE id=?""",
                    (_iso(current), _iso(current + timedelta(seconds=lease_seconds)), _iso(current), session_id),
                )
            else:
                connection.execute(
                    """UPDATE worker_sessions
                    SET status='RUNNING',heartbeat_at=?,lease_expires_at=?,next_restart_at=NULL,
                        updated_at=?,revision=revision+1 WHERE id=?""",
                    (_iso(current), _iso(current + timedelta(seconds=lease_seconds)), _iso(current), session_id),
                )
        return self.get_session(session_id)

    def record_failure(self, session_id: str, error: BaseException, *, exit_code: int | None = None) -> dict[str, Any]:
        current = _now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT consecutive_failure_count,status FROM worker_sessions WHERE id=?", (session_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
            if str(row["status"]) not in ACTIVE_SESSION_STATES:
                raise DomainRuleError("WORKER_SESSION_NOT_ACTIVE", "WorkerSession 已停止，不能记录 restart")
            failures = int(row["consecutive_failure_count"]) + 1
            delay_seconds = min(60, 2 ** min(failures - 1, 6))
            next_restart = current + timedelta(seconds=delay_seconds)
            connection.execute(
                """UPDATE worker_sessions SET status='BACKING_OFF',restart_count=restart_count+1,
                consecutive_failure_count=?,next_restart_at=?,lease_expires_at=?,last_exit_code=?,last_error_redacted=?,
                updated_at=?,revision=revision+1 WHERE id=?""",
                (
                    failures,
                    _iso(next_restart),
                    _iso(next_restart + timedelta(seconds=30)),
                    exit_code,
                    type(error).__name__,
                    _iso(current),
                    session_id,
                ),
            )
        return self.get_session(session_id)

    def mark_running(self, session_id: str, *, lease_seconds: int = 30) -> dict[str, Any]:
        current = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM worker_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
            if str(row["status"]) != "BACKING_OFF":
                raise DomainRuleError("WORKER_SESSION_NOT_BACKING_OFF", "WorkerSession 当前不在 restart backoff")
            connection.execute(
                """UPDATE worker_sessions SET status='RUNNING',heartbeat_at=?,lease_expires_at=?,
                next_restart_at=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (_iso(current), _iso(current + timedelta(seconds=lease_seconds)), _iso(current), session_id),
            )
        return self.get_session(session_id)

    def record_success(self, session_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM worker_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
            if str(row["status"]) != "RUNNING":
                raise DomainRuleError("WORKER_SESSION_NOT_ACTIVE", "WorkerSession 当前不接受成功状态")
            connection.execute(
                """UPDATE worker_sessions SET consecutive_failure_count=0,updated_at=?,
                revision=revision+1 WHERE id=?""",
                (_iso(_now()), session_id),
            )
        return self.get_session(session_id)

    def stop(self, session_id: str, *, exit_code: int = 0) -> dict[str, Any]:
        current = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM worker_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKER_SESSION_NOT_FOUND", "WorkerSession 不存在")
            if str(row["status"]) not in {"STOPPED", "STALE", "INCOMPATIBLE"}:
                connection.execute(
                    """UPDATE worker_sessions SET status='STOPPED',stopped_at=?,last_exit_code=?,
                    updated_at=?,revision=revision+1 WHERE id=?""",
                    (_iso(current), exit_code, _iso(current), session_id),
                )
        return self.get_session(session_id)

    def reconcile(self, *, current: datetime | None = None) -> dict[str, Any]:
        observed = current or _now()
        with self.database.transaction() as connection:
            stale_ids = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM worker_sessions
                    WHERE status IN ('STARTING','RUNNING','BACKING_OFF','DRAINING') AND lease_expires_at<=?""",
                    (_iso(observed),),
                ).fetchall()
            ]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                connection.execute(
                    f"""UPDATE worker_sessions SET status='STALE',updated_at=?,revision=revision+1
                    WHERE id IN ({placeholders})""",
                    [_iso(observed), *stale_ids],
                )
        # A dead session is stronger evidence than a still-unexpired attempt
        # lease: the owning process can no longer heartbeat or complete it.
        # Orphan session-bound attempts immediately so a killed GPU worker does
        # not leak the global GPU lease for the remainder of a long Job lease.
        from local_drama.application.jobs import JobService

        session_recovered: list[dict[str, Any]] = []
        jobs = JobService(self.database, self.settings)
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            with self.database.transaction() as connection:
                attempts = connection.execute(
                    f"""SELECT a.id,a.job_id,a.attempt_no,a.provider_job_id,j.project_id,j.max_attempts
                    FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                    WHERE a.worker_session_id IN ({placeholders}) AND a.state IN ('CLAIMED','RUNNING')""",
                    stale_ids,
                ).fetchall()
                for attempt in attempts:
                    uncertain = bool(attempt["provider_job_id"])
                    next_job_state = "NEEDS_ATTENTION" if uncertain or int(attempt["attempt_no"]) >= int(attempt["max_attempts"]) else "QUEUED"
                    connection.execute(
                        """UPDATE job_attempts SET state='ORPHANED',error_code='WORKER_SESSION_STALE',
                        error_detail_redacted='worker session heartbeat expired; reconciled locally',
                        lease_token=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                        (_iso(observed), attempt["id"]),
                    )
                    connection.execute(
                        """UPDATE jobs SET state=?,next_run_at=?,last_error_code='WORKER_SESSION_STALE',
                        updated_at=?,revision=revision+1 WHERE id=?""",
                        (
                            next_job_state,
                            _iso(observed) if next_job_state == "QUEUED" else None,
                            _iso(observed),
                            attempt["job_id"],
                        ),
                    )
                    connection.execute(
                        "UPDATE job_resource_leases SET released_at=? WHERE attempt_id=? AND released_at IS NULL",
                        (_iso(observed), attempt["id"]),
                    )
                    jobs._emit(
                        connection,
                        "JOB_RECONCILED",
                        str(attempt["project_id"]),
                        "JOB_ATTEMPT",
                        str(attempt["id"]),
                        {
                            "job_id": str(attempt["job_id"]),
                            "attempt_state": "ORPHANED",
                            "job_state": next_job_state,
                            "uncertain_side_effect": uncertain,
                            "reason": "worker_session_stale",
                        },
                    )
                    session_recovered.append(
                        {
                            "attempt_id": str(attempt["id"]),
                            "job_id": str(attempt["job_id"]),
                            "job_state": next_job_state,
                            "uncertain_side_effect": uncertain,
                        }
                    )
        # Also retain the generic deadline-based recovery path for legacy or
        # external attempts that predate WorkerSession binding.
        job_result = jobs.reconcile(now=observed, actor="worker-session-reconciler")
        return {
            "stale_session_ids": stale_ids,
            "session_attempt_reconcile": {"reconciled": len(session_recovered), "items": session_recovered},
            "job_reconcile": job_result,
        }


class WorkerSupervisor:
    """Bounded in-process supervisor with persistent heartbeat/backoff facts.

    Running this command in a separate OS process allows it to continue after
    the browser closes.  It deliberately does not claim that closing the
    browser starts or keeps the supervisor alive; shell/UI status must be read
    from ``worker_sessions``.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database = database
        self.settings = settings
        self.sessions = WorkerSessionService(database, settings)
        self._sleep = sleep

    def run_until_idle(
        self,
        worker_id: str,
        *,
        channels: list[str] | None = None,
        max_jobs: int | None = 100,
        max_restarts: int = 5,
        worker_version: str | None = None,
        api_version: str | None = None,
        idle_poll_seconds: float | None = None,
        should_stop: Callable[[], bool] | None = None,
        recent_result_limit: int = 100,
    ) -> dict[str, Any]:
        from local_drama.application.comfy_jobs import ComfyGenerationService
        from local_drama.application.episode_production_runs import EpisodeProductionRunService
        from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
        from local_drama.application.storage_operations import StorageOperationService
        from local_drama.application.worker import LocalMediaWorker

        episode_runs = EpisodeProductionRunService(self.database, self.settings)
        session_reconcile = self.sessions.reconcile()
        provider_reconcile = ComfyGenerationService(self.database, self.settings).recover_uncertain_successes() if "GPU_H3" in (channels or ["CPU"]) else {"inspected": 0, "recovered": 0, "items": []}
        startup_reconcile = {
            "worker_sessions": session_reconcile,
            "provider_successes": provider_reconcile,
            "storage_operations": StorageOperationService(self.database, self.settings).reconcile(),
            "episode_runs": episode_runs.watchdog(stale_seconds=0, actor="worker-startup-watchdog"),
        }
        session = self.sessions.start_session(
            worker_id,
            worker_version=worker_version or self.settings.app_version,
            api_version=api_version or self.settings.app_version,
            channels=channels or ["CPU"],
        )
        if not bool(session["compatible"]):
            return {
                "session": session,
                "processed": 0,
                "results": [],
                "status": "INCOMPATIBLE",
                "startup_reconcile": startup_reconcile,
            }
        session_id = str(session["id"])
        results: list[dict[str, Any]] = []
        processed = 0
        restarts = 0
        needs_success_reset = False
        gpu_coordinator = GpuRuntimeCoordinator(self.database, self.settings)
        worker = LocalMediaWorker(self.database, self.settings, gpu_coordinator=gpu_coordinator)
        comfy_worker = ComfyGenerationService(self.database, self.settings, gpu_coordinator=gpu_coordinator)
        last_episode_watchdog_at = time.monotonic()
        last_episode_watchdog = startup_reconcile["episode_runs"]
        last_provider_reconcile = provider_reconcile
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[BaseException] = []

        def pump_heartbeat() -> None:
            while not heartbeat_stop.wait(10.0):
                try:
                    self.sessions.heartbeat(session_id)
                except BaseException as error:
                    heartbeat_errors.append(error)
                    return

        heartbeat_thread = threading.Thread(
            target=pump_heartbeat,
            name=f"worker-heartbeat-{worker_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            while max_jobs is None or processed < max(0, max_jobs):
                if should_stop is not None and should_stop():
                    break
                if heartbeat_errors:
                    raise DomainRuleError("WORKER_SESSION_HEARTBEAT_FAILED", "WorkerSession 后台 heartbeat 失败")
                self.sessions.heartbeat(session_id)
                try:
                    requested_channels = channels or ["CPU"]
                    result = None
                    if "GPU_H3" in requested_channels:
                        result = comfy_worker.run_once(
                            worker_id,
                            worker_session_id=session_id,
                            sleep=self._sleep,
                        )
                    if result is None:
                        local_channels = [channel for channel in requested_channels if channel != "GPU_H3"]
                        if local_channels:
                            result = worker.run_once(worker_id, local_channels, worker_session_id=session_id)
                except Exception as error:
                    restarts += 1
                    needs_success_reset = True
                    failed = self.sessions.record_failure(session_id, error, exit_code=1)
                    if restarts > max_restarts:
                        self.sessions.stop(session_id, exit_code=1)
                        raise
                    next_restart = datetime.fromisoformat(str(failed["next_restart_at"]))
                    self._sleep(max(0.0, (next_restart - _now()).total_seconds()))
                    self.sessions.mark_running(session_id)
                    gpu_coordinator = GpuRuntimeCoordinator(self.database, self.settings)
                    worker = LocalMediaWorker(self.database, self.settings, gpu_coordinator=gpu_coordinator)
                    comfy_worker = ComfyGenerationService(self.database, self.settings, gpu_coordinator=gpu_coordinator)
                    continue
                if result is not None or needs_success_reset:
                    self.sessions.record_success(session_id)
                    needs_success_reset = False
                if result is None:
                    if idle_poll_seconds is None:
                        break
                    now_monotonic = time.monotonic()
                    if now_monotonic - last_episode_watchdog_at >= 30.0:
                        last_episode_watchdog = episode_runs.watchdog()
                        if "GPU_H3" in requested_channels:
                            last_provider_reconcile = comfy_worker.recover_uncertain_successes()
                        last_episode_watchdog_at = now_monotonic
                    self._sleep(max(0.05, float(idle_poll_seconds)))
                    continue
                processed += 1
                results.append(result)
                if len(results) > max(1, int(recent_result_limit)):
                    results.pop(0)
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            stopped = self.sessions.stop(session_id, exit_code=0)
            return {
                "session": stopped,
                "processed": processed,
                "results": results,
                "status": "STOPPED",
                "startup_reconcile": startup_reconcile,
                "maintenance": {"episode_runs": last_episode_watchdog, "provider_successes": last_provider_reconcile},
            }
        except Exception:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            self.sessions.stop(session_id, exit_code=1)
            raise
        except BaseException:
            # A normal exception is persisted above.  KeyboardInterrupt,
            # termination and hard process kill are intentionally recovered by
            # the expiry reconciler instead of being mislabeled as clean stop.
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            raise
