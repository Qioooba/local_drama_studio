"""Lazy generation-matrix planning and confirmation over the persistent queue."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ExperimentService:
    LARGE_MATRIX_CONFIRMATION_THRESHOLD = 24

    def __init__(self, database: Database) -> None:
        self.database = database
        self.jobs = JobService(database)

    def create_plan(
        self,
        intent_id: str,
        title: str,
        axes: dict[str, list[Any]],
        *,
        max_parallel: int = 1,
        resource_estimate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not axes or any(not values for values in axes.values()):
            raise DomainRuleError("EXPERIMENT_AXES_REQUIRED", "实验矩阵至少需要一个非空轴")
        if max_parallel < 1 or max_parallel > 64:
            raise DomainRuleError("INVALID_EXPERIMENT_PARALLELISM", "实验 max_parallel 必须在 1—64 之间")
        cell_count = 1
        for values in axes.values():
            cell_count *= len(values)
        if cell_count > 10000:
            raise DomainRuleError("EXPERIMENT_TOO_LARGE", "实验矩阵超过 10000 个 cell，必须拆分计划")
        now = _now()
        experiment_id = str(uuid.uuid4())
        # Freeze the exact source Variant when the matrix is authored.  An
        # experiment without this anchor is only a collection of parameters;
        # resolving "the latest Variant" later would make retries and delayed
        # workers silently execute different creative inputs.
        base_variant_id: str | None = None
        estimate = resource_estimate or {"cpu_seconds_per_cell": 1, "disk_bytes_per_cell": 0, "gpu_slots": 0}
        with self.database.transaction() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id=?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在")
            base_variant = connection.execute(
                """SELECT id FROM generation_variants
                WHERE intent_id=? ORDER BY created_at DESC, variant_no DESC, id DESC LIMIT 1""",
                (intent_id,),
            ).fetchone()
            if base_variant is not None:
                base_variant_id = str(base_variant["id"])
            plan = {
                "axes": axes,
                "axis_order": list(axes),
                "cell_count": cell_count,
                "base_variant_id": base_variant_id,
            }
            connection.execute(
                """INSERT INTO generation_experiments
                (id, intent_id, title, axis_definitions_json, cell_count, max_parallel, resource_estimate_json, status, plan_hash, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, 'local-user', 1, 'v2')""",
                (experiment_id, intent_id, title, _json(plan), cell_count, max_parallel, _json(estimate), _hash(plan), now, now),
            )
        return self.get_plan(experiment_id)

    def get_plan(self, experiment_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT ge.*, gi.project_id, gi.owner_type, gi.owner_id, gi.purpose FROM generation_experiments ge JOIN generation_intents gi ON gi.id=ge.intent_id WHERE ge.id=?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("EXPERIMENT_NOT_FOUND", "GenerationExperiment 不存在")
            cells = connection.execute("SELECT * FROM experiment_cells WHERE experiment_id=? ORDER BY cell_key", (experiment_id,)).fetchall()
        return {
            **dict(row),
            "axes": json.loads(row["axis_definitions_json"]),
            "resource_estimate": json.loads(row["resource_estimate_json"]),
            "cells": [dict(cell) for cell in cells],
            "expanded_count": len(cells),
            "remaining_count": int(row["cell_count"]) - len(cells),
        }

    def estimate(self, experiment_id: str) -> dict[str, Any]:
        plan = self.get_plan(experiment_id)
        estimate = plan["resource_estimate"]
        return {
            "experiment_id": experiment_id,
            "cell_count": plan["cell_count"],
            "expanded_count": plan["expanded_count"],
            "remaining_count": plan["remaining_count"],
            "max_parallel": plan["max_parallel"],
            "estimated_cpu_seconds": int(plan["cell_count"]) * int(estimate.get("cpu_seconds_per_cell", 0)),
            "estimated_disk_bytes": int(plan["cell_count"]) * int(estimate.get("disk_bytes_per_cell", 0)),
            "gpu_slots": int(estimate.get("gpu_slots", 0)),
            "status": plan["status"],
            "plan_hash": plan["plan_hash"],
            "confirmation_required": plan["status"] == "DRAFT",
            "large_matrix_confirmation_required": int(plan["cell_count"]) > self.LARGE_MATRIX_CONFIRMATION_THRESHOLD,
            "large_matrix_confirmation_threshold": self.LARGE_MATRIX_CONFIRMATION_THRESHOLD,
            "would_create_jobs": False,
        }

    def _cell_combinations(self, plan: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        axes: dict[str, list[Any]] = plan["axes"]["axes"]
        order = list(plan["axes"]["axis_order"])
        return [
            ("|".join(f"{key}={value}" for key, value in zip(order, values, strict=True)), dict(zip(order, values, strict=True)))
            for values in itertools.product(*(axes[key] for key in order))
        ]

    def confirm(
        self,
        experiment_id: str,
        plan_hash: str,
        *,
        limit: int = 20,
        confirm_large_matrix: bool = False,
    ) -> dict[str, Any]:
        plan = self.get_plan(experiment_id)
        if plan["status"] != "DRAFT":
            raise DomainRuleError("EXPERIMENT_ALREADY_CONFIRMED", "实验已经确认；请使用 expand 继续懒展开")
        if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError(
                "EXPERIMENT_PLAN_STALE",
                "实验计划已变化，请重新读取 estimate 后确认",
                {"experiment_id": experiment_id, "current_plan_hash": str(plan["plan_hash"])},
            )
        if int(plan["cell_count"]) > self.LARGE_MATRIX_CONFIRMATION_THRESHOLD and not confirm_large_matrix:
            raise DomainRuleError(
                "EXPERIMENT_LARGE_MATRIX_CONFIRMATION_REQUIRED",
                "实验矩阵超过安全阈值，必须二次确认或拆分计划",
                {
                    "cell_count": int(plan["cell_count"]),
                    "threshold": self.LARGE_MATRIX_CONFIRMATION_THRESHOLD,
                },
            )
        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE generation_experiments SET status='CONFIRMED', updated_at=?, revision=revision+1 WHERE id=? AND status='DRAFT' AND plan_hash=?",
                (_now(), experiment_id, plan_hash),
            )
            if updated.rowcount != 1:
                raise DomainRuleError("EXPERIMENT_PLAN_STALE", "实验计划已变化，请重新读取 estimate 后确认")
        return self._expand(self.get_plan(experiment_id), limit)

    def expand(self, experiment_id: str, limit: int = 20, idempotency_prefix: str | None = None) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise DomainRuleError("INVALID_EXPERIMENT_BATCH", "experiment expand limit 必须在 1—500 之间")
        plan = self.get_plan(experiment_id)
        if plan["status"] != "CONFIRMED":
            raise DomainRuleError("EXPERIMENT_NOT_CONFIRMED", "实验尚未确认，不能创建 Job")
        return self._expand(plan, limit, idempotency_prefix)

    def _expand(self, plan: dict[str, Any], limit: int, idempotency_prefix: str | None = None) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise DomainRuleError("INVALID_EXPERIMENT_BATCH", "experiment expand limit 必须在 1—500 之间")
        experiment_id = str(plan["id"])
        existing = {str(cell["cell_key"]) for cell in plan["cells"]}
        created: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            for cell_key, values in self._cell_combinations(plan):
                if cell_key in existing or len(created) >= limit:
                    continue
                key = f"{idempotency_prefix}:{cell_key}" if idempotency_prefix else f"experiment:{experiment_id}:cell:{cell_key}"
                job = self.jobs.create_job_in_transaction(
                    connection,
                    str(plan["project_id"]),
                    "EXPERIMENT_CELL",
                    "GENERATION_INTENT",
                    str(plan["intent_id"]),
                    "CPU",
                    {
                        "experiment_id": experiment_id,
                        "cell_key": cell_key,
                        "parameters": values,
                        "base_variant_id": plan["axes"].get("base_variant_id"),
                    },
                    key,
                    max_attempts=3,
                )
                cell_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO experiment_cells (id, experiment_id, cell_key, variant_id, job_id, status) VALUES (?, ?, ?, NULL, ?, 'QUEUED')",
                    (cell_id, experiment_id, cell_key, job["id"]),
                )
                created.append({"id": cell_id, "cell_key": cell_key, "job_id": job["id"], "parameters": values})
        return {
            "experiment_id": experiment_id,
            "expanded": created,
            "expanded_count": plan["expanded_count"] + len(created),
            "remaining_count": int(plan["cell_count"]) - plan["expanded_count"] - len(created),
            "status": "CONFIRMED",
        }

    def cancel_cell(self, cell_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            cell = connection.execute("SELECT * FROM experiment_cells WHERE id=?", (cell_id,)).fetchone()
        if cell is None:
            raise DomainRuleError("EXPERIMENT_CELL_NOT_FOUND", "实验 cell 不存在")
        if cell["job_id"]:
            self.jobs.cancel(str(cell["job_id"]))
        with self.database.transaction() as connection:
            connection.execute("UPDATE experiment_cells SET status='CANCELLED', error_detail='cancelled_by_user' WHERE id=?", (cell_id,))
        return {**dict(cell), "status": "CANCELLED"}

    def cancel_remaining(self, experiment_id: str) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            experiment = connection.execute(
                """SELECT ge.*, gi.project_id FROM generation_experiments ge
                JOIN generation_intents gi ON gi.id=ge.intent_id WHERE ge.id=?""",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise DomainRuleError("EXPERIMENT_NOT_FOUND", "GenerationExperiment 不存在")
            if experiment["status"] == "COMPLETED":
                raise DomainRuleError("EXPERIMENT_NOT_CANCELLABLE", "已完成实验不能取消剩余项")
            rows = connection.execute(
                """SELECT ec.id AS cell_id, ec.status AS cell_status, j.id AS job_id, j.state AS job_state
                FROM experiment_cells ec LEFT JOIN jobs j ON j.id=ec.job_id
                WHERE ec.experiment_id=? ORDER BY ec.cell_key""",
                (experiment_id,),
            ).fetchall()
            cancelled: list[dict[str, str]] = []
            preserved: list[dict[str, str]] = []
            for row in rows:
                job_state = str(row["job_state"] or "")
                if job_state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    preserved.append({"cell_id": str(row["cell_id"]), "job_id": str(row["job_id"]), "job_state": job_state})
                    continue
                target = "CANCELLED" if job_state == "QUEUED" else "CANCEL_REQUESTED"
                if row["job_id"]:
                    connection.execute(
                        "UPDATE jobs SET state=?, cancel_requested_at=?, updated_at=?, revision=revision+1 WHERE id=?",
                        (target, now, now, row["job_id"]),
                    )
                    connection.execute(
                        "INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json) VALUES ('JOB_CANCEL_REQUESTED', ?, 'JOB', ?, ?)",
                        (experiment["project_id"], row["job_id"], _json({"state": target, "experiment_id": experiment_id})),
                    )
                connection.execute(
                    "UPDATE experiment_cells SET status=?, error_detail='experiment_cancel_remaining' WHERE id=?",
                    (target, row["cell_id"]),
                )
                cancelled.append({"cell_id": str(row["cell_id"]), "job_id": str(row["job_id"] or ""), "status": target})
            connection.execute(
                "UPDATE generation_experiments SET status='CANCELLED', updated_at=?, revision=revision+1 WHERE id=?",
                (now, experiment_id),
            )
            connection.execute(
                "INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json) VALUES ('EXPERIMENT_CANCELLED', ?, 'GENERATION_EXPERIMENT', ?, ?)",
                (
                    experiment["project_id"],
                    experiment_id,
                    _json({"cancelled_count": len(cancelled), "preserved_count": len(preserved)}),
                ),
            )
        return {
            "experiment_id": experiment_id,
            "status": "CANCELLED",
            "cancelled": cancelled,
            "preserved": preserved,
            "unexpanded_cancelled_count": int(experiment["cell_count"]) - len(rows),
        }
