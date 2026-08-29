"""Auditable V2 binding between a Comfy model Offering and an immutable workflow."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class ComfyWorkflowBinding:
    id: str
    runtime_model_installation_id: str
    capability_code: str
    workflow_version_id: str
    binding_status: str
    created: bool


@dataclass(frozen=True, slots=True)
class ComfyWorkflowBindingItem:
    id: str
    workflow_version_id: str
    binding_status: str
    capability_smoke_passed: bool


class ComfyWorkflowBindingService:
    """Creates only schema-validated V2 Comfy workflow bindings.

    The binding is intentionally below capability smoke and Profile
    publication: it proves the immutable workflow can be compiled against
    this server's Comfy node inventory, not that it generated an artifact.
    """

    def __init__(self, database: Database, settings: Settings, *, workflow_service: WorkflowService | None = None, comfy_client: ComfyClient | None = None) -> None:
        self.database = database
        self.settings = settings
        self.workflows = workflow_service or WorkflowService(database, settings)
        self.comfy = comfy_client or ComfyClient(settings.comfy_base_url, settings.comfy_output_root, allow_private_network=settings.allows_private_network)

    def bind(self, runtime_model_installation_id: str, capability_code: str, workflow_version_id: str) -> ComfyWorkflowBinding:
        candidate = self._candidate(runtime_model_installation_id, capability_code)
        workflow = self.workflows.get_version(workflow_version_id)
        if workflow["status"] != "PUBLISHED":
            raise DomainRuleError("MP_COMFY_WORKFLOW_NOT_PUBLISHED", "V2 Comfy 绑定只能引用已发布的不可变工作流版本。")
        workflow_capability = str(workflow.get("contract", {}).get("capability") or "").strip().upper()
        if workflow_capability != str(candidate["capability_code"]):
            raise DomainRuleError("MP_COMFY_WORKFLOW_CAPABILITY_MISMATCH", "工作流能力与目标 V2 Offering 不一致。")
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:comfy-binding:{runtime_model_installation_id}:{candidate['capability_id']}:{workflow_version_id}"))
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT binding_status FROM mp_runtime_model_workflow_bindings WHERE id=?",
                (binding_id,),
            ).fetchone()
        if existing is not None:
            return ComfyWorkflowBinding(
                binding_id,
                runtime_model_installation_id,
                str(candidate["capability_code"]),
                workflow_version_id,
                str(existing["binding_status"]),
                False,
            )
        validation = self.workflows.validate_against_comfy(workflow_version_id, self.comfy)
        if validation.get("status") != "PASS":
            raise DomainRuleError("MP_COMFY_WORKFLOW_SCHEMA_INVALID", "工作流未通过当前服务身份下的 Comfy 节点/输入 schema 验证。")
        now = _utc_now()
        binding_fact = {
            "validation_scope": "STRUCTURE_NODE_SCHEMA_AND_RUNTIME_LAYOUT",
            "required_node_count": len(validation.get("required_nodes") or []),
            "missing_node_count": len(validation.get("missing_nodes") or []),
            "schema_error_count": len(validation.get("schema_errors") or []),
        }
        with self.database.transaction() as connection:
            existing = connection.execute("SELECT id,binding_status FROM mp_runtime_model_workflow_bindings WHERE id=?", (binding_id,)).fetchone()
            if existing is not None:
                return ComfyWorkflowBinding(binding_id, runtime_model_installation_id, str(candidate["capability_code"]), workflow_version_id, str(existing["binding_status"]), False)
            connection.execute(
                """INSERT INTO mp_runtime_model_workflow_bindings
                (id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,workflow_validation_id,binding_status,binding_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?, 'SCHEMA_VALIDATED', ?,?,?)""",
                (binding_id, runtime_model_installation_id, candidate["capability_id"], workflow_version_id, workflow["content_hash"], validation["validation_id"], _json(binding_fact), now, now),
            )
            run_id = str(uuid.uuid4())
            result = {"workflow_version_id": workflow_version_id, "workflow_content_hash": workflow["content_hash"], **binding_fact}
            connection.execute(
                """INSERT INTO mp_validation_runs
                (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
                VALUES (?, 'RUNTIME_MODEL_WORKFLOW_BINDING', ?, 'WORKFLOW_SCHEMA_VALIDATION', 'SCHEMA_PASSED', ?, ?, ?, ?, ?)""",
                (run_id, binding_id, _json(result), now, now, now, now),
            )
            connection.execute(
                """INSERT INTO mp_validation_evidence
                (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), run_id, "WORKFLOW_SCHEMA_VALIDATION", _hash(result), _json(result), None, now, now),
            )
        return ComfyWorkflowBinding(binding_id, runtime_model_installation_id, str(candidate["capability_code"]), workflow_version_id, "SCHEMA_VALIDATED", True)

    def list(self, runtime_model_installation_id: str, capability_code: str) -> tuple[ComfyWorkflowBindingItem, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT binding.id,binding.workflow_version_id,binding.binding_status
                   FROM mp_runtime_model_workflow_bindings binding
                   JOIN mp_capability_definitions capability ON capability.id=binding.capability_definition_id
                   WHERE binding.runtime_model_installation_id=? AND capability.code=?
                   ORDER BY binding.created_at DESC,binding.id DESC""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchall()
            smoke_rows = connection.execute(
                """SELECT run.result_json FROM mp_validation_runs run
                   JOIN mp_capability_offerings offering ON offering.id=run.target_id
                   WHERE run.target_kind='CAPABILITY_OFFERING' AND run.validation_kind='CAPABILITY_SMOKE'
                     AND run.status='SMOKE_PASSED' AND offering.runtime_model_installation_id=?
                     AND offering.capability_definition_id=(SELECT id FROM mp_capability_definitions WHERE code=?)
                     AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence
                                 WHERE evidence.validation_run_id=run.id AND evidence.artifact_ref IS NOT NULL)""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchall()
        passed_binding_ids = {
            str(result.get("workflow_binding_id"))
            for row in smoke_rows
            for result in (_json_object(row["result_json"]),)
            if result.get("adapter") == "comfy.workflow.smoke.v1" and isinstance(result.get("workflow_binding_id"), str)
        }
        return tuple(
            ComfyWorkflowBindingItem(str(row["id"]), str(row["workflow_version_id"]), str(row["binding_status"]), str(row["id"]) in passed_binding_ids)
            for row in rows
        )

    def _candidate(self, runtime_model_installation_id: str, capability_code: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT installation.id,installation.install_state,runtime.kind AS runtime_kind,
                          offering.capability_definition_id AS capability_id,capability.code AS capability_code
                   FROM mp_runtime_model_installations installation
                   JOIN mp_runtime_installation_versions version ON version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=version.runtime_installation_id
                   JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE installation.id=? AND capability.code=?""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_CAPABILITY_OFFERING_NOT_FOUND", "该已登记模型没有声明请求的能力。")
        if str(row["runtime_kind"]) != "COMFYUI" or str(row["install_state"]) not in {"INTEGRITY_VERIFIED", "READY"}:
            raise DomainRuleError("MP_COMFY_WORKFLOW_BINDING_NOT_READY", "Comfy 模型安装必须先通过完整性验证，才能绑定工作流。")
        return row


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
