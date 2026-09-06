from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from types import MappingProxyType

from local_drama.model_platform.application.execution_planning import ExecutionPreview
from local_drama.model_platform.application.project_knowledge_retrieval import (
    ProjectKnowledgeRetrievalService,
    _cosine,
    _query_vector,
    _unpack_vector,
)


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args):
        class _Cursor:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
        return _Cursor(self.rows)


class _Database:
    def __init__(self, rows): self.rows = rows
    @contextmanager
    def connect(self): yield _Connection(self.rows)


def _vector(value: float) -> bytes:
    import struct
    return struct.pack("<4096f", *([value] * 4096))


def _row(run_id: str, source_id: str, vector: float, *, text: str = "灯还亮着"):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "index_run_id": run_id, "source_document_version_id": source_id, "ordinal": 1,
        "source_start": 0, "source_end": len(text), "text_sha256": digest,
        "chunk_manifest_json": json.dumps([{"ordinal": 1, "text": text}]),
        "vector_f32": _vector(vector),
    }


def test_query_receipt_and_cosine_are_strictly_4096_dimension() -> None:
    receipt = {"task": "embedding", "status": "PASS", "network_used": False, "dimension": 4096, "count": 1, "vectors": [[1] * 4096]}
    vector = _query_vector(receipt)
    assert len(vector) == 4096
    assert _cosine(vector, _unpack_vector(_vector(1.0))) == 1.0


def test_retrieval_uses_newest_successful_attempt_per_source_version() -> None:
    rows = [
        _row("run-new", "source-a", 1.0),
        _row("run-old", "source-a", 0.0),
        _row("run-b", "source-b", 0.5),
    ]
    service = object.__new__(ProjectKnowledgeRetrievalService)
    service.database = _Database(rows)

    selected = service._vectors("project-1", "profile-1")

    assert [item["index_run_id"] for item in selected] == ["run-new", "run-b"]


def test_search_uses_v2_profile_and_gpu_lease_before_ranking() -> None:
    class _Planning:
        def preview(self, request):
            assert request.capability_code == "EMBEDDING_TEXT"
            assert request.semantic_inputs["texts"] == ["问题"]
            return ExecutionPreview(
                capability_code="EMBEDDING_TEXT", execution_profile_version_id="profile-1", adapter_code="pytorch.embedding.qwen3", adapter_version="v1",
                resolution_reason="EXPLICIT_ASSIGNMENT", assignment_chain=(), resolved_parameters=MappingProxyType({}), network_policy=MappingProxyType({"mode": "LOCAL_ONLY"}), runtime_ready=True, blockers=(), resolution_hash="h",
            )

    class _Runtime:
        def embed(self, texts, *, instruction=None):
            assert texts == ["问题"] and instruction is None
            class _Receipt:
                payload = {"task": "embedding", "status": "PASS", "network_used": False, "dimension": 4096, "count": 1, "vectors": [[1] * 4096]}
            return _Receipt()

    class _Gpu:
        entered = False
        @contextmanager
        def session(self, runtime, **kwargs):
            assert str(runtime) == "PYTORCH"
            assert kwargs["owner_kind"] == "PROJECT_KNOWLEDGE_QUERY"
            self.entered = True
            yield {}

    service = object.__new__(ProjectKnowledgeRetrievalService)
    service.database = _Database([_row("run-1", "source-1", 1.0, text="沈砚拿走了灯")])
    service.planning = _Planning()
    service.runtime_factory = lambda: _Runtime()
    service.gpu_coordinator = _Gpu()

    profile, hits = service.search(project_id="project-1", query="问题", limit=5)

    assert profile == "profile-1"
    assert [(item.index_run_id, item.score) for item in hits] == [("run-1", 1.0)]
    assert hits[0].excerpt == "沈砚拿走了灯"
    assert service.gpu_coordinator.entered is True
