"""The published Profile resource policy is the runtime GPU authority.

Covers the four audited gaps: policy-driven runtime resolution, fail-closed
contradictions, policy-derived scheduler slots, and the previously unguarded
Comfy paths that only took the L1 scheduler lease.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from local_drama.application.comfy_lab import ComfyLabService
from local_drama.application.job_resources import (
    SCHEDULER_GPU_EXCLUSIVE_RESOURCE,
    ExecutionGpuPolicy,
    GpuRuntime,
    gpu_runtime_concurrency,
    gpu_runtime_for_job,
    resolve_profile_gpu_policy,
    scheduler_resource_key,
)
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerDescriptor, ExecutionHandlerRegistry
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_RESOURCE_POLICY_ID = "gpu-policy-authority-resource-v1"
_RUNTIME_VERSION_ID = "gpu-policy-authority-runtime-v1"
_PARAMETER_CONTRACT_ID = "gpu-policy-authority-parameter-v1"
_ADAPTER_BINDING_ID = "gpu-policy-authority-binding-v1"
_OFFERING_ID = "gpu-policy-authority-offering"
_MODEL_INSTALLATION_ID = "gpu-policy-authority-model"


def _seed_profile_contracts(database, *, resource_policy: dict[str, object]) -> str:
    """Seed one EMBEDDING_TEXT profile whose resource policy is parameterized."""

    with database.transaction() as connection:
        capability_id = str(connection.execute("SELECT id FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'").fetchone()[0])
        connection.execute(
            """INSERT OR IGNORE INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES ('gpu-policy-node','gpu-policy-node','GPU Policy Node','gpu-policy-node-fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
            VALUES ('gpu-policy-runtime','gpu-policy-node','pytorch','PYTORCH_PROCESS','SERVICE_MANAGED','PyTorch',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_installation_versions
            (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
            VALUES (?, 'gpu-policy-runtime',1,'pytorch.embedding.qwen3','v1','LOCAL_PROCESS','{}','gpu-policy-runtime-fingerprint','ACTIVE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_RUNTIME_VERSION_ID,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at)
            VALUES ('gpu-policy-family','gpu-policy-family','GPU policy model',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_model_releases
            (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at)
            VALUES ('gpu-policy-release','gpu-policy-family','gpu-policy-release','gpu-policy-release','v1','TEST',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_model_installations
            (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at)
            VALUES (?, 'gpu-policy-release', ?, 'gpu-policy-native','READY','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_MODEL_INSTALLATION_ID, _RUNTIME_VERSION_ID),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_capability_offerings
            (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at)
            VALUES (?, ?, ?, '{}','SMOKE_PASSED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_OFFERING_ID, _MODEL_INSTALLATION_ID, capability_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES ('gpu-policy-smoke','CAPABILITY_OFFERING',?,'CAPABILITY_SMOKE','SMOKE_PASSED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_OFFERING_ID,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_validation_evidence
            (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
            VALUES ('gpu-policy-smoke-evidence','gpu-policy-smoke','CAPABILITY_SMOKE','gpu-policy-smoke-hash','{}',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_parameter_contract_versions
            (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
            VALUES (?,?,1,'{"type":"object","properties":{}}','{"properties":{}}','gpu-policy-parameter-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_PARAMETER_CONTRACT_ID, capability_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_adapter_binding_contract_versions
            (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
            VALUES (?,'PYTORCH_PROCESS','pytorch.embedding.qwen3',1,'{}','gpu-policy-binding-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_ADAPTER_BINDING_ID,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_resource_policy_versions
            (id,code,version_no,policy_json,content_hash,created_at,updated_at)
            VALUES (?,?,1,?, 'gpu-policy-resource-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (_RESOURCE_POLICY_ID, "gpu-policy", json.dumps(resource_policy, sort_keys=True)),
        )
    return capability_id


def _publish_policy_profile(database, *, code: str, resource_policy: dict[str, object]) -> str:
    capability_id = _seed_profile_contracts(database, resource_policy=resource_policy)
    service = ProfilePublicationService(database)
    created = service.create_candidate(
        ProfileVersionDraft(
            profile_code=code,
            profile_title=code,
            capability_definition_id=capability_id,
            runtime_installation_version_id=_RUNTIME_VERSION_ID,
            parameter_contract_version_id=_PARAMETER_CONTRACT_ID,
            adapter_binding_contract_version_id=_ADAPTER_BINDING_ID,
            resource_policy_version_id=_RESOURCE_POLICY_ID,
            payload={"runtime_model_installation_ids": [_MODEL_INSTALLATION_ID], "defaults": {}},
        )
    )
    validation = service.record_validation(
        created.profile_version_id,
        validation_kind="PROFILE_SMOKE",
        status="SMOKE_PASSED",
        result={"payload_hash": created.payload_hash, "source_capability_validation_run_id": "gpu-policy-smoke"},
        evidence={"status": "pass"},
    )
    service.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="gpu-policy-authority")
    return created.profile_version_id


def _python_handler_registry() -> ExecutionHandlerRegistry:
    return ExecutionHandlerRegistry(
        [
            ExecutionHandlerDescriptor(
                "pytorch.embedding.qwen3",
                "v1",
                "EMBEDDING_TEXT",
                frozenset({"pytorch.embedding.qwen3"}),
                "CPU",
                "PYTORCH",
            )
        ]
    )


def _project(workspace, database) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="gpu_policy_authority",
        title="GPU policy authority",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


# --- (a) policy gpu_runtime is the runtime authority -------------------------


def test_policy_gpu_runtime_wins_over_the_handler_descriptor_fallback() -> None:
    # A silent handler (no declared runtime) lets the policy choose freely.
    runtime, policy = resolve_profile_gpu_policy({"gpu_runtime": "COMFY"}, handler_gpu_runtime=None)
    assert runtime is GpuRuntime.COMFY
    assert policy.gpu_runtime is GpuRuntime.COMFY

    # A policy that agrees with the handler keeps that runtime.
    runtime, _policy = resolve_profile_gpu_policy({"gpu_runtime": "COMFY", "exclusive_gpu": True}, handler_gpu_runtime="COMFY")
    assert runtime is GpuRuntime.COMFY

    # An empty policy keeps the handler descriptor authoritative.
    runtime, policy = resolve_profile_gpu_policy({}, handler_gpu_runtime="PYTORCH")
    assert runtime is GpuRuntime.PYTORCH
    assert policy.gpu_runtime is None


def test_policy_gpu_runtime_is_frozen_into_the_submitted_job_snapshot(database) -> None:
    _publish_policy_profile(database, code="gpu-policy-comfy", resource_policy={"gpu_runtime": "COMFY", "exclusive_gpu": True})
    planner = ExecutionPlanningService(database)
    preview = planner.preview(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(),
            semantic_inputs={"document_id": "document-1"},
            run_overrides={},
        )
    )
    assert dict(preview.resource_policy)["gpu_runtime"] == "COMFY"

    service = ExecutionSubmissionService(
        database,
        ExecutionHandlerRegistry(
            [
                # A descriptor with no declared runtime proves that the frozen
                # Profile policy -- not the handler -- chose the runtime.
                ExecutionHandlerDescriptor(
                    "pytorch.embedding.qwen3",
                    "v1",
                    "EMBEDDING_TEXT",
                    frozenset({"pytorch.embedding.qwen3"}),
                    "CPU",
                )
            ]
        ),
    )
    submitted = service.submit(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(),
            semantic_inputs={"document_id": "document-1"},
            run_overrides={},
            expected_resolution_hash=preview.resolution_hash,
        ),
        "gpu-policy-submit",
    )

    snapshot = submitted.job["input_snapshot"]
    assert snapshot["scheduler_runtime"] == "COMFY"
    assert snapshot["scheduler_resource_policy"] == {
        "gpu_runtime": "COMFY",
        "exclusive_gpu": True,
        "gpu_heavy_concurrency": None,
    }
    assert gpu_runtime_for_job(submitted.job) is GpuRuntime.COMFY


# --- (b) contradictory policy fails closed with a distinct code -------------


def test_contradictory_gpu_runtime_policy_fails_closed() -> None:
    with pytest.raises(DomainRuleError) as raised:
        resolve_profile_gpu_policy({"gpu_runtime": "COMFY", "exclusive_gpu": True}, handler_gpu_runtime="OLLAMA")
    assert raised.value.code == "MP_EXECUTION_GPU_POLICY_CONFLICT"
    assert raised.value.details["policy_gpu_runtime"] == "COMFY"
    assert raised.value.details["scheduler_runtime"] == "OLLAMA"

    # exclusive_gpu without any GPU runtime is also refused.
    with pytest.raises(DomainRuleError) as exclusive:
        resolve_profile_gpu_policy({"exclusive_gpu": True}, handler_gpu_runtime=None)
    assert exclusive.value.code == "MP_EXECUTION_GPU_POLICY_EXCLUSIVE_WITHOUT_RUNTIME"

    # An unknown runtime name cannot be silently ignored.
    with pytest.raises(DomainRuleError) as unknown:
        resolve_profile_gpu_policy({"gpu_runtime": "DIRECTML"}, handler_gpu_runtime=None)
    assert unknown.value.code == "MP_EXECUTION_RESOURCE_POLICY_INVALID"

    # exclusive_gpu with a wider declared pool is contradictory.
    with pytest.raises(DomainRuleError) as widened:
        resolve_profile_gpu_policy({"gpu_runtime": "COMFY", "exclusive_gpu": True, "gpu_heavy_concurrency": 4}, handler_gpu_runtime="COMFY")
    assert widened.value.code == "MP_EXECUTION_RESOURCE_POLICY_INVALID"


def test_contradictory_policy_blocks_submission_before_the_job_exists(database) -> None:
    _publish_policy_profile(database, code="gpu-policy-contradiction", resource_policy={"gpu_runtime": "COMFY", "exclusive_gpu": True})
    planner = ExecutionPlanningService(database)
    preview = planner.preview(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(),
            semantic_inputs={"document_id": "document-1"},
            run_overrides={},
        )
    )
    service = ExecutionSubmissionService(database, _python_handler_registry())

    with pytest.raises(DomainRuleError) as raised:
        service.submit(
            ExecutionPreviewRequest(
                capability_code="EMBEDDING_TEXT",
                scope=CapabilityScopeContext(),
                semantic_inputs={"document_id": "document-1"},
                run_overrides={},
                expected_resolution_hash=preview.resolution_hash,
            ),
            "gpu-policy-contradiction-submit",
        )
    assert raised.value.code == "MP_EXECUTION_GPU_POLICY_CONFLICT"
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM jobs WHERE type='MODEL_PLATFORM_EXECUTION'").fetchone()[0]
    assert count == 0


def test_frozen_job_snapshot_contradiction_also_fails_closed() -> None:
    job = {
        "id": "contradictory-job",
        "type": "MODEL_PLATFORM_EXECUTION",
        "channel": "GPU_H3",
        "input_snapshot": {
            "scheduler_runtime": "OLLAMA",
            "scheduler_resource_policy": {"gpu_runtime": "COMFY", "exclusive_gpu": True},
        },
    }
    with pytest.raises(DomainRuleError) as raised:
        gpu_runtime_for_job(job)
    assert raised.value.code == "MP_EXECUTION_GPU_POLICY_CONFLICT"


# --- (c) policy-derived scheduler slots ------------------------------------


def _heavy_gpu_job(job_id: str, *, policy: dict[str, object] | None = None, provider: str = "OLLAMA_LOOPBACK") -> dict[str, object]:
    snapshot: dict[str, object] = {"provider": provider, "base_url": "http://127.0.0.1:11434"}
    if policy is not None:
        snapshot["scheduler_resource_policy"] = policy
    return {"id": job_id, "type": "SCRIPT_BREAKDOWN_LOCAL_LLM", "channel": "CPU", "input_snapshot": snapshot}


def test_default_and_explicit_single_concurrency_keep_one_global_gpu_slot() -> None:
    plain = _heavy_gpu_job("job-plain")
    explicit = _heavy_gpu_job("job-explicit", policy={"gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"})
    exclusive = _heavy_gpu_job("job-exclusive", policy={"exclusive_gpu": True})

    assert scheduler_resource_key(plain, "worker-a") == SCHEDULER_GPU_EXCLUSIVE_RESOURCE
    assert scheduler_resource_key(explicit, "worker-a") == SCHEDULER_GPU_EXCLUSIVE_RESOURCE
    assert scheduler_resource_key(exclusive, "worker-b") == SCHEDULER_GPU_EXCLUSIVE_RESOURCE
    assert gpu_runtime_concurrency(ExecutionGpuPolicy()) == 1
    assert gpu_runtime_concurrency(ExecutionGpuPolicy(exclusive_gpu=True, gpu_concurrency=1)) == 1


def test_two_heavy_gpu_jobs_collide_on_the_single_shared_resource_key(workspace, database) -> None:
    project_id = _project(workspace, database)
    jobs = JobService(database, workspace)
    first = jobs.create_job(
        project_id,
        "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "PROJECT",
        project_id,
        "CPU",
        {"provider": "OLLAMA_LOOPBACK", "base_url": "http://127.0.0.1:11434", "scheduler_resource_policy": {"gpu_heavy_concurrency": 1}},
        "gpu-policy-collision-llm",
        priority=1,
    )
    jobs.create_job(
        project_id,
        "GENERATION_VARIANT",
        "PROJECT",
        project_id,
        "GPU_H3",
        {"workflow_version_id": "gpu-policy-collision", "scheduler_resource_policy": {"gpu_runtime": "COMFY", "exclusive_gpu": True}},
        "gpu-policy-collision-video",
        priority=2,
    )

    claim = jobs.claim("cpu-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == first["id"]
    with database.connect() as connection:
        lease = connection.execute("SELECT resource_key FROM job_resource_leases WHERE released_at IS NULL").fetchone()
    assert lease is not None and lease["resource_key"] == SCHEDULER_GPU_EXCLUSIVE_RESOURCE
    # The second heavy-GPU job must not run concurrently with the first.
    assert jobs.claim("gpu-worker", ["GPU_H3"]) is None

    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "cpu-worker", success=True)
    assert jobs.claim("gpu-worker", ["GPU_H3"]) is not None


def test_declared_wider_pool_spreads_heavy_jobs_over_named_slots() -> None:
    policy = {"gpu_heavy_concurrency": 3}
    worker_a = [scheduler_resource_key(_heavy_gpu_job(f"slot-job-{index}", policy=policy), "worker-a") for index in range(24)]
    worker_b = [scheduler_resource_key(_heavy_gpu_job(f"slot-job-{index}", policy=policy), "worker-b") for index in range(24)]
    assert all(key.startswith(f"{SCHEDULER_GPU_EXCLUSIVE_RESOURCE}#") for key in worker_a)
    assert all(1 <= int(key.rsplit("#", 1)[1]) <= 3 for key in worker_a)
    assert len(set(worker_a)) > 1  # a wider pool really admits more than one slot
    # Slot assignment is stable and independent of the claiming worker.
    assert worker_a == worker_b
    # CPU jobs never consume a GPU slot.
    cpu_job = {"id": "cpu-job", "type": "GENERATION_VARIANT", "channel": "CPU", "input_snapshot": {}}
    assert scheduler_resource_key(cpu_job, "worker-a") == "CHANNEL:CPU:worker-a"


# --- (d) previously unguarded Comfy paths ----------------------------------


class _StubCoordinator:
    """Records coordinator protocol use without touching a real GPU."""

    def __init__(self) -> None:
        self.sessions: list[tuple[str, str, str]] = []
        self.entered = 0
        self.exited = 0

    @contextmanager
    def session(self, runtime, *, owner_kind, owner_ref, **_kwargs):
        self.sessions.append((str(runtime), owner_kind, owner_ref))
        self.entered += 1
        try:
            yield {"token": "stub-token", "runtime": str(runtime), "owner_ref": owner_ref}
        finally:
            self.exited += 1


class _StubDesignerRuntime:
    def __init__(self, coordinator: _StubCoordinator) -> None:
        self.coordinator = coordinator
        self.inside_session: list[bool] = []

    def _record(self) -> None:
        self.inside_session.append(self.coordinator.entered > self.coordinator.exited)

    def queue_prompt(self, workflow, *, client_id):
        self._record()
        return {"prompt_id": "lab-prompt-1"}

    def wait_history(self, prompt_id, *, timeout_seconds):
        self._record()
        return {"status": "success"}


def test_comfy_lab_test_run_executes_inside_a_coordinator_session(workspace, monkeypatch) -> None:
    coordinator = _StubCoordinator()
    service = ComfyLabService(workspace, gpu_coordinator=coordinator)
    designer = _StubDesignerRuntime(coordinator)
    monkeypatch.setattr(service, "_status", lambda: {"status": "RUNNING", "endpoint": "http://127.0.0.1:8199"})

    result = service.test_run({"1": {"class_type": "SaveImage", "inputs": {}}}, execute=True, client=designer)

    assert result["status"] == "PASS"
    assert coordinator.sessions == [("COMFY", "COMFY_LAB_TEST_RUN", coordinator.sessions[0][2])]
    assert coordinator.entered == coordinator.exited == 1
    # The runtime call happened while the coordinator session was held.
    assert designer.inside_session == [True, True]


def test_comfy_lab_test_run_without_a_coordinator_stays_lease_free(workspace, monkeypatch) -> None:
    coordinator = _StubCoordinator()
    service = ComfyLabService(workspace)
    designer = _StubDesignerRuntime(coordinator)
    monkeypatch.setattr(service, "_status", lambda: {"status": "RUNNING", "endpoint": "http://127.0.0.1:8199"})

    result = service.test_run({"1": {"class_type": "SaveImage", "inputs": {}}}, execute=True, client=designer)

    assert result["status"] == "PASS"
    assert coordinator.sessions == []


def test_comfy_lab_api_injects_a_real_coordinator_for_test_runs(workspace, monkeypatch) -> None:
    """The HTTP path is a GPU path: it must construct and enter the coordinator."""

    from local_drama.application import comfy_lab as comfy_lab_module

    seen: list[str] = []
    real_session = comfy_lab_module.ComfyLabService._gpu_session

    def recording_session(self, capture_id):
        seen.append(str(self.gpu_coordinator is not None))
        return real_session(self, capture_id)

    monkeypatch.setattr(comfy_lab_module.ComfyLabService, "_gpu_session", recording_session)
    monkeypatch.setattr(
        comfy_lab_module.ComfyLabService,
        "_status",
        lambda self: {"status": "RUNNING", "endpoint": "http://127.0.0.1:8199"},
    )
    monkeypatch.setattr(
        comfy_lab_module.ComfyLabService,
        "_client",
        lambda self: _StubDesignerRuntime(_StubCoordinator()),
    )
    # Replace only the coordinator's session body: the API-constructed
    # coordinator is real (no test double is injected into app state), so its
    # lease/prepare lifecycle must be observable without touching a device.
    from local_drama.application.gpu_runtime import GpuRuntimeCoordinator

    sessions: list[str] = []

    @contextmanager
    def stub_session(self, runtime, **_kwargs):
        sessions.append(str(runtime))
        yield {"token": "api-token"}

    monkeypatch.setattr(GpuRuntimeCoordinator, "session", stub_session)

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/comfy-lab/test-runs",
            json={"workflow": {"1": {"class_type": "SaveImage", "inputs": {}}}, "execute": True},
        )

    assert response.status_code == 200
    assert response.json()["test_run"]["status"] == "PASS"
    assert seen == ["True"]
    assert sessions == ["COMFY"]


def test_comfy_submit_next_uses_the_injected_coordinator_around_the_provider_handoff(workspace, database, monkeypatch) -> None:
    """`submit_next` previously only took the L1 scheduler lease."""

    from local_drama.application.comfy_jobs import ComfyGenerationService
    from local_drama.application.workflows import WorkflowService

    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "gpu-policy-comfy-submit",
        "GPU policy comfy submit",
        {"1": {"class_type": "SaveImage", "inputs": {}}},
        {},
        {},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (str(version["id"]),))
    project_id = _project(workspace, database)
    JobService(database, workspace).create_job(
        project_id,
        "GENERATION_VARIANT",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}},
        "gpu-policy-submit-next",
    )

    coordinator = _StubCoordinator()
    service = ComfyGenerationService(database, workspace, gpu_coordinator=coordinator)
    assert coordinator.entered == 0

    def queue_prompt(*_args, **_kwargs):
        assert coordinator.entered == 1 and coordinator.exited == 0, "provider handoff must hold the GPU lease"
        return {"prompt_id": "gpu-policy-prompt"}

    monkeypatch.setattr(service.comfy, "queue_prompt", queue_prompt)

    submitted = service.submit_next("gpu-worker")

    assert submitted is not None and submitted["prompt_id"] == "gpu-policy-prompt"
    assert coordinator.sessions == [("COMFY", "JOB_ATTEMPT", str(submitted["attempt"]["id"]))]
    assert coordinator.entered == coordinator.exited == 1


def test_comfy_jobs_submit_next_http_route_enters_the_coordinator(workspace, database, monkeypatch) -> None:
    """`POST /comfy/jobs:submit-next` is a GPU path and must lease the device."""

    from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
    from local_drama.application.workflows import WorkflowService
    from local_drama.infrastructure.comfy import ComfyClient

    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "gpu-policy-comfy-route",
        "GPU policy comfy route",
        {"1": {"class_type": "SaveImage", "inputs": {}}},
        {},
        {},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (str(version["id"]),))
    project_id = _project(workspace, database)
    JobService(database, workspace).create_job(
        project_id,
        "GENERATION_VARIANT",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}},
        "gpu-policy-route-submit",
    )

    state = {"entered": 0, "exited": 0}
    sessions: list[str] = []

    @contextmanager
    def stub_session(self, runtime, **_kwargs):
        sessions.append(str(runtime))
        state["entered"] += 1
        try:
            yield {"token": "route-token"}
        finally:
            state["exited"] += 1

    def queue_prompt(self, *_args, **_kwargs):
        assert state["entered"] == 1 and state["exited"] == 0, "provider handoff must hold the GPU lease"
        return {"prompt_id": "gpu-policy-route-prompt"}

    monkeypatch.setattr(GpuRuntimeCoordinator, "session", stub_session)
    monkeypatch.setattr(ComfyClient, "queue_prompt", queue_prompt)

    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/comfy/jobs:submit-next", json={"worker_id": "gpu-worker"})

    assert response.status_code == 200
    assert response.json()["submission"]["prompt_id"] == "gpu-policy-route-prompt"
    assert sessions == ["COMFY"]
    assert state == {"entered": 1, "exited": 1}


def test_comfy_submit_next_can_yield_the_session_to_its_caller(workspace, database, monkeypatch) -> None:
    """`run_once` owns the lease for the whole attempt, so it must not nest."""

    from local_drama.application.comfy_jobs import ComfyGenerationService
    from local_drama.application.workflows import WorkflowService

    workflow_service = WorkflowService(database, workspace)
    version = workflow_service.register_package(
        "gpu-policy-comfy-owner",
        "GPU policy comfy owner",
        {"1": {"class_type": "SaveImage", "inputs": {}}},
        {},
        {},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (str(version["id"]),))
    project_id = _project(workspace, database)
    JobService(database, workspace).create_job(
        project_id,
        "GENERATION_VARIANT",
        "WORKFLOW_VERSION",
        str(version["id"]),
        "GPU_H3",
        {"workflow_version_id": str(version["id"]), "semantic_inputs": {}},
        "gpu-policy-owner",
    )

    coordinator = _StubCoordinator()
    service = ComfyGenerationService(database, workspace, gpu_coordinator=coordinator)
    monkeypatch.setattr(service.comfy, "queue_prompt", lambda *_args, **_kwargs: {"prompt_id": "owner-prompt"})

    with coordinator.session(GpuRuntime.COMFY, owner_kind="JOB_ATTEMPT", owner_ref="outer"):
        submitted = service.submit_next("gpu-worker", gpu_session=False)

    assert submitted is not None
    assert [item[1] for item in coordinator.sessions] == ["JOB_ATTEMPT"]
