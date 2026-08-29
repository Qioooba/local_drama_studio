"""Profile lifecycle for a real-smoke-validated, immutable Comfy workflow."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.application.comfy_smoke_contract import ComfySmokeContract, parse_comfy_smoke_contract
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_TEMPLATE = "comfy.workflow.profile.v1"
_RUNTIME_KIND = "COMFYUI"
_ADAPTER_CODE = "comfy.workflow.v1"


@dataclass(frozen=True, slots=True)
class ProvisionedComfyWorkflowProfile:
    profile_version_id: str
    profile_code: str
    created: bool


@dataclass(frozen=True, slots=True)
class ComfyWorkflowProfileSmokeResult:
    validation_run_id: str
    profile_version_id: str
    status: str


class ComfyWorkflowProfileService:
    """Publish only an exact binding that already produced a real artifact.

    V2 does not turn an arbitrary graph into a production Profile.  The
    binding must have completed the durable Comfy capability-smoke Job, whose
    evidence includes a registered artifact.  The Profile then freezes that
    binding plus its one-output contract for the formal Worker handler.
    """

    def __init__(self, database: Database, settings: Settings, *, workflows: WorkflowService | None = None) -> None:
        self.database = database
        self.settings = settings
        self.workflows = workflows or WorkflowService(database, settings)

    def provision(
        self,
        runtime_model_installation_id: str,
        capability_code: str,
        workflow_binding_id: str | None,
    ) -> ProvisionedComfyWorkflowProfile:
        binding = self._binding(runtime_model_installation_id, capability_code, workflow_binding_id)
        workflow = self.workflows.get_version(str(binding["workflow_version_id"]))
        smoke = self._workflow_smoke(binding, workflow)
        self._source_smoke(binding)
        contracts = self._ensure_contracts(str(binding["capability_id"]))
        capability = str(binding["capability_code"])
        profile_code = f"comfy-{str(binding['model_release_code'])}-{capability.lower()}"
        execution_binding = {
            "template": _TEMPLATE,
            "workflow_binding_id": str(binding["id"]),
            "workflow_version_id": str(binding["workflow_version_id"]),
            "workflow_content_hash": str(binding["workflow_content_hash"]),
            "smoke_contract_hash": str(binding["smoke_contract_hash"]),
            "timeout_seconds": smoke.timeout_seconds,
            "expected_output": {"media_kind": smoke.media_kind, "min_count": 1, "max_count": 1},
        }
        payload = {
            "template": _TEMPLATE,
            "runtime_model_installation_ids": [str(binding["runtime_model_installation_id"])],
            "defaults": {},
            "allowed_override_fields": [],
            "execution_binding": execution_binding,
        }
        service = ProfilePublicationService(self.database)
        try:
            created = service.create_candidate(
                ProfileVersionDraft(
                    profile_code=profile_code,
                    profile_title=f"{binding['model_title']} · {capability}",
                    capability_definition_id=str(binding["capability_id"]),
                    runtime_installation_version_id=str(binding["runtime_version_id"]),
                    parameter_contract_version_id=contracts["parameter_contract_version_id"],
                    adapter_binding_contract_version_id=contracts["adapter_binding_contract_version_id"],
                    resource_policy_version_id=contracts["resource_policy_version_id"],
                    workflow_version_id=str(binding["workflow_version_id"]),
                    payload=payload,
                )
            )
            return ProvisionedComfyWorkflowProfile(created.profile_version_id, profile_code, True)
        except DomainRuleError as error:
            if error.code != "MP_PROFILE_PAYLOAD_ALREADY_EXISTS":
                raise
            return ProvisionedComfyWorkflowProfile(str(error.details["profile_version_id"]), profile_code, False)

    def smoke(self, profile_version_id: str) -> ComfyWorkflowProfileSmokeResult:
        profile, binding = self._profile(profile_version_id)
        workflow = self.workflows.get_version(str(binding["workflow_version_id"]))
        smoke = self._workflow_smoke(binding, workflow)
        source = self._source_smoke(binding)
        validation = ProfilePublicationService(self.database).record_validation(
            profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED",
            result={
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": source,
                "output_contract_verified": True,
            },
            evidence={
                "adapter": "comfy.workflow.v2",
                "workflow_version_id": str(binding["workflow_version_id"]),
                "workflow_content_hash": str(binding["workflow_content_hash"]),
                "smoke_contract_hash": smoke.content_hash,
                "profile_binding_verified": True,
            },
        )
        return ComfyWorkflowProfileSmokeResult(validation.validation_run_id, profile_version_id, validation.status)

    def _binding(self, runtime_model_installation_id: str, capability_code: str, workflow_binding_id: str | None) -> dict[str, Any]:
        if not workflow_binding_id or not workflow_binding_id.strip():
            raise DomainRuleError("MP_COMFY_PROFILE_WORKFLOW_BINDING_REQUIRED", "Comfy Profile 必须明确选择已真实冒烟通过的工作流绑定。")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT binding.id,binding.runtime_model_installation_id,binding.capability_definition_id AS capability_id,
                          binding.workflow_version_id,binding.workflow_content_hash,
                          installation.runtime_installation_version_id AS runtime_version_id,runtime_installation.kind AS runtime_kind,runtime_version.status AS runtime_status,
                          installation.install_state,capability.code AS capability_code,release.code AS model_release_code,family.title AS model_title,
                          (SELECT smoke.smoke_contract_hash FROM mp_comfy_capability_smoke_jobs smoke
                           WHERE smoke.workflow_binding_id=binding.id ORDER BY smoke.created_at DESC,smoke.id DESC LIMIT 1) AS smoke_contract_hash
                   FROM mp_runtime_model_workflow_bindings binding
                   JOIN mp_runtime_model_installations installation ON installation.id=binding.runtime_model_installation_id
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime_installation ON runtime_installation.id=runtime_version.runtime_installation_id
                   JOIN mp_capability_definitions capability ON capability.id=binding.capability_definition_id
                   JOIN mp_model_releases release ON release.id=installation.release_id
                   JOIN mp_model_families family ON family.id=release.family_id
                   WHERE binding.id=? AND binding.runtime_model_installation_id=? AND capability.code=?
                     AND binding.binding_status='SCHEMA_VALIDATED'""",
                (workflow_binding_id.strip(), runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if (
            row is None
            or str(row["runtime_kind"]) != _RUNTIME_KIND
            or str(row["runtime_status"]) != "ACTIVE"
            or str(row["install_state"]) != "READY"
        ):
            raise DomainRuleError("MP_COMFY_PROFILE_OFFERING_NOT_READY", "Comfy Offering、运行时和指定工作流绑定必须全部就绪。")
        return dict(row)

    def _profile(self, profile_version_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.database.connect() as connection:
            profile = connection.execute(
                """SELECT profile.id,profile.payload_json,profile.payload_hash,profile.workflow_version_id,
                          capability.code AS capability_code,runtime_installation.kind AS runtime_kind,runtime_version.status AS runtime_status
                   FROM mp_execution_profile_versions profile
                   JOIN mp_capability_definitions capability ON capability.id=profile.capability_definition_id
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=profile.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime_installation ON runtime_installation.id=runtime_version.runtime_installation_id
                   WHERE profile.id=?""",
                (profile_version_id,),
            ).fetchone()
            payload = _object(profile["payload_json"]) if profile is not None else None
            execution = _object(payload.get("execution_binding")) if payload is not None else None
            binding = connection.execute(
                """SELECT binding.id,binding.runtime_model_installation_id,binding.capability_definition_id AS capability_id,
                          binding.workflow_version_id,binding.workflow_content_hash,
                          capability.code AS capability_code
                   FROM mp_runtime_model_workflow_bindings binding
                   JOIN mp_capability_definitions capability ON capability.id=binding.capability_definition_id
                   WHERE binding.id=? AND binding.binding_status='SCHEMA_VALIDATED'""",
                (execution.get("workflow_binding_id"),),
            ).fetchone() if execution is not None else None
        binding_data = dict(binding) if binding is not None else None
        if (
            profile is None or payload is None or execution is None or binding_data is None
            or payload.get("template") != _TEMPLATE or execution.get("template") != _TEMPLATE
            or str(profile["runtime_kind"]) != _RUNTIME_KIND or str(profile["runtime_status"]) != "ACTIVE"
            or str(profile["workflow_version_id"] or "") != str(binding_data["workflow_version_id"])
            or str(profile["capability_code"]) != str(binding_data["capability_code"])
            or not _binding_matches(execution, binding_data)
        ):
            raise DomainRuleError("MP_PROFILE_SMOKE_IMPLEMENTATION_UNAVAILABLE", "该 Profile 没有可验证的冻结 Comfy 工作流实现。")
        result_binding = binding_data
        result_binding["smoke_contract_hash"] = execution["smoke_contract_hash"]
        return dict(profile), result_binding

    def _workflow_smoke(self, binding: Mapping[str, Any], workflow: Mapping[str, Any]) -> ComfySmokeContract:
        if workflow.get("status") != "PUBLISHED" or str(workflow.get("content_hash") or "") != str(binding["workflow_content_hash"]):
            raise DomainRuleError("MP_COMFY_PROFILE_WORKFLOW_STALE", "Comfy Profile 绑定的工作流版本不再可用。")
        smoke = parse_comfy_smoke_contract(workflow.get("contract", {}), workflow.get("node_bindings", {}))
        if smoke.content_hash != str(binding.get("smoke_contract_hash") or ""):
            raise DomainRuleError("MP_COMFY_PROFILE_SMOKE_CONTRACT_STALE", "Comfy Profile 的真实 smoke 合同已变化。")
        if smoke.min_count != 1 or smoke.max_count != 1:
            raise DomainRuleError("MP_COMFY_PROFILE_OUTPUT_CARDINALITY_UNSUPPORTED", "当前正式 Comfy V2 Handler 只支持单一主产物工作流。")
        return smoke

    def _source_smoke(self, binding: Mapping[str, Any]) -> str:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT run.id,run.result_json FROM mp_validation_runs run
                   JOIN mp_capability_offerings offering ON offering.id=run.target_id
                   WHERE run.target_kind='CAPABILITY_OFFERING' AND run.validation_kind='CAPABILITY_SMOKE'
                     AND run.status='SMOKE_PASSED' AND offering.runtime_model_installation_id=?
                     AND offering.capability_definition_id=?
                     AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence
                                 WHERE evidence.validation_run_id=run.id AND evidence.artifact_ref IS NOT NULL)
                   ORDER BY run.finished_at DESC,run.created_at DESC,run.id DESC""",
                (binding["runtime_model_installation_id"], binding["capability_id"]),
            ).fetchall()
        for row in rows:
            result = _object(row["result_json"])
            if (
                result.get("adapter") == "comfy.workflow.smoke.v1"
                and result.get("workflow_binding_id") == binding["id"]
                and result.get("workflow_version_id") == binding["workflow_version_id"]
                and result.get("workflow_content_hash") == binding["workflow_content_hash"]
                and result.get("smoke_contract_hash") == binding["smoke_contract_hash"]
            ):
                return str(row["id"])
        raise DomainRuleError("MP_COMFY_PROFILE_SOURCE_SMOKE_REQUIRED", "指定 Comfy 工作流绑定尚未产生带 Artifact 的真实 capability smoke 证据。")

    def _ensure_contracts(self, capability_id: str) -> dict[str, str]:
        parameter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:comfy-workflow:parameter:{capability_id}:v1"))
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:comfy-workflow:binding:v1"))
        resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:comfy-workflow:resource:v1"))
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        ui_schema: dict[str, object] = {"properties": {}}
        binding = {"adapter_code": _ADAPTER_CODE, "template": "v1", "transport": "LOOPBACK_HTTP"}
        resource = {"network_policy": {"mode": "LOCAL_ONLY"}, "gpu_runtime": "COMFY", "exclusive_gpu": True}
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO mp_parameter_contract_versions (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (parameter_id, capability_id, 1, _json(schema), _json(ui_schema), _hash(schema), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_adapter_binding_contract_versions (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (binding_id, _RUNTIME_KIND, _ADAPTER_CODE, 1, _json(binding), _hash(binding), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_resource_policy_versions (id,code,version_no,policy_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (resource_id, "comfy-workflow-local", 1, _json(resource), _hash(resource), now, now))
        return {"parameter_contract_version_id": parameter_id, "adapter_binding_contract_version_id": binding_id, "resource_policy_version_id": resource_id}


def _binding_matches(execution: Mapping[str, Any], binding: Mapping[str, Any]) -> bool:
    return (
        str(execution.get("workflow_binding_id") or "") == str(binding.get("id") or "")
        and all(str(execution.get(key) or "") == str(binding.get(key) or "") for key in ("workflow_version_id", "workflow_content_hash"))
        and isinstance(execution.get("smoke_contract_hash"), str)
        and len(str(execution["smoke_contract_hash"])) == 64
    )


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Comfy Profile 数据不是合法 JSON 对象。") from error
    if not isinstance(decoded, dict):
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Comfy Profile 数据必须是 JSON 对象。")
    return decoded


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
