"""Local LLM profile lifecycle and real script-breakdown execution."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
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
                VALUES (?, ?, 1, 'SCRIPT_BREAKDOWN_LLM', ?, ?, ?, ?, NULL, ?, 'ONE_LOCAL_LLM_TASK', ?, ?, ?, 1, 'v2')
                ON CONFLICT(execution_profile_id, version_no) DO UPDATE SET model_bundle_json=excluded.model_bundle_json,
                input_contract_json=excluded.input_contract_json, parameter_schema_json=excluded.parameter_schema_json,
                status=excluded.status, capability_json=excluded.capability_json, updated_at=excluded.updated_at,
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
            if "LLM" not in str(row["capability"]).upper():
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

    def breakdown(self, session_id: str, profile_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.project_id, s.source_document_version_id, s.id AS session_id, s.status, v.extracted_text_rel,
                v.source_document_id FROM import_sessions s JOIN source_document_versions v ON v.id=s.source_document_version_id
                JOIN execution_profile_versions p ON p.id=? WHERE s.id=? AND p.status='PUBLISHED' AND p.capability LIKE '%LLM%'""",
                (profile_version_id, session_id),
            ).fetchone()
            profile = connection.execute("SELECT capability_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None or profile is None:
            raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "所选 Profile 不是已发布的本地 LLM 能力")
        capability = json.loads(profile["capability_json"] or "{}")
        model = capability.get("model")
        if not model:
            raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "已发布 Profile 未记录显式本地模型")
        extracted = Path(str(row["extracted_text_rel"]))
        project_root = (self.settings.projects_root / self._project_code(str(row["project_id"]))).resolve()
        text_path = (project_root / extracted).resolve()
        if not text_path.is_relative_to(project_root) or not text_path.is_file():
            raise DomainRuleError("SOURCE_TEXT_NOT_FOUND", "剧本提取文本不在项目目录或不存在")
        source_text = text_path.read_text(encoding="utf-8")
        draft = self.client(str(model)).chat_json(
            "你是本地剧本拆解器。最终答案只输出 JSON 对象。顶层必须且只能有 scenes 数组；每个 scene 必须包含 scene_no、title、summary、characters、shots；shots 必须是数组，每个 shot 必须包含 shot_no、visual、action、dialogue、duration_seconds。不得省略字段，不得臆造原文不存在的关键事实。",
            source_text,
        )
        if not isinstance(draft.get("scenes"), list):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解必须包含 scenes 数组")
        if not draft["scenes"]:
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "剧本拆解 scenes 不能为空")
        scene_fields = {"scene_no", "title", "summary", "characters", "shots"}
        shot_fields = {"shot_no", "visual", "action", "dialogue", "duration_seconds"}
        for scene_index, scene in enumerate(draft["scenes"], start=1):
            if not isinstance(scene, dict) or not scene_fields.issubset(scene) or not isinstance(scene.get("characters"), list) or not isinstance(scene.get("shots"), list):
                raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 不符合拆镜 schema", {"scene_index": scene_index})
            if not scene["shots"]:
                raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM scene 必须包含至少一个 shot", {"scene_index": scene_index})
            for shot_index, shot in enumerate(scene["shots"], start=1):
                if not isinstance(shot, dict) or not shot_fields.issubset(shot):
                    raise DomainRuleError(
                        "LOCAL_LLM_OUTPUT_INVALID",
                        "本地 LLM shot 不符合拆镜 schema",
                        {"scene_index": scene_index, "shot_index": shot_index},
                    )
        draft_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO script_breakdown_drafts (id, project_id, source_document_version_id, import_session_id, draft_json, confidence_json, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT_READY', ?, ?, 'local-llm', 1, 'v2')",
                (
                    draft_id,
                    row["project_id"],
                    row["source_document_version_id"],
                    session_id,
                    _json(draft),
                    _json({"source": "model_output", "model": model}),
                    now,
                    now,
                ),
            )
            connection.execute("UPDATE import_sessions SET status='BREAKDOWN_READY', updated_at=?, revision=revision+1 WHERE id=?", (now, session_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES ('local-llm', 'producer', 'SCRIPT_BREAKDOWN_COMPLETED', 'script_breakdown_draft', ?, ?, ?)",
                (draft_id, "本地 LLM 完成剧本拆解", _json({"session_id": session_id, "profile_version_id": profile_version_id, "model": model})),
            )
        return {"id": draft_id, "status": "DRAFT_READY", "profile_version_id": profile_version_id, "draft": draft}

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
            item.update({"application_status": "NOT_APPLIED", "automatic_apply": False, "requires_human_action": True})
            items.append(item)
        return items

    def _project_code(self, project_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute("SELECT code FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return str(row["code"])
