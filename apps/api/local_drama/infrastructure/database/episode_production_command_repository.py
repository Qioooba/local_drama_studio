"""Atomic v2 Episode Production run transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

TERMINAL_RUN_STATES = {"SUCCEEDED", "STOPPED", "FAILED", "CANCELLED", "LIMIT_REACHED"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class SqliteEpisodeProductionTransitionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def transition(
        self,
        run_id: str,
        *,
        action: str,
        expected_revision: int,
        idempotency_key: str,
        reason: str | None,
        actor: str,
    ) -> dict[str, Any]:
        if action not in {"PAUSED", "RESUMED", "CANCELLED", "RECOVERED"}:
            raise DomainRuleError("EPISODE_PRODUCTION_ACTION_INVALID", "不支持的整集生产命令", {"action": action})
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "命令必须提供 1—200 字符的 Idempotency-Key")
        normalized_reason = (reason or "").strip()
        if action in {"PAUSED", "RESUMED"} and not normalized_reason:
            raise DomainRuleError("EPISODE_PRODUCTION_REASON_REQUIRED", "暂停与恢复命令必须说明原因")
        scope = f"episode-production-run-v2:{run_id}:{action}"
        fingerprint = _hash({
            "run_id": run_id,
            "action": action,
            "expected_revision": expected_revision,
            "reason": normalized_reason,
        })
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if not hmac.compare_digest(str(prior["payload_hash"]), fingerprint):
                    raise DomainRuleError(
                        "EPISODE_PRODUCTION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能复用不同命令内容",
                    )
                replay = cast(dict[str, Any], json.loads(str(prior["response_json"])))
                replay["idempotent_replay"] = True
                return replay

            row = connection.execute(
                """SELECT r.*,json_extract(w.definition_json,'$.nodes[0].metadata.episode_id') AS episode_id
                FROM automation_workflow_runs r JOIN automation_workflows w ON w.id=r.workflow_id
                WHERE r.id=?""",
                (run_id,),
            ).fetchone()
            if row is None or not row["episode_id"]:
                raise DomainRuleError(
                    "EPISODE_PRODUCTION_RUN_NOT_FOUND",
                    "整集生产运行不存在",
                    {"run_id": run_id},
                )
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError(
                    "EPISODE_PRODUCTION_RUN_REVISION_CONFLICT",
                    "整集生产运行已变化，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": int(row["revision"])},
                )
            affected = 0
            now = _now()
            if action == "PAUSED":
                if str(row["status"]) != "RUNNING":
                    raise DomainRuleError("EPISODE_PRODUCTION_RUN_NOT_RUNNING", "只有运行中的整集生产才能暂停")
                jobs = connection.execute(
                    """SELECT j.id,j.project_id FROM automation_workflow_run_tasks t
                    JOIN jobs j ON j.id=t.job_id WHERE t.run_id=? AND j.state='QUEUED'""",
                    (run_id,),
                ).fetchall()
                for job in jobs:
                    connection.execute(
                        """UPDATE jobs SET state='NEEDS_ATTENTION',next_run_at=NULL,
                        last_error_code='AUTOMATION_MANUAL_PAUSE',
                        last_error_detail_redacted='workflow manually paused before dispatch',
                        updated_at=?,revision=revision+1 WHERE id=?""",
                        (now, job["id"]),
                    )
                    self._job_event(connection, job, run_id, "JOB_BLOCKED_BY_WORKFLOW_PAUSE", "NEEDS_ATTENTION")
                affected = len(jobs)
                pending = _json({"reason": normalized_reason, "source": "human", "ai_score_ignored": True})
                connection.execute(
                    """UPDATE automation_workflow_runs SET status='PAUSED_HITL',pending_gate_json=?,
                    human_approval_status='PENDING',updated_at=?,revision=revision+1 WHERE id=?""",
                    (pending, now, run_id),
                )
            elif action == "RESUMED":
                if str(row["status"]) != "PAUSED_HITL":
                    raise DomainRuleError("EPISODE_PRODUCTION_RUN_NOT_PAUSED", "只有暂停中的整集生产才能恢复")
                jobs = connection.execute(
                    """SELECT j.id,j.project_id FROM automation_workflow_run_tasks t
                    JOIN jobs j ON j.id=t.job_id WHERE t.run_id=? AND j.state='NEEDS_ATTENTION'
                    AND j.last_error_code IN ('AUTOMATION_HITL_REQUIRED','AUTOMATION_MANUAL_PAUSE')""",
                    (run_id,),
                ).fetchall()
                for job in jobs:
                    connection.execute(
                        """UPDATE jobs SET state='QUEUED',next_run_at=?,last_error_code=NULL,
                        last_error_detail_redacted=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                        (now, now, job["id"]),
                    )
                    self._job_event(connection, job, run_id, "JOB_RELEASED_BY_WORKFLOW_RESUME", "QUEUED")
                affected = len(jobs)
                connection.execute(
                    """UPDATE automation_workflow_runs SET status='RUNNING',pending_gate_json='{}',
                    human_approval_status='APPROVED',updated_at=?,revision=revision+1 WHERE id=?""",
                    (now, run_id),
                )
            elif action == "CANCELLED":
                if str(row["status"]) in TERMINAL_RUN_STATES:
                    raise DomainRuleError("EPISODE_PRODUCTION_RUN_NOT_CANCELLABLE", "已结束的整集生产不能取消")
                jobs = connection.execute(
                    """SELECT j.id,j.project_id,j.state FROM automation_workflow_run_tasks t
                    JOIN jobs j ON j.id=t.job_id WHERE t.run_id=?
                    AND j.state NOT IN ('SUCCEEDED','FAILED','CANCELLED')""",
                    (run_id,),
                ).fetchall()
                for job in jobs:
                    target = "CANCELLED" if str(job["state"]) in {"QUEUED", "NEEDS_ATTENTION", "ORPHANED"} else "CANCEL_REQUESTED"
                    connection.execute(
                        """UPDATE jobs SET state=?,cancel_requested_at=?,
                        finished_at=CASE WHEN ?='CANCELLED' THEN ? ELSE finished_at END,
                        updated_at=?,revision=revision+1 WHERE id=?""",
                        (target, now, target, now, now, job["id"]),
                    )
                    self._job_event(connection, job, run_id, "JOB_CANCEL_REQUESTED", target)
                affected = len(jobs)
                connection.execute(
                    """UPDATE automation_workflow_runs SET status='CANCELLED',completed_at=?,
                    updated_at=?,revision=revision+1 WHERE id=?""",
                    (now, now, run_id),
                )
            else:
                if str(row["status"]) != "RUNNING":
                    raise DomainRuleError("EPISODE_PRODUCTION_RUN_NOT_RUNNING", "只有运行中的整集生产才能执行恢复")
                jobs = connection.execute(
                    """SELECT j.id,j.project_id FROM automation_workflow_run_tasks t
                    JOIN jobs j ON j.id=t.job_id WHERE t.run_id=?
                    AND j.state IN ('ORPHANED','NEEDS_ATTENTION')
                    AND j.last_error_code='WORKER_LEASE_EXPIRED'
                    AND NOT EXISTS (SELECT 1 FROM job_attempts a WHERE a.job_id=j.id
                      AND a.provider_job_id IS NOT NULL ORDER BY a.attempt_no DESC LIMIT 1)""",
                    (run_id,),
                ).fetchall()
                for job in jobs:
                    connection.execute(
                        """UPDATE jobs SET state='QUEUED',next_run_at=?,last_error_code=NULL,
                        last_error_detail_redacted=NULL,finished_at=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                        (now, now, job["id"]),
                    )
                    self._job_event(connection, job, run_id, "JOB_RECOVERED", "QUEUED")
                affected = len(jobs)
                connection.execute(
                    "UPDATE automation_workflow_runs SET updated_at=?,revision=revision+1 WHERE id=?",
                    (now, run_id),
                )

            updated = connection.execute(
                "SELECT id,project_id,status,revision,updated_at FROM automation_workflow_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            result = {
                **dict(updated),
                "episode_id": str(row["episode_id"]),
                "affected_job_count": affected,
                "idempotent_replay": False,
            }
            event_payload = {
                "episode_id": str(row["episode_id"]),
                "action": action,
                "status": str(updated["status"]),
                "revision": int(updated["revision"]),
                "affected_job_count": affected,
            }
            connection.execute(
                """INSERT INTO outbox_events
                (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('EpisodeProductionRunChanged',?,'EPISODE_PRODUCTION_RUN',?,?)""",
                (updated["project_id"], run_id, _json(event_payload)),
            )
            connection.execute(
                """INSERT INTO automation_workflow_run_events
                (id,run_id,event_type,event_json,created_at,created_by) VALUES (?,?,?,?,?,?)""",
                (str(uuid.uuid4()), run_id, action, _json(event_payload), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'producer',?,'episode_production_run',?,?,?,?,?)""",
                (
                    actor,
                    f"EPISODE_PRODUCTION_RUN_{action}",
                    run_id,
                    expected_revision,
                    int(updated["revision"]),
                    f"整集生产运行 {action}",
                    _json({"episode_id": row["episode_id"], "affected_job_count": affected}),
                ),
            )
            connection.execute(
                """INSERT INTO command_idempotencies
                (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)""",
                (scope, key, fingerprint, _json(result)),
            )
            return result

    @staticmethod
    def _job_event(connection: Any, job: Any, run_id: str, event_type: str, state: str) -> None:
        connection.execute(
            """INSERT INTO outbox_events
            (type,project_id,subject_type,subject_id,payload_json) VALUES (?,?,?,?,?)""",
            (event_type, job["project_id"], "JOB", job["id"], _json({"run_id": run_id, "state": state})),
        )
