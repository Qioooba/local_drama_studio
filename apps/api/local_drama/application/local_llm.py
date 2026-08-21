"""Local LLM profile lifecycle and real script-breakdown execution."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.config import Settings
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:{value}"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:100] or "model"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _profile_runtime_contract(capability: dict[str, Any], fallback_base_url: str) -> dict[str, str]:
    """Select only immutable execution fields; readiness probes are volatile."""
    return {
        "provider": str(capability.get("provider") or ""),
        "base_url": str(capability.get("base_url") or fallback_base_url),
        "model": str(capability.get("model") or ""),
    }


def _validate_breakdown_output(value: dict[str, Any], source_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    required_top_level = {"scenes", "confidence", "questions", "source_passages"}
    if not required_top_level.issubset(value):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解缺少 scenes/confidence/questions/source_passages")
    confidence = value["confidence"]
    questions = value["questions"]
    passages = value["source_passages"]
    if not isinstance(confidence, dict) or not isinstance(confidence.get("overall"), (int, float)):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 confidence.overall 必须是 0 到 1 的数字")
    overall = float(confidence["overall"])
    notes = confidence.get("notes", [])
    if not 0 <= overall <= 1 or not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 confidence 不符合结构化契约")
    if not isinstance(questions, list) or any(not isinstance(item, str) or not item.strip() for item in questions):
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 questions 必须是非空字符串数组")
    if not isinstance(passages, list) or not passages:
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解必须包含来源段落")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not scenes:
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解必须包含非空 scenes 数组")
    scene_fields = {"scene_no", "title", "summary", "characters", "shots"}
    shot_fields = {"shot_no", "visual", "action", "dialogue", "duration_seconds"}
    scene_numbers: set[int] = set()
    for scene_index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or not scene_fields.issubset(scene) or not isinstance(scene.get("characters"), list) or not isinstance(scene.get("shots"), list):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 不符合拆镜 schema", {"scene_index": scene_index})
        if not isinstance(scene["scene_no"], int) or scene["scene_no"] in scene_numbers:
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "scene_no 必须是唯一整数", {"scene_index": scene_index})
        scene_numbers.add(scene["scene_no"])
        if not scene["shots"]:
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 必须包含至少一个 shot", {"scene_index": scene_index})
        for shot_index, shot in enumerate(scene["shots"], start=1):
            if not isinstance(shot, dict) or not shot_fields.issubset(shot):
                raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM shot 不符合拆镜 schema", {"scene_index": scene_index, "shot_index": shot_index})
    normalized_passages: list[dict[str, Any]] = []
    covered: set[int] = set()
    for passage_index, passage in enumerate(passages, start=1):
        if not isinstance(passage, dict) or not isinstance(passage.get("scene_no"), int) or not isinstance(passage.get("quote"), str):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "来源段落必须包含 scene_no 和 quote", {"passage_index": passage_index})
        quote = passage["quote"].strip()
        start = source_text.find(quote)
        if passage["scene_no"] not in scene_numbers or not quote or start < 0:
            raise DomainRuleError("LOCAL_LLM_SOURCE_QUOTE_INVALID", "来源段落必须逐字存在于导入文本并关联有效场次", {"passage_index": passage_index})
        covered.add(passage["scene_no"])
        normalized_passages.append({"scene_no": passage["scene_no"], "quote": quote, "source_start": start, "source_end": start + len(quote)})
    if covered != scene_numbers:
        raise DomainRuleError("LOCAL_LLM_SOURCE_QUOTE_INVALID", "每个建议场次都必须至少有一个来源段落")
    return {"scenes": scenes}, {"confidence": {"overall": overall, "notes": notes}, "questions": questions, "source_passages": normalized_passages}


class LocalLLMService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def client(self, model: str | None = None) -> LocalLLMClient:
        return LocalLLMClient(self.settings.llm_base_url, model or self.settings.llm_model)

    def status(self) -> dict[str, Any]:
        if not self.settings.llm_model:
            return {"status": "BLOCKED", "base_url": self.settings.llm_base_url, "model": None, "error_code": "LOCAL_LLM_MODEL_REQUIRED"}
        return self.client().probe(load_test=True)

    def sync_candidate(self, model: str | None = None, actor: str = "local-user") -> dict[str, Any]:
        selected_model = model or self.settings.llm_model
        if not selected_model:
            return self.status()
        client = self.client(selected_model)
        probe = client.probe(load_test=False)
        code = f"local-llm-ollama-{_slug(selected_model)}"
        runtime_id = _stable_id(f"runtime:{self.settings.llm_base_url}")
        profile_id = _stable_id(f"profile:{code}")
        version_id = _stable_id(f"profile-version:{code}:1")
        now = _now()
        status = "CANDIDATE_UNVERIFIED" if probe["status"] == "PASS" else "CANDIDATE_BLOCKED"
        capability_json = {
            "provider": "OLLAMA_LOOPBACK",
            "base_url": self.settings.llm_base_url,
            "model": selected_model,
            "probe": probe,
            "published": False,
            "activation_required": True,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO local_runtimes
                (id, code, title, transport, base_url, executable_ref, runtime_version, status, details_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, 'LOOPBACK_HTTP', ?, 'ollama', NULL, ?, ?, ?, ?, ?, 1, 'v2')
                ON CONFLICT(id) DO UPDATE SET code=excluded.code, title=excluded.title, base_url=excluded.base_url,
                status=excluded.status, details_json=excluded.details_json, updated_at=excluded.updated_at,
                revision=local_runtimes.revision+1""",
                (
                    runtime_id,
                    "ollama-loopback",
                    "Ollama 本地 LLM Runtime",
                    self.settings.llm_base_url,
                    status,
                    _json(probe),
                    now,
                    now,
                    actor,
                ),
            )
            current = connection.execute("SELECT status FROM execution_profile_versions WHERE id=?", (version_id,)).fetchone()
            readiness = client.probe(load_test=True) if current and current["status"] == "PUBLISHED" else probe
            profile_status = (
                "PUBLISHED"
                if current and current["status"] == "PUBLISHED" and readiness["status"] == "PASS"
                else "CANDIDATE_UNVERIFIED"
                if probe["status"] == "PASS"
                else "CANDIDATE_BLOCKED"
            )
            capability_json["readiness_probe"] = readiness
            connection.execute(
                """INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'v2')
                ON CONFLICT(code) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at, revision=execution_profiles.revision+1""",
                (profile_id, code, f"Ollama {selected_model} 剧本拆解候选 Profile", now, now, actor),
            )
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
                 parameter_schema_json, status, manifest_sha256, capability_json, worker_policy,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 1, 'LLM_STORY_PARSE', ?, ?, ?, ?, NULL, ?, 'ONE_LOCAL_LLM_TASK', ?, ?, ?, 1, 'v2')
                ON CONFLICT(execution_profile_id, version_no) DO UPDATE SET model_bundle_json=excluded.model_bundle_json,
                input_contract_json=excluded.input_contract_json, parameter_schema_json=excluded.parameter_schema_json,
                capability=excluded.capability,status=excluded.status, capability_json=excluded.capability_json, updated_at=excluded.updated_at,
                revision=execution_profile_versions.revision+1""",
                (
                    version_id,
                    profile_id,
                    _json({"runtime_id": runtime_id, "model": selected_model}),
                    _json({"source": "TXT|MD|DOCX", "output": "SCRIPT_BREAKDOWN_DRAFT", "transport": "LOOPBACK_HTTP"}),
                    _json({"temperature": {"value": 0, "locked": True}}),
                    profile_status,
                    _json(capability_json),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'LOCAL_LLM_CANDIDATE_SYNCED', 'execution_profile_version', ?, ?, ?)",
                (actor, version_id, "同步 Ollama 本地 LLM 候选 Profile", _json({"probe": probe, "model": selected_model})),
            )
        return {"profile_version_id": version_id, "profile_code": code, "status": profile_status, "probe": probe}

    def publish(self, profile_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT id, capability, capability_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROFILE_NOT_FOUND", "Profile 版本不存在")
            try:
                profile_capability = normalize_capability(str(row["capability"]))
            except ValueError as error:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_INVALID",
                    "Profile capability 不是可识别的 canonical capability",
                    {"profile_version_id": profile_version_id},
                ) from error
            if profile_capability != "LLM_STORY_PARSE":
                raise DomainRuleError("PROFILE_CAPABILITY_MISMATCH", "Profile 不是本地 LLM 能力")
            capability = json.loads(row["capability_json"] or "{}")
            model = str(capability.get("model") or "")
            probe = self.client(model).probe(load_test=True)
            if probe.get("status") != "PASS":
                raise DomainRuleError("LOCAL_LLM_VALIDATION_REQUIRED", "本地 LLM 必须真实加载所选模型后才能发布", {"model": model})
            now = _now()
            connection.execute(
                "UPDATE execution_profile_versions SET status='PUBLISHED', updated_at=?, revision=revision+1 WHERE id=?", (now, profile_version_id)
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'LOCAL_LLM_PROFILE_PUBLISHED', 'execution_profile_version', ?, ?, ?)",
                (actor, profile_version_id, "发布本地 LLM Profile", _json(probe)),
            )
        return {"profile_version_id": profile_version_id, "status": "PUBLISHED", "probe": probe}

    def _breakdown_context(self, session_id: str, profile_version_id: str) -> tuple[Any, dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.project_id, s.source_document_version_id, s.id AS session_id, s.status, v.extracted_text_rel,
                v.source_document_id, v.text_sha256, v.sha256 AS source_sha256
                FROM import_sessions s JOIN source_document_versions v ON v.id=s.source_document_version_id
                JOIN execution_profile_versions p ON p.id=? WHERE s.id=? AND p.status='PUBLISHED'
                AND p.capability='LLM_STORY_PARSE'""",
                (profile_version_id, session_id),
            ).fetchone()
            profile = connection.execute("SELECT capability_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None or profile is None:
            raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "所选 Profile 不是已发布的本地 LLM 能力")
        if str(row["status"]) not in {"COMMITTED", "BREAKDOWN_READY"}:
            raise DomainRuleError("IMPORT_SESSION_NOT_COMMITTED", "只有已确认 commit 的导入会话可以提交 AI 拆解任务")
        capability = json.loads(profile["capability_json"] or "{}")
        model = capability.get("model")
        if not model:
            raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "已发布 Profile 未记录显式本地模型")
        return row, capability

    def enqueue_breakdown(
        self,
        session_id: str,
        profile_version_id: str | None,
        idempotency_key: str,
        *,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Persist a local-LLM request as a Job without contacting Ollama.

        The immutable snapshot is deliberately bounded to source/profile facts;
        no generated scene or shot is materialized by this command.
        """
        if not profile_version_id:
            raise DomainRuleError("LOCAL_LLM_PROFILE_REQUIRED", "剧本拆解必须显式选择已发布的本地 LLM Profile")
        row, capability = self._breakdown_context(session_id, profile_version_id)
        model = str(capability["model"])
        base_url = str(capability.get("base_url") or self.settings.llm_base_url)
        runtime_contract = _profile_runtime_contract(capability, self.settings.llm_base_url)
        snapshot = {
            "schema_version": "localdrama.script-breakdown-job.v1",
            "import_session_id": session_id,
            "source_document_version_id": str(row["source_document_version_id"]),
            "source_sha256": str(row["source_sha256"]),
            "source_text_sha256": str(row["text_sha256"]),
            "profile_version_id": profile_version_id,
            "profile_capability_sha256": _sha256_json(runtime_contract),
            "model": model,
            "base_url": base_url,
            "automatic_apply": False,
            "requires_human_action": True,
        }
        return JobService(self.database, self.settings).create_job(
            str(row["project_id"]),
            "SCRIPT_BREAKDOWN_LOCAL_LLM",
            "IMPORT_SESSION",
            session_id,
            "CPU",
            snapshot,
            idempotency_key,
            execution_profile_version_id=profile_version_id,
            priority=60,
            # Local model failures require an explicit operator retry; this
            # prevents a broken prompt/model from being hammered automatically.
            max_attempts=1,
            actor=actor,
        )

    def _assert_job_can_persist(self, job_id: str, session_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT type,subject_type,subject_id,state FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("JOB_NOT_FOUND", "AI 拆解 Job 不存在", {"job_id": job_id})
        if (
            str(row["type"]) != "SCRIPT_BREAKDOWN_LOCAL_LLM"
            or str(row["subject_type"]) != "IMPORT_SESSION"
            or str(row["subject_id"]) != session_id
        ):
            raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_INVALID", "AI 拆解 Job 与导入会话不匹配")
        if str(row["state"]) == "CANCEL_REQUESTED":
            raise DomainRuleError("JOB_CANCELLED", "AI 拆解已请求取消；不会保存模型输出")
        if str(row["state"]) not in {"CLAIMED", "RUNNING"}:
            raise DomainRuleError("LOCAL_LLM_JOB_NOT_RUNNING", "只有正在执行的 AI 拆解 Job 可以保存草稿")

    def breakdown(
        self,
        session_id: str,
        profile_version_id: str,
        *,
        job_id: str | None = None,
        input_snapshot: dict[str, Any] | None = None,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        """Execute the model call; production routes enqueue this via JobService.

        ``job_id`` makes the draft id deterministic so lease recovery cannot
        duplicate a draft after a crash between persistence and Job completion.
        """
        row, capability = self._breakdown_context(session_id, profile_version_id)
        model = str(capability["model"])
        base_url = str(capability.get("base_url") or self.settings.llm_base_url)
        runtime_contract = _profile_runtime_contract(capability, self.settings.llm_base_url)
        if input_snapshot is not None:
            expected = {
                "import_session_id": session_id,
                "source_document_version_id": str(row["source_document_version_id"]),
                "source_sha256": str(row["source_sha256"]),
                "source_text_sha256": str(row["text_sha256"]),
                "profile_version_id": profile_version_id,
                "profile_capability_sha256": _sha256_json(runtime_contract),
                "model": model,
                "base_url": base_url,
                "automatic_apply": False,
                "requires_human_action": True,
            }
            if any(input_snapshot.get(key) != value for key, value in expected.items()):
                raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_STALE", "AI 拆解 Job 的源文本或 Profile 快照已变化")
        draft_id = _stable_id(f"breakdown-job:{job_id}") if job_id else str(uuid.uuid4())
        if job_id:
            self._assert_job_can_persist(job_id, session_id)
            with self.database.connect() as connection:
                existing = connection.execute(
                    "SELECT draft_json,status FROM script_breakdown_drafts WHERE id=?", (draft_id,)
                ).fetchone()
            if existing is not None:
                return {
                    "id": draft_id,
                    "status": str(existing["status"]),
                    "profile_version_id": profile_version_id,
                    "draft": json.loads(str(existing["draft_json"])),
                    "idempotent_replay": True,
                    "automatic_apply": False,
                    "requires_human_action": True,
                }
        if on_progress:
            on_progress({"phase": "CALLING_LOCAL_LLM", "percent": 20})
        extracted = Path(str(row["extracted_text_rel"]))
        project_root = (self.settings.projects_root / self._project_code(str(row["project_id"]))).resolve()
        text_path = (project_root / extracted).resolve()
        if not text_path.is_relative_to(project_root) or not text_path.is_file():
            raise DomainRuleError("SOURCE_TEXT_NOT_FOUND", "剧本提取文本不在项目目录或不存在")
        source_bytes = text_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != str(row["text_sha256"]):
            raise DomainRuleError("SOURCE_TEXT_CHANGED", "剧本提取文本 hash 已变化，拒绝执行旧 Job 快照")
        source_text = source_bytes.decode("utf-8")
        output = LocalLLMClient(base_url, model).chat_json(
            "你是本地剧本拆解器。最终答案只输出 JSON 对象，顶层必须包含 scenes、confidence、questions、source_passages。每个 scene 必须包含 scene_no、title、summary、characters、shots；每个 shot 必须包含 shot_no、visual、action、dialogue、duration_seconds。confidence 必须是 {overall:0到1,notes:字符串数组}；questions 是待人工确认的字符串数组；source_passages 是 {scene_no,quote} 数组，每个场次至少一条且 quote 必须逐字复制原文。不得臆造原文不存在的关键事实。",
            source_text,
        )
        if job_id:
            self._assert_job_can_persist(job_id, session_id)
        if on_progress:
            on_progress({"phase": "VALIDATING_OUTPUT", "percent": 80})
        draft, evidence = _validate_breakdown_output(output, source_text)
        evidence.update({"schema_version": "localdrama.script-breakdown-evidence.v1", "source": "model_output", "model": model, "profile_version_id": profile_version_id, "job_id": job_id})
        now = _now()
        with self.database.transaction() as connection:
            if job_id:
                persisted_job = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
                if persisted_job is None or str(persisted_job["state"]) == "CANCEL_REQUESTED":
                    raise DomainRuleError("JOB_CANCELLED", "AI 拆解已请求取消；不会保存模型输出")
                if str(persisted_job["state"]) not in {"CLAIMED", "RUNNING"}:
                    raise DomainRuleError("LOCAL_LLM_JOB_NOT_RUNNING", "AI 拆解 Job 状态已变化，拒绝保存模型输出")
            connection.execute(
                "INSERT OR IGNORE INTO script_breakdown_drafts (id, project_id, source_document_version_id, import_session_id, draft_json, confidence_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT_READY', ?, ?, 'local-llm', 1, 'v2')",
                (
                    draft_id,
                    row["project_id"],
                    row["source_document_version_id"],
                    session_id,
                    _json(draft),
                    _json(evidence),
                    now,
                    now,
                ),
            )
            connection.execute("UPDATE import_sessions SET status='BREAKDOWN_READY', updated_at=?, revision=revision+1 WHERE id=?", (now, session_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, job_id, summary, metadata_redacted_json) VALUES ('local-llm', 'producer', 'SCRIPT_BREAKDOWN_COMPLETED', 'script_breakdown_draft', ?, ?, ?, ?)",
                (draft_id, job_id, "本地 LLM 完成剧本拆解草稿（等待人工应用）", _json({"session_id": session_id, "profile_version_id": profile_version_id, "model": model, "job_id": job_id, "automatic_apply": False})),
            )
        if on_progress:
            on_progress({"phase": "DRAFT_READY", "percent": 95, "draft_id": draft_id})
        return {"id": draft_id, "status": "DRAFT_READY", "profile_version_id": profile_version_id, "draft": draft, "idempotent_replay": False, "automatic_apply": False, "requires_human_action": True}

    def list_breakdown_drafts(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            rows = connection.execute(
                """SELECT d.*,sd.code AS source_document_code,sd.title AS source_document_title
                FROM script_breakdown_drafts d
                JOIN source_document_versions sdv ON sdv.id=d.source_document_version_id
                JOIN source_documents sd ON sd.id=sdv.source_document_id
                WHERE d.project_id=? ORDER BY d.created_at DESC,d.id""",
                (project_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["draft"] = json.loads(item.pop("draft_json"))
            item["confidence"] = json.loads(item.pop("confidence_json"))
            complete = all(key in item["confidence"] for key in ("profile_version_id", "confidence", "questions", "source_passages"))
            applied = item["status"] == "APPLIED"
            item.update({"profile_version_id": item["confidence"].get("profile_version_id"), "evidence_status": "COMPLETE" if complete else "LEGACY_INCOMPLETE", "application_status": "APPLIED" if applied else "NOT_APPLIED", "automatic_apply": False, "requires_human_action": not applied})
            items.append(item)
        return items

    def _project_code(self, project_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute("SELECT code FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return str(row["code"])
