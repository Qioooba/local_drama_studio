"""Durable, V2-only state for multi-stage standalone Quick Create runs.

This domain intentionally does not import the legacy quick-generation service
or tables.  A run is a lineage record around immutable V2 execution snapshots;
each candidate/selection/downstream stage remains a separately auditable step.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

_MODES = frozenset({"TEXT_TO_VIDEO", "TEXT_TO_IMAGE_TO_VIDEO"})


@dataclass(frozen=True, slots=True)
class QuickCreateV2Run:
    id: str
    mode: str
    state: str
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class QuickCreateV2SelectedImage:
    """The only image a V2 image-to-video command is allowed to consume."""

    run_id: str
    prompt: str
    selected_step_id: str
    artifact_id: str


class QuickCreateV2RunService:
    """Owns V2 aggregate identity, candidate choice, and recovery lineage."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        mode: str,
        prompt: str,
        idempotency_key: str,
        input_payload: Mapping[str, Any] | None = None,
        plan: Mapping[str, Any] | None = None,
    ) -> QuickCreateV2Run:
        normalized_mode = mode.strip().upper()
        normalized_prompt = prompt.strip()
        if normalized_mode not in _MODES:
            raise DomainRuleError("QUICK_CREATE_V2_MODE_UNSUPPORTED", "V2 多阶段快速生成不支持该模式。")
        if not 2 <= len(normalized_prompt) <= 2000:
            raise DomainRuleError("QUICK_CREATE_V2_PROMPT_REQUIRED", "V2 快速生成描述必须是 2—2000 个字符。")
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "V2 快速生成必须提供 Idempotency-Key。")
        input_hash = _hash({"mode": normalized_mode, "prompt": normalized_prompt, "input": dict(input_payload or {})})
        now = _now()
        with self.database.transaction() as connection:
            existing = connection.execute("SELECT id,mode,state,input_hash FROM mp_quick_create_v2_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing is not None:
                if str(existing["mode"]) != normalized_mode or str(existing["input_hash"]) != input_hash:
                    raise DomainRuleError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key 已用于不同的 V2 快速生成输入。")
                return QuickCreateV2Run(str(existing["id"]), normalized_mode, str(existing["state"]), True)
            run_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_quick_create_v2_runs
                (id,idempotency_key,mode,state,source_prompt_json,plan_json,input_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (run_id, idempotency_key, normalized_mode, "PLANNED", _json({"text": normalized_prompt}), _json(dict(plan or {})), input_hash, now, now),
            )
        return QuickCreateV2Run(run_id, normalized_mode, "PLANNED", False)

    def attach_step(
        self,
        run_id: str,
        *,
        step_no: int,
        step_kind: str,
        capability_code: str,
        execution_snapshot_id: str,
        job_id: str,
        input_artifact_id: str | None = None,
        selection_rank: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        if step_no < 1 or not step_kind.strip() or not capability_code.strip():
            raise DomainRuleError("QUICK_CREATE_V2_STEP_INVALID", "V2 快速生成步骤无效。")
        with self.database.transaction() as connection:
            return self.attach_step_in_transaction(
                connection,
                run_id,
                step_no=step_no,
                step_kind=step_kind,
                capability_code=capability_code,
                execution_snapshot_id=execution_snapshot_id,
                job_id=job_id,
                input_artifact_id=input_artifact_id,
                selection_rank=selection_rank,
                payload=payload,
            )

    def attach_step_in_transaction(
        self,
        connection: Any,
        run_id: str,
        *,
        step_no: int,
        step_kind: str,
        capability_code: str,
        execution_snapshot_id: str,
        job_id: str,
        input_artifact_id: str | None = None,
        selection_rank: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        """Attach a step during the same transaction that created its V2 Job."""
        if step_no < 1 or not step_kind.strip() or not capability_code.strip():
            raise DomainRuleError("QUICK_CREATE_V2_STEP_INVALID", "V2 快速生成步骤无效。")
        self._assert_v2_job_link_in_transaction(connection, job_id, execution_snapshot_id)
        now = _now()
        content_hash = _hash({
            "run_id": run_id, "step_no": step_no, "step_kind": step_kind.strip().upper(), "capability_code": capability_code.strip().upper(),
            "execution_snapshot_id": execution_snapshot_id, "job_id": job_id, "input_artifact_id": input_artifact_id,
            "selection_rank": selection_rank, "payload": dict(payload or {}),
        })
        step_id = str(uuid.uuid4())
        run = connection.execute("SELECT mode,state FROM mp_quick_create_v2_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise DomainRuleError("QUICK_CREATE_V2_RUN_NOT_FOUND", "V2 快速生成记录不存在。")
        existing = connection.execute("SELECT id,content_hash FROM mp_quick_create_v2_steps WHERE run_id=? AND step_no=?", (run_id, step_no)).fetchone()
        if existing is not None:
            if str(existing["content_hash"]) != content_hash:
                raise DomainRuleError("QUICK_CREATE_V2_STEP_CONFLICT", "同一 V2 快速生成步骤已冻结为不同输入。")
            return str(existing["id"])
        connection.execute(
                """INSERT INTO mp_quick_create_v2_steps
                (id,run_id,step_no,step_kind,state,capability_code,execution_snapshot_id,job_id,input_artifact_id,selection_rank,payload_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (step_id, run_id, step_no, step_kind.strip().upper(), "SUBMITTED", capability_code.strip().upper(), execution_snapshot_id, job_id,
                 input_artifact_id, selection_rank, _json(dict(payload or {})), content_hash, now, now),
        )
        connection.execute("UPDATE mp_quick_create_v2_runs SET state='RUNNING',updated_at=?,revision=revision+1 WHERE id=?", (now, run_id))
        return step_id

    def select_image_candidate(self, run_id: str, step_id: str) -> None:
        """Freeze exactly one verified image candidate for the downstream I2V step."""
        now = _now()
        with self.database.transaction() as connection:
            run = connection.execute("SELECT mode,state,selected_step_id FROM mp_quick_create_v2_runs WHERE id=?", (run_id,)).fetchone()
            step = connection.execute(
                """SELECT step.id,step.run_id,step.step_kind,step.state,step.output_artifact_id,artifact.status,artifact.kind
                   FROM mp_quick_create_v2_steps step
                   LEFT JOIN artifacts artifact ON artifact.id=step.output_artifact_id
                   WHERE step.id=?""",
                (step_id,),
            ).fetchone()
            if run is None or step is None or str(step["run_id"]) != run_id:
                raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_NOT_FOUND", "V2 图片候选不存在。")
            if str(run["mode"]) != "TEXT_TO_IMAGE_TO_VIDEO" or str(step["step_kind"]) != "IMAGE_CANDIDATE":
                raise DomainRuleError("QUICK_CREATE_V2_SELECTION_UNSUPPORTED", "当前 V2 路线不支持该候选选择。")
            if str(step["state"]) != "READY" or not step["output_artifact_id"] or str(step["status"]) != "VERIFIED" or str(step["kind"]) != "COMFY_OUTPUT":
                raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_NOT_READY", "只能选择已验证的 V2 图片候选。")
            selected = run["selected_step_id"]
            if selected is not None and str(selected) != step_id:
                raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_ALREADY_SELECTED", "该 V2 快速生成已选择另一张候选图。")
            connection.execute("UPDATE mp_quick_create_v2_steps SET state='SELECTED',updated_at=?,revision=revision+1 WHERE id=?", (now, step_id))
            connection.execute(
                "UPDATE mp_quick_create_v2_runs SET selected_step_id=?,state='IMAGE_SELECTED',updated_at=?,revision=revision+1 WHERE id=?",
                (step_id, now, run_id),
            )

    def selected_image_for_video(self, run_id: str) -> QuickCreateV2SelectedImage:
        """Load a verified aggregate-owned image without accepting a browser path/id."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.id AS run_id,run.mode,run.state,run.source_prompt_json,
                          step.id AS step_id,step.step_kind,step.state AS step_state,step.output_artifact_id,
                          artifact.status AS artifact_status,artifact.kind AS artifact_kind
                   FROM mp_quick_create_v2_runs run
                   JOIN mp_quick_create_v2_steps step ON step.id=run.selected_step_id AND step.run_id=run.id
                   JOIN artifacts artifact ON artifact.id=step.output_artifact_id
                   WHERE run.id=?""",
                (run_id,),
            ).fetchone()
        if (
            row is None
            or str(row["mode"]) != "TEXT_TO_IMAGE_TO_VIDEO"
            or str(row["state"]) != "IMAGE_SELECTED"
            or str(row["step_kind"]) != "IMAGE_CANDIDATE"
            or str(row["step_state"]) != "SELECTED"
            or str(row["artifact_status"]) != "VERIFIED"
            or str(row["artifact_kind"]) != "COMFY_OUTPUT"
            or not row["output_artifact_id"]
        ):
            raise DomainRuleError("QUICK_CREATE_V2_SELECTED_IMAGE_REQUIRED", "V2 图生视频必须使用本次流程中已选择并验证的候选图。")
        prompt = _prompt_from_json(row["source_prompt_json"])
        if prompt is None:
            raise DomainRuleError("QUICK_CREATE_V2_PROMPT_REQUIRED", "V2 快速生成缺少已冻结的提示词。")
        return QuickCreateV2SelectedImage(
            run_id=str(row["run_id"]),
            prompt=prompt,
            selected_step_id=str(row["step_id"]),
            artifact_id=str(row["output_artifact_id"]),
        )

    def public_run(self, run_id: str) -> Mapping[str, Any]:
        """Return a browser-safe aggregate projection without legacy joins or paths."""
        with self.database.connect() as connection:
            run = connection.execute(
                """SELECT id,mode,state,selected_step_id,final_step_id,created_at,updated_at,revision
                   FROM mp_quick_create_v2_runs WHERE id=?""",
                (run_id,),
            ).fetchone()
            if run is None:
                raise DomainRuleError("QUICK_CREATE_V2_RUN_NOT_FOUND", "V2 快速生成记录不存在。")
            steps = connection.execute(
                """SELECT step.id,step.step_no,step.step_kind,step.state,step.capability_code,step.job_id,
                          step.input_artifact_id,step.output_artifact_id,step.selection_rank,
                          job.state AS job_state,job.progress_json,job.last_error_code,job.last_error_detail_redacted,
                          artifact.status AS output_artifact_status,artifact.kind AS output_artifact_kind
                   FROM mp_quick_create_v2_steps step
                   JOIN jobs job ON job.id=step.job_id
                   LEFT JOIN artifacts artifact ON artifact.id=step.output_artifact_id
                   WHERE step.run_id=? ORDER BY step.step_no,step.id""",
                (run_id,),
            ).fetchall()
        return {
            "id": str(run["id"]), "mode": str(run["mode"]), "state": str(run["state"]),
            "selected_step_id": str(run["selected_step_id"]) if run["selected_step_id"] else None,
            "final_step_id": str(run["final_step_id"]) if run["final_step_id"] else None,
            "created_at": str(run["created_at"]), "updated_at": str(run["updated_at"]), "revision": int(run["revision"]),
            "steps": tuple({
                "id": str(step["id"]), "step_no": int(step["step_no"]), "kind": str(step["step_kind"]),
                "state": str(step["state"]), "capability_code": str(step["capability_code"]), "job_id": str(step["job_id"]),
                "job_state": str(step["job_state"]), "progress": _json_mapping(step["progress_json"]),
                "error_code": str(step["last_error_code"]) if step["last_error_code"] else None,
                "error_detail_redacted": str(step["last_error_detail_redacted"]) if step["last_error_detail_redacted"] else None,
                "selection_rank": int(step["selection_rank"]) if step["selection_rank"] is not None else None,
                "input_artifact_id": str(step["input_artifact_id"]) if step["input_artifact_id"] else None,
                "output_artifact": ({
                    "artifact_id": str(step["output_artifact_id"]), "kind": str(step["output_artifact_kind"]),
                    "download_url": f"/api/v1/artifacts/{step['output_artifact_id']}/download",
                } if step["output_artifact_id"] and step["output_artifact_status"] == "VERIFIED" and step["output_artifact_kind"] == "COMFY_OUTPUT" else None),
            } for step in steps),
        }

    @staticmethod
    def next_step_no_in_transaction(connection: Any, run_id: str) -> int:
        """Reserve the next immutable ordinal inside submission's transaction."""
        row = connection.execute(
            "SELECT COALESCE(MAX(step_no), 0) AS last_step_no FROM mp_quick_create_v2_steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["last_step_no"]) + 1

    def record_ready_output(self, step_id: str, artifact_id: str) -> None:
        """Record the one verified artifact produced by an immutable V2 step."""
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT id,state FROM mp_quick_create_v2_steps WHERE id=?", (step_id,)).fetchone()
            artifact = connection.execute(
                """SELECT artifact.id,artifact.status,artifact.kind
                   FROM artifacts artifact
                   JOIN job_attempts attempt ON attempt.id=artifact.job_attempt_id
                   JOIN mp_quick_create_v2_steps step ON step.job_id=attempt.job_id
                   WHERE artifact.id=? AND step.id=?""",
                (artifact_id, step_id),
            ).fetchone()
            if row is None or artifact is None or str(artifact["status"]) != "VERIFIED" or str(artifact["kind"]) != "COMFY_OUTPUT":
                raise DomainRuleError("QUICK_CREATE_V2_OUTPUT_INVALID", "V2 快速生成步骤只能登记已验证 Comfy 制品。")
            connection.execute(
                "UPDATE mp_quick_create_v2_steps SET state='READY',output_artifact_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                (artifact_id, now, step_id),
            )

    def record_artifacts_for_job(self, job_id: str, artifacts: Sequence[Mapping[str, Any]]) -> None:
        """Advance one aggregate Step only after the queue registered its artifact.

        The worker calls this after ``JobService.register_artifact`` has
        computed the digest.  It is safe for unrelated V2 executions: a Job
        without a V2 Quick Create Step is intentionally ignored.
        """
        artifact_id = next(
            (str(item.get("id")) for item in artifacts if item.get("kind") == "COMFY_OUTPUT" and item.get("status") == "VERIFIED" and item.get("id")),
            None,
        )
        if artifact_id is None:
            return
        now = _now()
        with self.database.transaction() as connection:
            step = connection.execute(
                "SELECT id,run_id,step_kind FROM mp_quick_create_v2_steps WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if step is None:
                return
            artifact = connection.execute(
                """SELECT artifact.id FROM artifacts artifact
                   JOIN job_attempts attempt ON attempt.id=artifact.job_attempt_id
                   WHERE artifact.id=? AND attempt.job_id=? AND artifact.status='VERIFIED' AND artifact.kind='COMFY_OUTPUT'""",
                (artifact_id, job_id),
            ).fetchone()
            if artifact is None:
                raise DomainRuleError("QUICK_CREATE_V2_OUTPUT_INVALID", "V2 Worker 回调包含不属于该 Job 的制品。")
            connection.execute(
                "UPDATE mp_quick_create_v2_steps SET state='READY',output_artifact_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                (artifact_id, now, step["id"]),
            )
            if str(step["step_kind"]) == "VIDEO_I2V":
                connection.execute(
                    """UPDATE mp_quick_create_v2_runs
                       SET state='SUCCEEDED',final_step_id=?,updated_at=?,revision=revision+1
                       WHERE id=? AND selected_step_id IS NOT NULL""",
                    (step["id"], now, step["run_id"]),
                )
                return
            if str(step["step_kind"]) != "IMAGE_CANDIDATE":
                return
            run = connection.execute("SELECT mode,plan_json FROM mp_quick_create_v2_runs WHERE id=?", (step["run_id"],)).fetchone()
            if run is None or str(run["mode"]) != "TEXT_TO_IMAGE_TO_VIDEO":
                return
            expected_count = _candidate_count(run["plan_json"])
            ready_count = int(connection.execute(
                "SELECT COUNT(*) FROM mp_quick_create_v2_steps WHERE run_id=? AND step_kind='IMAGE_CANDIDATE' AND state='READY'",
                (step["run_id"],),
            ).fetchone()[0])
            if expected_count > 0 and ready_count == expected_count:
                connection.execute(
                    "UPDATE mp_quick_create_v2_runs SET state='AWAITING_SELECTION',updated_at=?,revision=revision+1 WHERE id=?",
                    (now, step["run_id"]),
                )

    def _assert_v2_job_link(self, job_id: str, snapshot_id: str) -> None:
        with self.database.connect() as connection:
            self._assert_v2_job_link_in_transaction(connection, job_id, snapshot_id)

    @staticmethod
    def _assert_v2_job_link_in_transaction(connection: Any, job_id: str, snapshot_id: str) -> None:
        linked = connection.execute(
            """SELECT link.job_id FROM mp_execution_job_links link
               JOIN jobs job ON job.id=link.job_id
               WHERE link.job_id=? AND link.execution_snapshot_id=?
                 AND job.type='MODEL_PLATFORM_EXECUTION' AND job.scope_kind='SYSTEM'""",
            (job_id, snapshot_id),
        ).fetchone()
        if linked is None:
            raise DomainRuleError("QUICK_CREATE_V2_STEP_LINK_INVALID", "V2 快速生成步骤必须绑定 SYSTEM scope 的 V2 Job 与冻结快照。")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _candidate_count(value: object) -> int:
    try:
        candidates = json.loads(str(value)).get("candidates")
    except (TypeError, ValueError, AttributeError):
        return 0
    return len(candidates) if isinstance(candidates, list) else 0


def _prompt_from_json(value: object) -> str | None:
    try:
        prompt = json.loads(str(value)).get("text")
    except (TypeError, ValueError, AttributeError):
        return None
    normalized = str(prompt).strip() if prompt is not None else ""
    return normalized if 2 <= len(normalized) <= 2000 else None


def _json_mapping(value: object) -> Mapping[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
