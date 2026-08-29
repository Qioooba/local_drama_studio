from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerRegistry
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers, production_worker_execution_handlers
from local_drama.model_platform.application.pytorch_embedding_execution import make_pytorch_embedding_handler


class _EmbeddingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def embed(self, texts, *, instruction=None):
        self.calls.append((list(texts), instruction))
        return SimpleNamespace(payload={
            "task": "embedding",
            "status": "PASS",
            "network_used": False,
            "dimension": 4096,
            "count": len(texts),
            "vectors": [[0.25] * 4096 for _ in texts],
            "model": "F:/not-for-artifact",
        })


def _snapshot(**overrides) -> WorkerExecutionSnapshot:
    values = {
        "job_id": "job-1",
        "execution_snapshot_id": "snapshot-1",
        "handler_code": "pytorch.embedding.qwen3",
        "handler_version": "v1",
        "capability_code": "EMBEDDING_TEXT",
        "adapter_code": "pytorch.embedding.qwen3",
        "adapter_version": "v1",
        "runtime_fingerprint": "runtime-fingerprint",
        "runtime_configuration": {},
        "model_bindings": ({"runtime_model_installation_id": "installation-1", "model_release_code": "release-1", "native_locator": "qwen3-embedding-8b"},),
        "resolved_parameters": {"instruction": "检索当前项目设定"},
        "semantic_inputs": {"texts": ["青云宗掌门是谁？", "顾长风是青云宗掌门。"]},
        "resource_policy": {"gpu_runtime": "PYTORCH"},
        "network_policy": {"mode": "LOCAL_ONLY"},
    }
    values.update(overrides)
    return WorkerExecutionSnapshot(**values)


def test_pytorch_embedding_handler_uses_frozen_snapshot_and_writes_only_controlled_artifact(workspace) -> None:
    runtime = _EmbeddingRuntime()
    handler = make_pytorch_embedding_handler(workspace, runtime_factory=lambda: runtime)
    output_root = workspace.work_root / "jobs" / "job-1"

    kind, relative_path = handler(_snapshot(), output_root)

    artifact_path = workspace.work_root / relative_path
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert kind == "EMBEDDING_RESULT"
    assert relative_path == "jobs/job-1/embedding/result.json"
    assert runtime.calls == [(["青云宗掌门是谁？", "顾长风是青云宗掌门。"], "检索当前项目设定")]
    assert payload["schema"] == "localdramastudio.embedding-result.v1"
    assert payload["dimension"] == 4096
    assert len(payload["vectors"]) == 2
    assert "F:/not-for-artifact" not in artifact_path.read_text(encoding="utf-8")


def test_pytorch_embedding_handler_rejects_changed_model_binding_before_launching_runtime(workspace) -> None:
    runtime = _EmbeddingRuntime()
    handler = make_pytorch_embedding_handler(workspace, runtime_factory=lambda: runtime)
    snapshot = _snapshot(model_bindings=({"runtime_model_installation_id": "installation-1", "model_release_code": "release-1", "native_locator": "other-model"},))

    with pytest.raises(DomainRuleError) as raised:
        handler(snapshot, workspace.work_root / "jobs" / "job-1")

    assert raised.value.code == "MP_PYTORCH_EMBEDDING_MODEL_BINDING_INVALID"
    assert runtime.calls == []


def test_production_submission_registry_declares_only_the_installed_embedding_handler() -> None:
    registry: ExecutionHandlerRegistry = production_execution_handlers()
    handler = registry.resolve("EMBEDDING_TEXT", "pytorch.embedding.qwen3")
    assert handler.code == "pytorch.embedding.qwen3"
    assert handler.gpu_runtime == "PYTORCH"
    with pytest.raises(DomainRuleError):
        registry.resolve("EMBEDDING_TEXT", "ollama.chat.v1")


def test_production_registries_declare_the_same_frozen_comfy_path(workspace) -> None:
    submission = production_execution_handlers().resolve("IMAGE_CONCEPT", "comfy.workflow.v1")
    worker = production_worker_execution_handlers(workspace)

    assert (submission.code, submission.version, submission.worker_channel, submission.gpu_runtime) == ("comfy.workflow.v2", "v1", "GPU_H3", "COMFY")
    assert worker is not None
