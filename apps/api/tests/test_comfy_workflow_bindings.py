from __future__ import annotations

import json
from pathlib import Path

from local_drama.application.worker import LocalMediaWorker
from local_drama.model_platform.application.comfy_capability_smoke_execution import ComfyCapabilitySmokeWorker
from local_drama.model_platform.application.comfy_capability_smoke_jobs import ComfyCapabilitySmokeSubmissionService
from local_drama.model_platform.application.comfy_workflow_bindings import ComfyWorkflowBindingService


class _WorkflowService:
    def __init__(self) -> None:
        self.validated: list[str] = []

    def get_version(self, version_id: str):
        assert version_id == "workflow-version-1"
        return {
            "id": version_id,
            "status": "PUBLISHED",
            "content_hash": "a" * 64,
            "contract": {"capability": "IMAGE_CONCEPT"},
        }

    def validate_against_comfy(self, version_id: str, client):
        assert client is not None
        self.validated.append(version_id)
        return {
            "status": "PASS",
            "validation_id": "workflow-validation-1",
            "required_nodes": ["UnetLoaderGGUF", "KSampler", "SaveImage"],
            "missing_nodes": [],
            "schema_errors": [],
        }


class _SmokeWorkflowService:
    def get_version(self, version_id: str):
        assert version_id == "workflow-version-1"
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
        }

    def compile_semantic_inputs(self, version_id: str, semantic_inputs: dict[str, object]):
        assert version_id == "workflow-version-1"
        assert semantic_inputs == {"PROMPT": "橙猫坐在窗边"}
        return {"workflow": {"1": {"class_type": "SaveImage", "inputs": {"text": semantic_inputs["PROMPT"]}}}}


class _SmokeComfy:
    def __init__(self, output: Path) -> None:
        self.output = output

    def queue_prompt(self, workflow, *, client_id: str):
        assert workflow["1"]["inputs"]["text"] == "橙猫坐在窗边"
        assert client_id.startswith("local-drama-comfy-smoke-")
        return {"prompt_id": "comfy-smoke-prompt"}

    def wait_history(self, prompt_id: str, *, timeout_seconds: float):
        assert prompt_id == "comfy-smoke-prompt"
        assert timeout_seconds == 90.0
        return {"status": "success", "history": {"outputs": {}}}

    def collect_outputs(self, history):
        assert history == {"outputs": {}}
        return [self.output]

def _prepare_comfy_offering(database) -> None:
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'").fetchone()[0]
        connection.execute("INSERT INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at) VALUES ('comfy-node','comfy-node','Comfy node','fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at) VALUES ('comfy-runtime','comfy-node','comfy','COMFYUI','SERVICE_MANAGED','Comfy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_installation_versions (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at) VALUES ('comfy-runtime-v1','comfy-runtime',1,'comfy.workflow.v1','v1','LOOPBACK_HTTP','{}','fingerprint','DRAFT',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at) VALUES ('comfy-family','comfy-family','Qwen Image',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_model_releases (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at) VALUES ('comfy-release','comfy-family','comfy-release','qwen-image','digest','MODEL_LOCK_BUNDLE',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_runtime_model_installations (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at) VALUES ('comfy-model','comfy-release','comfy-runtime-v1','qwen-image-2512-q5-k-m','INTEGRITY_VERIFIED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO mp_capability_offerings (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at) VALUES ('comfy-offering','comfy-model',?,'{}','NOT_RUN',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (capability_id,))


def test_comfy_workflow_binding_requires_integrity_and_persists_safe_schema_evidence(workspace, database) -> None:
    _prepare_comfy_offering(database)
    workflows = _WorkflowService()
    service = ComfyWorkflowBindingService(database, workspace, workflow_service=workflows, comfy_client=object())

    created = service.bind("comfy-model", "IMAGE_CONCEPT", "workflow-version-1")
    replay = service.bind("comfy-model", "IMAGE_CONCEPT", "workflow-version-1")

    with database.connect() as connection:
        binding = connection.execute("SELECT binding_status,binding_json,workflow_content_hash FROM mp_runtime_model_workflow_bindings WHERE id=?", (created.id,)).fetchone()
        evidence = connection.execute(
            "SELECT payload_json FROM mp_validation_evidence evidence JOIN mp_validation_runs run ON run.id=evidence.validation_run_id WHERE run.target_id=?",
            (created.id,),
        ).fetchone()["payload_json"]
    assert created.binding_status == "SCHEMA_VALIDATED"
    assert created.created is True
    assert replay.created is False
    assert workflows.validated == ["workflow-version-1"]
    assert binding["binding_status"] == "SCHEMA_VALIDATED"
    assert json.loads(binding["binding_json"]) == {
        "missing_node_count": 0,
        "required_node_count": 3,
        "schema_error_count": 0,
        "validation_scope": "STRUCTURE_NODE_SCHEMA_AND_RUNTIME_LAYOUT",
    }
    assert binding["workflow_content_hash"] == "a" * 64
    assert "base_url" not in evidence
    assert "qwen-image-2512" not in evidence


def test_comfy_smoke_submission_freezes_binding_and_contract_into_one_gpu_job(workspace, database) -> None:
    _prepare_comfy_offering(database)
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'").fetchone()[0]
        connection.execute(
            """INSERT INTO mp_runtime_model_workflow_bindings
            (id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,workflow_validation_id,binding_status,binding_json,created_at,updated_at)
            VALUES ('binding-1','comfy-model',?,'workflow-version-1',?,'validation-1','SCHEMA_VALIDATED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id, "a" * 64),
        )
    service = ComfyCapabilitySmokeSubmissionService(database, workspace, workflows=_SmokeWorkflowService())

    submitted = service.submit("binding-1", "comfy-smoke-1")
    replay = service.submit("binding-1", "comfy-smoke-1")

    with database.connect() as connection:
        link = connection.execute(
            "SELECT workflow_binding_id,workflow_version_id,workflow_content_hash,smoke_contract_hash FROM mp_comfy_capability_smoke_jobs WHERE job_id=?",
            (submitted.job_id,),
        ).fetchone()
    job = service.jobs.get_job(submitted.job_id)
    assert submitted.capability_code == "IMAGE_CONCEPT"
    assert submitted.workflow_version_id == "workflow-version-1"
    assert replay.idempotent_replay is True
    assert job["type"] == "MODEL_PLATFORM_COMFY_SMOKE"
    assert job["channel"] == "GPU_H3"
    assert job["input_snapshot"] == {
        "schema_version": "localdramastudio.comfy-capability-smoke-job.v1",
        "workflow_binding_id": "binding-1",
        "workflow_version_id": "workflow-version-1",
        "workflow_content_hash": "a" * 64,
        "smoke_contract_hash": submitted.smoke_contract_hash,
        "semantic_inputs": {"PROMPT": "橙猫坐在窗边"},
        "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
        "timeout_seconds": 90,
        "scheduler_runtime": "COMFY",
    }
    assert dict(link) == {
        "workflow_binding_id": "binding-1",
        "workflow_version_id": "workflow-version-1",
        "workflow_content_hash": "a" * 64,
        "smoke_contract_hash": submitted.smoke_contract_hash,
    }


def test_comfy_smoke_worker_copies_output_before_artifact_backed_evidence(workspace, database) -> None:
    _prepare_comfy_offering(database)
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'").fetchone()[0]
        connection.execute(
            """INSERT INTO mp_runtime_model_workflow_bindings
            (id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,workflow_validation_id,binding_status,binding_json,created_at,updated_at)
            VALUES ('binding-1','comfy-model',?,'workflow-version-1',?,'validation-1','SCHEMA_VALIDATED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id, "a" * 64),
        )
    workflows = _SmokeWorkflowService()
    submitted = ComfyCapabilitySmokeSubmissionService(database, workspace, workflows=workflows).submit("binding-1", "comfy-smoke-worker-1")
    source = workspace.work_root / "fake-comfy-output.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake-png")
    job = ComfyCapabilitySmokeSubmissionService(database, workspace, workflows=workflows).jobs.get_job(submitted.job_id)
    execution = ComfyCapabilitySmokeWorker(database, workspace, workflows=workflows, comfy=_SmokeComfy(source)).execute(job, workspace.work_root / "jobs" / submitted.job_id)

    copied = workspace.work_root / execution.relative_path
    assert copied.read_bytes() == b"fake-png"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mp_validation_runs").fetchone()[0] == 0
    assert execution.after_artifacts_registered is not None
    execution.after_artifacts_registered(({"id": "artifact-1", "sha256": "b" * 64, "kind": "COMFY_SMOKE_OUTPUT"},))
    with database.connect() as connection:
        validation = connection.execute("SELECT status,result_json FROM mp_validation_runs").fetchone()
        offering = connection.execute("SELECT validation_status FROM mp_capability_offerings WHERE id='comfy-offering'").fetchone()
    assert validation["status"] == "SMOKE_PASSED"
    assert json.loads(validation["result_json"])["artifact_count"] == 1
    assert offering["validation_status"] == "SMOKE_PASSED"


def test_local_worker_dispatches_frozen_comfy_smoke_on_gpu_h3(workspace, database) -> None:
    _prepare_comfy_offering(database)
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'").fetchone()[0]
        connection.execute(
            """INSERT INTO mp_runtime_model_workflow_bindings
            (id,runtime_model_installation_id,capability_definition_id,workflow_version_id,workflow_content_hash,workflow_validation_id,binding_status,binding_json,created_at,updated_at)
            VALUES ('binding-1','comfy-model',?,'workflow-version-1',?,'validation-1','SCHEMA_VALIDATED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id, "a" * 64),
        )
    workflows = _SmokeWorkflowService()
    submitted = ComfyCapabilitySmokeSubmissionService(database, workspace, workflows=workflows).submit("binding-1", "comfy-smoke-dispatch-1")
    source = workspace.work_root / "fake-comfy-dispatch.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake-dispatch-png")
    worker = LocalMediaWorker(
        database,
        workspace,
        comfy_smoke_worker_factory=lambda: ComfyCapabilitySmokeWorker(database, workspace, workflows=workflows, comfy=_SmokeComfy(source)),
    )

    result = worker.run_once("comfy-smoke-worker", ["GPU_H3"])

    assert result is not None
    assert result["job"]["id"] == submitted.job_id
    assert result["artifact"]["kind"] == "COMFY_SMOKE_OUTPUT"
    assert result["result"]["job_state"] == "SUCCEEDED"
    with database.connect() as connection:
        validation = connection.execute("SELECT status FROM mp_validation_runs").fetchone()
        installation = connection.execute("SELECT install_state FROM mp_runtime_model_installations WHERE id='comfy-model'").fetchone()
    assert validation["status"] == "SMOKE_PASSED"
    assert installation["install_state"] == "READY"
