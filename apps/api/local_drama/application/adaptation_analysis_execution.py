"""Worker-side execution of one immutable adaptation analysis DAG node."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from local_drama.application.local_llm import LocalLLMService
from local_drama.application.ports.adaptation_planning import AdaptationPlanningRepository
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


ProgressCallback = Callable[[dict[str, Any]], None]


class AdaptationAnalysisExecutionService:
    """Execute a node without ever trusting stale queue text or Profile state."""

    def __init__(self, repository: AdaptationPlanningRepository, settings: Settings, llm: LocalLLMService, *, database: Any = None) -> None:
        self.repository = repository
        self.settings = settings
        self.llm = llm
        self._database = database

    def execute_node(self, job: dict[str, Any], *, on_progress: ProgressCallback) -> dict[str, Any]:
        raw_snapshot: Any = job.get("input_snapshot")
        snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}
        self._validate_snapshot(job, snapshot)
        on_progress({"phase": "VERIFYING_INPUT", "percent": 10})
        context = self._load_context(job, snapshot)
        user_input = self._user_input(snapshot, context)
        if str(snapshot["stage"]) in {"CHUNK_MAP", "ARC_REDUCE", "EPISODE_BOUNDARY"}:
            knowledge_block = self._knowledge_block(snapshot, context, user_input)
            if knowledge_block:
                user_input = f"{user_input}\n\n{knowledge_block}"
        request_hash = hashlib.sha256(
            _json({"snapshot": snapshot, "model": context["model"], "provider": context["provider"]}).encode("utf-8")
        ).hexdigest()
        invocation_id = self.repository.record_analysis_invocation(
            job_id=str(job["id"]),
            run_node_id=str(snapshot["run_node_id"]),
            profile_version_id=str(snapshot["profile_version_id"]),
            provider=context["provider"],
            model=context["model"],
            request_sha256=request_hash,
        )
        started = time.monotonic()
        try:
            on_progress({"phase": "CALLING_LLM", "percent": 25})
            output = self.llm.client(
                model=context["model"],
                provider=context["provider"],
                base_url=context["base_url"],
                provider_connection_id=context["provider_connection_id"],
            ).chat_json(
                self._system_prompt(str(snapshot["stage"])),
                user_input,
                inference_options={"num_ctx": 8192},
            )
            if not isinstance(output, dict):
                raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", "分层分析模型没有返回 JSON 对象")
            on_progress({"phase": "PERSISTING_RESULT", "percent": 85})
            self.repository.persist_analysis_node_success(
                snapshot=snapshot,
                output=output,
                latency_ms=int((time.monotonic() - started) * 1000),
                invocation_id=invocation_id,
            )
            on_progress({"phase": "NODE_READY", "percent": 100})
            return {"node_key": snapshot["node_key"], "stage": snapshot["stage"], "status": "SUCCEEDED"}
        except Exception as error:
            self.repository.record_analysis_failure(
                invocation_id=invocation_id,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type=type(error).__name__,
            )
            if isinstance(error, DomainRuleError):
                raise
            raise DomainRuleError("ADAPTATION_LLM_EXECUTION_FAILED", "分层分析模型调用失败") from error

    def _project_id_for_source(self, source_document_version_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT i.project_id FROM import_sessions i
                JOIN source_document_versions v ON v.id=i.source_document_version_id
                WHERE v.id=?""",
                (source_document_version_id,),
            ).fetchone()
        return str(row["project_id"]) if row else None

    def _knowledge_block(self, snapshot: dict[str, Any], context: dict[str, Any], user_input: str) -> str | None:
        """Best-effort project-knowledge injection (§6.1.1); fails open to no-op.

        Only same-source hits are injected, and the block explicitly subordinates
        retrieved fragments to the inline source text, so the analysis
        anti-fabrication contract stays intact.  The frozen job snapshot is not
        modified: retrieval is a runtime augmentation like the source file read.
        """
        if self._database is None:
            return None
        try:
            project_id = self._project_id_for_source(str(snapshot["source_document_version_id"]))
            if project_id is None:
                return None
            from local_drama.model_platform.application.project_knowledge_retrieval import (
                ProjectKnowledgeRetrievalService,
            )

            retrieval = ProjectKnowledgeRetrievalService(self._database, self.settings)
            _profile_version_id, hits = retrieval.search(
                project_id=project_id, query=user_input[:1000], limit=6,
            )
        except Exception:
            return None
        source_id = str(snapshot["source_document_version_id"])
        source_text = str(context["source_text"])
        fragments: list[str] = []
        for hit in hits:
            if str(hit.source_document_version_id) != source_id or hit.source_end <= hit.source_start:
                continue
            fragment = source_text[max(0, hit.source_start):hit.source_end].strip()
            if fragment:
                fragments.append(fragment)
        if not fragments:
            return None
        lines = "\n".join(f"[片段{index + 1}] {fragment}" for index, fragment in enumerate(fragments[:4]))
        return (
            "项目知识库检索到的同源原文片段（仅作设定参考；与上方正文冲突时以上方正文为准）：\n" + lines
        )

    def _validate_snapshot(self, job: dict[str, Any], snapshot: dict[str, Any]) -> None:
        required = {"plan_id", "plan_revision_id", "run_id", "run_node_id", "node_key", "stage", "source_document_version_id", "source_text_sha256", "profile_version_id"}
        if str(job.get("type")) != "ADAPTATION_ANALYSIS_LOCAL_LLM" or not required <= set(snapshot):
            raise DomainRuleError("ADAPTATION_JOB_SNAPSHOT_INVALID", "分层分析 Job 缺少不可变运行快照")
        if snapshot.get("automatic_apply") is not False or snapshot.get("creates_media") is not False:
            raise DomainRuleError("ADAPTATION_JOB_SNAPSHOT_INVALID", "分层分析 Job 不能自动应用或生成媒体")

    def _load_context(self, job: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        rows = self.repository.analysis_execution_context(job_id=str(job["id"]), snapshot=snapshot)
        node, source, profile = rows["node"], rows["source"], rows["profile"]
        if node is None or str(node["job_id"] or "") != str(job["id"]) or str(node["input_fingerprint"]) != str(snapshot["input_fingerprint"]):
            raise DomainRuleError("ADAPTATION_JOB_SNAPSHOT_STALE", "分析节点已变化，拒绝执行旧 Job")
        if source is None or str(source["text_sha256"] or "") != str(snapshot["source_text_sha256"]):
            raise DomainRuleError("ADAPTATION_SOURCE_CHANGED", "原稿提取文本已变化，拒绝执行旧分析 Job")
        if profile is None or str(profile["status"]) != "PUBLISHED" or str(profile["capability"]).upper() != "LLM_STORY_PARSE":
            raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "分层分析 Profile 已不可用")
        capability = _loads(profile["capability_json"], {})
        bundle = _loads(profile["model_bundle_json"], {})
        provider_connection_id = str(bundle.get("provider_connection_id") or capability.get("provider_connection_id") or "").strip() or None
        if provider_connection_id:
            selected_connection = self.repository.active_provider_connection(connection_id=provider_connection_id)
            provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
            model = str(bundle.get("model") or selected_connection.get("model") or "").strip()
            base_url = str(selected_connection["base_url"]).strip()
        else:
            provider = str(bundle.get("provider") or capability.get("provider") or "OLLAMA_LOOPBACK").upper()
            model = str(bundle.get("model") or capability.get("model") or "").strip()
            base_url = str(capability.get("base_url") or self.settings.llm_base_url).strip()
        if not model:
            raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "分层分析 Profile 未冻结模型")
        source_path = controlled_path(
            self.settings.projects_root / str(source["root_rel"]),
            str(source["extracted_text_rel"] or ""),
            must_exist=True,
            require_file=True,
            code="ADAPTATION_SOURCE_TEXT_MISSING",
        )
        source_text = source_path.read_text(encoding="utf-8")
        if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != str(snapshot["source_text_sha256"]):
            raise DomainRuleError("ADAPTATION_SOURCE_CHANGED", "原稿文件 hash 已变化，拒绝执行旧分析 Job")
        return {
            "node_descriptor": _loads(node["output_json"], {}),
            "source_text": source_text,
            "upstream": [
                {"stage": item["stage"], "node_key": item["node_key"], "output": _loads(item["output_json"], {})}
                for item in rows["upstream"]
            ],
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "provider_connection_id": provider_connection_id,
        }

    @staticmethod
    def _system_prompt(stage: str) -> str:
        contracts = {
            "CHUNK_MAP": "输出 JSON 对象，包含 summary（字符串）、events（数组）、characters（字符串数组）、open_threads（字符串数组）、confidence（0到1数字）。事件只能基于输入原文。",
            "ARC_REDUCE": "输出 JSON 对象，包含 arcs（数组）；每项必须有 title、summary、dramatic_promise、open_threads。只能归纳上游节点事实。",
            "SEASON_PLAN": "输出 JSON 对象，包含 seasons（数组）；每项必须有 title、release_intent、arc_titles。只能基于故事弧。",
            "EPISODE_BOUNDARY": "输出 JSON 对象，包含 episodes（数组）；每项必须有 title、logline、opening_carry、core_conflict、payoff、ending_hook、source_node_keys（引用输入中已有的 CHUNK_MAP node_key 字符串数组）。可选 estimated_duration_ms、arc_ordinal、season_ordinal。不得创造原稿事实。",
            "VALIDATE": "输出 JSON 对象，包含 status（PASS 或 NEEDS_REVIEW）、issues（数组）和 summary（字符串）。检查上游规划是否可审核，不得改写它。",
        }
        return "你是长篇小说改编分析器。仅输出合法 JSON，不输出 Markdown。" + contracts.get(stage, "")

    @staticmethod
    def _user_input(snapshot: dict[str, Any], context: dict[str, Any]) -> str:
        stage = str(snapshot["stage"])
        if stage == "CHUNK_MAP":
            start = int(snapshot.get("context_source_start") or 0)
            end = int(snapshot.get("context_source_end") or 0)
            if end <= start:
                raise DomainRuleError("ADAPTATION_NODE_RANGE_INVALID", "原稿分块缺少有效上下文范围")
            source_text: str = context["source_text"]
            return source_text[start:end]
        upstream = [item for item in context["upstream"] if item["output"].get("planning_state") == "SUCCEEDED"]
        if not upstream:
            raise DomainRuleError("ADAPTATION_UPSTREAM_NOT_READY", "上游分析节点尚未完成")
        return _json({"stage": stage, "upstream": upstream})
