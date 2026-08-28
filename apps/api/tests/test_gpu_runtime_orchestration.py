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


def test_coordinator_serializes_processes_and_evicts_owner_runtime(workspace, database) -> None:
    comfy = _ComfyRuntime()
    ollama = _OllamaRuntime()
    first = GpuRuntimeCoordinator(database, workspace, comfy=comfy, ollama=ollama, sleep=lambda _seconds: None)
    second = GpuRuntimeCoordinator(database, workspace, comfy=comfy, ollama=ollama, sleep=lambda _seconds: None)

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


def test_coordinator_refuses_to_evict_an_external_comfy_job(workspace, database) -> None:
    comfy = _ComfyRuntime()
    comfy.busy = True
    coordinator = GpuRuntimeCoordinator(
        database,
        workspace,
        comfy=comfy,
        ollama=_OllamaRuntime(),
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
