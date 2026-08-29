"""Frozen-snapshot Worker implementation for Qwen3 local text embeddings."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Protocol, Sequence

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic
from local_drama.infrastructure.local_ai_subprocess import LocalAiExecution, LocalAiSubprocessRuntime
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot

_HANDLER_CODE = "pytorch.embedding.qwen3"
_HANDLER_VERSION = "v1"
_NATIVE_LOCATOR = "qwen3-embedding-8b"


class EmbeddingRuntime(Protocol):
    def embed(self, texts: Sequence[str], *, instruction: str | None = None) -> LocalAiExecution: ...


def make_pytorch_embedding_handler(
    settings: Settings,
    *,
    runtime_factory: Callable[[], EmbeddingRuntime] | None = None,
) -> Callable[[WorkerExecutionSnapshot, Path], tuple[str, str]]:
    """Return the only Worker handler for the installed PyTorch embedding path."""

    runtime_factory = runtime_factory or (lambda: LocalAiSubprocessRuntime(settings))

    def execute(snapshot: WorkerExecutionSnapshot, output_root: Path) -> tuple[str, str]:
        texts = _texts(snapshot)
        instruction = _instruction(snapshot)
        _assert_snapshot_contract(snapshot)
        try:
            receipt = runtime_factory().embed(texts, instruction=instruction).payload
        except (OSError, RuntimeError, ValueError) as error:
            raise DomainRuleError("MP_PYTORCH_EMBEDDING_EXECUTION_FAILED", "本机 Qwen3 Embedding 执行失败。") from error
        artifact = _artifact(receipt, len(texts))
        target = output_root / "embedding" / "result.json"
        write_atomic(target, lambda path: path.write_text(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"))
        try:
            relative = target.relative_to(settings.work_root).as_posix()
        except ValueError as error:
            raise DomainRuleError("MP_EXECUTION_OUTPUT_ROOT_INVALID", "V2 Worker 输出目录不在受控 work_root 内。") from error
        return "EMBEDDING_RESULT", relative

    return execute


def _assert_snapshot_contract(snapshot: WorkerExecutionSnapshot) -> None:
    if snapshot.capability_code != "EMBEDDING_TEXT" or snapshot.adapter_code != _HANDLER_CODE:
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_SNAPSHOT_MISMATCH", "冻结快照不属于 Qwen3 Embedding Handler。")
    if len(snapshot.model_bindings) != 1 or snapshot.model_bindings[0].get("native_locator") != _NATIVE_LOCATOR:
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_MODEL_BINDING_INVALID", "冻结快照没有绑定受控 Qwen3 Embedding 模型。")
    if str(snapshot.network_policy.get("mode") or "").upper() != "LOCAL_ONLY":
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_NETWORK_POLICY_INVALID", "Qwen3 Embedding Handler 只允许 LOCAL_ONLY 网络策略。")


def _texts(snapshot: WorkerExecutionSnapshot) -> list[str]:
    value = snapshot.semantic_inputs.get("texts")
    if not isinstance(value, list) or not value or len(value) > 32:
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_INPUT_INVALID", "Embedding 执行需要 1—32 条 texts 语义输入。")
    texts = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(texts) != len(value) or any(len(item) > 8192 for item in texts):
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_INPUT_INVALID", "每条 Embedding 文本必须非空且不超过 8192 字符。")
    return texts


def _instruction(snapshot: WorkerExecutionSnapshot) -> str | None:
    value = snapshot.resolved_parameters.get("instruction")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_PARAMETER_INVALID", "Embedding instruction 必须是 1—512 字符文本。")
    return value.strip()


def _artifact(receipt: object, expected_count: int) -> dict[str, object]:
    if not isinstance(receipt, dict):
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_RECEIPT_INVALID", "Embedding Adapter 没有返回合法回执。")
    vectors = receipt.get("vectors")
    if (
        receipt.get("task") != "embedding"
        or receipt.get("status") != "PASS"
        or receipt.get("network_used") is not False
        or receipt.get("dimension") != 4096
        or receipt.get("count") != expected_count
        or not isinstance(vectors, list)
        or len(vectors) != expected_count
        or any(not isinstance(vector, list) or len(vector) != 4096 for vector in vectors)
    ):
        raise DomainRuleError("MP_PYTORCH_EMBEDDING_RECEIPT_INVALID", "Embedding Adapter 回执不满足冻结输出合同。")
    normalized: list[list[float]] = []
    for vector in vectors:
        try:
            numeric = [float(value) for value in vector]
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_PYTORCH_EMBEDDING_RECEIPT_INVALID", "Embedding 向量包含非法数值。") from error
        if not all(math.isfinite(value) for value in numeric):
            raise DomainRuleError("MP_PYTORCH_EMBEDDING_RECEIPT_INVALID", "Embedding 向量包含非有限数值。")
        normalized.append(numeric)
    return {"schema": "localdramastudio.embedding-result.v1", "dimension": 4096, "count": expected_count, "vectors": normalized}


def pytorch_embedding_handler_identity() -> tuple[str, str]:
    return _HANDLER_CODE, _HANDLER_VERSION
