"""Cross-process orchestration for one physical CUDA device.

The database lease serializes API and worker processes.  Runtime adapters own
the actual model eviction because CUDA memory can only be released by the
process that allocated it; the coordinator dispatches to them polymorphically
(``prepare`` evicts every other runtime, ``cleanup`` evicts the leased one).
Switching is strict; idle cleanup is best-effort and leaves an auditable
DEGRADED state instead of corrupting a completed Job.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Iterator, Mapping

from local_drama.application.job_resources import GPU_EXCLUSIVE_RESOURCE, GpuRuntime, gpu_runtime_for_job
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.ports.gpu_lifecycle import GpuEvictMode, GpuLifecycleAdapterRegistry, GpuMemoryReleaseGate
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.gpu_lifecycle_adapters import build_gpu_lifecycle_components


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


class GpuRuntimeCoordinator:
    LEASE_SECONDS = 120
    HEARTBEAT_SECONDS = 20

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        comfy: Any | None = None,
        ollama: Any | None = None,
        llama_manager: Any | None = None,
        adapters: GpuLifecycleAdapterRegistry | None = None,
        memory_gate: GpuMemoryReleaseGate | None = None,
        system_probe: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.database = database
        self.settings = settings
        self._sleep = sleep
        # Adapters are passive at construction time; nothing is started,
        # unloaded, or probed until a lease is actually prepared.
        if (adapters is None) is not (memory_gate is None):
            raise ValueError("adapters and memory_gate must be injected together")
        if adapters is not None and system_probe is not None:
            raise ValueError("system_probe belongs to the default lifecycle wiring")
        if adapters is None:
            components = build_gpu_lifecycle_components(
                settings,
                comfy=comfy,
                ollama=ollama,
                llama_manager=llama_manager,
                system_probe=system_probe,
                sleep=sleep,
            )
            self.adapters = components.adapters
            self.memory_gate = components.memory_gate
        else:
            self.adapters = adapters
            assert memory_gate is not None
            self.memory_gate = memory_gate

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

    def prepare(
        self,
        runtime: GpuRuntime,
        *,
        owner_ref: str,
        activation_context: Mapping[str, object] | None = None,
    ) -> None:
        # Evict every other runtime that can hold the device, in adapter
        # order: the managed llama-server child dies first so the ComfyUI
        # VRAM gate verifies its release before the target loads.
        for adapter in self.adapters.others(runtime):
            adapter.evict(GpuEvictMode.SWITCH)
        # A warm runtime may intentionally survive between leases (the LLM
        # gateway idle window and back-to-back queued jobs both do this).
        # Re-adopt/reuse it instead of demanding that its own VRAM be free.
        # Other runtimes were still evicted above, preserving exclusivity.
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT resident_runtime FROM gpu_runtime_state WHERE resource_key=?",
                (GPU_EXCLUSIVE_RESOURCE,),
            ).fetchone()
        if state is not None and str(state["resident_runtime"] or "") == runtime.value:
            self.adapters.get(runtime).activate(activation_context)
            self._mark_ready(runtime, owner_ref)
            return
        # Verify only after every old runtime has been evicted. Keeping this
        # gate inside the Comfy adapter made an Ollama -> llama.cpp switch
        # inspect VRAM before Ollama had been unloaded.
        self.memory_gate.wait_until_released()
        self.adapters.get(runtime).activate(activation_context)
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
            # Back-to-back jobs on one runtime keep weights resident; the
            # managed llama-server survives here and the next lease reuses it.
            return True
        self.adapters.get(runtime).evict(GpuEvictMode.RELEASE)
        self.memory_gate.wait_until_released()
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
        activation_context: Mapping[str, object] | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> Iterator[dict[str, str]]:
        while True:
            try:
                lease = self.acquire(runtime, owner_kind=owner_kind, owner_ref=owner_ref)
                break
            except DomainRuleError as error:
                if error.code != "GPU_RUNTIME_BUSY" or on_wait is None:
                    raise
                # Resource contention is waiting, not an execution failure. The
                # owner keeps its job lease alive and checks cancellation here.
                on_wait()
                self._sleep(1)
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
            self.prepare(runtime, owner_ref=owner_ref, activation_context=activation_context)
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
