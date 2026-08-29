from __future__ import annotations

import json

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot
from local_drama.model_platform.application.ollama_text_execution import make_ollama_text_handler


def _snapshot(**changes) -> WorkerExecutionSnapshot:
    values = {
        "job_id": "job-1",
        "execution_snapshot_id": "snapshot-1",
        "handler_code": "ollama.text.v2",
        "handler_version": "v1",
        "capability_code": "LLM_STORY_PARSE",
        "adapter_code": "ollama.chat.v1",
        "adapter_version": "v1",
        "runtime_fingerprint": "runtime-fingerprint",
        "runtime_configuration": {"base_url": "http://127.0.0.1:11434"},
        "model_bindings": ({"native_locator": "qwen3.8:27b"},),
        "resolved_parameters": {"temperature": 0.2, "max_tokens": 64},
        "semantic_inputs": {"system_prompt": "Return structured JSON.", "user_prompt": "Plan one scene."},
        "resource_policy": {},
        "network_policy": {"mode": "LOCAL_ONLY"},
        "execution_binding": {"json_schema": {"type": "object"}},
    }
    values.update(changes)
    return WorkerExecutionSnapshot(**values)


def test_ollama_handler_uses_only_frozen_local_contract_and_writes_controlled_artifact(workspace) -> None:
    calls: list[tuple[str, str, str, dict[str, object] | None, dict[str, object] | None]] = []

    class _Client:
        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            calls.append((system, user, images, json_schema, inference_options))
            return {"scene": "one"}

    output_root = workspace.work_root / "model-platform-execution"
    handler = make_ollama_text_handler(workspace, client_factory=lambda model: _Client())
    artifact_kind, relative = handler(_snapshot(), output_root)

    assert artifact_kind == "OLLAMA_TEXT_RESULT"
    assert relative == "model-platform-execution/ollama/result.json"
    assert json.loads((workspace.work_root / relative).read_text(encoding="utf-8")) == {
        "schema": "localdramastudio.ollama-text-result.v1",
        "capability_code": "LLM_STORY_PARSE",
        "result": {"scene": "one"},
    }
    assert calls == [("Return structured JSON.", "Plan one scene.", None, {"type": "object"}, {"temperature": 0.2, "max_tokens": 64})]


def test_ollama_handler_rejects_stale_runtime_wiring_before_contacting_client(workspace) -> None:
    handler = make_ollama_text_handler(workspace, client_factory=lambda model: pytest.fail("client must not be created"))
    with pytest.raises(DomainRuleError, match="服务身份配置不一致"):
        handler(_snapshot(runtime_configuration={"base_url": "http://127.0.0.1:19999"}), workspace.work_root / "out")
