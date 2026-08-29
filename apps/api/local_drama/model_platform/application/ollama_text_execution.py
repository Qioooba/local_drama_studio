"""Frozen-snapshot Worker handler for local Ollama structured text tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot

_HANDLER_CODE = "ollama.text.v2"
_HANDLER_VERSION = "v1"
_ADAPTER_CODE = "ollama.chat.v1"
_CAPABILITIES = frozenset({"LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"})


class OllamaStructuredClient(Protocol):
    def chat_json(
        self,
        system: str,
        user: str,
        images: list[str] | None = None,
        *,
        json_schema: dict[str, object] | None = None,
        inference_options: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


def make_ollama_text_handler(
    settings: Settings,
    *,
    client_factory: Callable[[str], OllamaStructuredClient] | None = None,
) -> Callable[[WorkerExecutionSnapshot, Path], tuple[str, str]]:
    """Execute only a local, snapshot-bound structured text request."""

    client_factory = client_factory or (
        lambda model: LocalLLMClient(
            settings.llm_base_url,
            model,
            provider="OLLAMA_LOOPBACK",
            allow_private_network=settings.allows_private_network,
        )
    )

    def execute(snapshot: WorkerExecutionSnapshot, output_root: Path) -> tuple[str, str]:
        model = _assert_snapshot(snapshot, settings)
        system, user = _prompts(snapshot)
        try:
            result = client_factory(model).chat_json(
                system,
                user,
                json_schema=_schema(snapshot),
                inference_options=dict(snapshot.resolved_parameters),
            )
        except DomainRuleError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise DomainRuleError("MP_OLLAMA_TEXT_EXECUTION_FAILED", "本机 Ollama V2 文本执行失败。") from error
        target = output_root / "ollama" / "result.json"
        artifact = {"schema": "localdramastudio.ollama-text-result.v1", "capability_code": snapshot.capability_code, "result": result}
        write_atomic(target, lambda path: path.write_text(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"))
        try:
            relative = target.relative_to(settings.work_root).as_posix()
        except ValueError as error:
            raise DomainRuleError("MP_EXECUTION_OUTPUT_ROOT_INVALID", "V2 Worker 输出目录不在受控 work_root 内。") from error
        return "OLLAMA_TEXT_RESULT", relative

    return execute


def _assert_snapshot(snapshot: WorkerExecutionSnapshot, settings: Settings) -> str:
    if snapshot.capability_code not in _CAPABILITIES or snapshot.adapter_code != _ADAPTER_CODE:
        raise DomainRuleError("MP_OLLAMA_TEXT_SNAPSHOT_MISMATCH", "冻结快照不属于受控 Ollama V2 文本 Handler。")
    if str(snapshot.network_policy.get("mode") or "").upper() != "LOCAL_ONLY":
        raise DomainRuleError("MP_OLLAMA_TEXT_NETWORK_POLICY_INVALID", "Ollama V2 文本 Handler 只允许 LOCAL_ONLY 网络策略。")
    configured = str(snapshot.runtime_configuration.get("base_url") or "").rstrip("/")
    if configured != settings.llm_base_url.rstrip("/"):
        raise DomainRuleError("MP_OLLAMA_TEXT_RUNTIME_STALE", "冻结 Ollama Runtime 与当前服务身份配置不一致。")
    if len(snapshot.model_bindings) != 1:
        raise DomainRuleError("MP_OLLAMA_TEXT_MODEL_BINDING_INVALID", "Ollama V2 文本任务必须绑定一个已验证模型。")
    model = snapshot.model_bindings[0].get("native_locator")
    if not isinstance(model, str) or not model.strip():
        raise DomainRuleError("MP_OLLAMA_TEXT_MODEL_BINDING_INVALID", "冻结模型绑定缺少 Ollama tag。")
    return model.strip()


def _prompts(snapshot: WorkerExecutionSnapshot) -> tuple[str, str]:
    system = snapshot.semantic_inputs.get("system_prompt")
    user = snapshot.semantic_inputs.get("user_prompt")
    if not all(isinstance(value, str) and value.strip() and len(value) <= 8192 for value in (system, user)):
        raise DomainRuleError("MP_OLLAMA_TEXT_INPUT_INVALID", "Ollama V2 文本任务需要 1—8192 字符的 system_prompt 与 user_prompt。")
    assert isinstance(system, str) and isinstance(user, str)
    return system.strip(), user.strip()


def _schema(snapshot: WorkerExecutionSnapshot) -> dict[str, object] | None:
    value = snapshot.execution_binding.get("json_schema")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainRuleError("MP_OLLAMA_TEXT_BINDING_INVALID", "冻结的 json_schema 必须是 JSON 对象。")
    return {str(key): item for key, item in value.items()}


def ollama_text_handler_identity() -> tuple[str, str]:
    return _HANDLER_CODE, _HANDLER_VERSION
