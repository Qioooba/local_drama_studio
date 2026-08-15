"""Bounded declarative workflow plans with explicit human-in-the-loop gates.

This module is intentionally data-only.  A workflow definition is a finite
batch plus a small allow-listed condition language; it never evaluates Python,
shell, URLs, provider prompts, or arbitrary user code.  Each run persists its
counters and gate state in SQLite.  Machine checks may pause a run, but neither
machine checks nor AI scores can create an approval decision.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

from .jobs import JobService

MODES = frozenset({"MANUAL", "ASSISTED", "BATCH_AUTOMATED"})
GATE_MODES = frozenset({"NONE", "BEFORE_RUN", "EACH_ITERATION", "ON_CONDITION"})
CONDITION_FIELDS = frozenset(
    {
        "machine_check.status",
        "machine_check.ok",
        "machine_check.score",
        "review_decision.status",
        "review_decision.approved",
        "task.status",
        "iteration",
        "task_count",
        "disk_bytes",
    }
)
CONDITION_OPERATORS = frozenset({"EQ", "NEQ", "GT", "GTE", "LT", "LTE", "IN"})
CONDITION_ACTIONS = frozenset({"CONTINUE", "PAUSE_HITL", "STOP"})
RUN_STATES = frozenset({"RUNNING", "PAUSED_HITL", "SUCCEEDED", "STOPPED", "FAILED", "CANCELLED", "LIMIT_REACHED"})
MAX_NODES = 100
MAX_BATCH_ITEMS = 10_000
MAX_CONDITIONS = 50
MAX_ITERATIONS = 100_000
MAX_TASKS = 100_000
MAX_DISK_BYTES = 1 << 50


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _decode(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _require_nonempty(value: str, code: str, label: str, max_length: int = 200) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise DomainRuleError(code, f"{label} 不能为空且不能超过 {max_length} 个字符")
    return normalized


class AutomationWorkflowService:
    """Persistence and state transitions for finite local workflow runs."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.jobs = JobService(database)

    @staticmethod
    def _normalize_definition(
        *,
        mode: str,
        nodes: list[dict[str, Any]],
        batch_items: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        max_iterations: int,
        max_tasks: int,
        max_disk_bytes: int,
        human_gate: str,
        repeat_batch: bool,
    ) -> dict[str, Any]:
        normalized_mode = mode.strip().upper()
        if normalized_mode not in MODES:
            raise DomainRuleError("AUTOMATION_WORKFLOW_MODE_INVALID", "workflow mode 必须是 MANUAL、ASSISTED 或 BATCH_AUTOMATED")
        if human_gate.strip().upper() not in GATE_MODES:
            raise DomainRuleError("AUTOMATION_WORKFLOW_GATE_INVALID", "human_gate 必须是 NONE、BEFORE_RUN、EACH_ITERATION 或 ON_CONDITION")
        if not 1 <= max_iterations <= MAX_ITERATIONS:
            raise DomainRuleError("AUTOMATION_MAX_ITERATIONS_INVALID", f"max_iterations 必须在 1—{MAX_ITERATIONS} 之间")
        if not 1 <= max_tasks <= MAX_TASKS:
            raise DomainRuleError("AUTOMATION_MAX_TASKS_INVALID", f"max_tasks 必须在 1—{MAX_TASKS} 之间")
        if not 1 <= max_disk_bytes <= MAX_DISK_BYTES:
            raise DomainRuleError("AUTOMATION_MAX_DISK_INVALID", f"max_disk_bytes 必须在 1—{MAX_DISK_BYTES} 之间")
        if not nodes or len(nodes) > MAX_NODES:
            raise DomainRuleError("AUTOMATION_NODES_INVALID", f"nodes 数量必须在 1—{MAX_NODES} 之间")
        if not batch_items or len(batch_items) > MAX_BATCH_ITEMS:
            raise DomainRuleError("AUTOMATION_BATCH_INVALID", f"batch_items 数量必须在 1—{MAX_BATCH_ITEMS} 之间")
        if len(conditions) > MAX_CONDITIONS:
            raise DomainRuleError("AUTOMATION_CONDITIONS_INVALID", f"conditions 数量不能超过 {MAX_CONDITIONS}")

        normalized_nodes: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        for raw in nodes:
            if not isinstance(raw, dict):
                raise DomainRuleError("AUTOMATION_NODE_INVALID", "每个 node 必须是对象")
            node_id = _require_nonempty(str(raw.get("id", "")), "AUTOMATION_NODE_INVALID", "node.id", 120)
            if node_id in node_ids:
                raise DomainRuleError("AUTOMATION_NODE_DUPLICATE", "node.id 必须唯一", {"node_id": node_id})
            node_ids.add(node_id)
            node_type = _require_nonempty(str(raw.get("type", "TASK")), "AUTOMATION_NODE_INVALID", "node.type", 80)
            # Keep this contract declarative.  Executable code and network
            # targets are never accepted into a workflow definition.
            forbidden = {"code", "script", "shell", "command", "url", "endpoint", "provider_url"}
            if forbidden.intersection(raw):
                raise DomainRuleError("AUTOMATION_EXECUTABLE_DEFINITION", "workflow node 只能包含声明式字段，禁止代码、命令或网络地址")
            normalized_nodes.append(
                {
                    "id": node_id,
                    "type": node_type,
                    "requires_human_approval": bool(raw.get("requires_human_approval", False)),
                    "metadata": raw.get("metadata", {}) if isinstance(raw.get("metadata", {}), dict) else {},
                }
            )

        normalized_batch: list[dict[str, Any]] = []
        for index, raw in enumerate(batch_items):
            if not isinstance(raw, dict):
                raise DomainRuleError("AUTOMATION_BATCH_ITEM_INVALID", "每个 batch item 必须是对象")
            key = _require_nonempty(str(raw.get("key", f"item-{index + 1}")), "AUTOMATION_BATCH_ITEM_INVALID", "batch item key", 200)
            if any(key == item["key"] for item in normalized_batch):
                raise DomainRuleError("AUTOMATION_BATCH_ITEM_DUPLICATE", "batch item key 必须唯一", {"key": key})
            # Input is a snapshot for a local command, not an instruction to
            # execute arbitrary work.
            if any(name in raw for name in {"code", "script", "shell", "command", "url", "endpoint"}):
                raise DomainRuleError("AUTOMATION_EXECUTABLE_DEFINITION", "batch item 禁止代码、命令或网络地址")
            normalized_batch.append({"key": key, "payload": raw.get("payload", {}) if isinstance(raw.get("payload", {}), dict) else {}})

        normalized_conditions: list[dict[str, Any]] = []
        for raw in conditions:
            if not isinstance(raw, dict):
                raise DomainRuleError("AUTOMATION_CONDITION_INVALID", "每个 condition 必须是对象")
            field = str(raw.get("field", "")).strip()
            operator = str(raw.get("operator", "EQ")).strip().upper()
            action = str(raw.get("action", "CONTINUE")).strip().upper()
            if field not in CONDITION_FIELDS:
                raise DomainRuleError("AUTOMATION_CONDITION_FIELD_INVALID", "condition.field 不是允许的结构化字段", {"field": field})
            if operator not in CONDITION_OPERATORS:
                raise DomainRuleError("AUTOMATION_CONDITION_OPERATOR_INVALID", "condition.operator 不是允许的比较运算")
            if action not in CONDITION_ACTIONS:
                # In particular, AUTO_APPROVE/APPROVE can never be encoded.
                raise DomainRuleError("AUTOMATION_AUTO_APPROVAL_FORBIDDEN", "条件不能自动批准，正式批准必须由人工完成")
            normalized_conditions.append({"field": field, "operator": operator, "value": raw.get("value"), "action": action})

        # A node-level hard gate is equivalent to an explicit on-condition
        # pause and is retained in the immutable definition snapshot.
        node_gate = any(item["requires_human_approval"] for item in normalized_nodes)
        return {
            "mode": normalized_mode,
            "nodes": normalized_nodes,
            "batch_items": normalized_batch,
            "conditions": normalized_conditions,
            "max_iterations": max_iterations,
            "max_tasks": max_tasks,
            "max_disk_bytes": max_disk_bytes,
            "human_gate": human_gate.strip().upper(),
            "repeat_batch": bool(repeat_batch),
            "node_gate": node_gate,
            "ai_approval_allowed": False,
            "network_contacted": False,
            "local_only": True,
        }

    def create_workflow(
        self,
        project_id: str,
        *,
        code: str,
        title: str,
        mode: str,
        nodes: list[dict[str, Any]],
        batch_items: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        max_iterations: int,
        max_tasks: int,
        max_disk_bytes: int,
        human_gate: str = "ON_CONDITION",
        repeat_batch: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized_code = _require_nonempty(code, "AUTOMATION_WORKFLOW_INVALID", "workflow code", 120)
        normalized_title = _require_nonempty(title, "AUTOMATION_WORKFLOW_INVALID", "workflow title", 200)
        definition = self._normalize_definition(
            mode=mode,
            nodes=nodes,
            batch_items=batch_items,
            conditions=conditions,
            max_iterations=max_iterations,
            max_tasks=max_tasks,
            max_disk_bytes=max_disk_bytes,
            human_gate=human_gate,
            repeat_batch=repeat_batch,
        )
        workflow_id = str(uuid.uuid4())
        now = _now()
        plan_hash = _hash({"project_id": project_id, "code": normalized_code, "title": normalized_title, "definition": definition})
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            if connection.execute("SELECT 1 FROM automation_workflows WHERE project_id=? AND code=?", (project_id, normalized_code)).fetchone():
                raise DomainRuleError("AUTOMATION_WORKFLOW_CODE_EXISTS", "同一项目内 workflow code 已存在", {"code": normalized_code})
            connection.execute(
                """INSERT INTO automation_workflows
                (id,project_id,code,title,mode,definition_json,plan_hash,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,'ACTIVE',?,?,?,1,'v1')""",
                (workflow_id, project_id, normalized_code, normalized_title, definition["mode"], _json(definition), plan_hash, now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'producer','AUTOMATION_WORKFLOW_CREATED','automation_workflow',?,?,?)",
                (actor, workflow_id, "创建声明式有限自动化 workflow", _json({"project_id": project_id, "plan_hash": plan_hash, "max_iterations": max_iterations, "max_tasks": max_tasks, "max_disk_bytes": max_disk_bytes, "ai_approval_allowed": False})),
            )
        return self.get_workflow(workflow_id)

    @staticmethod
    def _workflow_view(row: Any) -> dict[str, Any]:
        definition = cast(dict[str, Any], _decode(row["definition_json"], {}))
        return {
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "code": str(row["code"]),
            "title": str(row["title"]),
            "mode": str(row["mode"]),
            "status": str(row["status"]),
            "definition": definition,
            "plan_hash": str(row["plan_hash"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": int(row["revision"]),
            "local_only": True,
            "network_contacted": False,
            "ai_approval_allowed": False,
        }

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM automation_workflows WHERE id=?", (workflow_id,)).fetchone()
        if row is None:
            raise DomainRuleError("AUTOMATION_WORKFLOW_NOT_FOUND", "workflow 不存在", {"workflow_id": workflow_id})
        return self._workflow_view(row)

    def list_workflows(self, project_id: str, *, include_archived: bool = False) -> dict[str, Any]:
        with self.database.connect() as connection:
            if include_archived:
                rows = connection.execute("SELECT * FROM automation_workflows WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM automation_workflows WHERE project_id=? AND status='ACTIVE' ORDER BY updated_at DESC", (project_id,)).fetchall()
        return {"items": [self._workflow_view(row) for row in rows], "local_only": True, "network_contacted": False}

    def plan_workflow(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        definition = workflow["definition"]
        batch_count = len(definition["batch_items"])
        repeat_batch = bool(definition["repeat_batch"])
        estimated_tasks = min(int(definition["max_tasks"]), int(definition["max_iterations"])) if repeat_batch else min(batch_count, int(definition["max_tasks"]))
        return {
            "workflow_id": workflow_id,
            "project_id": workflow["project_id"],
            "plan_hash": workflow["plan_hash"],
            "mode": workflow["mode"],
            "batch_count": batch_count,
            "estimated_iterations": min(estimated_tasks, int(definition["max_iterations"])),
            "estimated_tasks": estimated_tasks,
            "estimated_disk_bytes": estimated_tasks * int(definition["max_disk_bytes"] // max(estimated_tasks, 1)),
            "max_iterations": int(definition["max_iterations"]),
            "max_tasks": int(definition["max_tasks"]),
            "max_disk_bytes": int(definition["max_disk_bytes"]),
            "human_gate": definition["human_gate"],
            "node_gate": bool(definition["node_gate"]),
            "conditions": definition["conditions"],
            "requires_human_confirmation": True,
            "ai_scores_can_approve": False,
            "network_contacted": False,
            "local_only": True,
        }

    def _event(self, connection: Any, run_id: str, event_type: str, payload: dict[str, Any], actor: str) -> None:
        now = _now()
        connection.execute(
            "INSERT INTO automation_workflow_run_events (id,run_id,event_type,event_json,created_at,created_by) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), run_id, event_type, _json(payload), now, actor),
        )
        run = connection.execute("SELECT project_id FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
        if run:
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'producer',?,?,?, ?,?)",
                (actor, f"AUTOMATION_RUN_{event_type}", "automation_workflow_run", run_id, f"声明式 workflow run: {event_type}", _json({"project_id": run["project_id"], **payload})),
            )

    @staticmethod
    def _run_view(row: Any, tasks: list[Any], events: list[Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "workflow_id": str(row["workflow_id"]),
            "project_id": str(row["project_id"]),
            "status": str(row["status"]),
            "plan_hash": str(row["plan_hash"]),
            "iteration_count": int(row["iteration_count"]),
            "task_count": int(row["task_count"]),
            "disk_bytes": int(row["disk_bytes"]),
            "limits": {"max_iterations": int(row["max_iterations"]), "max_tasks": int(row["max_tasks"]), "max_disk_bytes": int(row["max_disk_bytes"])},
            "pending_gate": _decode(row["pending_gate_json"], {}),
            "machine_context": _decode(row["machine_context_json"], {}),
            "ai_scores": _decode(row["ai_scores_json"], {}),
            "human_approval_status": str(row["human_approval_status"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": int(row["revision"]),
            "tasks": [
                {
                    "id": str(item["id"]),
                    "ordinal": int(item["ordinal"]),
                    "item_key": str(item["item_key"]),
                    "item": _decode(item["item_json"], {}),
                    "status": str(item["status"]),
                    "produced_bytes": int(item["produced_bytes"]),
                    "machine_context": _decode(item["machine_context_json"], {}),
                    "review_status": str(item["review_status"]),
                    "job_id": str(item["job_id"]) if item["job_id"] else None,
                    "job_state": str(item["job_state"]) if item["job_state"] else None,
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in tasks
            ],
            "events": [
                {"id": str(event["id"]), "event_type": str(event["event_type"]), "payload": _decode(event["event_json"], {}), "created_at": event["created_at"], "created_by": event["created_by"]}
                for event in events
            ],
            "local_only": True,
            "network_contacted": False,
            "ai_scores_can_approve": False,
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在", {"run_id": run_id})
            tasks = connection.execute("SELECT t.*, j.state AS job_state FROM automation_workflow_run_tasks t LEFT JOIN jobs j ON j.id=t.job_id WHERE t.run_id=? ORDER BY t.ordinal", (run_id,)).fetchall()
            events = connection.execute("SELECT * FROM automation_workflow_run_events WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()
        return self._run_view(row, tasks, events)

    def list_runs(self, project_id: str, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise DomainRuleError("AUTOMATION_RUN_PAGE_INVALID", "run limit 必须在 1—500 之间")
        with self.database.connect() as connection:
            if status:
                rows = connection.execute("SELECT * FROM automation_workflow_runs WHERE project_id=? AND status=? ORDER BY created_at DESC LIMIT ?", (project_id, status, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM automation_workflow_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            views = []
            for row in rows:
                tasks = connection.execute("SELECT t.*, j.state AS job_state FROM automation_workflow_run_tasks t LEFT JOIN jobs j ON j.id=t.job_id WHERE t.run_id=? ORDER BY t.ordinal", (row["id"],)).fetchall()
                events = connection.execute("SELECT * FROM automation_workflow_run_events WHERE run_id=? ORDER BY created_at,id", (row["id"],)).fetchall()
                views.append(self._run_view(row, tasks, events))
        return {"items": views, "limit": limit, "local_only": True, "network_contacted": False}

    def start_run(self, workflow_id: str, *, plan_hash: str, idempotency_key: str, actor: str = "local-user") -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "workflow run 必须提供有效 Idempotency-Key")
        workflow = self.get_workflow(workflow_id)
        if workflow["status"] != "ACTIVE":
            raise DomainRuleError("AUTOMATION_WORKFLOW_INACTIVE", "workflow 已停用，不能启动")
        if not hmac.compare_digest(str(workflow["plan_hash"]), plan_hash):
            raise DomainRuleError("AUTOMATION_PLAN_STALE", "workflow plan 已变化，请重新获取 plan")
        definition = workflow["definition"]
        payload_hash = _hash({"workflow_id": workflow_id, "plan_hash": plan_hash})
        scope = f"automation-workflow-run:{workflow_id}"
        run_id = str(uuid.uuid4())
        now = _now()
        gate = str(definition["human_gate"])
        initial_status = "PAUSED_HITL" if gate == "BEFORE_RUN" else "RUNNING"
        pending_gate = {"reason": "BEFORE_RUN_APPROVAL", "source": "workflow_definition", "ai_score_ignored": True} if initial_status == "PAUSED_HITL" else {}
        approval_status = "PENDING" if initial_status == "PAUSED_HITL" else "NOT_REQUIRED"
        with self.database.transaction() as connection:
            prior = connection.execute("SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (scope, idempotency_key)).fetchone()
            if prior:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 Idempotency-Key 不能复用不同 workflow plan")
                replay = cast(dict[str, Any], json.loads(str(prior["response_json"])))
                replay["idempotent_replay"] = True
                return self.get_run(str(replay["id"]))
            connection.execute(
                """INSERT INTO automation_workflow_runs
                (id,workflow_id,project_id,status,plan_hash,iteration_count,task_count,disk_bytes,max_iterations,max_tasks,max_disk_bytes,pending_gate_json,machine_context_json,ai_scores_json,human_approval_status,started_at,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, workflow_id, workflow["project_id"], initial_status, plan_hash, 0, 0, 0, definition["max_iterations"], definition["max_tasks"], definition["max_disk_bytes"], _json(pending_gate), _json({}), _json({}), approval_status, now, now, now, actor, 1, "v1"),
            )
            self._event(connection, run_id, "STARTED", {"status": initial_status, "human_gate": gate, "ai_score_ignored": True}, actor)
            connection.execute("INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)", (scope, idempotency_key, payload_hash, _json({"id": run_id})))
        return self.get_run(run_id)

    @staticmethod
    def _value(context: dict[str, Any], field: str, *, iteration: int, task_count: int, disk_bytes: int) -> Any:
        if field == "iteration":
            return iteration
        if field == "task_count":
            return task_count
        if field == "disk_bytes":
            return disk_bytes
        current: Any = context
        for part in field.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @staticmethod
    def _condition_matches(condition: dict[str, Any], context: dict[str, Any], *, iteration: int, task_count: int, disk_bytes: int) -> bool:
        left = AutomationWorkflowService._value(context, str(condition["field"]), iteration=iteration, task_count=task_count, disk_bytes=disk_bytes)
        right = condition.get("value")
        operator = str(condition["operator"])
        if operator == "EQ":
            return bool(left == right)
        if operator == "NEQ":
            return bool(left != right)
        if operator == "IN":
            return isinstance(right, list) and left in right
        try:
            if operator == "GT":
                return bool(left > right)
            if operator == "GTE":
                return bool(left >= right)
            if operator == "LT":
                return bool(left < right)
            if operator == "LTE":
                return bool(left <= right)
        except TypeError:
            return False
        return False

    def step_run(
        self,
        run_id: str,
        *,
        machine_context: dict[str, Any] | None = None,
        ai_scores: dict[str, Any] | None = None,
        produced_bytes: int = 0,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if produced_bytes < 0:
            raise DomainRuleError("AUTOMATION_DISK_DELTA_INVALID", "produced_bytes 不能为负数")
        machine = machine_context or {}
        scores = ai_scores or {}
        if not isinstance(machine, dict) or not isinstance(scores, dict):
            raise DomainRuleError("AUTOMATION_CONTEXT_INVALID", "machine_context 和 ai_scores 必须是对象")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在")
            if row["status"] != "RUNNING":
                raise DomainRuleError("AUTOMATION_RUN_NOT_RUNNING", "只有 RUNNING 的 workflow run 才能 step", {"status": row["status"]})
            workflow = connection.execute("SELECT * FROM automation_workflows WHERE id=?", (row["workflow_id"],)).fetchone()
            if workflow is None:
                raise DomainRuleError("AUTOMATION_WORKFLOW_NOT_FOUND", "workflow 不存在")
            definition = cast(dict[str, Any], _decode(workflow["definition_json"], {}))
            iteration = int(row["iteration_count"]) + 1
            task_count = int(row["task_count"])
            disk_bytes = int(row["disk_bytes"])
            if iteration > int(row["max_iterations"]) or task_count >= int(row["max_tasks"]):
                connection.execute("UPDATE automation_workflow_runs SET status='LIMIT_REACHED',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (_now(), _now(), run_id))
                self._event(connection, run_id, "LIMIT_REACHED", {"iteration": iteration, "task_count": task_count, "reason": "max_iterations_or_max_tasks"}, actor)
            else:
                batch = definition["batch_items"]
                if not definition.get("repeat_batch", False) and task_count >= len(batch):
                    connection.execute("UPDATE automation_workflow_runs SET status='SUCCEEDED',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (_now(), _now(), run_id))
                    self._event(connection, run_id, "COMPLETED", {"reason": "finite_batch_exhausted"}, actor)
                elif disk_bytes + produced_bytes > int(row["max_disk_bytes"]):
                    connection.execute("UPDATE automation_workflow_runs SET status='LIMIT_REACHED',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (_now(), _now(), run_id))
                    self._event(connection, run_id, "LIMIT_REACHED", {"reason": "max_disk_bytes", "disk_bytes": disk_bytes + produced_bytes, "max_disk_bytes": int(row["max_disk_bytes"])}, actor)
                else:
                    item = batch[task_count % len(batch)]
                    context = {**machine, "task": {"status": str(machine.get("task", {}).get("status", "SUCCEEDED")) if isinstance(machine.get("task"), dict) else "SUCCEEDED"}, "iteration": iteration}
                    next_status = "RUNNING"
                    pending_gate: dict[str, Any] = {}
                    action = "CONTINUE"
                    for condition in definition["conditions"]:
                        if self._condition_matches(condition, context, iteration=iteration, task_count=task_count + 1, disk_bytes=disk_bytes + produced_bytes):
                            action = str(condition["action"])
                            break
                    machine_status = str(machine.get("status", "PASS")).upper()
                    if machine_status in {"FAIL", "FAILED", "BLOCKED"}:
                        action = "PAUSE_HITL"
                        pending_gate = {"reason": "MACHINE_CHECK_REQUIRES_HITL", "machine_status": machine_status, "ai_score_ignored": True}
                    elif action == "PAUSE_HITL":
                        pending_gate = {"reason": "DECLARATIVE_CONDITION", "ai_score_ignored": True}
                    if definition["human_gate"] == "EACH_ITERATION" or (definition["node_gate"] and action == "CONTINUE"):
                        action = "PAUSE_HITL"
                        pending_gate = {"reason": "NODE_HUMAN_GATE", "ai_score_ignored": True}
                    if action == "STOP":
                        next_status = "STOPPED"
                    elif action == "PAUSE_HITL":
                        next_status = "PAUSED_HITL"
                    elif definition.get("repeat_batch", False) and (iteration >= int(row["max_iterations"]) or task_count + 1 >= int(row["max_tasks"])):
                        next_status = "LIMIT_REACHED"
                    elif not definition.get("repeat_batch", False) and task_count + 1 >= len(batch):
                        next_status = "SUCCEEDED"
                    task_status = "SUCCEEDED" if machine_status not in {"FAIL", "FAILED", "BLOCKED"} else "BLOCKED_HITL"
                    task_id = str(uuid.uuid4())
                    task_key = f"{iteration}:{item['key']}"
                    now = _now()
                    # Keep the workflow's bounded state machine authoritative:
                    # the durable Job is an audit/execution hand-off and
                    # carries the explicit HITL gate in its immutable snapshot.
                    # A worker can therefore not infer approval from an AI
                    # score or from the queue state alone.
                    previous_job = connection.execute(
                        "SELECT job_id FROM automation_workflow_run_tasks WHERE run_id=? AND job_id IS NOT NULL ORDER BY ordinal DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    dependencies = [str(previous_job["job_id"])] if previous_job else []
                    job = self.jobs.create_job_in_transaction(
                        connection,
                        str(row["project_id"]),
                        "AUTOMATION_WORKFLOW_TASK",
                        "AUTOMATION_WORKFLOW_TASK",
                        task_id,
                        "CPU",
                        {
                            "schema_version": "localdrama.automation-task.v1",
                            "automation_workflow_id": str(row["workflow_id"]),
                            "automation_run_id": run_id,
                            "automation_task_id": task_id,
                            "ordinal": task_count + 1,
                            "item_key": str(item["key"]),
                            "plan_hash": str(row["plan_hash"]),
                            "approval_required": next_status == "PAUSED_HITL",
                            "approval_status": "PENDING" if next_status == "PAUSED_HITL" else "NOT_REQUIRED",
                            "machine_status": machine_status,
                            "local_only": True,
                            "network_contacted": False,
                        },
                        f"automation-task:{task_id}",
                        max_attempts=1,
                        depends_on_job_ids=dependencies,
                        actor=actor,
                    )
                    if next_status == "PAUSED_HITL":
                        # QUEUED is claimable by a worker.  Keep a gated task
                        # in NEEDS_ATTENTION until the explicit human resume
                        # transition below, so a scheduler cannot bypass the
                        # workflow's HITL boundary.
                        connection.execute(
                            "UPDATE jobs SET state='NEEDS_ATTENTION',last_error_code='AUTOMATION_HITL_REQUIRED',last_error_detail_redacted='awaiting explicit human workflow decision',updated_at=?,revision=revision+1 WHERE id=? AND state='QUEUED'",
                            (now, job["id"]),
                        )
                        self.jobs._emit(connection, "JOB_BLOCKED_HITL", str(row["project_id"]), "JOB", str(job["id"]), {"state": "NEEDS_ATTENTION", "run_id": run_id, "task_id": task_id})
                    connection.execute(
                        "INSERT INTO automation_workflow_run_tasks (id,run_id,ordinal,item_key,item_json,status,produced_bytes,machine_context_json,review_status,job_id,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
                        (task_id, run_id, task_count + 1, task_key, _json(item), task_status, produced_bytes, _json(machine), "PENDING" if next_status == "PAUSED_HITL" else "NOT_REQUIRED", job["id"], now, now),
                    )
                    connection.execute(
                        """UPDATE automation_workflow_runs SET status=?,iteration_count=?,task_count=?,disk_bytes=?,pending_gate_json=?,machine_context_json=?,ai_scores_json=?,human_approval_status=?,completed_at=?,updated_at=?,revision=revision+1 WHERE id=?""",
                        (next_status, iteration, task_count + 1, disk_bytes + produced_bytes, _json(pending_gate), _json(machine), _json(scores), "PENDING" if next_status == "PAUSED_HITL" else str(row["human_approval_status"]), _now() if next_status in {"SUCCEEDED", "STOPPED", "LIMIT_REACHED"} else None, now, run_id),
                    )
                    self._event(connection, run_id, "STEP_COMPLETED", {"task_id": task_id, "task_key": task_key, "job_id": job["id"], "job_dependency_ids": dependencies, "status": next_status, "machine_status": machine_status, "ai_score_ignored": True, "produced_bytes": produced_bytes}, actor)
                    if next_status == "PAUSED_HITL":
                        self._event(connection, run_id, "HITL_REQUIRED", pending_gate, actor)
        return self.get_run(run_id)

    def resume_run(self, run_id: str, *, decision: str, note: str, actor: str = "local-user") -> dict[str, Any]:
        normalized = decision.strip().upper()
        if normalized not in {"HUMAN_APPROVED", "HUMAN_REJECTED"}:
            raise DomainRuleError("AUTOMATION_HITL_DECISION_INVALID", "HITL decision 必须是 HUMAN_APPROVED 或 HUMAN_REJECTED")
        if not note.strip() or len(note) > 2_000:
            raise DomainRuleError("AUTOMATION_HITL_NOTE_REQUIRED", "人工 HITL 决定必须填写 note")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在")
            if row["status"] != "PAUSED_HITL":
                raise DomainRuleError("AUTOMATION_HITL_NOT_PENDING", "workflow run 当前没有等待人工决定", {"status": row["status"]})
            now = _now()
            gated_jobs = connection.execute(
                "SELECT j.id,j.project_id FROM automation_workflow_run_tasks t JOIN jobs j ON j.id=t.job_id WHERE t.run_id=? AND j.state='NEEDS_ATTENTION' AND j.last_error_code='AUTOMATION_HITL_REQUIRED'",
                (run_id,),
            ).fetchall()
            if normalized == "HUMAN_APPROVED":
                for job in gated_jobs:
                    connection.execute(
                        "UPDATE jobs SET state='QUEUED',next_run_at=?,last_error_code=NULL,last_error_detail_redacted=NULL,updated_at=?,revision=revision+1 WHERE id=?",
                        (now, now, job["id"]),
                    )
                    self.jobs._emit(connection, "JOB_HITL_APPROVED", str(job["project_id"]), "JOB", str(job["id"]), {"state": "QUEUED", "run_id": run_id, "decision": normalized})
                connection.execute("UPDATE automation_workflow_runs SET status='RUNNING',pending_gate_json='{}',human_approval_status='APPROVED',updated_at=?,revision=revision+1 WHERE id=?", (now, run_id))
                self._event(connection, run_id, "HITL_APPROVED", {"decision": normalized, "note": note, "ai_score_ignored": True}, actor)
            else:
                for job in gated_jobs:
                    connection.execute(
                        "UPDATE jobs SET state='CANCELLED',cancel_requested_at=?,last_error_code='AUTOMATION_HITL_REJECTED',last_error_detail_redacted='human rejected workflow task',finished_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (now, now, now, job["id"]),
                    )
                    self.jobs._emit(connection, "JOB_HITL_REJECTED", str(job["project_id"]), "JOB", str(job["id"]), {"state": "CANCELLED", "run_id": run_id, "decision": normalized})
                connection.execute("UPDATE automation_workflow_runs SET status='FAILED',pending_gate_json='{}',human_approval_status='REJECTED',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (now, now, run_id))
                self._event(connection, run_id, "HITL_REJECTED", {"decision": normalized, "note": note, "ai_score_ignored": True}, actor)
        return self.get_run(run_id)

    def pause_run(self, run_id: str, *, reason: str = "MANUAL_PAUSE", actor: str = "local-user") -> dict[str, Any]:
        normalized_reason = _require_nonempty(reason, "AUTOMATION_PAUSE_REASON_REQUIRED", "pause reason", 500)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在")
            if row["status"] != "RUNNING":
                raise DomainRuleError("AUTOMATION_RUN_NOT_RUNNING", "只有 RUNNING 的 workflow run 才能暂停")
            pending = {"reason": normalized_reason, "source": "human", "ai_score_ignored": True}
            now = _now()
            connection.execute("UPDATE automation_workflow_runs SET status='PAUSED_HITL',pending_gate_json=?,human_approval_status='PENDING',updated_at=?,revision=revision+1 WHERE id=?", (_json(pending), now, run_id))
            self._event(connection, run_id, "MANUAL_PAUSE", pending, actor)
        return self.get_run(run_id)

    def cancel_run(self, run_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在")
            if row["status"] in {"SUCCEEDED", "STOPPED", "FAILED", "CANCELLED", "LIMIT_REACHED"}:
                raise DomainRuleError("AUTOMATION_RUN_NOT_CANCELLABLE", "已结束的 workflow run 不能取消")
            now = _now()
            connection.execute("UPDATE automation_workflow_runs SET status='CANCELLED',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (now, now, run_id))
            self._event(connection, run_id, "CANCELLED", {"reason": "human"}, actor)
        return self.get_run(run_id)
