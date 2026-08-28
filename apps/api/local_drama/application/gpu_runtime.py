"""Cross-process orchestration for one physical CUDA device.

The database lease serializes API and worker processes.  Runtime adapters own
the actual model eviction because CUDA memory can only be released by the
process that allocated it.  Switching is strict; idle cleanup is best-effort
and leaves an auditable DEGRADED state instead of corrupting a completed Job.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator
from urllib.parse import urlparse

from local_drama.application.job_resources import GPU_EXCLUSIVE_RESOURCE, GpuRuntime, gpu_runtime_for_job
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.ollama_runtime import OllamaRuntimeClient


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


class GpuRuntimeCoordinator:
    LEASE_SECONDS = 120
    HEARTBEAT_SECONDS = 20
    SWITCH_TIMEOUT_SECONDS = 30.0
    MIN_FREE_RATIO = 0.80

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        comfy: Any | None = None,
        ollama: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.database = database
        self.settings = settings
        self.comfy = comfy or ComfyClient(
            settings.comfy_base_url,
            settings.comfy_output_root,
            timeout_seconds=5,
            allow_private_network=settings.allows_private_network,
        )
        llm_host = (urlparse(settings.llm_base_url).hostname or "").casefold()
        self.ollama = ollama or (
            OllamaRuntimeClient(settings.llm_base_url, timeout_seconds=10)
            if llm_host in {"127.0.0.1", "localhost", "::1"}
            else None
        )
        self._sleep = sleep

    def acquire(self, runtime: GpuRuntime, *, owner_kind: str, owner_ref: str) -> dict[str, str]:
        current = _now()
        token = uuid.uuid4().hex + uuid.uuid4().hex
        expires = current + timedelta(seconds=self.LEASE_SECONDS)
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE gpu_runtime_leases SET released_at=?,release_reason='LEASE_EXPIRED'
                WHERE resource_key=? AND released_at IS NULL AND lease_expires_at<=?""",
                (_iso(current), GPU_EXCLUSIVE_RESOURCE, _iso(current)),
            )
            active = connection.execute(
                """SELECT owner_kind,owner_ref,runtime_kind,lease_expires_at
                FROM gpu_runtime_leases WHERE resource_key=? AND released_at IS NULL""",
                (GPU_EXCLUSIVE_RESOURCE,),
            ).fetchone()
            if active is not None:
                raise DomainRuleError(
                    "GPU_RUNTIME_BUSY",
                    "单卡 GPU 正由其他任务独占使用",
                    {
                        "owner_kind": str(active["owner_kind"]),
                        "owner_ref": str(active["owner_ref"]),
                        "runtime_kind": str(active["runtime_kind"]),
                        "lease_expires_at": str(active["lease_expires_at"]),
                    },
                    suggested_action="等待当前 GPU 任务结束后重试",
                )
            connection.execute(
                """INSERT INTO gpu_runtime_leases
                (id,resource_key,owner_token,owner_kind,owner_ref,runtime_kind,acquired_at,heartbeat_at,lease_expires_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    GPU_EXCLUSIVE_RESOURCE,
                    token,
                    owner_kind,
                    owner_ref,
                    runtime.value,
                    _iso(current),
                    _iso(current),
                    _iso(expires),
                ),
            )
            connection.execute(
                """UPDATE gpu_runtime_state SET status='TRANSITIONING',active_owner_ref=?,
                last_error_code=NULL,last_error_detail_redacted=NULL,updated_at=? WHERE resource_key=?""",
                (owner_ref, _iso(current), GPU_EXCLUSIVE_RESOURCE),
            )
        return {"token": token, "runtime": runtime.value, "owner_ref": owner_ref}

    def heartbeat(self, token: str) -> None:
        current = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE gpu_runtime_leases SET heartbeat_at=?,lease_expires_at=?
                WHERE owner_token=? AND released_at IS NULL""",
                (_iso(current), _iso(current + timedelta(seconds=self.LEASE_SECONDS)), token),
            )
            if cursor.rowcount != 1:
                raise DomainRuleError("GPU_RUNTIME_LEASE_LOST", "单卡 GPU 运行时租约已丢失")

    def release(self, token: str, *, reason: str = "COMPLETED") -> None:
        current = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE gpu_runtime_leases SET released_at=?,release_reason=?
                WHERE owner_token=? AND released_at IS NULL""",
                (_iso(current), reason, token),
            )
            connection.execute(
                """UPDATE gpu_runtime_state SET
                status=CASE
                  WHEN status='DEGRADED' THEN 'DEGRADED'
                  WHEN resident_runtime IS NOT NULL THEN 'RESIDENT'
                  ELSE 'IDLE'
                END,
                active_owner_ref=NULL,last_release_at=?,updated_at=?
                WHERE resource_key=? AND active_owner_ref=(SELECT owner_ref FROM gpu_runtime_leases WHERE owner_token=?)""",
                (_iso(current), _iso(current), GPU_EXCLUSIVE_RESOURCE, token),
            )

    def _mark_ready(self, runtime: GpuRuntime, owner_ref: str) -> None:
        current = _iso(_now())
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE gpu_runtime_state SET resident_runtime=?,status='READY',active_owner_ref=?,
                last_transition_at=?,last_error_code=NULL,last_error_detail_redacted=NULL,updated_at=?
                WHERE resource_key=?""",
                (runtime.value, owner_ref, current, current, GPU_EXCLUSIVE_RESOURCE),
            )

    def _mark_degraded(self, error: BaseException) -> None:
        current = _iso(_now())
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE gpu_runtime_state SET status='DEGRADED',last_error_code=?,
                last_error_detail_redacted=?,updated_at=? WHERE resource_key=?""",
                (
                    str(getattr(error, "code", type(error).__name__))[:80],
                    str(getattr(error, "message", type(error).__name__))[:500],
                    current,
                    GPU_EXCLUSIVE_RESOURCE,
                ),
            )

    @staticmethod
    def _queue_is_empty(queue: dict[str, Any]) -> bool:
        return not list(queue.get("queue_running") or []) and not list(queue.get("queue_pending") or [])

    def _wait_for_free_vram(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.SWITCH_TIMEOUT_SECONDS
        last: dict[str, Any] = {}
        while True:
            stats = self.comfy.system_stats()
            devices = list(stats.get("devices") or [])
            if not devices:
                return stats
            device = devices[0] if isinstance(devices[0], dict) else {}
            total = int(device.get("vram_total") or 0)
            free = int(device.get("vram_free") or 0)
            last = {"vram_total": total, "vram_free": free}
            if total <= 0 or free / total >= self.MIN_FREE_RATIO:
                return last
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "GPU_VRAM_NOT_RELEASED",
                    "运行时已请求卸载模型，但显存未在时限内释放",
                    last,
                    suggested_action="检查是否有项目外 CUDA 进程占用显存",
                )
            self._sleep(0.25)

    def prepare(self, runtime: GpuRuntime, *, owner_ref: str) -> None:
        if runtime in {GpuRuntime.OLLAMA, GpuRuntime.PYTORCH}:
            comfy_available = True
            try:
                queue = self.comfy.queue()
            except DomainRuleError as error:
                if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                    if runtime is GpuRuntime.OLLAMA:
                        self._mark_ready(runtime, owner_ref)
                        return
                    comfy_available = False
                    queue = {"queue_running": [], "queue_pending": []}
                else:
                    raise
            if not self._queue_is_empty(queue):
                raise DomainRuleError("GPU_RUNTIME_EXTERNAL_COMFY_BUSY", "ComfyUI 仍有运行或排队任务，拒绝抢占显存")
            if comfy_available:
                self.comfy.free_memory(unload_models=True, free_memory=True)
                self._wait_for_free_vram()
        if runtime in {GpuRuntime.COMFY, GpuRuntime.PYTORCH}:
            if self.ollama is None:
                self._mark_ready(runtime, owner_ref)
                return
            try:
                self.ollama.unload_all()
                waiter = getattr(self.ollama, "wait_until_unloaded", None)
                if callable(waiter):
                    waiter(timeout_seconds=self.SWITCH_TIMEOUT_SECONDS)
            except DomainRuleError as error:
                # A stopped Ollama service owns no VRAM. Other lifecycle errors
                # remain fatal because they leave ownership uncertain.
                if error.code != "OLLAMA_RUNTIME_UNAVAILABLE":
                    raise
        self._mark_ready(runtime, owner_ref)

    def _same_runtime_waiting(self, runtime: GpuRuntime) -> bool:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT type,channel,input_snapshot_json FROM jobs
                WHERE state='QUEUED' ORDER BY priority,created_at LIMIT 128"""
            ).fetchall()
        return any(gpu_runtime_for_job(dict(row)) is runtime for row in rows)

    def cleanup(self, runtime: GpuRuntime, *, retain_if_same_runtime_waiting: bool) -> bool:
        if retain_if_same_runtime_waiting and self._same_runtime_waiting(runtime):
            return True
        if runtime is GpuRuntime.OLLAMA:
            if self.ollama is None:
                raise DomainRuleError("OLLAMA_RUNTIME_UNMANAGED", "本机 Ollama Runtime 未配置为可管理的 loopback endpoint")
            try:
                self.ollama.unload_all()
                waiter = getattr(self.ollama, "wait_until_unloaded", None)
                if callable(waiter):
                    waiter(timeout_seconds=self.SWITCH_TIMEOUT_SECONDS)
            except DomainRuleError as error:
                if error.code != "OLLAMA_RUNTIME_UNAVAILABLE":
                    raise
        elif runtime is GpuRuntime.COMFY:
            queue = self.comfy.queue()
            if not self._queue_is_empty(queue):
                raise DomainRuleError("GPU_RUNTIME_COMFY_STILL_BUSY", "ComfyUI 任务未终止，不能释放模型")
            self.comfy.free_memory(unload_models=True, free_memory=True)
            self._wait_for_free_vram()
        # PYTORCH jobs execute in one-shot child processes. Process exit is the
        # authoritative CUDA release boundary, so no external runtime endpoint
        # remains to unload here.
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE gpu_runtime_state SET resident_runtime=NULL,updated_at=? WHERE resource_key=?""",
                (_iso(_now()), GPU_EXCLUSIVE_RESOURCE),
            )
        return False

    @contextmanager
    def session(
        self,
        runtime: GpuRuntime,
        *,
        owner_kind: str,
        owner_ref: str,
        retain_if_same_runtime_waiting: bool = False,
    ) -> Iterator[dict[str, str]]:
        lease = self.acquire(runtime, owner_kind=owner_kind, owner_ref=owner_ref)
        stop = threading.Event()
        heartbeat_errors: list[BaseException] = []

        def pump() -> None:
            while not stop.wait(self.HEARTBEAT_SECONDS):
                try:
                    self.heartbeat(lease["token"])
                except BaseException as error:  # persisted and surfaced at the operation boundary
                    heartbeat_errors.append(error)
                    return

        thread = threading.Thread(target=pump, name=f"gpu-runtime-{runtime.value.lower()}", daemon=True)
        thread.start()
        succeeded = False
        try:
            self.prepare(runtime, owner_ref=owner_ref)
            yield lease
            if heartbeat_errors:
                raise DomainRuleError("GPU_RUNTIME_LEASE_LOST", "执行期间单卡 GPU 运行时租约丢失")
            succeeded = True
        except BaseException as error:
            self._mark_degraded(error)
            raise
        finally:
            stop.set()
            thread.join(timeout=2)
            if succeeded:
                try:
                    self.cleanup(runtime, retain_if_same_runtime_waiting=retain_if_same_runtime_waiting)
                except BaseException as cleanup_error:
                    # Output authority must not be reversed by post-success
                    # cleanup. The next switch sees DEGRADED and retries strict
                    # eviction before loading another runtime.
                    self._mark_degraded(cleanup_error)
            self.release(lease["token"], reason="COMPLETED" if succeeded else "FAILED")

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT * FROM gpu_runtime_state WHERE resource_key=?", (GPU_EXCLUSIVE_RESOURCE,)
            ).fetchone()
            lease = connection.execute(
                """SELECT owner_kind,owner_ref,runtime_kind,acquired_at,heartbeat_at,lease_expires_at
                FROM gpu_runtime_leases WHERE resource_key=? AND released_at IS NULL""",
                (GPU_EXCLUSIVE_RESOURCE,),
            ).fetchone()
        return {"state": dict(state) if state else None, "active_lease": dict(lease) if lease else None}
