"""SQLite adapter for the Adaptation Planning bounded context."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.adaptation_planning import SourceDocumentSnapshot
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import endpoint_is_remote
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:{value}"))


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", f"分层分析输出缺少 {field}")
    return normalized


def _items(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", f"分层分析输出缺少有效 {field}")
    return [dict(item) for item in value]


def _analysis_stage_dependencies(*, stage: str, jobs_by_stage: dict[str, list[str]]) -> list[str]:
    if stage == "CHUNK_MAP":
        return []
    if stage == "ARC_REDUCE":
        return list(jobs_by_stage.get("CHUNK_MAP", []))
    if stage == "SEASON_PLAN":
        return list(jobs_by_stage.get("ARC_REDUCE", []))
    if stage == "EPISODE_BOUNDARY":
        return list(jobs_by_stage.get("SEASON_PLAN", []))
    if stage == "VALIDATE":
        return list(jobs_by_stage.get("EPISODE_BOUNDARY", []))
    raise DomainRuleError("ADAPTATION_STAGE_INVALID", "未知的分层分析节点阶段", {"stage": stage})


class SqliteAdaptationPlanRepository:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def source_snapshot(self, *, project_id: str, source_document_version_id: str) -> SourceDocumentSnapshot:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT sd.id AS source_document_id, sd.project_id, sd.title, sdv.id AS source_document_version_id,
                          sdv.source_name, sdv.sha256 AS source_sha256, sdv.text_sha256, sdv.extracted_text_rel,
                          p.root_rel
                   FROM source_document_versions sdv
                   JOIN source_documents sd ON sd.id=sdv.source_document_id
                   JOIN projects p ON p.id=sd.project_id
                   WHERE sdv.id=? AND sd.project_id=? AND sdv.parse_status='PARSED'""",
                (source_document_version_id, project_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError(
                "ADAPTATION_SOURCE_NOT_FOUND",
                "找不到已解析的原稿版本；请先完成原稿导入与解析",
                {"source_document_version_id": source_document_version_id},
            )
        project_root = self.settings.projects_root / str(row["root_rel"])
        path = controlled_path(
            project_root,
            str(row["extracted_text_rel"] or ""),
            must_exist=True,
            require_file=True,
            code="ADAPTATION_SOURCE_TEXT_MISSING",
        )
        text = path.read_text(encoding="utf-8")
        return SourceDocumentSnapshot(
            project_id=str(row["project_id"]),
            source_document_id=str(row["source_document_id"]),
            source_document_version_id=str(row["source_document_version_id"]),
            title=str(row["title"]),
            source_name=str(row["source_name"]),
            source_sha256=str(row["source_sha256"]),
            text_sha256=str(row["text_sha256"] or ""),
            text=text,
        )

    def list_source_versions(self, *, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT sd.id AS source_document_id, sd.title, sdv.id AS source_document_version_id,
                          sdv.version_no, sdv.source_name, sdv.sha256, sdv.text_sha256, sdv.parse_status,
                          sdv.created_at,
                          (
                            SELECT preview_json
                            FROM import_sessions session
                            WHERE session.source_document_version_id=sdv.id
                            ORDER BY session.updated_at DESC
                            LIMIT 1
                          ) AS preview_json
                   FROM source_documents sd
                   JOIN source_document_versions sdv ON sdv.source_document_id=sd.id
                   WHERE sd.project_id=?
                   ORDER BY sdv.updated_at DESC, sdv.created_at DESC""",
                (project_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            preview = _loads(row["preview_json"], {})
            items.append(
                {
                    "source_document_id": str(row["source_document_id"]),
                    "source_document_version_id": str(row["source_document_version_id"]),
                    "title": str(row["title"]),
                    "source_name": str(row["source_name"]),
                    "version_no": int(row["version_no"]),
                    "parse_status": str(row["parse_status"]),
                    "source_sha256": str(row["sha256"]),
                    "text_sha256": str(row["text_sha256"] or ""),
                    "character_count": int(preview.get("character_count") or 0),
                    "paragraph_count": int(preview.get("paragraph_count") or 0),
                    "chapters": preview.get("chapters") if isinstance(preview.get("chapters"), list) else [],
                    "preview_truncated": bool(preview.get("preview_truncated")),
                    "created_at": str(row["created_at"]),
                }
            )
        return items

    def find_plan_for_request(self, *, project_id: str, request_fingerprint: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT p.id AS plan_id, p.artifact_status, r.id AS revision_id, r.revision_no,
                          run.id AS run_id, run.run_status, run.created_at
                   FROM adaptation_plan_runs run
                   JOIN adaptation_plans p ON p.id=run.plan_id
                   JOIN adaptation_plan_revisions r ON r.id=run.plan_revision_id
                   WHERE p.project_id=? AND run.request_fingerprint=?
                   ORDER BY run.created_at DESC LIMIT 1""",
                (project_id, request_fingerprint),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_plan(
        self,
        *,
        plan: dict[str, Any],
        revision: dict[str, Any],
        run: dict[str, Any],
        source_units: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (plan["project_id"],)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            existing = connection.execute(
                """SELECT p.id AS plan_id, p.artifact_status, r.id AS revision_id, r.revision_no,
                          run.id AS run_id, run.run_status, run.created_at
                   FROM adaptation_plan_runs run
                   JOIN adaptation_plans p ON p.id=run.plan_id
                   JOIN adaptation_plan_revisions r ON r.id=run.plan_revision_id
                   WHERE p.project_id=? AND run.request_fingerprint=?""",
                (plan["project_id"], run["request_fingerprint"]),
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "idempotent": True}

            connection.execute(
                """INSERT INTO adaptation_plans
                   (id,project_id,source_document_version_id,mode,artifact_status,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,?,?,?,? ,?,1,'v1')""",
                (
                    plan["id"],
                    plan["project_id"],
                    plan["source_document_version_id"],
                    plan["mode"],
                    "DRAFT",
                    revision["id"],
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO adaptation_plan_revisions
                   (id,plan_id,revision_no,parent_revision_id,source_scope_json,constraints_json,diagnosis_json,
                    analysis_contract_version,prompt_schema_version,profile_version_id,runtime_contract_json,
                    content_sha256,validation_summary_json,created_reason,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,1,NULL,?,?,?,?,?,?,?,?,?,'AI_GENERATED',?,?,?,1,'v1')""",
                (
                    revision["id"],
                    plan["id"],
                    _json(revision["source_scope"]),
                    _json(revision["constraints"]),
                    _json(revision["diagnosis"]),
                    revision["analysis_contract_version"],
                    revision["prompt_schema_version"],
                    revision.get("profile_version_id"),
                    _json(revision["runtime_contract"]),
                    revision["content_sha256"],
                    _json(revision["validation_summary"]),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO adaptation_plan_runs
                   (id,plan_id,plan_revision_id,run_status,request_fingerprint,total_nodes,completed_nodes,failed_nodes,
                    estimated_input_tokens,estimated_cost_microunits,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,'PREPARED',?,0,0,0,?,?,?,? ,?,1,'v1')""",
                (
                    run["id"],
                    plan["id"],
                    revision["id"],
                    run["request_fingerprint"],
                    run.get("estimated_input_tokens"),
                    run.get("estimated_cost_microunits"),
                    now,
                    now,
                    actor,
                ),
            )
            for unit in source_units:
                connection.execute(
                    """INSERT INTO source_document_units
                       (id,source_document_version_id,ordinal,unit_kind,source_start,source_end,text_sha256,chapter_ordinal,
                        created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v1')
                       ON CONFLICT(source_document_version_id,ordinal) DO NOTHING""",
                    (
                        str(uuid.uuid4()),
                        plan["source_document_version_id"],
                        unit["ordinal"],
                        unit["unit_kind"],
                        unit["source_start"],
                        unit["source_end"],
                        unit["text_sha256"],
                        unit.get("chapter_ordinal"),
                        now,
                        now,
                        actor,
                    ),
                )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','ADAPTATION_PLAN_CREATED','adaptation_plan',?,?,?)""",
                (
                    actor,
                    plan["id"],
                    "创建长篇改编规划草稿",
                    _json(
                        {
                            "source_document_version_id": plan["source_document_version_id"],
                            "mode": plan["mode"],
                            "run_id": run["id"],
                            "request_fingerprint": run["request_fingerprint"],
                        }
                    ),
                ),
            )
        return {
            "plan_id": plan["id"],
            "artifact_status": "DRAFT",
            "revision_id": revision["id"],
            "revision_no": 1,
            "run_id": run["id"],
            "run_status": "PREPARED",
            "created_at": now,
            "idempotent": False,
        }

    def list_plans(self, *, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT p.id, p.mode, p.artifact_status, p.source_document_version_id, p.updated_at,
                          sd.title AS source_title, r.id AS revision_id, r.revision_no, r.diagnosis_json,
                          (SELECT run_status FROM adaptation_plan_runs run WHERE run.plan_id=p.id ORDER BY run.created_at DESC LIMIT 1) AS latest_run_status,
                          (SELECT COUNT(*) FROM adaptation_episode_items epi WHERE epi.plan_revision_id=r.id) AS episode_count
                   FROM adaptation_plans p
                   JOIN source_document_versions sdv ON sdv.id=p.source_document_version_id
                   JOIN source_documents sd ON sd.id=sdv.source_document_id
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   WHERE p.project_id=? AND p.archived_at IS NULL
                   ORDER BY p.updated_at DESC""",
                (project_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "mode": str(row["mode"]),
                "artifact_status": str(row["artifact_status"]),
                "source_document_version_id": str(row["source_document_version_id"]),
                "source_title": str(row["source_title"]),
                "revision_id": str(row["revision_id"]),
                "revision_no": int(row["revision_no"]),
                "diagnosis": _loads(row["diagnosis_json"], {}),
                "latest_run_status": str(row["latest_run_status"] or "PREPARED"),
                "episode_count": int(row["episode_count"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def planning_context(self, *, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT p.id AS plan_id, p.project_id, p.source_document_version_id, p.artifact_status,
                          r.id AS revision_id, r.source_scope_json, r.content_sha256,
                          run.id AS run_id, run.run_status
                   FROM adaptation_plans p
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   LEFT JOIN adaptation_plan_runs run ON run.id=(
                      SELECT id FROM adaptation_plan_runs latest WHERE latest.plan_id=p.id ORDER BY latest.created_at DESC LIMIT 1
                   )
                   WHERE p.id=?""",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
        if str(row["artifact_status"]) not in {"DRAFT", "IN_REVIEW"}:
            raise DomainRuleError("ADAPTATION_PLAN_NOT_EDITABLE", "当前改编规划不能再编排分析节点")
        if row["run_id"] is None:
            raise DomainRuleError("ADAPTATION_RUN_NOT_FOUND", "改编规划缺少可恢复的分析运行")
        return {
            "plan_id": str(row["plan_id"]),
            "project_id": str(row["project_id"]),
            "source_document_version_id": str(row["source_document_version_id"]),
            "revision_id": str(row["revision_id"]),
            "source_scope": _loads(row["source_scope_json"], {}),
            "content_sha256": str(row["content_sha256"]),
            "run_id": str(row["run_id"]),
            "run_status": str(row["run_status"]),
        }

    def save_analysis_manifest(
        self,
        *,
        plan_id: str,
        revision_id: str,
        run_id: str,
        nodes: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            run = connection.execute(
                """SELECT id, plan_id, plan_revision_id, run_status
                   FROM adaptation_plan_runs WHERE id=? AND plan_id=?""",
                (run_id, plan_id),
            ).fetchone()
            if run is None or str(run["plan_revision_id"]) != revision_id:
                raise DomainRuleError("ADAPTATION_RUN_NOT_FOUND", "改编规划运行与当前修订不一致")
            if str(run["run_status"]) not in {"PREPARED", "QUEUED"}:
                raise DomainRuleError("ADAPTATION_RUN_NOT_PREPARABLE", "当前分析运行不能重新编排节点")
            existing = connection.execute(
                "SELECT COUNT(*) FROM adaptation_plan_run_nodes WHERE run_id=?",
                (run_id,),
            ).fetchone()
            existing_count = int(existing[0]) if existing else 0
            if existing_count:
                return {
                    "run_id": run_id,
                    "run_status": str(run["run_status"]),
                    "total_nodes": existing_count,
                    "idempotent": True,
                }
            for node in nodes:
                connection.execute(
                    """INSERT INTO adaptation_plan_run_nodes
                       (id,run_id,node_key,stage,core_source_start,core_source_end,context_source_start,context_source_end,
                        input_fingerprint,output_json,quality_flags_json,created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?,1,'v1')""",
                    (
                        str(uuid.uuid4()),
                        run_id,
                        node["node_key"],
                        node["stage"],
                        node.get("core_source_start"),
                        node.get("core_source_end"),
                        node.get("context_source_start"),
                        node.get("context_source_end"),
                        node["input_fingerprint"],
                        _json(node["output"]),
                        "[]",
                        now,
                        now,
                        actor,
                    ),
                )
            connection.execute(
                """UPDATE adaptation_plan_runs
                   SET total_nodes=?, updated_at=?, revision=revision+1
                   WHERE id=?""",
                (len(nodes), now, run_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','ADAPTATION_ANALYSIS_MANIFESTED','adaptation_plan_run',?,?,?)""",
                (
                    actor,
                    run_id,
                    "生成分层故事分析节点清单",
                    _json({"plan_id": plan_id, "node_count": len(nodes), "llm_invocations": 0, "jobs_created": 0}),
                ),
            )
        return {"run_id": run_id, "run_status": "PREPARED", "total_nodes": len(nodes), "idempotent": False}

    def analysis_readiness(self, *, plan_id: str, profile_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            plan = connection.execute(
                """SELECT p.id, p.project_id, r.id AS revision_id, run.id AS run_id,
                          (SELECT COUNT(*) FROM adaptation_plan_run_nodes node WHERE node.run_id=run.id) AS node_count,
                          (SELECT COUNT(*) FROM adaptation_plan_run_nodes node WHERE node.run_id=run.id AND node.stage='CHUNK_MAP') AS map_node_count,
                          (SELECT COALESCE(SUM(node.context_source_end-node.context_source_start),0)
                           FROM adaptation_plan_run_nodes node WHERE node.run_id=run.id AND node.stage='CHUNK_MAP') AS input_character_count
                   FROM adaptation_plans p
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   LEFT JOIN adaptation_plan_runs run ON run.id=(
                      SELECT id FROM adaptation_plan_runs latest WHERE latest.plan_id=p.id ORDER BY latest.created_at DESC LIMIT 1
                   )
                   WHERE p.id=?""",
                (plan_id,),
            ).fetchone()
            profile = connection.execute(
                """SELECT v.id, v.status, v.capability, v.capability_json, v.model_bundle_json,
                          p.code, p.title, v.version_no
                   FROM execution_profile_versions v
                   JOIN execution_profiles p ON p.id=v.execution_profile_id
                   WHERE v.id=?""",
                (profile_version_id,),
            ).fetchone()
        if plan is None:
            raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
        blockers: list[dict[str, str]] = []
        if int(plan["node_count"] or 0) == 0:
            blockers.append({"code": "ANALYSIS_MANIFEST_REQUIRED", "message": "请先生成分层分析节点清单"})
        if profile is None:
            blockers.append({"code": "PROFILE_NOT_FOUND", "message": "所选文本规划 Profile 不存在"})
            profile_fact: dict[str, Any] | None = None
            provider = ""
            model = ""
            base_url = ""
        else:
            capability = _loads(profile["capability_json"], {})
            bundle = _loads(profile["model_bundle_json"], {})
            provider_connection_id = str(bundle.get("provider_connection_id") or capability.get("provider_connection_id") or "").strip()
            if provider_connection_id:
                with self.database.connect() as connection:
                    selected_connection = connection.execute(
                        "SELECT protocol,base_url,model,status FROM provider_connections WHERE id=?", (provider_connection_id,)
                    ).fetchone()
                if selected_connection is None or str(selected_connection["status"]) != "ACTIVE":
                    provider = ""
                    model = ""
                    base_url = ""
                    blockers.append({"code": "PROVIDER_CONNECTION_UNAVAILABLE", "message": "所选 Profile 关联的 Provider Connection 不可用"})
                else:
                    provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
                    model = str(bundle.get("model") or selected_connection["model"] or "")
                    base_url = str(selected_connection["base_url"] or "")
            else:
                provider = str(bundle.get("provider") or capability.get("provider") or "OLLAMA_LOOPBACK").upper()
                model = str(bundle.get("model") or capability.get("model") or "")
                base_url = str(capability.get("base_url") or "")
            profile_fact = {
                "id": str(profile["id"]),
                "code": str(profile["code"]),
                "title": str(profile["title"]),
                "version_no": int(profile["version_no"]),
                "status": str(profile["status"]),
                "capability": str(profile["capability"]),
                "provider": provider,
                "model": model,
            }
            if str(profile["status"]) != "PUBLISHED":
                blockers.append({"code": "PROFILE_NOT_PUBLISHED", "message": "只有已发布的文本规划 Profile 可以提交分析"})
            if str(profile["capability"]).upper() != "LLM_STORY_PARSE":
                blockers.append({"code": "PROFILE_CAPABILITY_MISMATCH", "message": "所选 Profile 不具备 LLM_STORY_PARSE 能力"})
            if not model:
                blockers.append({"code": "PROFILE_MODEL_MISSING", "message": "所选 Profile 没有冻结模型标识"})
        remote = bool(base_url and endpoint_is_remote(base_url))
        input_characters = int(plan["input_character_count"] or 0)
        return {
            "plan_id": str(plan["id"]),
            "revision_id": str(plan["revision_id"]),
            "run_id": str(plan["run_id"]) if plan["run_id"] else None,
            "profile": profile_fact,
            "execution": {
                "state": "READY" if not blockers else "BLOCKED",
                "planned_node_count": int(plan["node_count"] or 0),
                "map_node_count": int(plan["map_node_count"] or 0),
                "estimated_input_characters": input_characters,
                "estimated_input_tokens": max(1, (input_characters + 1) // 2) if input_characters else 0,
                "provider_remote": remote,
                "requires_remote_outbound_confirmation": remote,
                "creates_media": False,
                "materializes_project_structure": False,
            },
            "blockers": blockers,
            "read_only": True,
        }

    def approve_plan(self, *, plan_id: str, expected_content_sha256: str, actor: str) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT p.id AS plan_id,p.artifact_status,r.id AS revision_id,r.content_sha256,
                          run.run_status,
                          (SELECT COUNT(*) FROM adaptation_episode_items item WHERE item.plan_revision_id=r.id) AS episode_count,
                          (SELECT COUNT(*) FROM adaptation_episode_items item
                           WHERE item.plan_revision_id=r.id AND NOT EXISTS (
                              SELECT 1 FROM adaptation_episode_source_spans span WHERE span.plan_episode_id=item.id
                           )) AS unsupported_episode_count
                   FROM adaptation_plans p
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   LEFT JOIN adaptation_plan_runs run ON run.id=(
                      SELECT id FROM adaptation_plan_runs latest WHERE latest.plan_id=p.id ORDER BY latest.created_at DESC LIMIT 1
                   )
                   WHERE p.id=?""",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
            if str(row["content_sha256"]) != expected_content_sha256:
                raise DomainRuleError("ADAPTATION_REVIEW_STALE", "规划修订已变化，请刷新后重新审核")
            if str(row["artifact_status"]) == "APPROVED":
                return {
                    "plan_id": str(row["plan_id"]),
                    "artifact_status": "APPROVED",
                    "revision_id": str(row["revision_id"]),
                    "approved_at": now,
                    "idempotent": True,
                }
            if str(row["artifact_status"]) != "IN_REVIEW" or str(row["run_status"] or "") != "READY_FOR_REVIEW":
                raise DomainRuleError("ADAPTATION_REVIEW_NOT_READY", "只有完成校验的待审核规划可以批准")
            if int(row["episode_count"] or 0) < 1:
                raise DomainRuleError("ADAPTATION_REVIEW_NO_EPISODES", "规划尚未生成待审核分集")
            if int(row["unsupported_episode_count"] or 0) > 0:
                raise DomainRuleError("ADAPTATION_REVIEW_EVIDENCE_REQUIRED", "每个候选分集都必须保留来源证据")
            connection.execute(
                "UPDATE adaptation_plans SET artifact_status='APPROVED',updated_at=?,revision=revision+1 WHERE id=?",
                (now, plan_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','ADAPTATION_PLAN_APPROVED','adaptation_plan',?,?,?)""",
                (actor, plan_id, "批准长篇改编规划草稿", _json({"revision_id": row["revision_id"], "episode_count": row["episode_count"]})),
            )
        return {
            "plan_id": plan_id,
            "artifact_status": "APPROVED",
            "revision_id": str(row["revision_id"]),
            "approved_at": now,
            "idempotent": False,
        }

    def materialization_preflight(self, *, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            context = self._materialization_context(connection, plan_id=plan_id)
        return {
            "plan_id": context["plan_id"],
            "revision_id": context["revision_id"],
            "artifact_status": context["artifact_status"],
            "strategy": "APPEND_NEW",
            "ready": not context["blockers"],
            "blockers": context["blockers"],
            "existing_structure": context["existing_structure"],
            "would_create": context["would_create"],
            "manifest_sha256": context["manifest_sha256"],
            "already_materialized": context["already_materialized"],
            "mutated": False,
        }

    def materialize_plan(
        self, *, plan_id: str, expected_content_sha256: str, idempotency_key: str, actor: str
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            context = self._materialization_context(connection, plan_id=plan_id)
            if context["blockers"]:
                raise DomainRuleError("ADAPTATION_MATERIALIZATION_BLOCKED", "当前规划不能物化为真实项目结构", {"blockers": context["blockers"]})
            if context["content_sha256"] != expected_content_sha256:
                raise DomainRuleError("ADAPTATION_REVIEW_STALE", "规划修订已变化，请刷新后重新确认发布")
            existing = connection.execute(
                """SELECT id,manifest_sha256 FROM adaptation_plan_materializations WHERE plan_revision_id=?""",
                (context["revision_id"],),
            ).fetchone()
            if existing is not None:
                return self._materialization_response(connection, str(existing["id"]), idempotent=True)
            idempotent = connection.execute(
                "SELECT id,plan_id,plan_revision_id FROM adaptation_plan_materializations WHERE project_id=? AND idempotency_key=?",
                (context["project_id"], idempotency_key),
            ).fetchone()
            if idempotent is not None:
                if str(idempotent["plan_id"]) != context["plan_id"] or str(idempotent["plan_revision_id"]) != context["revision_id"]:
                    raise DomainRuleError("IDEMPOTENCY_KEY_REUSED", "该 Idempotency-Key 已用于另一份改编规划发布")
                return self._materialization_response(connection, str(idempotent["id"]), idempotent=True)

            materialization_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO adaptation_plan_materializations
                   (id,plan_id,plan_revision_id,project_id,strategy,manifest_sha256,idempotency_key,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?, 'APPEND_NEW',?,?,?,?,?,1,'v1')""",
                (
                    materialization_id,
                    context["plan_id"],
                    context["revision_id"],
                    context["project_id"],
                    context["manifest_sha256"],
                    idempotency_key,
                    now,
                    now,
                    actor,
                ),
            )
            next_season_number = int(context["existing_structure"]["max_season_number"])
            next_season_order = int(context["existing_structure"]["max_season_display_order"])
            next_global_episode_number = int(context["existing_structure"]["max_global_episode_number"])
            created_seasons: dict[str, str] = {}
            season_episode_numbers: dict[str, int] = {}
            for candidate in context["candidates"]:
                group_key = str(candidate["season_group_id"] or "unassigned")
                season_id = created_seasons.get(group_key)
                if season_id is None:
                    next_season_number += 1
                    next_season_order += 1
                    season_id = str(uuid.uuid4())
                    created_seasons[group_key] = season_id
                    season_episode_numbers[season_id] = 0
                    connection.execute(
                        """INSERT INTO seasons (id,project_id,number,display_order,code,title,created_at,updated_at,created_by)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            season_id,
                            context["project_id"],
                            next_season_number,
                            next_season_order,
                            f"SEASON_{next_season_number:03d}",
                            str(candidate["season_title"] or f"改编规划第 {next_season_number} 季"),
                            now,
                            now,
                            actor,
                        ),
                    )
                season_episode_numbers[season_id] += 1
                next_global_episode_number += 1
                episode_id = str(uuid.uuid4())
                source_ranges = connection.execute(
                    """SELECT source_document_version_id,unicode_start,unicode_end,evidence_sha256,span_role
                       FROM adaptation_episode_source_spans WHERE plan_episode_id=? ORDER BY ordinal""",
                    (candidate["plan_episode_id"],),
                ).fetchall()
                connection.execute(
                    """INSERT INTO episodes (id,season_id,number,display_order,code,title,narrative_status,production_status,
                       target_duration_ms,source_range_json,created_at,updated_at,created_by)
                       VALUES (?,?,?,?,?,?,'OUTLINE','NOT_STARTED',?,?,?, ?,?)""",
                    (
                        episode_id,
                        season_id,
                        season_episode_numbers[season_id],
                        season_episode_numbers[season_id],
                        f"EPISODE_{next_global_episode_number:03d}",
                        str(candidate["title"]),
                        int(candidate["target_duration_ms"]),
                        _json([dict(item) for item in source_ranges]),
                        now,
                        now,
                        actor,
                    ),
                )
                connection.execute(
                    """INSERT INTO adaptation_materialized_episode_links
                       (id,materialization_id,plan_episode_id,season_id,episode_id,created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), materialization_id, candidate["plan_episode_id"], season_id, episode_id, now, now, actor),
                )
            connection.execute(
                "UPDATE adaptation_plans SET artifact_status='MATERIALIZED',updated_at=?,revision=revision+1 WHERE id=?",
                (now, context["plan_id"]),
            )
            connection.execute(
                "UPDATE projects SET updated_at=?,revision=revision+1 WHERE id=?",
                (now, context["project_id"]),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','ADAPTATION_PLAN_MATERIALIZED','adaptation_plan',?,?,?)""",
                (actor, context["plan_id"], "将已批准改编规划追加发布为真实季集", _json({"materialization_id": materialization_id, "strategy": "APPEND_NEW", "episode_count": len(context["candidates"])})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                   VALUES ('project.changed',?,'adaptation_plan',?,?)""",
                (context["project_id"], context["plan_id"], _json({"action": "ADAPTATION_PLAN_MATERIALIZED", "materialization_id": materialization_id})),
            )
            return self._materialization_response(connection, materialization_id, idempotent=False)

    def _materialization_context(self, connection: Any, *, plan_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT p.id AS plan_id,p.project_id,p.artifact_status,r.id AS revision_id,r.content_sha256,
                      (SELECT COUNT(*) FROM seasons season WHERE season.project_id=p.project_id) AS season_count,
                      (SELECT COALESCE(MAX(season.number),0) FROM seasons season WHERE season.project_id=p.project_id) AS max_season_number,
                      (SELECT COALESCE(MAX(season.display_order),0) FROM seasons season WHERE season.project_id=p.project_id) AS max_season_display_order,
                      (SELECT COUNT(*) FROM episodes episode JOIN seasons season ON season.id=episode.season_id WHERE season.project_id=p.project_id) AS episode_count
               FROM adaptation_plans p JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id WHERE p.id=?""",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
        candidates = connection.execute(
            """SELECT item.id AS plan_episode_id,item.display_ordinal,item.title,item.target_duration_ms,item.season_group_id,
                      season.title AS season_title
               FROM adaptation_episode_items item
               LEFT JOIN adaptation_season_groups season ON season.id=item.season_group_id
               WHERE item.plan_revision_id=? ORDER BY item.display_ordinal""",
            (row["revision_id"],),
        ).fetchall()
        blockers: list[dict[str, str]] = []
        if str(row["artifact_status"]) not in {"APPROVED", "MATERIALIZED"}:
            blockers.append({"code": "ADAPTATION_PLAN_NOT_APPROVED", "message": "只有已批准的规划才可以发布真实项目结构"})
        if not candidates:
            blockers.append({"code": "ADAPTATION_REVIEW_NO_EPISODES", "message": "规划没有可发布的候选分集"})
        unsupported = connection.execute(
            """SELECT COUNT(*) FROM adaptation_episode_items item WHERE item.plan_revision_id=? AND NOT EXISTS
               (SELECT 1 FROM adaptation_episode_source_spans span WHERE span.plan_episode_id=item.id)""",
            (row["revision_id"],),
        ).fetchone()
        if int(unsupported[0] if unsupported else 0):
            blockers.append({"code": "ADAPTATION_REVIEW_EVIDENCE_REQUIRED", "message": "候选分集缺少来源证据"})
        existing = connection.execute(
            "SELECT id FROM adaptation_plan_materializations WHERE plan_revision_id=?", (row["revision_id"],)
        ).fetchone()
        episode_codes = connection.execute(
            """SELECT episode.code FROM episodes episode JOIN seasons season ON season.id=episode.season_id
               WHERE season.project_id=?""",
            (row["project_id"],),
        ).fetchall()
        numbered = [
            int(str(item["code"])[8:])
            for item in episode_codes
            if str(item["code"]).startswith("EPISODE_") and str(item["code"])[8:].isdigit()
        ]
        serializable_candidates = [dict(item) for item in candidates]
        manifest_sha256 = _hash({"revision": row["content_sha256"], "strategy": "APPEND_NEW", "candidates": serializable_candidates})
        group_keys = {str(item["season_group_id"] or "unassigned") for item in candidates}
        return {
            "plan_id": str(row["plan_id"]),
            "project_id": str(row["project_id"]),
            "revision_id": str(row["revision_id"]),
            "content_sha256": str(row["content_sha256"]),
            "artifact_status": str(row["artifact_status"]),
            "candidates": serializable_candidates,
            "blockers": blockers,
            "already_materialized": existing is not None,
            "manifest_sha256": manifest_sha256,
            "existing_structure": {
                "season_count": int(row["season_count"] or 0),
                "max_season_number": int(row["max_season_number"] or 0),
                "max_season_display_order": int(row["max_season_display_order"] or 0),
                "episode_count": int(row["episode_count"] or 0),
                "max_global_episode_number": max(numbered, default=0),
            },
            "would_create": {"season_count": len(group_keys), "episode_count": len(candidates), "strategy": "APPEND_NEW"},
        }

    def _materialization_response(self, connection: Any, materialization_id: str, *, idempotent: bool) -> dict[str, Any]:
        row = connection.execute(
            "SELECT plan_id,plan_revision_id,project_id,strategy,created_at FROM adaptation_plan_materializations WHERE id=?",
            (materialization_id,),
        ).fetchone()
        links = connection.execute(
            """SELECT link.plan_episode_id,season.id AS season_id,season.code AS season_code,episode.id AS episode_id,episode.code AS episode_code
               FROM adaptation_materialized_episode_links link
               JOIN seasons season ON season.id=link.season_id JOIN episodes episode ON episode.id=link.episode_id
               WHERE link.materialization_id=? ORDER BY episode.code""",
            (materialization_id,),
        ).fetchall()
        return {
            "materialization_id": materialization_id,
            "plan_id": str(row["plan_id"]),
            "revision_id": str(row["plan_revision_id"]),
            "project_id": str(row["project_id"]),
            "strategy": str(row["strategy"]),
            "created_at": str(row["created_at"]),
            "items": [dict(item) for item in links],
            "idempotent": idempotent,
        }

    def workspace(self, *, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT p.id AS plan_id, p.project_id, p.source_document_version_id, p.mode, p.artifact_status,
                          p.updated_at, sd.title AS source_title, sdv.source_name,
                          r.id AS revision_id, r.revision_no, r.source_scope_json, r.constraints_json,
                          r.diagnosis_json, r.validation_summary_json, r.content_sha256,
                          run.id AS run_id, run.run_status, run.total_nodes, run.completed_nodes, run.failed_nodes,
                          run.estimated_input_tokens, run.estimated_cost_microunits
                   FROM adaptation_plans p
                   JOIN source_document_versions sdv ON sdv.id=p.source_document_version_id
                   JOIN source_documents sd ON sd.id=sdv.source_document_id
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   LEFT JOIN adaptation_plan_runs run ON run.id=(
                      SELECT id FROM adaptation_plan_runs latest WHERE latest.plan_id=p.id ORDER BY latest.created_at DESC LIMIT 1
                   )
                   WHERE p.id=?""",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
            episodes = connection.execute(
                """SELECT id,logical_episode_id,display_ordinal,title,logline,target_duration_ms,estimated_duration_ms,
                          review_state,lock_state,
                          (SELECT COUNT(*) FROM adaptation_episode_source_spans span WHERE span.plan_episode_id=epi.id) AS evidence_count
                   FROM adaptation_episode_items epi WHERE plan_revision_id=? ORDER BY display_ordinal LIMIT 100""",
                (row["revision_id"],),
            ).fetchall()
            nodes = connection.execute(
                """SELECT node_key,stage,core_source_start,core_source_end,context_source_start,context_source_end,
                          job_id,output_json,quality_flags_json
                   FROM adaptation_plan_run_nodes WHERE run_id=? ORDER BY
                     CASE stage WHEN 'CHUNK_MAP' THEN 1 WHEN 'ARC_REDUCE' THEN 2 WHEN 'SEASON_PLAN' THEN 3
                                WHEN 'EPISODE_BOUNDARY' THEN 4 WHEN 'VALIDATE' THEN 5 ELSE 99 END,
                     node_key
                   LIMIT 200""",
                (row["run_id"],),
            ).fetchall() if row["run_id"] else []
        return {
            "plan": {
                "id": str(row["plan_id"]),
                "project_id": str(row["project_id"]),
                "source_document_version_id": str(row["source_document_version_id"]),
                "source_title": str(row["source_title"]),
                "source_name": str(row["source_name"]),
                "mode": str(row["mode"]),
                "artifact_status": str(row["artifact_status"]),
                "updated_at": str(row["updated_at"]),
            },
            "revision": {
                "id": str(row["revision_id"]),
                "revision_no": int(row["revision_no"]),
                "source_scope": _loads(row["source_scope_json"], {}),
                "constraints": _loads(row["constraints_json"], {}),
                "diagnosis": _loads(row["diagnosis_json"], {}),
                "validation_summary": _loads(row["validation_summary_json"], {}),
                "content_sha256": str(row["content_sha256"]),
            },
            "run": {
                "id": str(row["run_id"]) if row["run_id"] else None,
                "status": str(row["run_status"] or "PREPARED"),
                "total_nodes": int(row["total_nodes"] or 0),
                "completed_nodes": int(row["completed_nodes"] or 0),
                "failed_nodes": int(row["failed_nodes"] or 0),
                "estimated_input_tokens": row["estimated_input_tokens"],
                "estimated_cost_microunits": row["estimated_cost_microunits"],
            },
            "episodes": [dict(item) for item in episodes],
            "analysis_nodes": [
                {
                    "node_key": str(item["node_key"]),
                    "stage": str(item["stage"]),
                    "core_source_start": item["core_source_start"],
                    "core_source_end": item["core_source_end"],
                    "context_source_start": item["context_source_start"],
                    "context_source_end": item["context_source_end"],
                    "job_id": item["job_id"],
                    "state": str(_loads(item["output_json"], {}).get("planning_state") or "UNKNOWN"),
                    "quality_flags": _loads(item["quality_flags_json"], []),
                }
                for item in nodes
            ],
            "next_action": (
                "MATERIALIZED"
                if str(row["artifact_status"]) == "MATERIALIZED"
                else "PUBLISH_PLAN"
                if str(row["artifact_status"]) == "APPROVED"
                else "REVIEW_PLAN"
                if episodes or str(row["run_status"] or "") == "READY_FOR_REVIEW"
                else "MONITOR_ANALYSIS"
                if str(row["run_status"] or "") in {"QUEUED", "RUNNING"}
                else "CONFIGURE_ANALYSIS_PROFILE"
                if nodes
                else "PREPARE_ANALYSIS"
            ),
        }

    def enqueue_analysis_run(
        self,
        *,
        plan_id: str,
        profile_version_id: str,
        allow_remote_outbound: bool,
        idempotency_key: str,
        actor: str,
        create_job: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        """Freeze the queue decision in one transaction and translate the manifest into a Job DAG.

        Only source offsets and immutable hashes are stored in Jobs: the worker
        re-reads and verifies the source file before each LLM call, so source
        prose is never duplicated into the queue. ``create_job`` is injected so
        Job creation stays owned by the application layer.
        """
        with self.database.transaction() as connection:
            run = connection.execute(
                """SELECT p.id AS plan_id,p.project_id,p.artifact_status,p.source_document_version_id,
                          r.id AS revision_id,r.source_scope_json,r.content_sha256,
                          ar.id AS run_id,ar.run_status
                   FROM adaptation_plans p
                   JOIN adaptation_plan_revisions r ON r.id=p.current_revision_id
                   JOIN adaptation_plan_runs ar ON ar.id=(
                       SELECT id FROM adaptation_plan_runs latest WHERE latest.plan_id=p.id ORDER BY latest.created_at DESC LIMIT 1
                   )
                   WHERE p.id=?""",
                (plan_id,),
            ).fetchone()
            profile = connection.execute(
                """SELECT v.id,v.status,v.capability,v.capability_json,v.model_bundle_json,
                          p.code,p.title,v.version_no
                   FROM execution_profile_versions v
                   JOIN execution_profiles p ON p.id=v.execution_profile_id WHERE v.id=?""",
                (profile_version_id,),
            ).fetchone()
            if run is None:
                raise DomainRuleError("ADAPTATION_PLAN_NOT_FOUND", "改编规划不存在")
            if str(run["artifact_status"]) not in {"DRAFT", "IN_REVIEW"}:
                raise DomainRuleError("ADAPTATION_PLAN_NOT_EDITABLE", "当前改编规划不能提交新的分析运行")
            if profile is None or str(profile["status"]) != "PUBLISHED" or str(profile["capability"]).upper() != "LLM_STORY_PARSE":
                raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "请选择已发布的文本规划 LLM Profile")
            capability = _loads(profile["capability_json"], {})
            bundle = _loads(profile["model_bundle_json"], {})
            provider_connection_id = str(bundle.get("provider_connection_id") or capability.get("provider_connection_id") or "").strip() or None
            if provider_connection_id:
                selected_connection = connection.execute(
                    "SELECT protocol,base_url,model,status FROM provider_connections WHERE id=?", (provider_connection_id,)
                ).fetchone()
                if selected_connection is None or str(selected_connection["status"]) != "ACTIVE":
                    raise DomainRuleError("PROVIDER_CONNECTION_UNAVAILABLE", "所选 Profile 关联的 Provider Connection 不可用")
                provider = "OLLAMA_LOOPBACK" if str(selected_connection["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
                model = str(bundle.get("model") or selected_connection["model"] or "").strip()
                base_url = str(selected_connection["base_url"]).strip()
            else:
                provider = str(bundle.get("provider") or capability.get("provider") or "OLLAMA_LOOPBACK").upper()
                model = str(bundle.get("model") or capability.get("model") or "").strip()
                base_url = str(capability.get("base_url") or self.settings.llm_base_url).strip()
            if not model:
                raise DomainRuleError("LOCAL_LLM_PROFILE_CONFIG_MISMATCH", "所选 Profile 未冻结显式模型")
            remote = endpoint_is_remote(base_url)
            if remote and not allow_remote_outbound:
                raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "原稿将发送到远程 LLM；请明确确认本次出境处理")
            if str(run["run_status"]) not in {"PREPARED", "QUEUED"}:
                raise DomainRuleError("ADAPTATION_RUN_NOT_QUEUEABLE", "当前分析运行不能再次提交")
            nodes = connection.execute(
                """SELECT id,node_key,stage,core_source_start,core_source_end,context_source_start,context_source_end,
                          input_fingerprint,job_id
                   FROM adaptation_plan_run_nodes WHERE run_id=?
                   ORDER BY CASE stage WHEN 'CHUNK_MAP' THEN 1 WHEN 'ARC_REDUCE' THEN 2 WHEN 'SEASON_PLAN' THEN 3
                                      WHEN 'EPISODE_BOUNDARY' THEN 4 WHEN 'VALIDATE' THEN 5 ELSE 99 END,node_key""",
                (run["run_id"],),
            ).fetchall()
            if not nodes:
                raise DomainRuleError("ANALYSIS_MANIFEST_REQUIRED", "请先生成分层分析节点清单")

            source = connection.execute(
                "SELECT text_sha256 FROM source_document_versions WHERE id=?",
                (run["source_document_version_id"],),
            ).fetchone()
            if source is None or not str(source["text_sha256"] or ""):
                raise DomainRuleError("ADAPTATION_SOURCE_NOT_FOUND", "冻结原稿版本不可用")
            scope = _loads(run["source_scope_json"], {})
            jobs_by_stage: dict[str, list[str]] = {}
            created_job_ids: list[str] = []
            for node in nodes:
                stage = str(node["stage"])
                dependencies = _analysis_stage_dependencies(stage=stage, jobs_by_stage=jobs_by_stage)
                existing_job_id = str(node["job_id"] or "")
                if existing_job_id:
                    jobs_by_stage.setdefault(stage, []).append(existing_job_id)
                    continue
                snapshot = {
                    "schema_version": "adaptation-analysis-job/v1",
                    "plan_id": str(run["plan_id"]),
                    "plan_revision_id": str(run["revision_id"]),
                    "run_id": str(run["run_id"]),
                    "run_node_id": str(node["id"]),
                    "node_key": str(node["node_key"]),
                    "stage": stage,
                    "input_fingerprint": str(node["input_fingerprint"]),
                    "source_document_version_id": str(run["source_document_version_id"]),
                    "source_text_sha256": str(source["text_sha256"]),
                    "source_scope": scope,
                    "core_source_start": node["core_source_start"],
                    "core_source_end": node["core_source_end"],
                    "context_source_start": node["context_source_start"],
                    "context_source_end": node["context_source_end"],
                    "profile_version_id": profile_version_id,
                    "profile": {"provider": provider, "model": model, "base_url": base_url, "provider_connection_id": provider_connection_id},
                    "provider_remote": remote,
                    "remote_outbound_confirmed": bool(remote and allow_remote_outbound),
                    "automatic_apply": False,
                    "creates_media": False,
                    "materializes_project_structure": False,
                }
                job = create_job(
                    connection,
                    str(run["project_id"]),
                    "ADAPTATION_ANALYSIS_LOCAL_LLM",
                    "ADAPTATION_PLAN_RUN",
                    str(run["run_id"]),
                    "CPU",
                    snapshot,
                    f"{idempotency_key}:{node['node_key']}",
                    execution_profile_version_id=profile_version_id,
                    priority=55,
                    max_attempts=1,
                    depends_on_job_ids=dependencies,
                    actor=actor,
                    subject_kind="ADAPTATION_PLAN_RUN",
                    scope_kind="PROJECT",
                    scope_project_id=str(run["project_id"]),
                    stage_code="ADAPTATION_ANALYSIS",
                )
                job_id = str(job["id"])
                connection.execute(
                    "UPDATE adaptation_plan_run_nodes SET job_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (job_id, _utc_now(), node["id"]),
                )
                jobs_by_stage.setdefault(stage, []).append(job_id)
                created_job_ids.append(job_id)

            if created_job_ids:
                now = _utc_now()
                connection.execute(
                    "UPDATE adaptation_plan_runs SET run_status='QUEUED',started_at=COALESCE(started_at,?),updated_at=?,revision=revision+1 WHERE id=?",
                    (now, now, run["run_id"]),
                )
                connection.execute(
                    """INSERT INTO audit_events
                       (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                       VALUES (?,'producer','ADAPTATION_ANALYSIS_QUEUED','adaptation_plan_run',?,?,?)""",
                    (
                        actor,
                        run["run_id"],
                        "提交长篇改编分层分析 Job DAG",
                        _json({"plan_id": plan_id, "profile_version_id": profile_version_id, "job_count": len(nodes), "remote": remote}),
                    ),
                )
        return {
            "run_id": str(run["run_id"]),
            "run_status": "QUEUED",
            "job_count": len(nodes),
            "created_job_ids": created_job_ids,
            "provider_remote": remote,
            "idempotent": not created_job_ids,
        }

    def analysis_execution_context(self, *, job_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Load the raw rows one analysis node execution needs; the caller validates staleness."""
        with self.database.connect() as connection:
            node = connection.execute(
                """SELECT n.output_json,n.input_fingerprint,n.job_id,r.run_status
                   FROM adaptation_plan_run_nodes n JOIN adaptation_plan_runs r ON r.id=n.run_id
                   WHERE n.id=? AND n.run_id=?""",
                (snapshot["run_node_id"], snapshot["run_id"]),
            ).fetchone()
            source = connection.execute(
                """SELECT v.text_sha256,v.extracted_text_rel,p.root_rel
                   FROM source_document_versions v
                   JOIN source_documents d ON d.id=v.source_document_id
                   JOIN projects p ON p.id=d.project_id WHERE v.id=?""",
                (snapshot["source_document_version_id"],),
            ).fetchone()
            profile = connection.execute(
                """SELECT status,capability,capability_json,model_bundle_json
                   FROM execution_profile_versions WHERE id=?""",
                (snapshot["profile_version_id"],),
            ).fetchone()
            upstream = connection.execute(
                """SELECT stage,node_key,output_json FROM adaptation_plan_run_nodes WHERE run_id=?
                   AND stage<>? ORDER BY CASE stage WHEN 'CHUNK_MAP' THEN 1 WHEN 'ARC_REDUCE' THEN 2
                   WHEN 'SEASON_PLAN' THEN 3 WHEN 'EPISODE_BOUNDARY' THEN 4 ELSE 5 END,node_key""",
                (snapshot["run_id"], snapshot["stage"]),
            ).fetchall()
        return {
            "job_id": job_id,
            "node": dict(node) if node is not None else None,
            "source": dict(source) if source is not None else None,
            "profile": dict(profile) if profile is not None else None,
            "upstream": [dict(item) for item in upstream],
        }

    def active_provider_connection(self, *, connection_id: str) -> dict[str, Any]:
        """Return the frozen connection binding an execution Profile runs on.

        The secret is intentionally not part of this read: the LLM client
        resolves credentials through its own execution boundary.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT protocol,base_url,model,status FROM provider_connections WHERE id=?", (connection_id,)
            ).fetchone()
        if row is None or str(row["status"]).upper() != "ACTIVE":
            raise DomainRuleError("PROVIDER_CONNECTION_UNAVAILABLE", "所选 Profile 关联的 Provider Connection 不可用")
        return dict(row)

    def record_analysis_invocation(
        self,
        *,
        job_id: str,
        run_node_id: str,
        profile_version_id: str,
        provider: str,
        model: str,
        request_sha256: str,
    ) -> str:
        invocation_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            attempt = connection.execute(
                "SELECT id FROM job_attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (job_id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO llm_invocations
                   (id,job_attempt_id,run_node_id,profile_version_id,provider,model,prompt_contract_version,request_sha256,status,
                    uncertain_side_effect,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,?,?, 'adaptation-analysis/v1',?,'PENDING',0,?,?,?,1,'v1')""",
                (invocation_id, attempt["id"] if attempt else None, run_node_id, profile_version_id, provider, model, request_sha256, _utc_now(), _utc_now(), "local-worker"),
            )
        return invocation_id

    def persist_analysis_node_success(self, *, snapshot: dict[str, Any], output: dict[str, Any], latency_ms: int, invocation_id: str) -> None:
        """Persist one node output, its stage artifacts, and run/plan progression atomically."""
        with self.database.transaction() as connection:
            row = connection.execute("SELECT output_json FROM adaptation_plan_run_nodes WHERE id=?", (snapshot["run_node_id"],)).fetchone()
            descriptor = _loads(row["output_json"], {}) if row else {}
            descriptor.update({"planning_state": "SUCCEEDED", "result": output})
            self._persist_stage_artifacts(connection, snapshot, output)
            connection.execute(
                "UPDATE adaptation_plan_run_nodes SET output_json=?,output_sha256=?,updated_at=?,revision=revision+1 WHERE id=?",
                (_json(descriptor), hashlib.sha256(_json(output).encode("utf-8")).hexdigest(), _utc_now(), snapshot["run_node_id"]),
            )
            completed = int(connection.execute(
                "SELECT COUNT(*) FROM adaptation_plan_run_nodes WHERE run_id=? AND json_extract(output_json,'$.planning_state')='SUCCEEDED'",
                (snapshot["run_id"],),
            ).fetchone()[0])
            total = int(connection.execute("SELECT total_nodes FROM adaptation_plan_runs WHERE id=?", (snapshot["run_id"],)).fetchone()[0])
            final = str(snapshot["stage"]) == "VALIDATE" and completed == total
            connection.execute(
                """UPDATE adaptation_plan_runs SET completed_nodes=?,run_status=?,completed_at=CASE WHEN ? THEN ? ELSE completed_at END,
                   updated_at=?,revision=revision+1 WHERE id=?""",
                (completed, "READY_FOR_REVIEW" if final else "RUNNING", final, _utc_now(), _utc_now(), snapshot["run_id"]),
            )
            if final:
                connection.execute(
                    "UPDATE adaptation_plans SET artifact_status='IN_REVIEW',updated_at=?,revision=revision+1 WHERE id=?",
                    (_utc_now(), snapshot["plan_id"]),
                )
            connection.execute(
                "UPDATE llm_invocations SET status='SUCCEEDED',latency_ms=?,updated_at=?,revision=revision+1 WHERE id=?",
                (latency_ms, _utc_now(), invocation_id),
            )

    def record_analysis_failure(self, *, invocation_id: str, latency_ms: int, error_type: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE llm_invocations SET status='FAILED',latency_ms=?,uncertain_side_effect=1,finish_reason=?,updated_at=?,revision=revision+1 WHERE id=?",
                (latency_ms, error_type, _utc_now(), invocation_id),
            )

    def _persist_stage_artifacts(self, connection: Any, snapshot: dict[str, Any], output: dict[str, Any]) -> None:
        stage = str(snapshot["stage"])
        revision_id = str(snapshot["plan_revision_id"])
        now = _utc_now()
        if stage == "ARC_REDUCE":
            for ordinal, item in enumerate(_items(output.get("arcs"), field="arcs"), start=1):
                logical_id = _stable_id(f"adaptation-arc:{revision_id}:{ordinal}")
                connection.execute(
                    """INSERT OR IGNORE INTO adaptation_story_arcs
                       (id,plan_revision_id,logical_arc_id,ordinal,title,summary,dramatic_promise,beginning_state_json,ending_state_json,
                        open_threads_json,created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'local-worker',1,'v1')""",
                    (
                        _stable_id(f"adaptation-arc-row:{revision_id}:{ordinal}"),
                        revision_id,
                        logical_id,
                        ordinal,
                        _required_text(item.get("title"), field="arcs.title"),
                        _required_text(item.get("summary"), field="arcs.summary"),
                        _required_text(item.get("dramatic_promise"), field="arcs.dramatic_promise"),
                        "{}",
                        "{}",
                        _json(item.get("open_threads") if isinstance(item.get("open_threads"), list) else []),
                        now,
                        now,
                    ),
                )
        elif stage == "SEASON_PLAN":
            for ordinal, item in enumerate(_items(output.get("seasons"), field="seasons"), start=1):
                logical_id = _stable_id(f"adaptation-season:{revision_id}:{ordinal}")
                connection.execute(
                    """INSERT OR IGNORE INTO adaptation_season_groups
                       (id,plan_revision_id,logical_season_id,ordinal,title,release_intent,created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,'local-worker',1,'v1')""",
                    (
                        _stable_id(f"adaptation-season-row:{revision_id}:{ordinal}"),
                        revision_id,
                        logical_id,
                        ordinal,
                        _required_text(item.get("title"), field="seasons.title"),
                        _required_text(item.get("release_intent"), field="seasons.release_intent"),
                        now,
                        now,
                    ),
                )
        elif stage == "EPISODE_BOUNDARY":
            self._persist_episode_items(connection, snapshot, output, now)

    def _persist_episode_items(self, connection: Any, snapshot: dict[str, Any], output: dict[str, Any], now: str) -> None:
        revision_id = str(snapshot["plan_revision_id"])
        episodes = _items(output.get("episodes"), field="episodes")
        if len(episodes) > 2_000:
            raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", "分集规划输出超过 2000 集上限")
        map_nodes = connection.execute(
            """SELECT id,node_key,core_source_start,core_source_end,input_fingerprint
               FROM adaptation_plan_run_nodes WHERE run_id=? AND stage='CHUNK_MAP'""",
            (snapshot["run_id"],),
        ).fetchall()
        by_key = {str(item["node_key"]): item for item in map_nodes}
        constraints = connection.execute(
            "SELECT constraints_json FROM adaptation_plan_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        target_duration_ms = int(_loads(constraints["constraints_json"], {}).get("target_duration_ms") or 120_000) if constraints else 120_000
        arcs = connection.execute("SELECT id,ordinal FROM adaptation_story_arcs WHERE plan_revision_id=?", (revision_id,)).fetchall()
        seasons = connection.execute("SELECT id,ordinal FROM adaptation_season_groups WHERE plan_revision_id=?", (revision_id,)).fetchall()
        arcs_by_ordinal = {int(item["ordinal"]): str(item["id"]) for item in arcs}
        seasons_by_ordinal = {int(item["ordinal"]): str(item["id"]) for item in seasons}
        for ordinal, item in enumerate(episodes, start=1):
            source_node_keys = item.get("source_node_keys")
            if not isinstance(source_node_keys, list) or not source_node_keys or not all(isinstance(key, str) for key in source_node_keys):
                raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", "每个分集必须引用至少一个 source_node_keys")
            source_nodes = [by_key.get(key) for key in source_node_keys]
            if any(node is None for node in source_nodes):
                raise DomainRuleError("ADAPTATION_LLM_OUTPUT_INVALID", "分集引用了不属于当前运行的原稿分块")
            duration = item.get("estimated_duration_ms")
            estimated_duration_ms = int(duration) if isinstance(duration, int) and 10_000 <= duration <= 3_600_000 else target_duration_ms
            arc_ordinal = item.get("arc_ordinal") if isinstance(item.get("arc_ordinal"), int) else None
            season_ordinal = item.get("season_ordinal") if isinstance(item.get("season_ordinal"), int) else None
            episode_id = _stable_id(f"adaptation-episode-row:{revision_id}:{ordinal}")
            connection.execute(
                """INSERT OR IGNORE INTO adaptation_episode_items
                   (id,plan_revision_id,logical_episode_id,display_ordinal,story_arc_id,season_group_id,title,logline,opening_carry,
                    core_conflict,payoff,ending_hook,target_duration_ms,estimated_duration_ms,canon_delta_json,evidence_json,confidence_json,
                    review_state,lock_state,created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT','UNLOCKED',?,?, 'local-worker',1,'v1')""",
                (
                    episode_id,
                    revision_id,
                    _stable_id(f"adaptation-episode:{revision_id}:{ordinal}"),
                    ordinal,
                    arcs_by_ordinal.get(arc_ordinal),
                    seasons_by_ordinal.get(season_ordinal),
                    _required_text(item.get("title"), field="episodes.title"),
                    _required_text(item.get("logline"), field="episodes.logline"),
                    str(item.get("opening_carry") or ""),
                    _required_text(item.get("core_conflict"), field="episodes.core_conflict"),
                    _required_text(item.get("payoff"), field="episodes.payoff"),
                    _required_text(item.get("ending_hook"), field="episodes.ending_hook"),
                    target_duration_ms,
                    estimated_duration_ms,
                    "{}",
                    _json([{"source_node_key": key} for key in source_node_keys]),
                    _json({"model": output.get("confidence")}),
                    now,
                    now,
                ),
            )
            for span_ordinal, node in enumerate(source_nodes, start=1):
                assert node is not None
                connection.execute(
                    """INSERT OR IGNORE INTO adaptation_episode_source_spans
                       (id,plan_episode_id,source_document_version_id,ordinal,span_role,unicode_start,unicode_end,evidence_sha256,
                        created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?, 'local-worker',1,'v1')""",
                    (
                        _stable_id(f"adaptation-episode-span:{episode_id}:{span_ordinal}"),
                        episode_id,
                        snapshot["source_document_version_id"],
                        span_ordinal,
                        "PRIMARY" if span_ordinal == 1 else "SUPPORTING",
                        int(node["core_source_start"]),
                        int(node["core_source_end"]),
                        str(node["input_fingerprint"]),
                        now,
                        now,
                    ),
                )
