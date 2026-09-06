"""Prepare immutable V2 project-knowledge embedding batches before queueing."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from local_drama.application.source_text import source_paragraphs
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_snapshots import ExecutionSnapshot
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers

_MAX_TEXT_CHARACTERS = 8192
_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeIndexPreparation:
    index_run_id: str
    execution_profile_version_id: str
    chunk_count: int
    reused: bool
    status: str
    attempt_no: int


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeIndexQueueResult:
    index_run_id: str
    queued_batch_count: int
    job_ids: tuple[str, ...]


class ProjectKnowledgeIndexPreparationService:
    """Persist source-bound V2 batches; this service never accepts a path or vector from callers."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.planning = ExecutionPlanningService(database)

    def prepare(self, *, project_id: str, source_document_version_id: str, actor: str = "local-user") -> ProjectKnowledgeIndexPreparation:
        source = self._source(project_id, source_document_version_id)
        text = self._read_source(project_id, str(source["extracted_text_rel"]))
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(source["text_sha256"]):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_SOURCE_HASH_MISMATCH", "知识库源文本 hash 与已登记版本不一致。")
        chunks = _chunks(text)
        if not chunks:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_SOURCE_EMPTY", "知识库源文本没有可索引的段落。")
        first = self.planning.preview(ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT", scope=CapabilityScopeContext(project_id=project_id),
            semantic_inputs={"texts": [item["text"] for item in chunks[:_BATCH_SIZE]]}, run_overrides={},
        ))
        if not first.executable or first.execution_profile_version_id is None:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_EMBEDDING_NOT_READY", "项目知识索引没有可执行的 V2 Embedding Profile。", {"blockers": list(first.blockers)})
        source_hash = str(source["text_sha256"])
        with self.database.transaction() as connection:
            existing = connection.execute(
                """SELECT id,chunk_count,status,attempt_no FROM mp_project_knowledge_index_runs
                   WHERE source_document_version_id=? AND execution_profile_version_id=? AND source_text_hash=?
                   ORDER BY attempt_no DESC LIMIT 1""",
                (source_document_version_id, first.execution_profile_version_id, source_hash),
            ).fetchone()
            if existing is not None and str(existing["status"]) not in {"FAILED", "QUEUE_FAILED"}:
                return ProjectKnowledgeIndexPreparation(
                    str(existing["id"]), first.execution_profile_version_id, int(existing["chunk_count"]), True,
                    str(existing["status"]), int(existing["attempt_no"]),
                )
            retry_of = str(existing["id"]) if existing is not None else None
            attempt_no = int(existing["attempt_no"]) + 1 if existing is not None else 1
            run_id, now = str(uuid.uuid4()), _utc_now()
            connection.execute(
                """INSERT INTO mp_project_knowledge_index_runs
                (id,project_id,source_document_version_id,capability_definition_id,execution_profile_version_id,source_text_hash,chunk_count,attempt_no,retry_of_index_run_id,status,created_at,updated_at,created_by)
                SELECT ?,?,?,id,?,?,?,?,?,?,?,?,? FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'""",
                (run_id, project_id, source_document_version_id, first.execution_profile_version_id, source_hash, len(chunks), attempt_no, retry_of, "PREPARED", now, now, actor),
            )
            for ordinal, batch in enumerate(_batches(chunks), start=1):
                connection.execute(
                    """INSERT INTO mp_project_knowledge_index_batches
                    (id,index_run_id,ordinal,chunk_manifest_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), run_id, ordinal, json.dumps(batch, ensure_ascii=False, separators=(",", ":")), "PREPARED", now, now),
                )
        return ProjectKnowledgeIndexPreparation(run_id, first.execution_profile_version_id, len(chunks), False, "PREPARED", attempt_no)

    def _source(self, project_id: str, source_document_version_id: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT version.extracted_text_rel,version.text_sha256 FROM source_document_versions version
                JOIN source_documents document ON document.id=version.source_document_id
                WHERE version.id=? AND document.project_id=? AND version.parse_status='PARSED'""",
                (source_document_version_id, project_id),
            ).fetchone()
        if row is None or not str(row["extracted_text_rel"] or ""):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_SOURCE_NOT_FOUND", "源文本版本不存在、未解析或不属于当前项目。")
        return cast(sqlite3.Row, row)

    def _read_source(self, project_id: str, relative: str) -> str:
        with self.database.connect() as connection:
            root = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
        if root is None:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_PROJECT_NOT_FOUND", "项目不存在。")
        path = controlled_path(self.settings.projects_root / str(root["root_rel"]), relative, must_exist=True, require_file=True, code="MP_PROJECT_KNOWLEDGE_SOURCE_PATH_INVALID")
        return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


class ProjectKnowledgeIndexQueueService:
    """Turn a prepared V2 knowledge run into atomically linked Worker Jobs."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.planning = ExecutionPlanningService(database)
        self.submissions = ExecutionSubmissionService(database, production_execution_handlers())

    def queue(self, index_run_id: str) -> ProjectKnowledgeIndexQueueResult:
        run, batches = self._claim_prepared_run(index_run_id)
        requests: list[tuple[str, ExecutionPreviewRequest]] = []
        for batch in batches:
            manifest = _manifest_items(batch["chunk_manifest_json"])
            semantic_inputs = {
                "project_knowledge_index_run_id": index_run_id,
                "project_knowledge_index_batch_id": str(batch["id"]),
                "texts": [str(item["text"]) for item in manifest],
            }
            preview = self.planning.preview(
                ExecutionPreviewRequest(
                    capability_code="EMBEDDING_TEXT",
                    scope=CapabilityScopeContext(project_id=str(run["project_id"])),
                    semantic_inputs=semantic_inputs,
                    run_overrides={},
                )
            )
            if not preview.executable or preview.execution_profile_version_id != str(run["execution_profile_version_id"]):
                raise DomainRuleError(
                    "MP_PROJECT_KNOWLEDGE_PROFILE_CHANGED",
                    "知识库索引准备后有效 Embedding Profile 已变化，必须重新准备。",
                    {"blockers": list(preview.blockers)},
                )
            requests.append(
                (
                    str(batch["id"]),
                    ExecutionPreviewRequest(
                        capability_code="EMBEDDING_TEXT",
                        scope=CapabilityScopeContext(project_id=str(run["project_id"])),
                        semantic_inputs=semantic_inputs,
                        run_overrides={},
                        expected_resolution_hash=preview.resolution_hash,
                    ),
                )
            )

        jobs: list[str] = []
        try:
            for batch_id, request in requests:
                submitted = self.submissions.submit(
                    request,
                    f"project-knowledge-index:{index_run_id}:{batch_id}",
                    after_linked_in_transaction=cast(
                        "Callable[[Any, dict[str, Any], ExecutionSnapshot], None]",
                        lambda connection, job, snapshot, batch_id=batch_id: self._link_batch_in_transaction(
                            connection, index_run_id, batch_id, str(job["id"]), snapshot.id
                        ),
                    ),
                )
                jobs.append(str(submitted.job["id"]))
        except Exception:
            self._record_queue_failure(index_run_id)
            raise
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE mp_project_knowledge_index_runs
                   SET status='QUEUED',updated_at=?,revision=revision+1
                   WHERE id=? AND status='QUEUING'""",
                (_utc_now(), index_run_id),
            )
        return ProjectKnowledgeIndexQueueResult(index_run_id, len(jobs), tuple(jobs))

    def _claim_prepared_run(self, index_run_id: str) -> tuple[Mapping[str, object], list[Mapping[str, object]]]:
        with self.database.transaction() as connection:
            run = connection.execute(
                """SELECT id,project_id,execution_profile_version_id,status
                   FROM mp_project_knowledge_index_runs WHERE id=?""",
                (index_run_id,),
            ).fetchone()
            if run is None:
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_INDEX_NOT_FOUND", "知识库索引准备记录不存在。")
            if str(run["status"]) != "PREPARED":
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_INDEX_NOT_PREPARED", "只有 PREPARED 的知识库索引可以入队。", {"status": str(run["status"])})
            batches = connection.execute(
                """SELECT id,chunk_manifest_json,status FROM mp_project_knowledge_index_batches
                   WHERE index_run_id=? ORDER BY ordinal""",
                (index_run_id,),
            ).fetchall()
            if not batches or any(str(batch["status"]) != "PREPARED" for batch in batches):
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_STATE_INVALID", "知识库索引批次不完整或不是 PREPARED 状态。")
            connection.execute(
                """UPDATE mp_project_knowledge_index_runs
                   SET status='QUEUING',updated_at=?,revision=revision+1 WHERE id=?""",
                (_utc_now(), index_run_id),
            )
        return dict(run), [dict(batch) for batch in batches]

    @staticmethod
    def _link_batch_in_transaction(connection: Any, index_run_id: str, batch_id: str, job_id: str, snapshot_id: str) -> None:
        updated = connection.execute(
            """UPDATE mp_project_knowledge_index_batches
               SET execution_snapshot_id=?,job_id=?,status='QUEUED',updated_at=?
               WHERE id=? AND index_run_id=? AND status='PREPARED'""",
            (snapshot_id, job_id, _utc_now(), batch_id, index_run_id),
        ).rowcount
        if updated != 1:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_LINK_FAILED", "知识库索引批次不能与 V2 Job 原子绑定。")

    def _record_queue_failure(self, index_run_id: str) -> None:
        with self.database.transaction() as connection:
            now = _utc_now()
            connection.execute(
                """UPDATE mp_project_knowledge_index_runs
                   SET status='QUEUE_FAILED',failure_code='MP_PROJECT_KNOWLEDGE_QUEUE_FAILED',updated_at=?,revision=revision+1
                   WHERE id=? AND status='QUEUING'""",
                (now, index_run_id),
            )
            connection.execute(
                """UPDATE mp_project_knowledge_index_batches
                   SET status='QUEUE_FAILED',updated_at=?
                   WHERE index_run_id=? AND status='PREPARED'""",
                (now, index_run_id),
            )


class ProjectKnowledgeIndexCompletionService:
    """Verify one embedding artifact and commit its vectors as an all-or-nothing batch."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def record_if_project_knowledge_batch(self, snapshot: Any, artifacts: tuple[dict[str, Any], ...]) -> bool:
        run_id = snapshot.semantic_inputs.get("project_knowledge_index_run_id")
        batch_id = snapshot.semantic_inputs.get("project_knowledge_index_batch_id")
        if not isinstance(run_id, str) or not run_id or not isinstance(batch_id, str) or not batch_id:
            return False
        try:
            artifact = next((item for item in artifacts if item.get("kind") == "EMBEDDING_RESULT"), None)
            if artifact is None:
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_MISSING", "知识库 Embedding Job 没有登记 EMBEDDING_RESULT 产物。")
            manifest, vectors = self._validated_batch(snapshot, run_id, batch_id, artifact)
        except DomainRuleError as error:
            self.record_failure_if_project_knowledge_batch(snapshot, error.code)
            raise
        with self.database.transaction() as connection:
            batch = connection.execute(
                """SELECT status,execution_snapshot_id,job_id,artifact_id FROM mp_project_knowledge_index_batches
                   WHERE id=? AND index_run_id=?""",
                (batch_id, run_id),
            ).fetchone()
            if (
                batch is None
                or str(batch["execution_snapshot_id"] or "") != str(snapshot.execution_snapshot_id)
                or str(batch["job_id"] or "") != str(snapshot.job_id)
            ):
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_SNAPSHOT_MISMATCH", "知识库批次与 Worker 快照不匹配。")
            if str(batch["status"]) == "COMPLETED":
                if str(batch["artifact_id"] or "") == str(artifact["id"]):
                    return True
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_ALREADY_COMPLETED", "已完成批次不能绑定另一份 Embedding 产物。")
            if str(batch["status"]) != "QUEUED":
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_NOT_QUEUED", "只有已原子入队的知识库批次可以写入向量。")
            now = _utc_now()
            for item, vector in zip(manifest, vectors, strict=True):
                connection.execute(
                    """INSERT INTO mp_project_knowledge_vectors
                       (id,index_run_id,batch_id,ordinal,source_start,source_end,text_sha256,vector_f32,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), run_id, batch_id,
                        int(cast(int, item["ordinal"])),
                        int(cast(int, item["source_start"])),
                        int(cast(int, item["source_end"])),
                        str(item["text_sha256"]), _pack_vector(vector), now,
                    ),
                )
            connection.execute(
                """UPDATE mp_project_knowledge_index_batches
                   SET artifact_id=?,status='COMPLETED',updated_at=? WHERE id=?""",
                (str(artifact["id"]), now, batch_id),
            )
            complete = connection.execute(
                "SELECT COUNT(*) AS count FROM mp_project_knowledge_index_batches WHERE index_run_id=? AND status='COMPLETED'",
                (run_id,),
            ).fetchone()
            total = connection.execute(
                "SELECT COUNT(*) AS count FROM mp_project_knowledge_index_batches WHERE index_run_id=?",
                (run_id,),
            ).fetchone()
            run = connection.execute("SELECT status FROM mp_project_knowledge_index_runs WHERE id=?", (run_id,)).fetchone()
            status = str(run["status"]) if run is not None and str(run["status"]) in {"FAILED", "QUEUE_FAILED"} else "SUCCEEDED" if int(complete["count"]) == int(total["count"]) else "QUEUED"
            connection.execute(
                """UPDATE mp_project_knowledge_index_runs
                   SET completed_batch_count=?,status=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (int(complete["count"]), status, now, run_id),
            )
        return True

    def record_failure_if_project_knowledge_batch(self, snapshot: Any, failure_code: str) -> bool:
        """Persist a terminal index failure without touching already verified vectors."""
        run_id = snapshot.semantic_inputs.get("project_knowledge_index_run_id")
        batch_id = snapshot.semantic_inputs.get("project_knowledge_index_batch_id")
        if not isinstance(run_id, str) or not run_id or not isinstance(batch_id, str) or not batch_id:
            return False
        with self.database.transaction() as connection:
            batch = connection.execute(
                """SELECT execution_snapshot_id,job_id,status FROM mp_project_knowledge_index_batches
                   WHERE id=? AND index_run_id=?""",
                (batch_id, run_id),
            ).fetchone()
            if batch is None:
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_NOT_FOUND", "知识库失败批次不存在。")
            if str(batch["execution_snapshot_id"] or "") != str(snapshot.execution_snapshot_id) or str(batch["job_id"] or "") != str(snapshot.job_id):
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_SNAPSHOT_MISMATCH", "知识库失败批次与 Worker 快照不匹配。")
            now = _utc_now()
            if str(batch["status"]) == "QUEUED":
                connection.execute("UPDATE mp_project_knowledge_index_batches SET status='FAILED',updated_at=? WHERE id=?", (now, batch_id))
            connection.execute(
                """UPDATE mp_project_knowledge_index_runs
                   SET status='FAILED',failure_code=?,updated_at=?,revision=revision+1
                   WHERE id=? AND status NOT IN ('SUCCEEDED','QUEUE_FAILED')""",
                (failure_code[:120], now, run_id),
            )
        return True

    def _validated_batch(
        self, snapshot: Any, run_id: str, batch_id: str, artifact: Mapping[str, Any]
    ) -> tuple[list[dict[str, object]], list[list[float]]]:
        with self.database.connect() as connection:
            batch = connection.execute(
                "SELECT chunk_manifest_json FROM mp_project_knowledge_index_batches WHERE id=? AND index_run_id=?",
                (batch_id, run_id),
            ).fetchone()
        if batch is None:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_BATCH_NOT_FOUND", "知识库批次不存在。")
        manifest = _manifest_items(batch["chunk_manifest_json"])
        texts = snapshot.semantic_inputs.get("texts")
        if not isinstance(texts, list) or texts != [item["text"] for item in manifest]:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_TEXT_MANIFEST_MISMATCH", "冻结 Embedding 文本与知识库批次清单不一致。")
        relative = artifact.get("sandbox_rel_path")
        if not isinstance(relative, str) or not relative:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_PATH_INVALID", "Embedding 产物缺少受控路径。")
        path = controlled_path(self.settings.work_root, relative, must_exist=True, require_file=True, code="MP_PROJECT_KNOWLEDGE_ARTIFACT_PATH_INVALID")
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 产物不是合法 JSON。") from error
        if not isinstance(payload, dict):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 产物必须是 JSON 对象。")
        vectors = payload.get("vectors")
        if payload.get("schema") != "localdramastudio.embedding-result.v1" or payload.get("dimension") != 4096 or payload.get("count") != len(manifest) or not isinstance(vectors, list) or len(vectors) != len(manifest):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 产物不满足知识库向量合同。")
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != 4096:
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 向量维度不正确。")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as error:
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 向量存在非法数值。") from error
            if not all(math.isfinite(value) for value in values):
                raise DomainRuleError("MP_PROJECT_KNOWLEDGE_ARTIFACT_INVALID", "Embedding 向量存在非有限数值。")
            normalized.append(values)
        return manifest, normalized


def _chunks(text: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for paragraph in source_paragraphs(text):
        for offset in range(0, len(paragraph.text), _MAX_TEXT_CHARACTERS):
            value = paragraph.text[offset:offset + _MAX_TEXT_CHARACTERS]
            result.append({"ordinal": len(result) + 1, "source_start": paragraph.start + offset, "source_end": paragraph.start + offset + len(value), "text": value, "text_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()})
    return result


def _batches(chunks: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    return [chunks[index:index + _BATCH_SIZE] for index in range(0, len(chunks), _BATCH_SIZE)]


def _manifest_items(value: object) -> list[dict[str, object]]:
    try:
        items = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次清单已损坏。") from error
    if not isinstance(items, list) or not items or len(items) > _BATCH_SIZE:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次清单数量无效。")
    required = ("ordinal", "source_start", "source_end", "text", "text_sha256")
    normalized: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次清单字段不完整。")
        text = item["text"]
        if not isinstance(text, str) or not text or len(text) > _MAX_TEXT_CHARACTERS or hashlib.sha256(text.encode("utf-8")).hexdigest() != item["text_sha256"]:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次文本或 hash 无效。")
        try:
            start, end, ordinal = int(item["source_start"]), int(item["source_end"]), int(item["ordinal"])
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次偏移无效。") from error
        if ordinal < 1 or start < 0 or end < start:
            raise DomainRuleError("MP_PROJECT_KNOWLEDGE_MANIFEST_INVALID", "知识库批次偏移范围无效。")
        normalized.append({"ordinal": ordinal, "source_start": start, "source_end": end, "text": text, "text_sha256": str(item["text_sha256"])})
    return normalized


def _pack_vector(vector: list[float]) -> bytes:
    try:
        return struct.pack(f"<{len(vector)}f", *vector)
    except struct.error as error:
        raise DomainRuleError("MP_PROJECT_KNOWLEDGE_VECTOR_PACK_FAILED", "Embedding 向量不能编码为 float32。") from error


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
