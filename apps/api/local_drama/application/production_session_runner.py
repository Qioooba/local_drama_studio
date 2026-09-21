from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_preparation import EpisodePreparationService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.jobs import JobService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.production_asset_inputs import ProductionAssetInputService
from local_drama.application.production_session_budgets import ProductionSessionBudgetService
from local_drama.application.production_sessions import ProductionSessionService, _digest, _json
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stage_for_action(action: str) -> str:
    normalized = action.upper()
    if normalized in {"STORY_PARSE", "SCRIPT_BREAKDOWN"}:
        return "PREPARATION"
    if normalized in {
        "ASSET_IDENTITY",
        "ASSET_HERO_COMPLETION",
        "ASSET_COMPLETION",
        "ASSET_GENERATION",
    }:
        return "ASSETS"
    if normalized in {"EPISODE_PLAN", "SHOT_PLAN", "SHOT_READY"}:
        return "SHOT_PLAN"
    if "KEYFRAME" in normalized:
        return "KEYFRAMES"
    if "VIDEO" in normalized:
        return "VIDEO"
    if normalized in {"TTS_BATCH", "TTS_FINALIZE", "TTS", "SUBTITLE", "AUDIO", "AUDIO_SUBTITLE"}:
        return "AUDIO_SUBTITLE"
    if normalized in {"TIMELINE", "TIMELINE_ASSEMBLY", "COMPOSE", "RENDER", "DELIVERY"}:
        return "TIMELINE_PREVIEW"
    return "PREPARATION"


class ProductionSessionRunner:
    """Advance durable production sessions through existing episode services."""

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.sessions = ProductionSessionService(database)
        self.preparation = EpisodePreparationService(database, settings)
        self.episode_runs = EpisodeProductionRunService(database, settings)
        self.automation = AutomationWorkflowService(database)

    @staticmethod
    def _item_rows(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM production_session_items WHERE session_id=? ORDER BY ordinal",
                (session_id,),
            ).fetchall()
        ]

    def _link_run_jobs(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        item_id: str,
        run: dict[str, Any],
        actor: str,
    ) -> None:
        for task in run.get("tasks", []):
            job_id = str(task.get("job_id") or "")
            if not job_id:
                continue
            raw_item = task.get("item")
            item_payload: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
            raw_payload = item_payload.get("payload")
            payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
            action = str(payload.get("action") or task.get("item_key") or "AUTOMATION_TASK")
            connection.execute(
                """INSERT OR IGNORE INTO production_session_job_links
                   (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                    created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,?,?,'ACTIVE',?,?,?,1,'production-session.v1')""",
                (
                    str(uuid.uuid4()),
                    session_id,
                    item_id,
                    job_id,
                    _stage_for_action(action),
                    "EPISODE_WORKFLOW_TASK",
                    _now(),
                    _now(),
                    actor,
                ),
            )

    def _update_item(
        self,
        item_id: str,
        *,
        state: str,
        stage: str,
        progress: dict[str, Any],
        error: DomainRuleError | None = None,
    ) -> None:
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE production_session_items
                   SET state=?,current_stage=?,progress_json=?,last_error_code=?,last_error_message=?,
                       updated_at=?,revision=revision+1 WHERE id=?""",
                (
                    state,
                    stage,
                    _json(progress),
                    error.code if error else None,
                    error.message if error else None,
                    now,
                    item_id,
                ),
            )

    def _dispatch_episode_run(
        self,
        session: dict[str, Any],
        item: dict[str, Any],
        progress: dict[str, Any],
        *,
        actor: str,
    ) -> str:
        try:
            episode_id = str(item["episode_id"])
            attempt = max(1, int(progress.get("session_run_attempt") or 1))
            idempotency_key = f"production-session:{session['id']}:item:{item['id']}:episode-run:v{attempt}"
            requested_operation = str(progress.get("requested_operation") or "")
            if requested_operation == "RECOMPOSE_ONLY":
                impact = EpisodeWorkerActionService(self.database, self.settings).operation_impact(
                    episode_id,
                    operation="RECOMPOSE_ONLY",
                    target_shot_ids=(),
                    target_take_count=1,
                    tts_enabled=bool(session["configuration"].get("tts_enabled", True)),
                    production_mode=str(session["production_mode"]),
                    checkpoint_policy=str(session["checkpoint_policy"]),
                    production_session_id=str(session["id"]),
                )
                with self.database.connect() as connection:
                    episode_revision = int(connection.execute("SELECT revision FROM episodes WHERE id=?", (episode_id,)).fetchone()[0])
                run = self.episode_runs.start(
                    episode_id,
                    idempotency_key=idempotency_key,
                    tts_enabled=bool(session["configuration"].get("tts_enabled", True)),
                    production_mode=str(session["production_mode"]),
                    checkpoint_policy=str(session["checkpoint_policy"]),
                    operation="RECOMPOSE_ONLY",
                    target_shot_ids=(),
                    target_take_count=1,
                    expected_plan_hash=str(impact["plan_hash"]),
                    expected_episode_revision=episode_revision,
                    production_session_id=str(session["id"]),
                    actor=actor,
                )
            else:
                run = self.episode_runs.start(
                    episode_id,
                    idempotency_key=idempotency_key,
                    tts_enabled=bool(session["configuration"].get("tts_enabled", True)),
                    production_mode=str(session["production_mode"]),
                    checkpoint_policy=str(session["checkpoint_policy"]),
                    min_free_disk_bytes=int(session["configuration"].get("min_free_disk_bytes") or 1),
                    front_half_only=False,
                    production_session_id=str(session["id"]),
                    actor=actor,
                )
        except DomainRuleError as error:
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage="PREPARATION",
                progress=progress,
                error=error,
            )
            return "BLOCKED"
        progress = {
            **progress,
            "episode_run_id": str(run["id"]),
            "episode_run_status": str(run["status"]),
        }
        stage = self._stage_from_run(run)
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE production_session_items
                   SET state='RUNNING',current_stage=?,progress_json=?,last_error_code=NULL,
                       last_error_message=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (stage, _json(progress), _now(), item["id"]),
            )
            self._link_run_jobs(
                connection,
                session_id=str(session["id"]),
                item_id=str(item["id"]),
                run=run,
                actor=actor,
            )
        return "DISPATCHED"

    def _prepare_or_dispatch(
        self,
        session: dict[str, Any],
        item: dict[str, Any],
        *,
        actor: str,
    ) -> str:
        progress = json.loads(str(item.get("progress_json") or "{}"))
        with self.database.connect() as connection:
            budget = ProductionSessionBudgetService.inspect_with_connection(connection, str(session["id"]))
        hard_blockers = budget.get("hard_blockers") or []
        if hard_blockers:
            blocker = hard_blockers[0]
            progress = {
                **progress,
                "budget_wait": {
                    "kind": "HARD_BUDGET",
                    "blockers": hard_blockers,
                    "usage": budget.get("usage") or {},
                    "limits": budget.get("limits") or {},
                    "observed_at": budget.get("observed_at"),
                },
            }
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage="BUDGET_WAIT",
                progress=progress,
                error=DomainRuleError(
                    str(blocker["code"]),
                    str(blocker["message"]),
                    {
                        "usage": blocker["usage"],
                        "limit": blocker["limit"],
                        "limit_key": blocker["limit_key"],
                    },
                ),
            )
            return "BLOCKED"
        resource_wait = budget.get("resource_wait")
        if resource_wait:
            progress = {
                **progress,
                "resource_wait": {
                    **resource_wait,
                    "observed_at": budget.get("observed_at"),
                },
            }
            self._update_item(
                str(item["id"]),
                state="WAITING",
                stage="RESOURCE_WAIT",
                progress=progress,
            )
            return "WAITING"
        progress.pop("budget_wait", None)
        min_free_disk_bytes = int(session["configuration"].get("min_free_disk_bytes") or 1)
        disk = shutil.disk_usage(self.settings.data_root)
        if int(disk.free) < min_free_disk_bytes:
            progress = {
                **progress,
                "resource_wait": {
                    "code": "PRODUCTION_SESSION_DISK_RESERVE_WAIT",
                    "free_disk_bytes": int(disk.free),
                    "min_free_disk_bytes": min_free_disk_bytes,
                    "observed_at": _now(),
                },
            }
            self._update_item(
                str(item["id"]),
                state="WAITING",
                stage="RESOURCE_WAIT",
                progress=progress,
            )
            return "WAITING"
        progress.pop("resource_wait", None)
        retry_result = self._retry_machine_dependencies(item, progress, actor=actor)
        if retry_result is None:
            return "BLOCKED"
        progress = retry_result
        if int(item.get("shot_count") or 0) > 0:
            prepared = self._ensure_asset_inputs(session, item, progress, actor=actor)
            if prepared is None:
                return "BLOCKED"
            return self._dispatch_episode_run(session, item, prepared, actor=actor)
        try:
            result = self.preparation.prepare(
                str(item["episode_id"]),
                idempotency_key=f"production-session:{session['id']}:item:{item['id']}:prepare:v1",
            )
        except DomainRuleError as error:
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage="PREPARATION",
                progress=progress,
                error=error,
            )
            return "BLOCKED"
        progress = {**progress, "preparation": result}
        if str(result["status"]) == "QUEUED":
            job_id = str(result["job_id"])
            now = _now()
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE production_session_items
                       SET state='WAITING',current_stage='PREPARATION',progress_json=?,
                           updated_at=?,revision=revision+1 WHERE id=?""",
                    (_json(progress), now, item["id"]),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO production_session_job_links
                       (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                        created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,'PREPARATION',?,'ACTIVE',?,?,?,1,'production-session.v1')""",
                    (
                        str(uuid.uuid4()),
                        session["id"],
                        item["id"],
                        job_id,
                        ("EPISODE_PREPARATION_REUSED" if str(result.get("job_ownership") or "OWNED") == "REUSED" else "EPISODE_PREPARATION_OWNED"),
                        now,
                        now,
                        actor,
                    ),
                )
            return "WAITING"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE production_session_items SET shot_count=?,progress_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                (int(result.get("shot_count") or 0), _json(progress), _now(), item["id"]),
            )
        refreshed = {**item, "shot_count": int(result.get("shot_count") or 0), "progress_json": _json(progress)}
        prepared = self._ensure_asset_inputs(session, refreshed, progress, actor=actor)
        if prepared is None:
            return "BLOCKED"
        return self._dispatch_episode_run(session, refreshed, prepared, actor=actor)

    def _retry_machine_dependencies(
        self,
        item: dict[str, Any],
        progress: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        retry = progress.get("retry")
        if not isinstance(retry, dict) or str(retry.get("strategy") or "") not in {
            "RETRY_FAILED_STAGE",
            "FULL_EPISODE",
        }:
            return progress
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT j.id,j.state,j.last_error_code
                   FROM production_session_job_links psl
                   JOIN jobs j ON j.id=psl.job_id
                   WHERE psl.session_item_id=? AND psl.link_state='ACTIVE'
                     AND (psl.role='IDENTITY_HERO' OR psl.role LIKE 'IDENTITY_VIEW_%')
                     AND j.state IN ('FAILED','NEEDS_ATTENTION','ORPHANED')
                   ORDER BY j.created_at,j.id""",
                (item["id"],),
            ).fetchall()
        retried: list[str] = []
        jobs = JobService(self.database, self.settings)
        for row in rows:
            try:
                jobs.retry(str(row["id"]), actor=actor)
            except DomainRuleError as error:
                self._update_item(
                    str(item["id"]),
                    state="BLOCKED",
                    stage="ASSETS",
                    progress=progress,
                    error=error,
                )
                return None
            retried.append(str(row["id"]))
        if not retried:
            return progress
        return {
            **progress,
            "retried_machine_dependency_job_ids": list(
                dict.fromkeys(
                    [
                        *(
                            progress.get("retried_machine_dependency_job_ids", [])
                            if isinstance(progress.get("retried_machine_dependency_job_ids"), list)
                            else []
                        ),
                        *retried,
                    ]
                )
            ),
        }

    def _ensure_asset_inputs(
        self,
        session: dict[str, Any],
        item: dict[str, Any],
        progress: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        """Prepare exact session-only asset mappings before strict preflight."""

        try:
            result = ProductionAssetInputService(self.database).ensure_episode_inputs(
                str(session["id"]),
                str(item["episode_id"]),
                actor=actor,
            )
        except DomainRuleError as error:
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage="ASSETS",
                progress=progress,
                error=error,
            )
            return None
        updated = {**progress, "asset_inputs": result}
        if not result["ready"]:
            blocked_error = DomainRuleError(
                "PRODUCTION_SESSION_ASSET_IDENTITY_REVIEW_REQUIRED",
                "资产身份存在歧义或无效输入，需要人工核对",
                {"blockers": result["blockers"]},
            )
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage="ASSETS",
                progress=updated,
                error=blocked_error,
            )
            return None
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE production_session_items
                   SET progress_json=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (_json(updated), _now(), item["id"]),
            )
        return updated

    @staticmethod
    def _stage_from_run(run: dict[str, Any]) -> str:
        tasks = run.get("tasks") or []
        if not tasks:
            return "PREPARATION"
        task = tasks[-1]
        raw_item = task.get("item")
        item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
        raw_payload = item.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        return _stage_for_action(str(payload.get("action") or task.get("item_key") or ""))

    def _refresh_existing_item(
        self,
        session: dict[str, Any],
        item: dict[str, Any],
        *,
        actor: str,
    ) -> str:
        progress = json.loads(str(item.get("progress_json") or "{}"))
        if str(item.get("current_stage") or "") == "RESOURCE_WAIT":
            return self._prepare_or_dispatch(session, item, actor=actor)
        run_id = str(progress.get("episode_run_id") or "")
        if not run_id:
            preparation = progress.get("preparation") if isinstance(progress.get("preparation"), dict) else {}
            job_id = str(preparation.get("job_id") or "")
            if not job_id:
                return str(item["state"])
            connection = self.database.connect()
            try:
                job = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                connection.close()
            job_state = str(job["state"]) if job is not None else "MISSING"
            if job_state == "SUCCEEDED":
                return self._prepare_or_dispatch(session, item, actor=actor)
            if job_state in {"FAILED", "NEEDS_ATTENTION", "CANCELLED", "MISSING"}:
                error = DomainRuleError(
                    "PRODUCTION_SESSION_PREPARATION_JOB_BLOCKED",
                    "分集准备任务未能完成",
                    {"job_id": job_id, "job_state": job_state},
                )
                self._update_item(
                    str(item["id"]),
                    state="BLOCKED",
                    stage="PREPARATION",
                    progress=progress,
                    error=error,
                )
                return "BLOCKED"
            return "WAITING"
        try:
            run = self.automation.get_run(run_id)
        except DomainRuleError as error:
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage=str(item["current_stage"]),
                progress=progress,
                error=error,
            )
            return "BLOCKED"
        with self.database.connect() as connection:
            dependency_failure = connection.execute(
                """SELECT j.id,j.last_error_detail_redacted,t.item_json
                   FROM automation_workflow_run_tasks t
                   JOIN jobs j ON j.id=t.job_id
                   WHERE t.run_id=? AND j.state='NEEDS_ATTENTION'
                     AND j.last_error_code='JOB_DEPENDENCY_FAILED'
                   ORDER BY t.ordinal LIMIT 1""",
                (run_id,),
            ).fetchone()
        if dependency_failure is not None:
            try:
                failed_item = json.loads(str(dependency_failure["item_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                failed_item = {}
            raw_payload = failed_item.get("payload") if isinstance(failed_item, dict) else None
            payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
            failed_stage = _stage_for_action(str(payload.get("action") or ""))
            dependency_error = DomainRuleError(
                "PRODUCTION_SESSION_DEPENDENCY_FAILED",
                "上游生成任务失败，已停止本集后续生产；可在修复后局部重试",
                {
                    "workflow_run_id": run_id,
                    "blocked_job_id": str(dependency_failure["id"]),
                    "detail": str(dependency_failure["last_error_detail_redacted"] or ""),
                },
            )
            progress = {
                **progress,
                "episode_run_status": str(run["status"]),
                "dependency_failure": dependency_error.details,
            }
            self._update_item(
                str(item["id"]),
                state="BLOCKED",
                stage=failed_stage,
                progress=progress,
                error=dependency_error,
            )
            return "BLOCKED"
        run_status = str(run["status"])
        progress = {**progress, "episode_run_status": run_status}
        stage = self._stage_from_run(run)
        if run_status == "PAUSED_HITL":
            with self.database.connect() as connection:
                budget = ProductionSessionBudgetService.inspect_with_connection(connection, str(session["id"]))
            hard_blockers = budget.get("hard_blockers") or []
            if hard_blockers:
                blocker = hard_blockers[0]
                progress["budget_wait"] = {
                    "kind": "HARD_BUDGET",
                    "blockers": hard_blockers,
                    "usage": budget.get("usage") or {},
                    "limits": budget.get("limits") or {},
                    "observed_at": budget.get("observed_at"),
                    "episode_run_id": run_id,
                }
                self._update_item(
                    str(item["id"]),
                    state="BLOCKED",
                    stage="BUDGET_WAIT",
                    progress=progress,
                    error=DomainRuleError(
                        str(blocker["code"]),
                        str(blocker["message"]),
                        {
                            "usage": blocker["usage"],
                            "limit": blocker["limit"],
                            "limit_key": blocker["limit_key"],
                        },
                    ),
                )
                return "BLOCKED"
            pending_gate = run.get("pending_gate") or {}
            gate_reason = str(pending_gate.get("reason") or "")
            if gate_reason not in {"CONFIGURED_CREATOR_CHECKPOINT", "NODE_HUMAN_GATE"}:
                progress["pending_gate"] = pending_gate
                gate_error = DomainRuleError(
                    "PRODUCTION_SESSION_STAGE_REVIEW_REQUIRED",
                    "本集当前阶段需要人工修复或审核；其他独立分集可继续生产",
                    {
                        "episode_run_id": run_id,
                        "stage": stage,
                        "gate_reason": gate_reason or "UNSPECIFIED",
                    },
                )
                self._update_item(
                    str(item["id"]),
                    state="BLOCKED",
                    stage=stage,
                    progress=progress,
                    error=gate_error,
                )
                return "BLOCKED"
        if run_status == "SUCCEEDED":
            state, stage = "WAITING", "WAITING_REVIEW"
        elif run_status == "PAUSED_HITL":
            state = "WAITING"
            progress["pending_gate"] = run.get("pending_gate") or {}
        elif run_status in {"FAILED", "STOPPED", "LIMIT_REACHED"}:
            state = "FAILED"
        elif run_status == "CANCELLED":
            state = "CANCELLED"
        else:
            state = "RUNNING"
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE production_session_items
                   SET state=?,current_stage=?,progress_json=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (state, stage, _json(progress), _now(), item["id"]),
            )
            self._link_run_jobs(
                connection,
                session_id=str(session["id"]),
                item_id=str(item["id"]),
                run=run,
                actor=actor,
            )
        return state

    def _summarize(self, session_id: str, *, actor: str) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            current = self.sessions._session_view(connection, session_id)
            rows = self._item_rows(connection, session_id)
            counts = {
                "total": len(rows),
                "pending": sum(str(row["state"]) == "PENDING" for row in rows),
                "running": sum(str(row["state"]) == "RUNNING" for row in rows),
                "waiting": sum(str(row["state"]) == "WAITING" for row in rows),
                "blocked": sum(str(row["state"]) == "BLOCKED" for row in rows),
                "failed": sum(str(row["state"]) == "FAILED" for row in rows),
                "completed": sum(str(row["state"]) == "COMPLETED" for row in rows),
                "cancelled": sum(str(row["state"]) == "CANCELLED" for row in rows),
            }
            review_waiting = sum(str(row["state"]) == "WAITING" and str(row["current_stage"]) == "WAITING_REVIEW" for row in rows)
            gate_waiting = 0
            for row in rows:
                if str(row["state"]) != "WAITING":
                    continue
                try:
                    progress = json.loads(str(row.get("progress_json") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    progress = {}
                if str(progress.get("episode_run_status") or "") == "PAUSED_HITL" and progress.get("pending_gate"):
                    gate_waiting += 1
            machine_waiting = max(0, counts["waiting"] - review_waiting - gate_waiting)
            counts["review_waiting"] = review_waiting
            counts["gate_waiting"] = gate_waiting
            counts["machine_waiting"] = machine_waiting
            status = str(current["status"])
            if status not in {"PAUSED", "CANCELLED", "COMPLETED"}:
                automatable = counts["pending"] + counts["running"] + machine_waiting
                requires_user = bool(
                    (
                        counts["blocked"] + counts["failed"] > 0
                        and automatable == 0
                    )
                    or (
                        gate_waiting > 0
                        and counts["running"] == 0
                        and machine_waiting == 0
                    )
                )
                if requires_user:
                    status = "WAITING_USER"
                elif rows and review_waiting + counts["completed"] + counts["cancelled"] == len(rows):
                    status = "WAITING_REVIEW"
                else:
                    status = "RUNNING"
            active_rows = [
                row
                for row in rows
                if str(row["state"]) in {"RUNNING", "WAITING", "BLOCKED", "FAILED"}
            ]

            def active_priority(row: dict[str, Any]) -> int:
                state = str(row["state"])
                stage = str(row["current_stage"])
                if state in {"BLOCKED", "FAILED"}:
                    return 0
                if state == "WAITING" and stage != "WAITING_REVIEW":
                    return 1
                if state == "RUNNING":
                    return 2
                return 3

            active = min(active_rows, key=active_priority) if active_rows else (rows[0] if rows else None)
            current_stage = str(active["current_stage"]) if active else str(current["current_stage"])
            changed = current["counters"] != counts or current["status"] != status or current["current_stage"] != current_stage
            if changed:
                connection.execute(
                    """UPDATE production_sessions SET status=?,current_stage=?,counters_json=?,updated_at=?,
                       revision=revision+1 WHERE id=?""",
                    (status, current_stage, _json(counts), now, session_id),
                )
                connection.execute(
                    """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                       VALUES ('production.session.progress',?,'production_session',?,?)""",
                    (
                        current["project_id"],
                        session_id,
                        _json({"status": status, "current_stage": current_stage, "counters": counts}),
                    ),
                )
            return self.sessions._session_view(connection, session_id)

    def reconcile(self, session_id: str, *, actor: str = "production-session-runner") -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session["status"] in {
            "READY",
            "PAUSED",
            "WAITING_USER",
            "CANCELLED",
            "COMPLETED",
        }:
            return {"session": session, "dispatched_count": 0, "waiting_count": 0, "blocked_count": 0}
        connection = self.database.connect()
        try:
            items = self._item_rows(connection, session_id)
        finally:
            connection.close()
        refreshed_outcomes: list[str] = []
        for item in items:
            if str(item["state"]) in {"RUNNING", "WAITING"} and str(item["current_stage"]) != "WAITING_REVIEW":
                refreshed_outcomes.append(self._refresh_existing_item(session, item, actor=actor))

        connection = self.database.connect()
        try:
            items = self._item_rows(connection, session_id)
        finally:
            connection.close()
        active_count = sum(str(item["state"]) in {"RUNNING", "WAITING"} and str(item["current_stage"]) != "WAITING_REVIEW" for item in items)
        capacity = max(0, int(session["configuration"].get("max_parallel_episodes") or 1) - active_count)
        outcomes: list[str] = []
        for item in items:
            if capacity <= 0:
                break
            if str(item["state"]) != "PENDING":
                continue
            outcome = self._prepare_or_dispatch(session, item, actor=actor)
            outcomes.append(outcome)
            if outcome in {"DISPATCHED", "WAITING"}:
                capacity -= 1
        updated = self._summarize(session_id, actor=actor)
        return {
            "session": updated,
            "dispatched_count": outcomes.count("DISPATCHED"),
            "waiting_count": outcomes.count("WAITING"),
            "blocked_count": outcomes.count("BLOCKED") + refreshed_outcomes.count("BLOCKED"),
        }

    def reconcile_active(self, *, actor: str = "production-session-runner") -> dict[str, Any]:
        connection = self.database.connect()
        try:
            session_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM production_sessions WHERE status IN ('RUNNING','WAITING_REVIEW') ORDER BY updated_at,id"
                ).fetchall()
            ]
        finally:
            connection.close()
        results = [self.reconcile(session_id, actor=actor) for session_id in session_ids]
        return {"inspected": len(session_ids), "items": results}

    def start(
        self,
        session_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "启动生产会话必须提供有效 Idempotency-Key")
        expected_revision = int(command["expected_revision"])
        payload_hash = _digest({"expected_revision": expected_revision})
        scope = f"production-session:{session_id}:start"
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
                        "相同 Idempotency-Key 不能启动不同版本的生产会话",
                    )
                stored = json.loads(str(prior["response_json"]))
                stored["session"] = self.sessions._session_view(connection, session_id)
                stored["idempotent_replay"] = True
                return stored
            current = self.sessions._session_view(connection, session_id)
            if current["revision"] != expected_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": current["revision"]},
                )
            if current["status"] != "READY":
                raise DomainRuleError(
                    "PRODUCTION_SESSION_STATE_INVALID",
                    "只有 READY 的生产会话可以启动",
                    {"status": current["status"]},
                )
            connection.execute(
                """UPDATE production_sessions SET status='RUNNING',started_at=COALESCE(started_at,?),
                   updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                (now, now, session_id, expected_revision),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_STARTED','production_session',?,?,?,?,?)""",
                (
                    str(command.get("actor") or "local-user"),
                    session_id,
                    expected_revision,
                    expected_revision + 1,
                    "启动一键生产会话",
                    _json({"status": "RUNNING"}),
                ),
            )
            reserved = {
                "session": self.sessions._session_view(connection, session_id),
                "dispatched_count": 0,
                "waiting_count": 0,
                "blocked_count": 0,
                "outcome": "STARTED",
                "idempotent_replay": False,
            }
            # Reserve the command before dispatching external durable work.
            # If the process exits after this commit, the worker's active
            # session reconciliation resumes pending items without duplicating
            # episode runs.
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(reserved)),
            )
        reconciled = self.reconcile(session_id, actor=str(command.get("actor") or "local-user"))
        result = {**reconciled, "outcome": "STARTED", "idempotent_replay": False}
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE command_idempotencies SET response_json=? WHERE scope=? AND idempotency_key=?",
                (_json(result), scope, key),
            )
        return result
