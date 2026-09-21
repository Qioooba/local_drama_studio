from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.production_session_budgets import (
    DEFAULT_DISPATCH_SHOTS_PER_TICK,
    DEFAULT_MAX_ATTEMPTS_TOTAL,
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_MAX_NEW_JOBS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_QUEUED_GPU_JOBS,
    ProductionSessionBudgetService,
    normalized_budget_configuration,
)
from local_drama.domain.errors import DomainRuleError

_STAGES = [
    "PREPARATION",
    "ASSETS",
    "SHOT_PLAN",
    "KEYFRAMES",
    "VIDEO",
    "AUDIO_SUBTITLE",
    "TIMELINE_PREVIEW",
    "MACHINE_QC",
    "WAITING_REVIEW",
]
_CANDIDATES_PER_MODE = {"DRAFT": 1, "BALANCED": 2, "QUALITY": 4}
_TERMINAL_STATUSES = {"COMPLETED", "CANCELLED"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ProductionSessionService:
    """Durable user intent for one-click production.

    This foundation owns planning, creation, query and operator control only.
    Dispatchers added by later work packages advance item stages and attach
    durable jobs. Creating a session therefore never claims that generation
    has started and never writes a human approval.
    """

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    @staticmethod
    def _normalize_request(command: dict[str, Any]) -> dict[str, Any]:
        return {
            "scope_type": str(command["scope_type"]),
            "episode_ids": [str(value) for value in command.get("episode_ids") or []],
            "production_mode": str(command.get("production_mode") or "BALANCED"),
            "checkpoint_policy": str(command.get("checkpoint_policy") or "ON_EXCEPTION"),
            "tts_enabled": bool(command.get("tts_enabled", True)),
            "max_parallel_episodes": int(command.get("max_parallel_episodes") or 1),
            "min_free_disk_bytes": int(command.get("min_free_disk_bytes") or 1),
            "max_duration_seconds": int(command.get("max_duration_seconds") or DEFAULT_MAX_DURATION_SECONDS),
            "max_new_jobs": int(command.get("max_new_jobs") or DEFAULT_MAX_NEW_JOBS),
            "max_attempts_total": int(command.get("max_attempts_total") or DEFAULT_MAX_ATTEMPTS_TOTAL),
            "max_output_bytes": int(command.get("max_output_bytes") or DEFAULT_MAX_OUTPUT_BYTES),
            "max_queued_gpu_jobs": int(command.get("max_queued_gpu_jobs") or DEFAULT_MAX_QUEUED_GPU_JOBS),
            "dispatch_shots_per_tick": int(command.get("dispatch_shots_per_tick") or DEFAULT_DISPATCH_SHOTS_PER_TICK),
        }

    @staticmethod
    def _load_episodes(
        connection: sqlite3.Connection,
        project_id: str,
        *,
        scope_type: str,
        requested_episode_ids: list[str],
    ) -> tuple[sqlite3.Row, list[dict[str, Any]]]:
        project = connection.execute(
            "SELECT id,code,title,revision FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        if len(requested_episode_ids) != len(set(requested_episode_ids)):
            raise DomainRuleError("PRODUCTION_SESSION_EPISODE_DUPLICATE", "分集范围不能包含重复分集")
        if scope_type == "SINGLE_EPISODE" and len(requested_episode_ids) != 1:
            raise DomainRuleError("PRODUCTION_SESSION_SINGLE_EPISODE_REQUIRED", "单集生产必须且只能选择 1 集")

        rows = connection.execute(
            """SELECT e.id,e.code,e.title,e.display_order,e.revision,
                      se.display_order AS season_order,
                      COUNT(DISTINCT s.id) AS shot_count,
                      COUNT(DISTINCT CASE WHEN a.status='ACTIVE' THEN s.id END) AS asset_bound_shot_count
               FROM episodes e
               JOIN seasons se ON se.id=e.season_id
               LEFT JOIN shots s ON s.episode_id=e.id AND s.archived_at IS NULL
               LEFT JOIN shot_asset_bindings sab ON sab.shot_id=s.id
               LEFT JOIN story_assets a ON a.id=sab.asset_id AND a.project_id=se.project_id
               WHERE se.project_id=?
               GROUP BY e.id,e.code,e.title,e.display_order,e.revision,se.display_order
               ORDER BY se.display_order,e.display_order,e.id""",
            (project_id,),
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        selected_ids = requested_episode_ids or [str(row["id"]) for row in rows]
        missing = [episode_id for episode_id in selected_ids if episode_id not in by_id]
        if missing:
            raise DomainRuleError(
                "EPISODE_PROJECT_MISMATCH",
                "所选分集不属于当前项目",
                {"project_id": project_id, "episode_ids": missing},
            )
        selected = [by_id[episode_id] for episode_id in selected_ids]
        selected.sort(key=lambda row: (int(row["season_order"]), int(row["display_order"]), str(row["id"])))
        if not selected:
            raise DomainRuleError("PRODUCTION_SESSION_EPISODES_REQUIRED", "项目中没有可生产的分集")
        return project, [dict(row) for row in selected]

    @classmethod
    def _plan_with_connection(
        cls,
        connection: sqlite3.Connection,
        project_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = cls._normalize_request(command)
        project, episodes = cls._load_episodes(
            connection,
            project_id,
            scope_type=normalized["scope_type"],
            requested_episode_ids=normalized["episode_ids"],
        )
        episode_facts: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        total_shots = 0
        for ordinal, episode in enumerate(episodes, start=1):
            shot_count = int(episode["shot_count"] or 0)
            asset_bound_shot_count = int(episode["asset_bound_shot_count"] or 0)
            missing_asset_binding_count = max(0, shot_count - asset_bound_shot_count)
            total_shots += shot_count
            readiness = "READY" if shot_count and not missing_asset_binding_count else "NEEDS_PREPARATION"
            if not shot_count:
                warnings.append(
                    {
                        "code": "EPISODE_NEEDS_PREPARATION",
                        "message": f"{episode['code']} 尚无镜头，运行时将先执行分集准备",
                        "episode_id": str(episode["id"]),
                    }
                )
            elif missing_asset_binding_count:
                warnings.append(
                    {
                        "code": "EPISODE_ASSET_BINDINGS_MISSING",
                        "message": (
                            f"{episode['code']} 有 {missing_asset_binding_count} 个镜头尚无有效关键资产绑定；"
                            "系统会尝试使用拆解提案自动准备，没有提案时会停下等待补充"
                        ),
                        "episode_id": str(episode["id"]),
                        "missing_shot_count": missing_asset_binding_count,
                    }
                )
            episode_facts.append(
                {
                    "episode_id": str(episode["id"]),
                    "code": str(episode["code"]),
                    "title": str(episode["title"]) if episode["title"] is not None else None,
                    "ordinal": ordinal,
                    "revision": int(episode["revision"]),
                    "shot_count": shot_count,
                    "asset_bound_shot_count": asset_bound_shot_count,
                    "missing_asset_binding_count": missing_asset_binding_count,
                    "readiness": readiness,
                }
            )
        configuration = {
            "tts_enabled": normalized["tts_enabled"],
            "max_parallel_episodes": normalized["max_parallel_episodes"],
            "min_free_disk_bytes": normalized["min_free_disk_bytes"],
            "max_duration_seconds": normalized["max_duration_seconds"],
            "max_new_jobs": normalized["max_new_jobs"],
            "max_attempts_total": normalized["max_attempts_total"],
            "max_output_bytes": normalized["max_output_bytes"],
            "max_queued_gpu_jobs": normalized["max_queued_gpu_jobs"],
            "dispatch_shots_per_tick": normalized["dispatch_shots_per_tick"],
            "episode_ids": [item["episode_id"] for item in episode_facts],
            "candidate_count_per_shot": _CANDIDATES_PER_MODE[normalized["production_mode"]],
        }
        hash_input = {
            "schema_version": "localdrama.production-session-plan.v1",
            "project_id": project_id,
            "project_revision": int(project["revision"]),
            "scope_type": normalized["scope_type"],
            "production_mode": normalized["production_mode"],
            "checkpoint_policy": normalized["checkpoint_policy"],
            "configuration": configuration,
            "episodes": [
                {
                    "episode_id": item["episode_id"],
                    "revision": item["revision"],
                    "shot_count": item["shot_count"],
                    "asset_bound_shot_count": item["asset_bound_shot_count"],
                    "missing_asset_binding_count": item["missing_asset_binding_count"],
                }
                for item in episode_facts
            ],
            "stages": _STAGES,
        }
        return {
            "project_id": project_id,
            "scope_type": normalized["scope_type"],
            "production_mode": normalized["production_mode"],
            "checkpoint_policy": normalized["checkpoint_policy"],
            "episode_count": len(episode_facts),
            "total_shot_count": total_shots,
            "estimated_candidate_count": total_shots * _CANDIDATES_PER_MODE[normalized["production_mode"]],
            "can_create": True,
            "plan_hash": _digest(hash_input),
            "configuration": configuration,
            "stages": list(_STAGES),
            "warnings": warnings,
            "episodes": episode_facts,
        }

    def plan(self, project_id: str, command: dict[str, Any]) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            return self._plan_with_connection(connection, project_id, command)
        finally:
            connection.close()

    @staticmethod
    def _allowed_actions(status: str, *, has_resumable_gate: bool = False) -> list[str]:
        return {
            "READY": ["START", "PAUSE", "CANCEL"],
            "RUNNING": ["PAUSE", "CANCEL"],
            "PAUSED": ["RESUME", "CANCEL"],
            "WAITING_USER": (["RESUME", "CANCEL"] if has_resumable_gate else ["CANCEL"]),
            "WAITING_REVIEW": ["PAUSE", "CANCEL"],
            "FAILED": ["CANCEL"],
        }.get(status, [])

    @classmethod
    def _session_view(cls, connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT ps.*,(SELECT COUNT(*) FROM production_session_items psi WHERE psi.session_id=ps.id) AS item_count
               FROM production_sessions ps WHERE ps.id=?""",
            (session_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError(
                "PRODUCTION_SESSION_NOT_FOUND",
                "生产会话不存在",
                {"session_id": session_id},
            )
        has_resumable_gate = bool(
            connection.execute(
                """SELECT 1
                   FROM production_session_items psi
                   JOIN automation_workflow_runs awr
                     ON awr.id=json_extract(psi.progress_json,'$.episode_run_id')
                   WHERE psi.session_id=? AND awr.status='PAUSED_HITL'
                   LIMIT 1""",
                (session_id,),
            ).fetchone()
        )
        status = str(row["status"])
        budget = ProductionSessionBudgetService.inspect_with_connection(connection, session_id)
        return {
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "scope_type": str(row["scope_type"]),
            "production_mode": str(row["production_mode"]),
            "checkpoint_policy": str(row["checkpoint_policy"]),
            "status": status,
            "current_stage": str(row["current_stage"]),
            "plan_hash": str(row["plan_hash"]),
            "configuration": json.loads(str(row["configuration_json"] or "{}")),
            "budget": budget,
            "counters": json.loads(str(row["counters_json"] or "{}")),
            "item_count": int(row["item_count"] or 0),
            "last_error_code": row["last_error_code"],
            "last_error_message": row["last_error_message"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "created_by": str(row["created_by"]),
            "revision": int(row["revision"]),
            "allowed_actions": cls._allowed_actions(
                status,
                has_resumable_gate=has_resumable_gate,
            ),
        }

    def create(
        self,
        project_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "创建生产会话必须提供有效 Idempotency-Key")
        normalized = self._normalize_request(command)
        request_payload = {
            **normalized,
            "expected_plan_hash": str(command["expected_plan_hash"]),
        }
        payload_hash = _digest(request_payload)
        scope = f"production-session:create:{project_id}"
        now = _now()
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能创建不同的生产会话",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["idempotent_replay"] = True
                return replay

            plan = self._plan_with_connection(connection, project_id, normalized)
            expected_plan_hash = str(command["expected_plan_hash"])
            if plan["plan_hash"] != expected_plan_hash:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_PLAN_STALE",
                    "生产范围或上游内容已变化，请重新预检",
                    {"expected_plan_hash": expected_plan_hash, "actual_plan_hash": plan["plan_hash"]},
                )
            active = connection.execute(
                """SELECT id,status,scope_type,created_at FROM production_sessions
                   WHERE project_id=? AND status IN
                     ('READY','RUNNING','PAUSED','WAITING_USER','WAITING_REVIEW')
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            if active is not None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_ACTIVE_CONFLICT",
                    "当前项目已有未结束的一键生产会话，请继续、取消或完成该会话后再创建新的会话",
                    {
                        "existing_session_id": str(active["id"]),
                        "existing_status": str(active["status"]),
                        "existing_scope_type": str(active["scope_type"]),
                    },
                )
            session_id = str(uuid.uuid4())
            actor = str(command.get("actor") or "local-user")
            counters = {
                "total": plan["episode_count"],
                "pending": plan["episode_count"],
                "running": 0,
                "waiting": 0,
                "blocked": 0,
                "failed": 0,
                "completed": 0,
                "cancelled": 0,
            }
            connection.execute(
                """INSERT INTO production_sessions
                   (id,project_id,scope_type,production_mode,checkpoint_policy,status,current_stage,
                    plan_hash,request_hash,idempotency_key,configuration_json,counters_json,
                    created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,?,'READY','PREPARATION',?,?,?,?,?,?,?, ?,1,'production-session.v1')""",
                (
                    session_id,
                    project_id,
                    plan["scope_type"],
                    plan["production_mode"],
                    plan["checkpoint_policy"],
                    plan["plan_hash"],
                    payload_hash,
                    key,
                    _json(plan["configuration"]),
                    _json(counters),
                    now,
                    now,
                    actor,
                ),
            )
            for episode in plan["episodes"]:
                connection.execute(
                    """INSERT INTO production_session_items
                       (id,session_id,episode_id,ordinal,state,current_stage,source_episode_revision,
                        shot_count,progress_json,created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,'PENDING','PREPARATION',?,?,?, ?,?,?,1,'production-session.v1')""",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        episode["episode_id"],
                        episode["ordinal"],
                        episode["revision"],
                        episode["shot_count"],
                        _json({"completion_percent": 0, "stages": {}}),
                        now,
                        now,
                        actor,
                    ),
                )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,after_revision,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_CREATED','production_session',?,1,?,?)""",
                (
                    actor,
                    session_id,
                    "创建一键生产会话",
                    _json(
                        {
                            "project_id": project_id,
                            "scope_type": plan["scope_type"],
                            "episode_count": plan["episode_count"],
                            "production_mode": plan["production_mode"],
                            "plan_hash": plan["plan_hash"],
                        }
                    ),
                ),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                   VALUES ('production.session.created',?,'production_session',?,?)""",
                (project_id, session_id, _json({"status": "READY", "revision": 1})),
            )
            result = {"session": self._session_view(connection, session_id), "idempotent_replay": False}
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(result)),
            )
            return result

    def get(self, session_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            return self._session_view(connection, session_id)
        finally:
            connection.close()

    def list_sessions(
        self,
        project_id: str,
        *,
        cursor: int,
        limit: int,
        status: str | None = None,
    ) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            normalized_status = str(status or "").strip().upper()
            if normalized_status and normalized_status not in {
                "READY",
                "RUNNING",
                "PAUSED",
                "WAITING_USER",
                "WAITING_REVIEW",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_STATUS_INVALID",
                    "生产会话状态筛选无效",
                    {"status": status},
                )
            clause = " AND status=?" if normalized_status else ""
            params: list[Any] = [project_id]
            if normalized_status:
                params.append(normalized_status)
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM production_sessions WHERE project_id=?{clause}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""SELECT id FROM production_sessions WHERE project_id=?{clause}
                    ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                [*params, limit, cursor],
            ).fetchall()
            items = [self._session_view(connection, str(row["id"])) for row in rows]
            next_cursor = cursor + len(items) if cursor + len(items) < total else None
            return {
                "items": items,
                "cursor": cursor,
                "limit": limit,
                "total": total,
                "next_cursor": next_cursor,
                "read_only": True,
                "request_shape": "bounded_production_sessions_v2",
            }
        finally:
            connection.close()

    def list_items(self, session_id: str, *, cursor: int, limit: int) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            self._session_view(connection, session_id)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM production_session_items WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """SELECT psi.*,e.code AS episode_code,e.title AS episode_title
                   FROM production_session_items psi
                   JOIN episodes e ON e.id=psi.episode_id
                   WHERE psi.session_id=? ORDER BY psi.ordinal LIMIT ? OFFSET ?""",
                (session_id, limit, cursor),
            ).fetchall()
            items = [
                {
                    "id": str(row["id"]),
                    "session_id": str(row["session_id"]),
                    "episode_id": str(row["episode_id"]),
                    "episode_code": str(row["episode_code"]),
                    "episode_title": str(row["episode_title"]) if row["episode_title"] is not None else None,
                    "ordinal": int(row["ordinal"]),
                    "state": str(row["state"]),
                    "current_stage": str(row["current_stage"]),
                    "source_episode_revision": int(row["source_episode_revision"]),
                    "shot_count": int(row["shot_count"]),
                    "progress": json.loads(str(row["progress_json"] or "{}")),
                    "last_error_code": row["last_error_code"],
                    "last_error_message": row["last_error_message"],
                    "updated_at": str(row["updated_at"]),
                    "revision": int(row["revision"]),
                }
                for row in rows
            ]
            next_cursor = cursor + len(items) if cursor + len(items) < total else None
            return {
                "items": items,
                "cursor": cursor,
                "limit": limit,
                "total": total,
                "next_cursor": next_cursor,
                "read_only": True,
                "request_shape": "bounded_production_session_items_v2",
            }
        finally:
            connection.close()

    @staticmethod
    def _retry_prerequisites(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        episode_id: str,
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        item_state = connection.execute(
            """SELECT current_stage,last_error_code
               FROM production_session_items
               WHERE session_id=? AND episode_id=?""",
            (session_id, episode_id),
        ).fetchone()
        # A session-only MACHINE_TEMPORARY asset is a valid unattended input
        # until the final review boundary.  Do not turn an unrelated runtime
        # or preflight repair into an early human identity approval gate.
        # Identity-stage failures and final-review retries still require the
        # creator to resolve the pending proposal first.
        identity_review_required = bool(
            item_state is None
            or str(item_state["current_stage"] or "")
            in {"ASSETS", "ASSET_COMPLETION", "WAITING_REVIEW"}
            or str(item_state["last_error_code"] or "")
            in {
                "ASSET_REVIEW_REQUIRED",
                "PRODUCTION_SESSION_ASSET_IDENTITY_REVIEW_REQUIRED",
                "PRODUCTION_SESSION_IDENTITY_INPUT_REQUIRED",
            }
        )
        unresolved_assets = int(
            connection.execute(
                """SELECT COUNT(*)
                   FROM production_session_asset_inputs i
                   JOIN story_asset_proposals p ON p.id=i.asset_proposal_id
                   JOIN story_assets a ON a.id=i.story_asset_id
                   WHERE i.session_id=? AND i.episode_id=? AND i.state='ACTIVE'
                     AND NOT (
                       p.status IN ('ACCEPTED_NEW','ACCEPTED_MERGE')
                       AND COALESCE(p.resolved_asset_id,'')=i.story_asset_id
                       AND a.status='ACTIVE'
                     )""",
                (session_id, episode_id),
            ).fetchone()[0]
        )
        if unresolved_assets and identity_review_required:
            blockers.append(
                {
                    "action": "REVIEW_ASSET_IDENTITIES",
                    "code": "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED",
                    "message": f"{unresolved_assets} 个机器临时资产身份仍需人工确认",
                }
            )
        budget = ProductionSessionBudgetService.inspect_with_connection(connection, session_id)
        if budget.get("hard_blockers"):
            blockers.append(
                {
                    "action": "EXTEND_BUDGET",
                    "code": "PRODUCTION_SESSION_BUDGET_EXTENSION_REQUIRED",
                    "message": "生产预算已耗尽，请先提高对应预算",
                    "hard_blockers": budget["hard_blockers"],
                }
            )
        return blockers

    def retry_item(
        self,
        session_id: str,
        item_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "重试分集必须提供有效 Idempotency-Key")
        expected_session_revision = int(command["expected_session_revision"])
        expected_item_revision = int(command["expected_item_revision"])
        strategy = str(command.get("strategy") or "RETRY_FAILED_STAGE").strip().upper()
        if strategy not in {"RETRY_FAILED_STAGE", "RECOMPOSE_ONLY", "FULL_EPISODE"}:
            raise DomainRuleError(
                "PRODUCTION_SESSION_RETRY_STRATEGY_INVALID",
                "重试策略必须是 RETRY_FAILED_STAGE、RECOMPOSE_ONLY 或 FULL_EPISODE",
            )
        payload_hash = _digest(
            {
                "item_id": item_id,
                "expected_session_revision": expected_session_revision,
                "expected_item_revision": expected_item_revision,
                "strategy": strategy,
            }
        )
        scope = f"production-session:retry-item:{session_id}:{item_id}"
        actor = str(command.get("actor") or "local-user")
        now = _now()
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能执行不同的分集重试",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["session"] = self._session_view(connection, session_id)
                replay["idempotent_replay"] = True
                return replay
            session = self._session_view(connection, session_id)
            if session["revision"] != expected_session_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新后重试",
                    {
                        "expected_revision": expected_session_revision,
                        "actual_revision": session["revision"],
                    },
                )
            if session["status"] in {"COMPLETED", "CANCELLED"}:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_TERMINAL",
                    "已结束的生产会话不能重试分集",
                    {"status": session["status"]},
                )
            item = connection.execute(
                "SELECT * FROM production_session_items WHERE id=? AND session_id=?",
                (item_id, session_id),
            ).fetchone()
            if item is None:
                raise DomainRuleError("PRODUCTION_SESSION_ITEM_NOT_FOUND", "生产会话分集项不存在")
            if int(item["revision"]) != expected_item_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_ITEM_REVISION_CONFLICT",
                    "分集状态已变化，请刷新后重试",
                    {
                        "expected_revision": expected_item_revision,
                        "actual_revision": int(item["revision"]),
                    },
                )
            if str(item["state"]) not in {"BLOCKED", "FAILED", "WAITING"}:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_ITEM_NOT_RETRYABLE",
                    "只有阻塞、失败或待审的分集可以局部重试",
                    {"state": str(item["state"])},
                )
            prerequisites = self._retry_prerequisites(
                connection,
                session_id=session_id,
                episode_id=str(item["episode_id"]),
            )
            if prerequisites:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_RETRY_PREREQUISITE_REQUIRED",
                    "请先完成返工计划的前置处理",
                    {"prerequisites": prerequisites},
                )
            progress = json.loads(str(item["progress_json"] or "{}"))
            progress["session_run_attempt"] = int(progress.get("session_run_attempt") or 1) + 1
            progress["retry"] = {
                "strategy": strategy,
                "requested_at": now,
                "previous_state": str(item["state"]),
                "previous_stage": str(item["current_stage"]),
                "previous_error_code": item["last_error_code"],
            }
            progress.pop("episode_run_id", None)
            progress.pop("episode_run_status", None)
            progress.pop("pending_gate", None)
            should_recompose = strategy == "RECOMPOSE_ONLY" or (
                strategy == "RETRY_FAILED_STAGE" and str(item["current_stage"]) in {"TIMELINE_PREVIEW", "WAITING_REVIEW"}
            )
            if should_recompose:
                progress["requested_operation"] = "RECOMPOSE_ONLY"
                stage = "TIMELINE_PREVIEW"
            else:
                progress.pop("requested_operation", None)
                stage = "PREPARATION"
            connection.execute(
                """UPDATE production_session_items SET state='PENDING',current_stage=?,progress_json=?,
                   last_error_code=NULL,last_error_message=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (stage, _json(progress), now, item_id),
            )
            connection.execute(
                """UPDATE production_sessions SET status='RUNNING',current_stage=?,finished_at=NULL,
                   last_error_code=NULL,last_error_message=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (stage, now, session_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_ITEM_RETRY','production_session_item',?,?,?)""",
                (
                    actor,
                    item_id,
                    "重试一键生产分集",
                    _json({"session_id": session_id, "strategy": strategy, "stage": stage}),
                ),
            )
            updated_item = connection.execute(
                "SELECT id,episode_id,state,current_stage,revision FROM production_session_items WHERE id=?",
                (item_id,),
            ).fetchone()
            result = {
                "session": self._session_view(connection, session_id),
                "item": dict(updated_item),
                "outcome": "RETRY_QUEUED",
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(result)),
            )
            return result

    def extend_budget(
        self,
        session_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "扩展生产预算必须提供有效 Idempotency-Key")
        budget_keys = (
            "max_duration_seconds",
            "max_new_jobs",
            "max_attempts_total",
            "max_output_bytes",
            "max_queued_gpu_jobs",
            "dispatch_shots_per_tick",
        )
        requested = {field: int(command[field]) for field in budget_keys if command.get(field) is not None}
        if not requested:
            raise DomainRuleError(
                "PRODUCTION_SESSION_BUDGET_EXTENSION_REQUIRED",
                "请至少提高一项生产预算",
            )
        expected_revision = int(command["expected_revision"])
        payload_hash = _digest({"expected_revision": expected_revision, "requested": requested})
        scope = f"production-session:{session_id}:extend-budget"
        actor = str(command.get("actor") or "local-user")
        now = _now()
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能扩展不同的生产预算",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["session"] = self._session_view(connection, session_id)
                replay["idempotent_replay"] = True
                return replay
            session = self._session_view(connection, session_id)
            if session["revision"] != expected_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新后重试",
                    {
                        "expected_revision": expected_revision,
                        "actual_revision": session["revision"],
                    },
                )
            if session["status"] in _TERMINAL_STATUSES:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_TERMINAL",
                    "已结束的生产会话不能扩展预算",
                    {"status": session["status"]},
                )
            configuration = dict(session["configuration"])
            current_limits = normalized_budget_configuration(configuration)
            not_increased = {
                field: {"current": current_limits[field], "requested": value} for field, value in requested.items() if value <= current_limits[field]
            }
            if not_increased:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_BUDGET_NOT_INCREASED",
                    "预算扩展只能提高现有上限",
                    {"fields": not_increased},
                )
            configuration.update(requested)
            budget_items = connection.execute(
                """SELECT id,progress_json FROM production_session_items
                   WHERE session_id=? AND state='BLOCKED'
                     AND last_error_code LIKE 'PRODUCTION_SESSION_%_BUDGET_EXHAUSTED'""",
                (session_id,),
            ).fetchall()
            for budget_item in budget_items:
                progress = json.loads(str(budget_item["progress_json"] or "{}"))
                progress["session_run_attempt"] = int(progress.get("session_run_attempt") or 1) + 1
                progress["budget_extension"] = {
                    "extended_at": now,
                    "fields": requested,
                }
                progress.pop("episode_run_id", None)
                progress.pop("episode_run_status", None)
                progress.pop("pending_gate", None)
                progress.pop("budget_wait", None)
                connection.execute(
                    """UPDATE production_session_items
                       SET state='PENDING',current_stage='PREPARATION',progress_json=?,
                           last_error_code=NULL,last_error_message=NULL,updated_at=?,revision=revision+1
                       WHERE id=?""",
                    (_json(progress), now, budget_item["id"]),
                )
            rearmed_count = len(budget_items)
            target_status = "RUNNING" if rearmed_count and session["status"] == "WAITING_USER" else session["status"]
            connection.execute(
                """UPDATE production_sessions
                   SET configuration_json=?,status=?,last_error_code=NULL,
                       last_error_message=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (_json(configuration), target_status, now, session_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_BUDGET_EXTENDED',
                           'production_session',?,?,?)""",
                (
                    actor,
                    session_id,
                    "扩展一键生产预算",
                    _json(
                        {
                            "previous": {field: current_limits[field] for field in requested},
                            "updated": requested,
                            "rearmed_item_count": rearmed_count,
                        }
                    ),
                ),
            )
            result = {
                "session": self._session_view(connection, session_id),
                "extended": requested,
                "rearmed_item_count": rearmed_count,
                "outcome": "BUDGET_EXTENDED",
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(result)),
            )
            return result

    def control(
        self,
        session_id: str,
        action: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        action = action.upper()
        if action not in {"PAUSE", "RESUME", "CANCEL"}:
            raise DomainRuleError("PRODUCTION_SESSION_ACTION_INVALID", "不支持的生产会话控制动作")
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "控制生产会话必须提供有效 Idempotency-Key")
        payload_hash = _digest({"action": action, "expected_revision": int(command["expected_revision"])})
        scope = f"production-session:{session_id}:{action.lower()}"
        now = _now()
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能执行不同的会话控制请求",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["idempotent_replay"] = True
                return replay
            current = self._session_view(connection, session_id)
            expected_revision = int(command["expected_revision"])
            if current["revision"] != expected_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": current["revision"]},
                )
            status = current["status"]
            transitions = {
                "PAUSE": {"READY": "PAUSED", "RUNNING": "PAUSED", "WAITING_REVIEW": "PAUSED"},
                "RESUME": {"PAUSED": "RUNNING", "WAITING_USER": "RUNNING"},
                "CANCEL": {
                    "READY": "CANCELLED",
                    "RUNNING": "CANCELLED",
                    "PAUSED": "CANCELLED",
                    "WAITING_USER": "CANCELLED",
                    "WAITING_REVIEW": "CANCELLED",
                    "FAILED": "CANCELLED",
                },
            }
            target = transitions[action].get(status)
            if target is None:
                if status in _TERMINAL_STATUSES:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_TERMINAL",
                        "生产会话已结束，不能再执行控制动作",
                        {"status": status, "action": action},
                    )
                raise DomainRuleError(
                    "PRODUCTION_SESSION_STATE_INVALID",
                    "当前生产会话状态不允许此操作",
                    {"status": status, "action": action},
                )
            actor = str(command.get("actor") or "local-user")
            finished_at = now if target == "CANCELLED" else current["finished_at"]
            item_rows = connection.execute(
                "SELECT progress_json FROM production_session_items WHERE session_id=?",
                (session_id,),
            ).fetchall()
            run_ids = sorted(
                {str(progress.get("episode_run_id")) for row in item_rows if (progress := json.loads(str(row["progress_json"] or "{}"))).get("episode_run_id")}
            )
            paused_run_ids = {
                run_id
                for run_id in run_ids
                if (
                    run := connection.execute(
                        "SELECT status FROM automation_workflow_runs WHERE id=?",
                        (run_id,),
                    ).fetchone()
                )
                is not None
                and str(run["status"]) == "PAUSED_HITL"
            }
            if action == "RESUME" and status == "WAITING_USER" and not paused_run_ids:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_STATE_INVALID",
                    "当前人工处理项没有可继续的阶段门禁",
                    {"status": status, "action": action},
                )
            if action == "PAUSE":
                for run_id in run_ids:
                    run = connection.execute("SELECT status FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
                    if run is None or str(run["status"]) != "RUNNING":
                        continue
                    pending = {
                        "reason": "PRODUCTION_SESSION_MANUAL_PAUSE",
                        "source": "production_session",
                        "production_session_id": session_id,
                        "ai_score_ignored": True,
                    }
                    connection.execute(
                        """UPDATE jobs SET state='NEEDS_ATTENTION',next_run_at=NULL,
                           last_error_code='AUTOMATION_MANUAL_PAUSE',
                           last_error_detail_redacted='production session manually paused before dispatch',
                           updated_at=?,revision=revision+1
                           WHERE id IN (SELECT job_id FROM automation_workflow_run_tasks WHERE run_id=?)
                             AND state='QUEUED'""",
                        (now, run_id),
                    )
                    connection.execute(
                        """UPDATE automation_workflow_runs SET status='PAUSED_HITL',pending_gate_json=?,
                           human_approval_status='PENDING',updated_at=?,revision=revision+1 WHERE id=?""",
                        (_json(pending), now, run_id),
                    )
            elif action == "RESUME":
                for run_id in run_ids:
                    run = connection.execute(
                        "SELECT status,pending_gate_json FROM automation_workflow_runs WHERE id=?",
                        (run_id,),
                    ).fetchone()
                    if run is None or str(run["status"]) != "PAUSED_HITL":
                        continue
                    connection.execute(
                        """UPDATE jobs SET state='QUEUED',next_run_at=?,last_error_code=NULL,
                           last_error_detail_redacted=NULL,updated_at=?,revision=revision+1
                           WHERE id IN (SELECT job_id FROM automation_workflow_run_tasks WHERE run_id=?)
                             AND state='NEEDS_ATTENTION'
                             AND last_error_code IN ('AUTOMATION_HITL_REQUIRED','AUTOMATION_MANUAL_PAUSE')""",
                        (now, now, run_id),
                    )
                    connection.execute(
                        """UPDATE automation_workflow_runs SET status='RUNNING',pending_gate_json='{}',
                           human_approval_status='APPROVED',updated_at=?,revision=revision+1 WHERE id=?""",
                        (now, run_id),
                    )
            elif action == "CANCEL":
                connection.execute(
                    """UPDATE jobs SET
                         state=CASE WHEN state IN ('QUEUED','NEEDS_ATTENTION','ORPHANED')
                                    THEN 'CANCELLED' ELSE 'CANCEL_REQUESTED' END,
                         cancel_requested_at=?,
                         finished_at=CASE WHEN state IN ('QUEUED','NEEDS_ATTENTION','ORPHANED')
                                          THEN ? ELSE finished_at END,
                         updated_at=?,revision=revision+1
                       WHERE id IN (
                         SELECT job_id FROM production_session_job_links
                         WHERE session_id=? AND role!='EPISODE_PREPARATION_REUSED'
                       )
                         AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')""",
                    (now, now, now, session_id),
                )
                for run_id in run_ids:
                    connection.execute(
                        """UPDATE automation_workflow_runs SET status='CANCELLED',completed_at=?,updated_at=?,
                           revision=revision+1 WHERE id=?
                           AND status NOT IN ('SUCCEEDED','STOPPED','FAILED','CANCELLED','LIMIT_REACHED')""",
                        (now, now, run_id),
                    )
            connection.execute(
                """UPDATE production_sessions
                   SET status=?,finished_at=?,updated_at=?,revision=revision+1
                   WHERE id=? AND revision=?""",
                (target, finished_at, now, session_id, expected_revision),
            )
            if target == "CANCELLED":
                connection.execute(
                    """UPDATE production_session_items
                       SET state='CANCELLED',updated_at=?,revision=revision+1
                       WHERE session_id=? AND state NOT IN ('COMPLETED','SKIPPED','CANCELLED')""",
                    (now, session_id),
                )
                counts = json.loads(_json(current["counters"]))
                cancelled = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM production_session_items WHERE session_id=? AND state='CANCELLED'",
                        (session_id,),
                    ).fetchone()[0]
                )
                counts.update({"pending": 0, "running": 0, "waiting": 0, "blocked": 0, "cancelled": cancelled})
                connection.execute(
                    "UPDATE production_sessions SET counters_json=? WHERE id=?",
                    (_json(counts), session_id),
                )
            updated = self._session_view(connection, session_id)
            outcome = {"PAUSE": "PAUSED", "RESUME": "RESUMED", "CANCEL": "CANCELLED"}[action]
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                   VALUES (?,'producer',?,'production_session',?,?,?,?,?)""",
                (
                    actor,
                    f"PRODUCTION_SESSION_{action}",
                    session_id,
                    expected_revision,
                    updated["revision"],
                    f"生产会话{outcome}",
                    _json({"from_status": status, "to_status": target}),
                ),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                   VALUES (?,?, 'production_session',?,?)""",
                (
                    f"production.session.{action.lower()}",
                    current["project_id"],
                    session_id,
                    _json({"status": target, "revision": updated["revision"]}),
                ),
            )
            result = {"session": updated, "outcome": outcome, "idempotent_replay": False}
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(result)),
            )
            return result
