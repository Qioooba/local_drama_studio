"""Durable submission boundary for a V2 Comfy capability smoke."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from local_drama.application.comfy_smoke_contract import parse_comfy_smoke_contract
from local_drama.application.jobs import JobService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class SubmittedComfyCapabilitySmoke:
    job_id: str
    workflow_binding_id: str
    runtime_model_installation_id: str
    capability_code: str
    workflow_version_id: str
    smoke_contract_hash: str
    idempotent_replay: bool


class ComfyCapabilitySmokeSubmissionService:
    """Freeze a schema-validated Comfy binding into one bounded GPU Job.

    This does not write CAPABILITY_SMOKE success.  Only the subsequent Worker
    can do so after an expected local artifact exists and is verified.
    """

    def __init__(self, database: Database, settings: Settings, *, workflows: WorkflowService | None = None) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.workflows = workflows or WorkflowService(database, settings)

    def submit(
        self,
        workflow_binding_id: str,
        idempotency_key: str,
        *,
        runtime_model_installation_id: str | None = None,
        capability_code: str | None = None,
    ) -> SubmittedComfyCapabilitySmoke:
        binding = self._binding(workflow_binding_id)
        if runtime_model_installation_id is not None and str(binding["runtime_model_installation_id"]) != runtime_model_installation_id:
            raise DomainRuleError("MP_COMFY_SMOKE_BINDING_TARGET_MISMATCH", "提交的工作流绑定不属于 URL 中指定的模型能力。")
        if capability_code is not None and str(binding["capability_code"]) != capability_code.strip().upper():
            raise DomainRuleError("MP_COMFY_SMOKE_BINDING_TARGET_MISMATCH", "提交的工作流绑定不属于 URL 中指定的模型能力。")
        workflow = self.workflows.get_version(str(binding["workflow_version_id"]))
        if workflow["status"] != "PUBLISHED" or str(workflow["content_hash"]) != str(binding["workflow_content_hash"]):
            raise DomainRuleError("MP_COMFY_SMOKE_WORKFLOW_STALE", "Comfy 绑定的工作流已非当前已发布不可变版本。")
        smoke = parse_comfy_smoke_contract(workflow["contract"], workflow["node_bindings"])
        snapshot = {
            "schema_version": "localdramastudio.comfy-capability-smoke-job.v1",
            "workflow_binding_id": str(binding["id"]),
            "workflow_version_id": str(binding["workflow_version_id"]),
            "workflow_content_hash": str(binding["workflow_content_hash"]),
            "smoke_contract_hash": smoke.content_hash,
            "semantic_inputs": dict(smoke.semantic_inputs),
            "expected_output": {"media_kind": smoke.media_kind, "min_count": smoke.min_count, "max_count": smoke.max_count},
            "timeout_seconds": smoke.timeout_seconds,
            "scheduler_runtime": "COMFY",
        }
        with self.database.transaction() as connection:
            job = self.jobs.create_job_in_transaction(
                connection,
                None,
                "MODEL_PLATFORM_COMFY_SMOKE",
                "RUNTIME_MODEL_WORKFLOW_BINDING",
                str(binding["id"]),
                "GPU_H3",
                snapshot,
                idempotency_key,
                max_attempts=1,
                subject_kind="RUNTIME_MODEL_WORKFLOW_BINDING",
                scope_kind="SYSTEM",
                stage_code="MODEL_PLATFORM_COMFY_SMOKE",
            )
            existing = connection.execute(
                "SELECT workflow_binding_id,smoke_contract_hash FROM mp_comfy_capability_smoke_jobs WHERE job_id=?",
                (job["id"],),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO mp_comfy_capability_smoke_jobs
                    (id,job_id,workflow_binding_id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,smoke_contract_hash,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), job["id"], binding["id"], binding["runtime_model_installation_id"], binding["capability_definition_id"],
                        binding["workflow_version_id"], binding["workflow_content_hash"], smoke.content_hash, _utc_now(),
                    ),
                )
            elif str(existing["workflow_binding_id"]) != str(binding["id"]) or str(existing["smoke_contract_hash"]) != smoke.content_hash:
                raise DomainRuleError("MP_COMFY_SMOKE_IDEMPOTENCY_MISMATCH", "同一幂等 Job 不能改写 Comfy binding 或 smoke 合同。")
        return SubmittedComfyCapabilitySmoke(
            job_id=str(job["id"]),
            workflow_binding_id=str(binding["id"]),
            runtime_model_installation_id=str(binding["runtime_model_installation_id"]),
            capability_code=str(binding["capability_code"]),
            workflow_version_id=str(binding["workflow_version_id"]),
            smoke_contract_hash=smoke.content_hash,
            idempotent_replay=bool(job.get("idempotent_replay")),
        )

    def _binding(self, workflow_binding_id: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT binding.id,binding.workflow_version_id,binding.workflow_content_hash,
                          binding.runtime_model_installation_id,binding.capability_definition_id,
                          capability.code AS capability_code
                   FROM mp_runtime_model_workflow_bindings binding
                   JOIN mp_capability_definitions capability ON capability.id=binding.capability_definition_id
                   WHERE binding.id=? AND binding.binding_status='SCHEMA_VALIDATED'""",
                (workflow_binding_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_COMFY_SMOKE_BINDING_NOT_READY", "Comfy 能力冒烟需要已完成 schema 验证的工作流绑定。")
        return row


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
