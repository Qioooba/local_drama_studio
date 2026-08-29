"""One-to-one handoff from durable Jobs to immutable V2 execution snapshots."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class ExecutionJobLink:
    job_id: str
    execution_snapshot_id: str
    handler_code: str
    handler_version: str


@dataclass(frozen=True, slots=True)
class WorkerExecutionSnapshot:
    job_id: str
    execution_snapshot_id: str
    handler_code: str
    handler_version: str
    capability_code: str
    adapter_code: str
    adapter_version: str
    runtime_fingerprint: str
    runtime_configuration: Mapping[str, Any]
    model_bindings: tuple[Mapping[str, str], ...]
    resolved_parameters: Mapping[str, Any]
    semantic_inputs: Mapping[str, Any]
    resource_policy: Mapping[str, Any]
    network_policy: Mapping[str, Any]
    execution_binding: Mapping[str, Any] = field(default_factory=dict)


class ExecutionJobLinkService:
    """Persists and reads the only V2 execution handoff accepted by workers."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def link(self, link: ExecutionJobLink) -> None:
        with self.database.transaction() as connection:
            self.link_in_transaction(connection, link)

    def link_in_transaction(self, connection: Any, link: ExecutionJobLink) -> None:
        if not link.handler_code.strip() or not link.handler_version.strip():
            raise DomainRuleError("MP_EXECUTION_HANDLER_REQUIRED", "V2 执行 Job 必须冻结 handler code 与版本。")
        snapshot = connection.execute("SELECT id FROM mp_execution_snapshots WHERE id=?", (link.execution_snapshot_id,)).fetchone()
        job = connection.execute("SELECT id,type FROM jobs WHERE id=?", (link.job_id,)).fetchone()
        if snapshot is None:
            raise DomainRuleError("MP_EXECUTION_SNAPSHOT_NOT_FOUND", "待关联的 V2 执行快照不存在。")
        if job is None or str(job["type"]) != "MODEL_PLATFORM_EXECUTION":
            raise DomainRuleError("MP_EXECUTION_JOB_INVALID", "V2 快照只能关联 MODEL_PLATFORM_EXECUTION Job。")
        try:
            connection.execute(
                """INSERT INTO mp_execution_job_links
                (id,job_id,execution_snapshot_id,handler_code,handler_version,created_at)
                VALUES (?,?,?,?,?,?)""",
                (str(uuid.uuid4()), link.job_id, link.execution_snapshot_id, link.handler_code, link.handler_version, _utc_now()),
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise DomainRuleError("MP_EXECUTION_JOB_ALREADY_LINKED", "Job 或快照已经关联到另一条 V2 执行记录。") from error
            raise

    def link_or_verify_in_transaction(self, connection: Any, link: ExecutionJobLink) -> bool:
        """Link once, or verify an idempotent replay froze the same handoff.

        Returns True when an existing exact link was reused.  A job key may
        never silently point at a changed snapshot or handler version.
        """
        existing = connection.execute(
            """SELECT execution_snapshot_id,handler_code,handler_version
               FROM mp_execution_job_links WHERE job_id=?""",
            (link.job_id,),
        ).fetchone()
        if existing is None:
            self.link_in_transaction(connection, link)
            return False
        if (
            str(existing["execution_snapshot_id"]) != link.execution_snapshot_id
            or str(existing["handler_code"]) != link.handler_code
            or str(existing["handler_version"]) != link.handler_version
        ):
            raise DomainRuleError(
                "MP_EXECUTION_IDEMPOTENCY_LINK_MISMATCH",
                "幂等执行请求不能将既有 Job 重新绑定到不同的快照或 handler。",
            )
        return True

    def load_for_worker(self, job_id: str) -> WorkerExecutionSnapshot:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT job.id AS job_id,link.execution_snapshot_id,link.handler_code,link.handler_version,
                          capability.code AS capability_code,snapshot.adapter_code,snapshot.adapter_version,snapshot.runtime_fingerprint,snapshot.runtime_configuration_json,snapshot.model_bindings_json,snapshot.execution_binding_json,
                          snapshot.resolved_parameters_json,snapshot.semantic_inputs_json,snapshot.resource_policy_json,snapshot.network_policy_json
                   FROM jobs job
                   JOIN mp_execution_job_links link ON link.job_id=job.id
                   JOIN mp_execution_snapshots snapshot ON snapshot.id=link.execution_snapshot_id
                   JOIN mp_capability_definitions capability ON capability.id=snapshot.capability_definition_id
                   WHERE job.id=? AND job.type='MODEL_PLATFORM_EXECUTION'""",
                (job_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_EXECUTION_JOB_LINK_NOT_FOUND", "该 Job 没有可供 Worker 使用的 V2 执行快照。")
        return WorkerExecutionSnapshot(
            job_id=str(row["job_id"]),
            execution_snapshot_id=str(row["execution_snapshot_id"]),
            handler_code=str(row["handler_code"]),
            handler_version=str(row["handler_version"]),
            capability_code=str(row["capability_code"]),
            adapter_code=str(row["adapter_code"]),
            adapter_version=str(row["adapter_version"]),
            runtime_fingerprint=str(row["runtime_fingerprint"]),
            runtime_configuration=_json_object(row["runtime_configuration_json"]),
            model_bindings=_json_bindings(row["model_bindings_json"]),
            resolved_parameters=_json_object(row["resolved_parameters_json"]),
            semantic_inputs=_json_object(row["semantic_inputs_json"]),
            resource_policy=_json_object(row["resource_policy_json"]),
            network_policy=_json_object(row["network_policy_json"]),
            execution_binding=_json_object(row["execution_binding_json"]),
        )


def _json_object(value: object) -> Mapping[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照中的 JSON 已损坏。") from error
    if not isinstance(parsed, dict):
        raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照中的 JSON 必须为对象。")
    return parsed


def _json_bindings(value: object) -> tuple[Mapping[str, str], ...]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照中的模型绑定 JSON 已损坏。") from error
    if not isinstance(parsed, list):
        raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照中的模型绑定必须是数组。")
    bindings: list[Mapping[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key].strip()
            for key in ("runtime_model_installation_id", "model_release_code", "native_locator")
        ):
            raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照包含无效模型绑定。")
        bindings.append({key: item[key] for key in ("runtime_model_installation_id", "model_release_code", "native_locator")})
    if not bindings:
        raise DomainRuleError("MP_EXECUTION_SNAPSHOT_INVALID", "冻结执行快照缺少模型绑定。")
    return tuple(bindings)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
