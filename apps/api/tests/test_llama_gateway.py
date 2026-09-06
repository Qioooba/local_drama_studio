from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from local_drama.application.job_resources import GpuRuntime
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.llama_gateway import LlamaGateway, create_llama_gateway_app


def test_gateway_health_and_catalog_do_not_load_model(workspace: Settings) -> None:
    settings = workspace.model_copy(
        update={
            "llama_gateway_enabled": True,
            "llama_gateway_host": "0.0.0.0",
            "llama_gateway_port": 28088,
            "llama_idle_timeout_seconds": 300,
            "llm_model": "test-model",
            "llama_server_args": ("--mmproj", "mmproj.gguf"),
        }
    )
    app = create_llama_gateway_app(settings)
    with TestClient(app) as client:
        health = client.get("/health")
        models = client.get("/v1/models")
    assert health.status_code == 200
    assert health.json()["model_loaded"] is False
    assert models.status_code == 200
    assert models.json()["data"][0]["capabilities"] == ["completion", "multimodal"]


def test_gateway_borrows_existing_llama_lease(workspace: Settings) -> None:
    class SameRuntimeCoordinator:
        def acquire(self, *_args: object, **_kwargs: object) -> dict[str, str]:
            raise DomainRuleError("GPU_RUNTIME_BUSY", "busy")

        def status(self) -> dict[str, object]:
            return {"active_lease": {"runtime_kind": GpuRuntime.LLAMA_CPP.value, "owner_ref": "worker-job"}}

    gateway = LlamaGateway(workspace)
    gateway.coordinator = SameRuntimeCoordinator()  # type: ignore[assignment]
    lease, borrowed = asyncio.run(gateway._acquire_wait())
    assert borrowed is True
    assert lease["owner_ref"] == "worker-job"
