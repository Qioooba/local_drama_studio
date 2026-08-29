"""Read only verified Project Knowledge V2 vectors using the selected Embedding Profile."""

from __future__ import annotations

import math
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.application.job_resources import GpuRuntime
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_ai_subprocess import LocalAiExecution, LocalAiSubprocessRuntime
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest

_DIMENSION = 4096


class QueryEmbeddingRuntime(Protocol):
    def embed(self, texts: Sequence[str], *, instruction: str | None = None) -> LocalAiExecution: ...


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeSearchHit:
    index_run_id: str
    source_document_version_id: str
    ordinal: int
    source_start: int
    source_end: int
    score: float


class ProjectKnowledgeRetrievalService:
    """A bounded server-side V2 read path; callers never send a vector or file path."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        runtime_factory: Callable[[], QueryEmbeddingRuntime] | None = None,
        gpu_coordinator: GpuRuntimeCoordinator | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.planning = ExecutionPlanningService(database)
        self.runtime_factory = runtime_factory or (lambda: LocalAiSubprocessRuntime(settings))
        self.gpu_coordinator = gpu_coordinator or GpuRuntimeCoordinator(database, settings)

    def search(self, *, project_id: str, query: str, limit: int = 8) -> tuple[str, tuple[ProjectKnowledgeSearchHit, ...]]:
        text = query.strip()
        if not text or len(text) > 8192:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_INVALID", "知识库查询必须是 1—8192 字符文本。")
        bounded_limit = max(1, min(int(limit), 20))
        preview = self.planning.preview(
            ExecutionPreviewRequest(
                capability_code="EMBEDDING_TEXT",
                scope=CapabilityScopeContext(project_id=project_id),
                semantic_inputs={"texts": [text], "operation": "PROJECT_KNOWLEDGE_QUERY"},
                run_overrides={},
            )
        )
        if not preview.executable or preview.execution_profile_version_id is None:
            raise DomainRuleError(
                "MP_PROJECT_KNOWLEDGE_QUERY_NOT_READY",
                "当前项目没有可执行的 V2 Embedding Profile。",
                {"blockers": list(preview.blockers)},
            )
        if preview.adapter_code != "pytorch.embedding.qwen3":
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_HANDLER_UNAVAILABLE", "当前 Embedding Profile 没有已安装的 V2 本机检索 Handler。")
        rows = self._vectors(project_id, preview.execution_profile_version_id)
        if not rows:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_INDEX_NOT_READY", "当前 V2 Embedding Profile 下没有已验证的项目知识索引。")
        instruction = preview.resolved_parameters.get("instruction")
        value = instruction.value if instruction is not None else None
        if value is not None and not isinstance(value, str):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_PARAMETER_INVALID", "Embedding instruction 必须是文本。")
        owner_ref = f"project-knowledge-query:{uuid.uuid4()}"
        with self.gpu_coordinator.session(GpuRuntime.PYTORCH, owner_kind="PROJECT_KNOWLEDGE_QUERY", owner_ref=owner_ref):
            query_vector = _query_vector(self.runtime_factory().embed([text], instruction=value).payload)
        ranked = sorted(
            (
                ProjectKnowledgeSearchHit(
                    index_run_id=str(row["index_run_id"]),
                    source_document_version_id=str(row["source_document_version_id"]),
                    ordinal=int(row["ordinal"]),
                    source_start=int(row["source_start"]),
                    source_end=int(row["source_end"]),
                    score=_cosine(query_vector, _unpack_vector(row["vector_f32"])),
                )
                for row in rows
            ),
            key=lambda item: (-item.score, item.source_document_version_id, item.ordinal),
        )
        return preview.execution_profile_version_id, tuple(ranked[:bounded_limit])

    def _vectors(self, project_id: str, profile_version_id: str) -> list[Mapping[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT run.id AS index_run_id,run.source_document_version_id,vector.ordinal,vector.source_start,
                          vector.source_end,vector.vector_f32
                   FROM mp_project_knowledge_index_runs run
                   JOIN mp_project_knowledge_vectors vector ON vector.index_run_id=run.id
                   WHERE run.project_id=? AND run.execution_profile_version_id=? AND run.status='SUCCEEDED'
                   ORDER BY run.source_document_version_id,run.attempt_no DESC,vector.ordinal""",
                (project_id, profile_version_id),
            ).fetchall()
        # For a source/version that has succeeded more than once, newest attempt
        # owns the retrieval projection. A failed later attempt never erases a
        # previous verified index.
        selected_run_by_source: dict[str, str] = {}
        selected: list[Mapping[str, Any]] = []
        for row in rows:
            source = str(row["source_document_version_id"])
            run_id = str(row["index_run_id"])
            selected_run = selected_run_by_source.setdefault(source, run_id)
            if selected_run == run_id:
                selected.append(dict(row))
        return selected


def _query_vector(receipt: object) -> list[float]:
    if not isinstance(receipt, dict):
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_RECEIPT_INVALID", "Embedding 查询没有返回合法回执。")
    vectors = receipt.get("vectors")
    if (
        receipt.get("task") != "embedding"
        or receipt.get("status") != "PASS"
        or receipt.get("network_used") is not False
        or receipt.get("dimension") != _DIMENSION
        or receipt.get("count") != 1
        or not isinstance(vectors, list)
        or len(vectors) != 1
        or not isinstance(vectors[0], list)
        or len(vectors[0]) != _DIMENSION
    ):
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_RECEIPT_INVALID", "Embedding 查询回执不满足 V2 输出合同。")
    try:
        vector = [float(value) for value in vectors[0]]
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_RECEIPT_INVALID", "Embedding 查询向量包含非法数值。") from error
    if not all(math.isfinite(value) for value in vector):
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_QUERY_RECEIPT_INVALID", "Embedding 查询向量包含非有限数值。")
    return vector


def _unpack_vector(value: bytes) -> list[float]:
    raw = bytes(value)
    if len(raw) != _DIMENSION * 4:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_VECTOR_INVALID", "已验证向量的 float32 长度不正确。")
    vector = list(struct.unpack(f"<{_DIMENSION}f", raw))
    if not all(math.isfinite(item) for item in vector):
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_VECTOR_INVALID", "已验证向量包含非有限数值。")
    return vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != _DIMENSION or len(right) != _DIMENSION:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_VECTOR_INVALID", "知识库向量维度不一致。")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    magnitude = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / magnitude if magnitude > 0 else 0.0
