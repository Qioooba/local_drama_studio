"""Worker execution and evidence recording for frozen V2 Comfy smoke Jobs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from local_drama.application.comfy_smoke_contract import parse_comfy_smoke_contract
from local_drama.application.worker_dispatch import WorkerExecution
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.comfy_execution_support import assert_comfy_outputs, copy_comfy_outputs


class ComfyCapabilitySmokeWorker:
    """Runs only an immutable smoke contract and records success after artifacts."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        workflows: WorkflowService | None = None,
        comfy: ComfyClient | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.workflows = workflows or WorkflowService(database, settings)
        self.comfy = comfy or ComfyClient(
            settings.comfy_base_url,
            settings.comfy_output_root,
            allow_private_network=settings.allows_private_network,
        )
        self.completion = ComfyCapabilitySmokeCompletionService(database)

    def execute(self, job: dict[str, Any], output_root: Path) -> WorkerExecution:
        try:
            binding, snapshot, smoke = self._frozen_contract(job)
            compiled = self.workflows.compile_semantic_inputs(str(binding["workflow_version_id"]), dict(smoke.semantic_inputs))
            response = self.comfy.queue_prompt(compiled["workflow"], client_id=f"local-drama-comfy-smoke-{job['id']}")
            waited = self.comfy.wait_history(str(response["prompt_id"]), timeout_seconds=float(smoke.timeout_seconds))
            if str(waited.get("status")) != "success":
                raise DomainRuleError("MP_COMFY_SMOKE_EXECUTION_FAILED", "Comfy capability smoke 没有成功完成。")
            outputs = self.comfy.collect_outputs(dict(waited.get("history") or {}))
            assert_comfy_outputs(outputs, media_kind=smoke.media_kind, min_count=smoke.min_count, max_count=smoke.max_count, error_prefix="MP_COMFY_SMOKE")
            copies = copy_comfy_outputs(outputs, output_root, folder="comfy-smoke")
            relative = [path.relative_to(self.settings.work_root).as_posix() for path in copies]
            return WorkerExecution(
                "COMFY_SMOKE_OUTPUT",
                relative[0],
                additional_artifacts=tuple(("COMFY_SMOKE_OUTPUT", item) for item in relative[1:]),
                after_artifacts_registered=lambda artifacts: self.completion.record_success(str(job["id"]), artifacts),
            )
        except DomainRuleError as error:
            self.completion.record_failure(str(job.get("id") or ""), error.code)
            raise
        except (OSError, RuntimeError, ValueError) as error:
            self.completion.record_failure(str(job.get("id") or ""), "MP_COMFY_SMOKE_EXECUTION_FAILED")
            raise DomainRuleError("MP_COMFY_SMOKE_EXECUTION_FAILED", "Comfy capability smoke 本机执行失败。") from error

    def _frozen_contract(self, job: Mapping[str, Any]):
        snapshot = job.get("input_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "localdramastudio.comfy-capability-smoke-job.v1":
            raise DomainRuleError("MP_COMFY_SMOKE_SNAPSHOT_INVALID", "Comfy smoke Job 快照无效。")
        with self.database.connect() as connection:
            binding = connection.execute(
                """SELECT smoke.workflow_binding_id,smoke.workflow_version_id,smoke.workflow_content_hash,smoke.smoke_contract_hash,
                          binding.runtime_model_installation_id,binding.capability_definition_id
                   FROM mp_comfy_capability_smoke_jobs smoke
                   JOIN mp_runtime_model_workflow_bindings binding ON binding.id=smoke.workflow_binding_id
                   WHERE smoke.job_id=?""",
                (job["id"],),
            ).fetchone()
        if binding is None:
            raise DomainRuleError("MP_COMFY_SMOKE_JOB_LINK_NOT_FOUND", "Comfy smoke Job 缺少不可变绑定记录。")
        if any(str(snapshot.get(key) or "") != str(binding[key]) for key in ("workflow_binding_id", "workflow_version_id", "workflow_content_hash", "smoke_contract_hash")):
            raise DomainRuleError("MP_COMFY_SMOKE_SNAPSHOT_MISMATCH", "Comfy smoke Job 快照与持久化绑定不一致。")
        workflow = self.workflows.get_version(str(binding["workflow_version_id"]))
        if workflow["status"] != "PUBLISHED" or str(workflow["content_hash"]) != str(binding["workflow_content_hash"]):
            raise DomainRuleError("MP_COMFY_SMOKE_WORKFLOW_STALE", "Comfy smoke 引用的工作流不再是绑定时的已发布版本。")
        smoke = parse_comfy_smoke_contract(workflow["contract"], workflow["node_bindings"])
        if smoke.content_hash != str(binding["smoke_contract_hash"]):
            raise DomainRuleError("MP_COMFY_SMOKE_CONTRACT_STALE", "Comfy smoke 合同哈希与冻结绑定不一致。")
        if snapshot.get("semantic_inputs") != dict(smoke.semantic_inputs) or snapshot.get("timeout_seconds") != smoke.timeout_seconds:
            raise DomainRuleError("MP_COMFY_SMOKE_SNAPSHOT_MISMATCH", "Comfy smoke 运行输入或时限未遵守冻结合同。")
        return binding, snapshot, smoke

class ComfyCapabilitySmokeCompletionService:
    """Promote an Offering only after the queue has registered all artifacts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record_success(self, job_id: str, artifacts: Sequence[Mapping[str, Any]]) -> None:
        if not artifacts:
            raise DomainRuleError("MP_COMFY_SMOKE_ARTIFACT_REQUIRED", "Comfy smoke 成功必须至少登记一个 artifact。")
        facts = self._facts(job_id)
        result = {
            "adapter": "comfy.workflow.smoke.v1",
            "workflow_binding_id": facts["workflow_binding_id"],
            "workflow_version_id": facts["workflow_version_id"],
            "workflow_content_hash": facts["workflow_content_hash"],
            "smoke_contract_hash": facts["smoke_contract_hash"],
            "artifact_count": len(artifacts),
        }
        evidence = {
            **result,
            "artifacts": [
                {"artifact_id": str(item["id"]), "sha256": str(item["sha256"]), "kind": str(item["kind"])}
                for item in artifacts
            ],
        }
        self._record(facts, "SMOKE_PASSED", result, evidence, f"artifact:{artifacts[0]['id']}")

    def record_failure(self, job_id: str, error_code: str) -> None:
        if not job_id:
            return
        try:
            facts = self._facts(job_id)
        except DomainRuleError:
            return
        self._record(
            facts,
            "FAILED",
            {"adapter": "comfy.workflow.smoke.v1", "error_code": error_code},
            {"adapter": "comfy.workflow.smoke.v1", "error_code": error_code},
            None,
        )

    def _facts(self, job_id: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT smoke.workflow_binding_id,smoke.workflow_version_id,smoke.workflow_content_hash,smoke.smoke_contract_hash,
                          smoke.runtime_model_installation_id,smoke.capability_definition_id,
                          offering.id AS offering_id,installation.runtime_installation_version_id,capability.code AS capability_code
                   FROM mp_comfy_capability_smoke_jobs smoke
                   JOIN mp_capability_offerings offering
                     ON offering.runtime_model_installation_id=smoke.runtime_model_installation_id
                    AND offering.capability_definition_id=smoke.capability_definition_id
                   JOIN mp_runtime_model_installations installation ON installation.id=smoke.runtime_model_installation_id
                   JOIN mp_capability_definitions capability ON capability.id=smoke.capability_definition_id
                   WHERE smoke.job_id=?""",
                (job_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_COMFY_SMOKE_JOB_LINK_NOT_FOUND", "Comfy smoke Job 缺少可验证的 Offering 链接。")
        return row

    def _record(self, facts, status: str, result: Mapping[str, Any], evidence: Mapping[str, Any], artifact_ref: str | None) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            run_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_validation_runs
                (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
                VALUES (?, 'CAPABILITY_OFFERING', ?, 'CAPABILITY_SMOKE', ?, ?, ?, ?, ?, ?)""",
                (run_id, facts["offering_id"], status, _json(result), now, now, now, now),
            )
            connection.execute(
                """INSERT INTO mp_validation_evidence
                (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), run_id, "CAPABILITY_SMOKE", _hash(evidence), _json(evidence), artifact_ref, now, now),
            )
            connection.execute("UPDATE mp_capability_offerings SET validation_status=?,updated_at=? WHERE id=?", (status, now, facts["offering_id"]))
            remaining = connection.execute(
                "SELECT COUNT(*) FROM mp_capability_offerings WHERE runtime_model_installation_id=? AND validation_status!='SMOKE_PASSED'",
                (facts["runtime_model_installation_id"],),
            ).fetchone()[0]
            if status == "SMOKE_PASSED" and int(remaining) == 0:
                connection.execute("UPDATE mp_runtime_model_installations SET install_state='READY',updated_at=? WHERE id=?", (now, facts["runtime_model_installation_id"]))
                runtime_remaining = connection.execute(
                    """SELECT COUNT(*) FROM mp_capability_offerings offering
                       JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
                       WHERE installation.runtime_installation_version_id=? AND offering.validation_status!='SMOKE_PASSED'""",
                    (facts["runtime_installation_version_id"],),
                ).fetchone()[0]
                if int(runtime_remaining) == 0:
                    connection.execute("UPDATE mp_runtime_installation_versions SET status='ACTIVE',updated_at=? WHERE id=?", (now, facts["runtime_installation_version_id"]))
            elif status == "FAILED":
                connection.execute("UPDATE mp_runtime_model_installations SET install_state='VALIDATION_FAILED',updated_at=? WHERE id=?", (now, facts["runtime_model_installation_id"]))
                connection.execute("UPDATE mp_runtime_installation_versions SET status='DEGRADED',updated_at=? WHERE id=?", (now, facts["runtime_installation_version_id"]))


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
