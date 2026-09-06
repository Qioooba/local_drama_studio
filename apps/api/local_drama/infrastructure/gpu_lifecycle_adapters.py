"""Runtime adapters behind the GPU lifecycle contract.

Each adapter reproduces the eviction semantics the coordinator previously
hardcoded: identical error codes, identical tolerance of an unreachable
external service while switching, and the ComfyUI VRAM gate after model
unload.  ``ManagedLlamaCppGpuLifecycleAdapter`` adds the one behaviour that
did not exist before -- starting and terminating an owned llama-server child.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from local_drama.application.job_resources import GpuRuntime
from local_drama.application.ports.gpu_lifecycle import GpuEvictMode, GpuLifecycleAdapterRegistry, GpuMemoryReleaseGate
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.llama_server_manager import LlamaServerLaunchSpec, LlamaServerManager, llama_launch_spec_from_settings
from local_drama.infrastructure.ollama_runtime import OllamaRuntimeClient


def _queue_is_empty(queue: dict[str, Any]) -> bool:
    return not list(queue.get("queue_running") or []) and not list(queue.get("queue_pending") or [])


class ComfyGpuLifecycleAdapter:
    """Evict ComfyUI models after enforcing its queue ownership boundary."""

    runtime = GpuRuntime.COMFY

    def __init__(self, comfy: Any) -> None:
        self.comfy = comfy

    def activate(self, context: Mapping[str, object] | None = None) -> None:
        del context
        return None

    def evict(self, mode: GpuEvictMode) -> bool:
        if mode is GpuEvictMode.SWITCH:
            try:
                queue = self.comfy.queue()
            except DomainRuleError as error:
                # A stopped ComfyUI owns no VRAM; switching past it is safe.
                if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                    return False
                raise
            if not _queue_is_empty(queue):
                raise DomainRuleError("GPU_RUNTIME_EXTERNAL_COMFY_BUSY", "ComfyUI 仍有运行或排队任务，拒绝抢占显存")
        else:
            queue = self.comfy.queue()
            if not _queue_is_empty(queue):
                raise DomainRuleError("GPU_RUNTIME_COMFY_STILL_BUSY", "ComfyUI 任务未终止，不能释放模型")
        self.comfy.free_memory(unload_models=True, free_memory=True)
        return True


class GpuMemoryProbe(Protocol):
    def snapshot(self) -> tuple[int, int] | None: ...


class NvidiaSmiGpuMemoryProbe:
    """Read device-wide VRAM rather than one runtime process's allocator."""

    def snapshot(self) -> tuple[int, int] | None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                timeout=5,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
        try:
            total_mib, free_mib = (int(item.strip()) for item in first_line.split(",", 1))
        except (TypeError, ValueError):
            return None
        mib = 1024**2
        return total_mib * mib, free_mib * mib


class SystemGpuMemoryReleaseGate:
    """Wait for device-wide VRAM release, with Comfy telemetry as fallback.

    ComfyUI's ``system_stats`` can expose allocator-local free memory on
    Windows and therefore cannot prove that another process released CUDA.
    nvidia-smi is the primary device-wide source; Comfy remains useful on
    systems where that executable is unavailable and for its own cleanup.
    """

    SWITCH_TIMEOUT_SECONDS = 30.0
    MIN_FREE_RATIO = 0.80

    def __init__(
        self,
        comfy: Any,
        *,
        system_probe: GpuMemoryProbe | None = None,
        min_free_ratio: float | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.comfy = comfy
        self.system_probe = system_probe or NvidiaSmiGpuMemoryProbe()
        self.min_free_ratio = self.MIN_FREE_RATIO if min_free_ratio is None else float(min_free_ratio)
        self._sleep = sleep

    def wait_until_released(self) -> bool:
        deadline = time.monotonic() + self.SWITCH_TIMEOUT_SECONDS
        while True:
            snapshot = self.system_probe.snapshot()
            if snapshot is None:
                snapshot = self._comfy_snapshot()
            if snapshot is None:
                return False
            total, free = snapshot
            if total <= 0 or free / total >= self.min_free_ratio:
                return True
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "GPU_VRAM_NOT_RELEASED",
                    "运行时已请求卸载模型，但显存未在时限内释放",
                    {"vram_total": total, "vram_free": free},
                    suggested_action="检查是否有项目外 CUDA 进程占用显存",
                )
            self._sleep(0.25)

    def _comfy_snapshot(self) -> tuple[int, int] | None:
        try:
            stats = self.comfy.system_stats()
        except DomainRuleError as error:
            if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                return None
            raise
        devices = list(stats.get("devices") or [])
        if not devices:
            return 0, 0
        device = devices[0] if isinstance(devices[0], dict) else {}
        return int(device.get("vram_total") or 0), int(device.get("vram_free") or 0)


class OllamaGpuLifecycleAdapter:
    """Unload every resident Ollama model via keep_alive=0."""

    runtime = GpuRuntime.OLLAMA

    def __init__(self, ollama: Any | None, *, switch_timeout_seconds: float = 30.0) -> None:
        self.ollama = ollama
        self.switch_timeout_seconds = switch_timeout_seconds

    def activate(self, context: Mapping[str, object] | None = None) -> None:
        del context
        return None

    def evict(self, mode: GpuEvictMode) -> bool:
        if self.ollama is None:
            if mode is GpuEvictMode.SWITCH:
                # An unmanaged or remote Ollama endpoint owns no local GPU.
                return False
            raise DomainRuleError("OLLAMA_RUNTIME_UNMANAGED", "本机 Ollama Runtime 未配置为可管理的 loopback endpoint")
        try:
            self.ollama.unload_all()
            waiter = getattr(self.ollama, "wait_until_unloaded", None)
            if callable(waiter):
                waiter(timeout_seconds=self.switch_timeout_seconds)
        except DomainRuleError as error:
            # A stopped Ollama service owns no VRAM. Other lifecycle errors
            # remain fatal because they leave ownership uncertain.
            if error.code != "OLLAMA_RUNTIME_UNAVAILABLE":
                raise
            return False
        return True


class PytorchProcessGpuLifecycleAdapter:
    """No external eviction: one-shot child processes release VRAM on exit."""

    runtime = GpuRuntime.PYTORCH

    def activate(self, context: Mapping[str, object] | None = None) -> None:
        del context
        return None

    def evict(self, mode: GpuEvictMode) -> bool:
        del mode
        return False


class ManagedLlamaCppGpuLifecycleAdapter:
    """Start and terminate an owned llama-server child for LLAMA_CPP leases."""

    runtime = GpuRuntime.LLAMA_CPP

    def __init__(
        self,
        manager: LlamaServerManager,
        spec_provider: Callable[[Mapping[str, object] | None], LlamaServerLaunchSpec],
    ) -> None:
        self.manager = manager
        self.spec_provider = spec_provider

    def activate(self, context: Mapping[str, object] | None = None) -> None:
        # The manager reuses a healthy server when the launch spec is
        # unchanged, so back-to-back text jobs keep the weights resident.
        self.manager.start(self.spec_provider(context))

    def evict(self, mode: GpuEvictMode) -> bool:
        del mode
        if not self.manager.is_running():
            # Avoid resolving a llama.cpp launch spec on installations that
            # have never run it.  A live manager PID record is the ownership
            # proof that permits a fresh worker to adopt and terminate it.
            if not self.manager.has_owned_process_record():
                return False
            if not self.manager.adopt_owned_process(self.spec_provider(None)):
                return False
        self.manager.stop()
        return True


@dataclass(frozen=True, slots=True)
class GpuLifecycleComponents:
    """Production lifecycle wiring sharing one telemetry/runtime client set."""

    adapters: GpuLifecycleAdapterRegistry
    memory_gate: GpuMemoryReleaseGate


def build_gpu_lifecycle_components(
    settings: Settings,
    *,
    comfy: Any | None = None,
    ollama: Any | None = None,
    llama_manager: LlamaServerManager | None = None,
    system_probe: GpuMemoryProbe | None = None,
    sleep: Any = time.sleep,
) -> GpuLifecycleComponents:
    """Wire the default adapter set from settings and optional test doubles.

    The llama-server manager is always constructed because construction is
    passive; it only launches a process when a LLAMA_CPP lease activates.
    """

    comfy_client = comfy or ComfyClient(
        settings.comfy_base_url,
        settings.comfy_output_root,
        timeout_seconds=5,
        allow_private_network=settings.allows_private_network,
    )
    manager = llama_manager or LlamaServerManager(
        log_dir=settings.logs_root / "llama",
        startup_timeout_seconds=settings.llama_startup_timeout_seconds,
        sleep=sleep,
    )
    # Eviction order: the managed llama-server child dies first so the ComfyUI
    # VRAM gate that follows verifies its ~20 GB release before anything new
    # claims the device.
    ollama_client = ollama or (
        OllamaRuntimeClient(settings.ollama_base_url, timeout_seconds=10)
        if settings.llm_provider.strip().upper() == "OLLAMA_LOOPBACK"
        else None
    )
    adapters = GpuLifecycleAdapterRegistry(
        (
            ManagedLlamaCppGpuLifecycleAdapter(
                manager,
                lambda context: llama_launch_spec_from_settings(
                    settings,
                    model_locator=str(context["model_locator"]) if context and context.get("model_locator") else None,
                ),
            ),
            ComfyGpuLifecycleAdapter(comfy_client),
            OllamaGpuLifecycleAdapter(ollama_client),
            PytorchProcessGpuLifecycleAdapter(),
        )
    )
    return GpuLifecycleComponents(
        adapters=adapters,
        memory_gate=SystemGpuMemoryReleaseGate(
            comfy_client,
            system_probe=system_probe,
            min_free_ratio=settings.gpu_switch_min_free_ratio,
            sleep=sleep,
        ),
    )


def build_gpu_lifecycle_adapters(
    settings: Settings,
    *,
    comfy: Any | None = None,
    ollama: Any | None = None,
    llama_manager: LlamaServerManager | None = None,
    system_probe: GpuMemoryProbe | None = None,
    sleep: Any = time.sleep,
) -> GpuLifecycleAdapterRegistry:
    """Compatibility helper for callers that only need adapter inspection."""

    return build_gpu_lifecycle_components(
        settings,
        comfy=comfy,
        ollama=ollama,
        llama_manager=llama_manager,
        system_probe=system_probe,
        sleep=sleep,
    ).adapters
