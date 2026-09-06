from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.job_resources import GpuRuntime
from local_drama.application.ports.gpu_lifecycle import GpuEvictMode, GpuLifecycleAdapterRegistry
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.gpu_lifecycle_adapters import (
    ComfyGpuLifecycleAdapter,
    ManagedLlamaCppGpuLifecycleAdapter,
    NvidiaSmiGpuMemoryProbe,
    OllamaGpuLifecycleAdapter,
    PytorchProcessGpuLifecycleAdapter,
    SystemGpuMemoryReleaseGate,
    build_gpu_lifecycle_adapters,
)
from local_drama.infrastructure.llama_server_manager import LlamaServerLaunchSpec


class _ComfyRuntime:
    def __init__(self, *, busy: bool = False, unavailable: bool = False, vram_stays_low: bool = False) -> None:
        self.free_calls = 0
        self.busy = busy
        self.unavailable = unavailable
        self.vram_stays_low = vram_stays_low

    def queue(self) -> dict[str, object]:
        if self.unavailable:
            raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "ComfyUI 不可达")
        return {"queue_running": [["prompt"]] if self.busy else [], "queue_pending": []}

    def free_memory(self, **_kwargs: object) -> dict[str, object]:
        self.free_calls += 1
        return {}

    def system_stats(self) -> dict[str, object]:
        if self.unavailable:
            raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "ComfyUI 不可达")
        total = 24 * 1024**3
        free = int(total * 0.05) if self.vram_stays_low else int(total * 0.95)
        return {"devices": [{"vram_total": total, "vram_free": free}]}


class _OllamaRuntime:
    def __init__(self, *, error_code: str | None = None) -> None:
        self.unload_calls = 0
        self.error_code = error_code

    def unload_all(self) -> list[str]:
        self.unload_calls += 1
        if self.error_code is not None:
            raise DomainRuleError(self.error_code, "ollama lifecycle failed")
        return ["qwen3.8:27b"]

    def wait_until_unloaded(self, *, timeout_seconds: float = 30.0) -> None:
        del timeout_seconds


class _LlamaManager:
    def __init__(self, *, start_error: DomainRuleError | None = None, orphaned: bool = False) -> None:
        self.start_calls: list[LlamaServerLaunchSpec] = []
        self.adopt_calls: list[LlamaServerLaunchSpec] = []
        self.stop_calls = 0
        self.running = False
        self.start_error = start_error
        self.orphaned = orphaned

    def is_running(self) -> bool:
        return self.running

    def has_owned_process_record(self) -> bool:
        return self.orphaned

    def start(self, spec: LlamaServerLaunchSpec) -> str:
        self.start_calls.append(spec)
        if self.start_error is not None:
            raise self.start_error
        self.running = True
        return "http://127.0.0.1:8101"

    def adopt_owned_process(self, spec: LlamaServerLaunchSpec) -> bool:
        self.adopt_calls.append(spec)
        self.running = self.orphaned
        return self.running

    def stop(self) -> bool:
        self.stop_calls += 1
        self.running = False
        return True


class _MemoryProbe:
    def __init__(self, *snapshots: tuple[int, int] | None) -> None:
        self.snapshots = list(snapshots)

    def snapshot(self) -> tuple[int, int] | None:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


def _spec() -> LlamaServerLaunchSpec:
    return LlamaServerLaunchSpec(
        executable=Path("llama-server.exe"),
        model_path=Path("model.gguf"),
        alias="test-model",
    )


def _spec_provider(_context: object = None) -> LlamaServerLaunchSpec:
    return _spec()


# -- ComfyUI adapter --------------------------------------------------------


def test_comfy_evict_switch_frees_models() -> None:
    comfy = _ComfyRuntime()
    adapter = ComfyGpuLifecycleAdapter(comfy)
    assert adapter.evict(GpuEvictMode.SWITCH) is True
    assert comfy.free_calls == 1


def test_comfy_evict_switch_tolerates_unavailable_service() -> None:
    adapter = ComfyGpuLifecycleAdapter(_ComfyRuntime(unavailable=True))
    assert adapter.evict(GpuEvictMode.SWITCH) is False


def test_comfy_evict_switch_refuses_busy_queue() -> None:
    adapter = ComfyGpuLifecycleAdapter(_ComfyRuntime(busy=True))
    with pytest.raises(DomainRuleError) as caught:
        adapter.evict(GpuEvictMode.SWITCH)
    assert caught.value.code == "GPU_RUNTIME_EXTERNAL_COMFY_BUSY"


def test_comfy_evict_release_refuses_busy_queue() -> None:
    adapter = ComfyGpuLifecycleAdapter(_ComfyRuntime(busy=True))
    with pytest.raises(DomainRuleError) as caught:
        adapter.evict(GpuEvictMode.RELEASE)
    assert caught.value.code == "GPU_RUNTIME_COMFY_STILL_BUSY"


def test_comfy_evict_release_requires_reachable_service() -> None:
    adapter = ComfyGpuLifecycleAdapter(_ComfyRuntime(unavailable=True))
    with pytest.raises(DomainRuleError) as caught:
        adapter.evict(GpuEvictMode.RELEASE)
    assert caught.value.code == "COMFY_LOOPBACK_UNAVAILABLE"


def test_memory_gate_times_out_when_vram_stays_low() -> None:
    total = 24 * 1024**3
    gate = SystemGpuMemoryReleaseGate(
        _ComfyRuntime(),
        system_probe=_MemoryProbe((total, int(total * 0.05))),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(DomainRuleError) as caught:
        gate.wait_until_released()
    assert caught.value.code == "GPU_VRAM_NOT_RELEASED"


def test_memory_gate_skips_when_comfy_telemetry_is_unavailable() -> None:
    gate = SystemGpuMemoryReleaseGate(
        _ComfyRuntime(unavailable=True),
        system_probe=_MemoryProbe(None),
        sleep=lambda _seconds: None,
    )
    assert gate.wait_until_released() is False


def test_memory_gate_prefers_device_wide_probe_over_comfy_allocator_view() -> None:
    total = 24 * 1024**3
    sleeps: list[float] = []
    gate = SystemGpuMemoryReleaseGate(
        _ComfyRuntime(),
        system_probe=_MemoryProbe((total, int(total * 0.05)), (total, int(total * 0.95))),
        sleep=sleeps.append,
    )
    assert gate.wait_until_released() is True
    assert sleeps == [0.25]


def test_nvidia_smi_probe_reads_total_and_free_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_drama.infrastructure.gpu_lifecycle_adapters.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="24564, 22760\n", stderr=""),
    )
    assert NvidiaSmiGpuMemoryProbe().snapshot() == (24564 * 1024**2, 22760 * 1024**2)


# -- Ollama adapter ---------------------------------------------------------


def test_ollama_evict_switch_without_client_skips() -> None:
    adapter = OllamaGpuLifecycleAdapter(None)
    assert adapter.evict(GpuEvictMode.SWITCH) is False


def test_ollama_evict_release_without_client_raises_unmanaged() -> None:
    adapter = OllamaGpuLifecycleAdapter(None)
    with pytest.raises(DomainRuleError) as caught:
        adapter.evict(GpuEvictMode.RELEASE)
    assert caught.value.code == "OLLAMA_RUNTIME_UNMANAGED"


def test_ollama_evict_tolerates_unavailable_service() -> None:
    ollama = _OllamaRuntime(error_code="OLLAMA_RUNTIME_UNAVAILABLE")
    adapter = OllamaGpuLifecycleAdapter(ollama)
    assert adapter.evict(GpuEvictMode.SWITCH) is False
    assert adapter.evict(GpuEvictMode.RELEASE) is False


def test_ollama_evict_propagates_other_errors_in_both_modes() -> None:
    for mode in (GpuEvictMode.SWITCH, GpuEvictMode.RELEASE):
        ollama = _OllamaRuntime(error_code="OLLAMA_MODELS_NOT_UNLOADED")
        adapter = OllamaGpuLifecycleAdapter(ollama)
        with pytest.raises(DomainRuleError) as caught:
            adapter.evict(mode)
        assert caught.value.code == "OLLAMA_MODELS_NOT_UNLOADED"


def test_ollama_evict_unloads_and_waits() -> None:
    ollama = _OllamaRuntime()
    adapter = OllamaGpuLifecycleAdapter(ollama)
    assert adapter.evict(GpuEvictMode.RELEASE) is True
    assert ollama.unload_calls == 1


# -- PyTorch adapter --------------------------------------------------------


def test_pytorch_adapter_is_inert() -> None:
    adapter = PytorchProcessGpuLifecycleAdapter()
    assert adapter.activate() is None
    assert adapter.evict(GpuEvictMode.SWITCH) is False
    assert adapter.evict(GpuEvictMode.RELEASE) is False


# -- Managed llama.cpp adapter ----------------------------------------------


def test_llama_adapter_activates_and_stops_manager() -> None:
    manager = _LlamaManager()
    adapter = ManagedLlamaCppGpuLifecycleAdapter(manager, _spec_provider)
    adapter.activate()
    assert manager.start_calls and manager.running is True
    assert adapter.evict(GpuEvictMode.RELEASE) is True
    assert manager.stop_calls == 1


def test_llama_adapter_activate_propagates_launch_failure() -> None:
    manager = _LlamaManager(start_error=DomainRuleError("LLAMA_SERVER_CRASHED", "crash"))
    adapter = ManagedLlamaCppGpuLifecycleAdapter(manager, _spec_provider)
    with pytest.raises(DomainRuleError) as caught:
        adapter.activate()
    assert caught.value.code == "LLAMA_SERVER_CRASHED"


def test_llama_adapter_evict_skips_when_not_running() -> None:
    manager = _LlamaManager()
    adapter = ManagedLlamaCppGpuLifecycleAdapter(manager, _spec_provider)
    assert adapter.evict(GpuEvictMode.SWITCH) is False
    assert manager.adopt_calls == []
    assert manager.stop_calls == 0


def test_llama_adapter_evict_adopts_owned_orphan_before_switch() -> None:
    manager = _LlamaManager(orphaned=True)
    adapter = ManagedLlamaCppGpuLifecycleAdapter(manager, _spec_provider)
    assert adapter.evict(GpuEvictMode.SWITCH) is True
    assert manager.adopt_calls == [_spec()]
    assert manager.stop_calls == 1


# -- Registry and factory ---------------------------------------------------


def test_registry_rejects_duplicate_and_missing_adapters() -> None:
    adapters = (
        ManagedLlamaCppGpuLifecycleAdapter(_LlamaManager(), _spec_provider),
        ComfyGpuLifecycleAdapter(_ComfyRuntime()),
        OllamaGpuLifecycleAdapter(_OllamaRuntime()),
        PytorchProcessGpuLifecycleAdapter(),
    )
    registry = GpuLifecycleAdapterRegistry(adapters)
    assert registry.get(GpuRuntime.LLAMA_CPP).runtime is GpuRuntime.LLAMA_CPP
    with pytest.raises(ValueError):
        GpuLifecycleAdapterRegistry(adapters[:1] + adapters[:1])
    with pytest.raises(ValueError):
        GpuLifecycleAdapterRegistry(adapters[1:])


def test_default_registry_evicts_llama_before_comfy_and_ollama(workspace) -> None:
    registry = build_gpu_lifecycle_adapters(workspace, sleep=lambda _seconds: None)
    others = registry.others(GpuRuntime.OLLAMA)
    # The managed llama-server child must die first so the ComfyUI VRAM gate
    # that follows verifies its release before the target runtime loads.
    assert [adapter.runtime for adapter in others] == [GpuRuntime.LLAMA_CPP, GpuRuntime.COMFY, GpuRuntime.PYTORCH]


def test_default_registry_keeps_ollama_lifecycle_endpoint_independent_from_inference_provider(workspace) -> None:
    for provider, inference_url in (
        ("OLLAMA_LOOPBACK", "http://127.0.0.1:11434"),
        ("LLAMA_CPP_MANAGED", "http://127.0.0.1:8101"),
        ("OPENAI_COMPAT", "https://api.deepseek.com"),
    ):
        registry = build_gpu_lifecycle_adapters(
            workspace.model_copy(
                update={
                    "llm_provider": provider,
                    "llm_base_url": inference_url,
                    "ollama_base_url": "http://127.0.0.1:12434",
                }
            ),
            sleep=lambda _seconds: None,
        )
        adapter = registry.get(GpuRuntime.OLLAMA)
        assert isinstance(adapter, OllamaGpuLifecycleAdapter)
        assert adapter.ollama is not None
        assert adapter.ollama.base_url == "http://127.0.0.1:12434"
