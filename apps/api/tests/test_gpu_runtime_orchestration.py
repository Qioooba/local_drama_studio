from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.application.job_resources import GpuRuntime, gpu_runtime_for_job, scheduler_resource_key
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


class _ComfyRuntime:
    def __init__(self) -> None:
        self.free_calls = 0
        self.busy = False
        self.released = False

    def queue(self) -> dict[str, object]:
        return {
            "queue_running": [["prompt"]] if self.busy else [],
            "queue_pending": [],
        }

    def free_memory(self, **_kwargs: object) -> dict[str, object]:
        self.free_calls += 1
        self.released = True
        return {}

    def system_stats(self) -> dict[str, object]:
        total = 24 * 1024**3
        return {
            "devices": [
                {
                    "vram_total": total,
                    "vram_free": int(total * (0.95 if self.released else 0.05)),
                }
            ]
        }


class _OllamaRuntime:
    def __init__(self) -> None:
        self.unload_calls = 0

    def unload_all(self) -> list[str]:
        self.unload_calls += 1
        return ["deepseek-r1:14b"]


class _FailingOllamaRuntime(_OllamaRuntime):
    def unload_all(self) -> list[str]:
        raise DomainRuleError("OLLAMA_CLEANUP_FAILED", "cleanup failed")


def _llama_workspace(workspace, tmp_path_factory):
    """Settings with a valid llama-server binary and GGUF path.

    The managed adapter validates its launch spec at activation time, so a
    LLAMA_CPP lease requires real configured files even when the manager
    itself is a test double.
    """

    llama_dir = tmp_path_factory.mktemp("llama")
    bin_path = llama_dir / "llama-server.exe"
    bin_path.write_bytes(b"")
    model_path = llama_dir / "qwen3.8-27b.gguf"
    model_path.write_bytes(b"")
    return workspace.model_copy(update={"llama_server_bin": bin_path, "llama_model_path": model_path})


class _LlamaManager:
    def __init__(self, *, start_error: DomainRuleError | None = None) -> None:
        self.start_calls: list[object] = []
        self.stop_calls = 0
        self.running = False
        self.start_error = start_error

    def is_running(self) -> bool:
        return self.running

    def start(self, spec: object) -> str:
        self.start_calls.append(spec)
        if self.start_error is not None:
            raise self.start_error
        self.running = True
        return "http://127.0.0.1:8101"

    def stop(self) -> bool:
        self.stop_calls += 1
        self.running = False
        return True


class _UnavailableSystemProbe:
    """Force deterministic fallback to the injected Comfy telemetry."""

    def snapshot(self) -> None:
        return None


def test_job_resource_authority_distinguishes_channel_from_physical_gpu() -> None:
    local_llm = {
        "type": "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "channel": "CPU",
        "input_snapshot": {
            "provider": "OLLAMA_LOOPBACK",
            "base_url": "http://127.0.0.1:11434",
        },
    }
    remote_llm = {
        **local_llm,
        "input_snapshot": {
            "provider": "OLLAMA_LOOPBACK",
            "base_url": "http://192.168.1.80:11434",
        },
    }
    assert gpu_runtime_for_job(local_llm) is GpuRuntime.OLLAMA
    assert scheduler_resource_key(local_llm, "worker-a") == "GPU_H3_HEAVY"
    assert gpu_runtime_for_job(remote_llm) is None
    assert scheduler_resource_key(remote_llm, "worker-a") == "CHANNEL:CPU:worker-a"
    assert gpu_runtime_for_job({"type": "GENERATION_VARIANT", "channel": "GPU_H3"}) is GpuRuntime.COMFY


def test_job_resource_authority_maps_managed_llama_cpp_provider() -> None:
    managed_llm = {
        "type": "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "channel": "CPU",
        "input_snapshot": {
            "provider": "LLAMA_CPP_MANAGED",
            "base_url": "http://127.0.0.1:8101",
        },
    }
    assert gpu_runtime_for_job(managed_llm) is GpuRuntime.LLAMA_CPP
    assert scheduler_resource_key(managed_llm, "worker-a") == "GPU_H3_HEAVY"

    probe_without_load = {
        "type": "LOCAL_LLM_PROBE",
        "channel": "CPU",
        "input_snapshot": {"provider": "LLAMA_CPP_MANAGED", "load_test": False},
    }
    assert gpu_runtime_for_job(probe_without_load) is None

    platform_execution = {
        "type": "MODEL_PLATFORM_EXECUTION",
        "channel": "CPU",
        "input_snapshot": {"scheduler_runtime": "LLAMA_CPP"},
    }
    assert gpu_runtime_for_job(platform_execution) is GpuRuntime.LLAMA_CPP


def test_llama_cpp_session_evicts_other_runtimes_and_stops_server(workspace, database, tmp_path_factory) -> None:
    comfy = _ComfyRuntime()
    ollama = _OllamaRuntime()
    manager = _LlamaManager()
    coordinator = GpuRuntimeCoordinator(
        database,
        _llama_workspace(workspace, tmp_path_factory),
        comfy=comfy,
        ollama=ollama,
        llama_manager=manager,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )

    with coordinator.session(GpuRuntime.LLAMA_CPP, owner_kind="TEST", owner_ref="llm-1"):
        assert comfy.free_calls == 1
        assert ollama.unload_calls == 1
        assert manager.start_calls and manager.running is True
        assert coordinator.status()["state"]["resident_runtime"] == "LLAMA_CPP"

    assert manager.stop_calls == 1
    assert coordinator.status()["state"]["resident_runtime"] is None


def test_prepare_comfy_stops_leftover_llama_server_before_other_evictions(workspace, database, tmp_path_factory) -> None:
    events: list[str] = []

    class _RecordingComfy(_ComfyRuntime):
        def free_memory(self, **kwargs: object) -> dict[str, object]:
            events.append("comfy-free")
            return super().free_memory(**kwargs)

    class _RecordingLlama(_LlamaManager):
        def stop(self) -> bool:
            events.append("llama-stop")
            return super().stop()

    manager = _RecordingLlama()
    manager.running = True  # leftover from a retained or crashed previous lease
    coordinator = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=_RecordingComfy(),
        ollama=_OllamaRuntime(),
        llama_manager=manager,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    coordinator.prepare(GpuRuntime.OLLAMA, owner_ref="llm-2")
    # The llama-server child dies first so the ComfyUI VRAM gate that follows
    # verifies its ~20 GB release before Ollama loads the next model.
    assert events == ["llama-stop", "comfy-free"]
    assert manager.stop_calls == 1


def test_prepare_waits_for_vram_only_after_all_old_runtimes_are_evicted(
    workspace,
    database,
    tmp_path_factory,
) -> None:
    events: list[str] = []

    class _RecordingComfy(_ComfyRuntime):
        def free_memory(self, **kwargs: object) -> dict[str, object]:
            events.append("comfy-free")
            return super().free_memory(**kwargs)

        def system_stats(self) -> dict[str, object]:
            events.append("vram-gate")
            return super().system_stats()

    class _RecordingOllama(_OllamaRuntime):
        def unload_all(self) -> list[str]:
            events.append("ollama-unload")
            return super().unload_all()

    class _RecordingLlama(_LlamaManager):
        def start(self, spec: object) -> str:
            events.append("llama-start")
            return super().start(spec)

    coordinator = GpuRuntimeCoordinator(
        database,
        _llama_workspace(workspace, tmp_path_factory),
        comfy=_RecordingComfy(),
        ollama=_RecordingOllama(),
        llama_manager=_RecordingLlama(),
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )

    coordinator.prepare(GpuRuntime.LLAMA_CPP, owner_ref="llama-order")

    assert events == ["comfy-free", "ollama-unload", "vram-gate", "llama-start"]


def test_llama_cpp_cleanup_retains_server_for_queued_same_runtime_job(workspace, database, tmp_path_factory) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="llama_retain",
        title="llama retain",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    jobs = JobService(database, workspace)
    jobs.create_job(
        str(project["id"]),
        "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "PROJECT",
        str(project["id"]),
        "CPU",
        {"provider": "LLAMA_CPP_MANAGED", "base_url": "http://127.0.0.1:8101"},
        "llama-retain-job",
        priority=1,
    )
    manager = _LlamaManager()
    coordinator = GpuRuntimeCoordinator(
        database,
        _llama_workspace(workspace, tmp_path_factory),
        comfy=_ComfyRuntime(),
        ollama=_OllamaRuntime(),
        llama_manager=manager,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    with coordinator.session(
        GpuRuntime.LLAMA_CPP,
        owner_kind="TEST",
        owner_ref="llm-retain",
        retain_if_same_runtime_waiting=True,
    ):
        assert manager.running is True
    # Back-to-back text jobs keep the weights resident instead of paying a
    # ~17 GB reload between every script-breakdown call.
    assert manager.stop_calls == 0
    assert manager.running is True


def test_llama_cpp_activation_failure_marks_runtime_degraded(workspace, database, tmp_path_factory) -> None:
    manager = _LlamaManager(start_error=DomainRuleError("LLAMA_SERVER_CRASHED", "crash on start"))
    coordinator = GpuRuntimeCoordinator(
        database,
        _llama_workspace(workspace, tmp_path_factory),
        comfy=_ComfyRuntime(),
        ollama=_OllamaRuntime(),
        llama_manager=manager,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(DomainRuleError) as caught:
        with coordinator.session(GpuRuntime.LLAMA_CPP, owner_kind="TEST", owner_ref="llm-fail"):
            pass
    assert caught.value.code == "LLAMA_SERVER_CRASHED"
    status = coordinator.status()
    assert status["active_lease"] is None
    assert status["state"]["status"] == "DEGRADED"
    assert status["state"]["last_error_code"] == "LLAMA_SERVER_CRASHED"


def test_coordinator_serializes_processes_and_evicts_owner_runtime(workspace, database) -> None:
    comfy = _ComfyRuntime()
    ollama = _OllamaRuntime()
    first = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=comfy,
        ollama=ollama,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    second = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=comfy,
        ollama=ollama,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )

    with first.session(GpuRuntime.OLLAMA, owner_kind="TEST", owner_ref="novel-1"):
        assert comfy.free_calls == 1
        status = first.status()
        assert status["state"]["resident_runtime"] == "OLLAMA"
        assert status["state"]["status"] == "READY"
        with pytest.raises(DomainRuleError) as captured:
            second.acquire(GpuRuntime.COMFY, owner_kind="TEST", owner_ref="video-1")
        assert captured.value.code == "GPU_RUNTIME_BUSY"

    assert ollama.unload_calls == 1
    assert first.status()["active_lease"] is None

    with second.session(GpuRuntime.COMFY, owner_kind="TEST", owner_ref="video-1"):
        assert ollama.unload_calls == 2
    assert comfy.free_calls == 2
    assert second.status()["state"]["resident_runtime"] is None


def test_session_waits_for_resource_without_stealing_lease(workspace, database) -> None:
    comfy = _ComfyRuntime()
    comfy.released = True
    coordinator = GpuRuntimeCoordinator(database, workspace, comfy=comfy, ollama=_OllamaRuntime(), system_probe=_UnavailableSystemProbe(), sleep=lambda _: None)
    held = coordinator.acquire(GpuRuntime.OLLAMA, owner_kind="API", owner_ref="prompt")
    waits = []

    def waiting():
        waits.append(True)
        assert coordinator.status()["active_lease"]["owner_ref"] == "prompt"
        if len(waits) == 2:
            coordinator.release(held["token"], reason="COMPLETED")

    with coordinator.session(GpuRuntime.COMFY, owner_kind="JOB_ATTEMPT", owner_ref="image", on_wait=waiting):
        assert len(waits) == 2
        assert coordinator.status()["active_lease"]["owner_ref"] == "image"
    assert coordinator.status()["active_lease"] is None


def test_waiting_session_can_cancel_without_releasing_other_owner(workspace, database) -> None:
    coordinator = GpuRuntimeCoordinator(database, workspace, comfy=_ComfyRuntime(), ollama=_OllamaRuntime(), system_probe=_UnavailableSystemProbe(), sleep=lambda _: None)
    held = coordinator.acquire(GpuRuntime.OLLAMA, owner_kind="API", owner_ref="prompt")

    def cancelled():
        raise DomainRuleError("JOB_CANCELLED", "cancelled")

    with pytest.raises(DomainRuleError, match="cancelled"):
        with coordinator.session(GpuRuntime.COMFY, owner_kind="JOB_ATTEMPT", owner_ref="image", on_wait=cancelled):
            pytest.fail("Cancelled job must not start")
    assert coordinator.status()["active_lease"]["owner_ref"] == "prompt"
    coordinator.release(held["token"], reason="COMPLETED")


def test_coordinator_refuses_to_evict_an_external_comfy_job(workspace, database) -> None:
    comfy = _ComfyRuntime()
    comfy.busy = True
    coordinator = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=comfy,
        ollama=_OllamaRuntime(),
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(DomainRuleError) as captured:
        with coordinator.session(GpuRuntime.OLLAMA, owner_kind="TEST", owner_ref="novel-busy"):
            pass
    assert captured.value.code == "GPU_RUNTIME_EXTERNAL_COMFY_BUSY"
    assert coordinator.status()["active_lease"] is None


def test_post_success_cleanup_failure_is_durable_without_reversing_success(workspace, database) -> None:
    coordinator = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=_ComfyRuntime(),
        ollama=_FailingOllamaRuntime(),
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    completed = False
    with coordinator.session(GpuRuntime.OLLAMA, owner_kind="TEST", owner_ref="novel-cleanup"):
        completed = True
    assert completed is True
    status = coordinator.status()
    assert status["active_lease"] is None
    assert status["state"]["status"] == "DEGRADED"
    assert status["state"]["last_error_code"] == "OLLAMA_CLEANUP_FAILED"


def test_scheduler_never_claims_local_ollama_and_comfy_concurrently(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="gpu_scheduler",
        title="GPU scheduler",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    jobs = JobService(database, workspace)
    llm = jobs.create_job(
        project_id,
        "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "PROJECT",
        project_id,
        "CPU",
        {"provider": "OLLAMA_LOOPBACK", "base_url": "http://127.0.0.1:11434"},
        "gpu-scheduler-llm",
        priority=1,
    )
    jobs.create_job(
        project_id,
        "GENERATION_VARIANT",
        "PROJECT",
        project_id,
        "GPU_H3",
        {"workflow_version_id": "test"},
        "gpu-scheduler-video",
        priority=2,
    )

    llm_claim = jobs.claim("cpu-worker", ["CPU"])
    assert llm_claim is not None and llm_claim["job"]["id"] == llm["id"]
    assert jobs.claim("gpu-worker", ["GPU_H3"]) is None
    jobs.complete(
        str(llm_claim["attempt"]["id"]),
        str(llm_claim["attempt"]["lease_token"]),
        "cpu-worker",
        success=True,
    )
    assert jobs.claim("gpu-worker", ["GPU_H3"]) is not None


def test_gpu_runtime_status_api_is_read_only(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/diagnostics/gpu-runtime")
    assert response.status_code == 200
    payload = response.json()["gpu_runtime"]
    assert payload["state"]["resource_key"] == "GPU:0:EXCLUSIVE"
    assert payload["active_lease"] is None
