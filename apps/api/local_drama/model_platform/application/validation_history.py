"""Safe, immutable validation-history read model for Model Platform V2.

The Model Center needs to explain *why* an installation is not assignable
without turning validation evidence into another runtime-configuration API.
This projection returns the facts that an operator needs to audit the state
machine, and deliberately omits every validation payload, path, endpoint,
credential and native locator.
"""

from __future__ import annotations

from dataclasses import dataclass

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class ValidationHistoryItem:
    validation_run_id: str
    target: str
    capability_code: str | None
    validation_kind: str
    status: str
    occurred_at: str


class ValidationHistoryService:
    """Read append-only V2 validation facts for one registered installation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list_for_installation(
        self,
        runtime_model_installation_id: str,
        *,
        limit: int = 20,
    ) -> tuple[ValidationHistoryItem, ...]:
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM mp_runtime_model_installations WHERE id=?",
                (runtime_model_installation_id,),
            ).fetchone()
            if exists is None:
                raise DomainRuleError("MP_RUNTIME_MODEL_INSTALLATION_NOT_FOUND", "指定的 V2 模型安装不存在。")
            rows = connection.execute(
                """SELECT * FROM (
                    SELECT run.id AS validation_run_id,
                           'INSTALLATION' AS target,
                           NULL AS capability_code,
                           run.validation_kind,
                           run.status,
                           COALESCE(run.finished_at, run.started_at, run.created_at) AS occurred_at
                    FROM mp_validation_runs run
                    WHERE run.target_kind='RUNTIME_MODEL_INSTALLATION'
                      AND run.target_id=?
                    UNION ALL
                    SELECT run.id AS validation_run_id,
                           'CAPABILITY' AS target,
                           capability.code AS capability_code,
                           run.validation_kind,
                           run.status,
                           COALESCE(run.finished_at, run.started_at, run.created_at) AS occurred_at
                    FROM mp_validation_runs run
                    JOIN mp_capability_offerings offering
                      ON run.target_kind='CAPABILITY_OFFERING' AND run.target_id=offering.id
                    JOIN mp_capability_definitions capability
                      ON capability.id=offering.capability_definition_id
                    WHERE offering.runtime_model_installation_id=?
                ) history
                ORDER BY occurred_at DESC, validation_run_id DESC
                LIMIT ?""",
                (runtime_model_installation_id, runtime_model_installation_id, limit),
            ).fetchall()
        return tuple(
            ValidationHistoryItem(
                validation_run_id=str(row["validation_run_id"]),
                target=str(row["target"]),
                capability_code=str(row["capability_code"]) if row["capability_code"] is not None else None,
                validation_kind=str(row["validation_kind"]),
                status=str(row["status"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in rows
        )
