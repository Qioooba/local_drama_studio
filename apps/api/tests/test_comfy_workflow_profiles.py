from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.comfy_smoke_contract import parse_comfy_smoke_contract
from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.comfy_workflow_execution import make_comfy_workflow_handler
from local_drama.model_platform.application.comfy_workflow_profiles import ComfyWorkflowProfileService
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot


class _WorkflowService:
    def get_version(self, version_id: str):
        assert version_id == "workflow-v1"
        return {
            "id": version_id,
            "status": "PUBLISHED",
            "content_hash": "a" * 64,
            "contract": {
                "capability": "IMAGE_CONCEPT",
                "input_slots": {"PROMPT": {"required": True}},
                "smoke_contract": {
                    "schema_version": "localdramastudio.comfy-smoke-contract.v1",
                    "semantic_inputs": {"PROMPT": "橙猫坐在窗边"},
                    "timeout_seconds": 90,
                    "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
                },
            },
            "node_bindings": {"PROMPT": {"node_id": "1", "input": "text"}},
            "workflow": {"1": {"class_type": "SaveImage", "inputs": {"text": "placeholder"}}},
        }

    def compile_semantic_inputs(self, version_id: str, semantic_inputs: dict[str, object]):
        workflow = self.get_version(version_id)["workflow"]
        workflow["1"]["inputs"]["text"] = semantic_inputs["PROMPT"]
        return {"workflow": workflow}


class _Comfy:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.compiled: dict[str, object] | None = None

    def queue_prompt(self, workflow, *, client_id: str):
        self.compiled = workflow
        assert client_id.startswith("local-drama-v2-")
        return {"prompt_id": "prompt-v1"}

    def wait_history(self, prompt_id: str, *, timeout_seconds: float):
        assert (prompt_id, timeout_seconds) == ("prompt-v1", 90.0)
        return {"status": "success", "history": {"outputs": {}}}

    def collect_outputs(self, history):
        assert history == {"outputs": {}}
        return [self.output]


def _prepare(database) -> None:
    workflow = _WorkflowService().get_version("workflow-v1")
    smoke_hash = parse_comfy_smoke_contract(workflow["contract"], workflow["node_bindings"]).content_hash
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'").fetchone()[0]
        connection.execute("INSERT INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at) VALUES ('node','node','Node','fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at) VALUES ('runtime','node','comfy','COMFYUI','SERVICE_MANAGED','Comfy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_installation_versions (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at) VALUES ('runtime-v1','runtime',1,'comfy.workflow.v1','v1','LOOPBACK_HTTP','{}','fingerprint','ACTIVE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at) VALUES ('family','family','Qwen Image',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_model_releases (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at) VALUES ('release','family','qwen-image-2512-q5-k-m','qwen-image','digest','MODEL_LOCK_BUNDLE',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_model_installations (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at) VALUES ('model','release','runtime-v1','qwen-image-2512-q5-k-m','READY','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_capability_offerings (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at) VALUES ('offering','model',?,'{}','SMOKE_PASSED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (capability_id,))
        connection.execute("INSERT INTO mp_runtime_model_workflow_bindings (id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,workflow_validation_id,binding_status,binding_json,created_at,updated_at) VALUES ('binding','model',?,'workflow-v1',?,'schema','SCHEMA_VALIDATED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (capability_id, "a" * 64))
        connection.execute("INSERT INTO jobs (id,type,project_id,subject_type,subject_id,subject_kind,scope_kind,scope_project_id,scope_episode_id,scope_shot_id,stage_code,state,channel,idempotency_key,input_snapshot_json,execution_profile_version_id,priority,max_attempts,next_run_at,created_at,updated_at,created_by,revision,schema_version) VALUES ('smoke-job','MODEL_PLATFORM_COMFY_SMOKE',NULL,'MODEL_PLATFORM_COMFY_SMOKE','smoke-job','MODEL_PLATFORM_COMFY_SMOKE','SYSTEM',NULL,NULL,NULL,'MODEL_PLATFORM_COMFY_SMOKE','SUCCEEDED','GPU_H3','smoke-job-key','{}',NULL,100,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')")
        connection.execute("INSERT INTO mp_comfy_capability_smoke_jobs (id,job_id,workflow_binding_id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,smoke_contract_hash,created_at) VALUES ('smoke-link','smoke-job','binding','model',?,'workflow-v1',?,?,CURRENT_TIMESTAMP)", (capability_id, "a" * 64, smoke_hash))
        result = json.dumps({"adapter": "comfy.workflow.smoke.v1", "workflow_binding_id": "binding", "workflow_version_id": "workflow-v1", "workflow_content_hash": "a" * 64, "smoke_contract_hash": smoke_hash})
        connection.execute("INSERT INTO mp_validation_runs (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at) VALUES ('source-smoke','CAPABILITY_OFFERING','offering','CAPABILITY_SMOKE','SMOKE_PASSED',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (result,))
        connection.execute("INSERT INTO mp_validation_evidence (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at) VALUES ('source-evidence','source-smoke','CAPABILITY_SMOKE',?,'{}','artifact:smoke',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", ("c" * 64,))


def test_comfy_profile_requires_artifact_backed_real_smoke_and_freezes_binding(database, workspace) -> None:
    _prepare(database)
    service = ComfyWorkflowProfileService(database, workspace, workflows=_WorkflowService())

    created = service.provision("model", "IMAGE_CONCEPT", "binding")
    smoked = service.smoke(created.profile_version_id)

    with database.connect() as connection:
        row = connection.execute("SELECT payload_json,workflow_version_id FROM mp_execution_profile_versions WHERE id=?", (created.profile_version_id,)).fetchone()
    payload = json.loads(row["payload_json"])
    assert created.created is True
    assert row["workflow_version_id"] == "workflow-v1"
    assert payload["execution_binding"] == {
        "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
        "smoke_contract_hash": parse_comfy_smoke_contract(_WorkflowService().get_version("workflow-v1")["contract"], _WorkflowService().get_version("workflow-v1")["node_bindings"]).content_hash,
        "template": "comfy.workflow.profile.v1",
        "timeout_seconds": 90,
        "workflow_binding_id": "binding",
        "workflow_content_hash": "a" * 64,
        "workflow_version_id": "workflow-v1",
    }
    assert smoked.status == "SMOKE_PASSED"

    with pytest.raises(DomainRuleError) as missing:
        service.provision("model", "IMAGE_CONCEPT", None)
    assert missing.value.code == "MP_COMFY_PROFILE_WORKFLOW_BINDING_REQUIRED"


def test_formal_comfy_handler_uses_only_frozen_workflow_binding_and_output_contract(workspace) -> None:
    source = workspace.work_root / "comfy-source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"image")
    comfy = _Comfy(source)
    snapshot = WorkerExecutionSnapshot(
        job_id="job-1", execution_snapshot_id="snapshot-1", handler_code="comfy.workflow.v2", handler_version="v1",
        capability_code="IMAGE_CONCEPT", adapter_code="comfy.workflow.v1", adapter_version="v1", runtime_fingerprint="fp",
        runtime_configuration={}, model_bindings=({"runtime_model_installation_id": "model", "model_release_code": "release", "native_locator": "qwen-image"},),
        resolved_parameters={}, semantic_inputs={"PROMPT": "夜雨中的古城"}, resource_policy={"gpu_runtime": "COMFY"}, network_policy={"mode": "LOCAL_ONLY"},
        execution_binding={"template": "comfy.workflow.profile.v1", "workflow_binding_id": "binding", "workflow_version_id": "workflow-v1", "workflow_content_hash": "a" * 64, "smoke_contract_hash": parse_comfy_smoke_contract(_WorkflowService().get_version("workflow-v1")["contract"], _WorkflowService().get_version("workflow-v1")["node_bindings"]).content_hash, "timeout_seconds": 90, "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1}},
    )

    kind, relative = make_comfy_workflow_handler(workspace, workflows=_WorkflowService(), comfy=comfy)(snapshot, workspace.work_root / "jobs" / "job-1")

    assert kind == "COMFY_OUTPUT"
    assert relative == "jobs/job-1/comfy-execution/output-1.png"
    assert (workspace.work_root / relative).read_bytes() == b"image"
    assert comfy.compiled == {"1": {"class_type": "SaveImage", "inputs": {"text": "夜雨中的古城"}}}
