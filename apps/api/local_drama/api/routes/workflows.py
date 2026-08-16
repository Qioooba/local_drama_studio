from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.workflows import (
    ComfyWorkerRequest,
    H3CandidateWorkflowRequest,
    H3I2VCandidateWorkflowRequest,
    WorkflowCompileRequest,
    WorkflowPackageRequest,
    WorkflowPublishRequest,
    WorkflowRevokeRequest,
)
from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient

router = APIRouter(tags=["workflows"])


def workflow_service(request: Request) -> WorkflowService:
    return WorkflowService(request.app.state.database, request.app.state.settings)


def comfy_client(request: Request) -> ComfyClient:
    settings = request.app.state.settings
    return ComfyClient(settings.comfy_base_url, settings.comfy_output_root)


def comfy_service(request: Request) -> ComfyGenerationService:
    return ComfyGenerationService(request.app.state.database, request.app.state.settings)


@router.post("/workflow-packages", status_code=201, operation_id="registerWorkflowPackage")
async def register_package(payload: WorkflowPackageRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow_version": workflow_service(request).register_package(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-packages:h3-candidate", status_code=201, operation_id="registerH3CandidateWorkflow")
async def register_h3_candidate(payload: H3CandidateWorkflowRequest, request: Request) -> dict[str, object]:
    try:
        workflow = H3WorkflowFactory(request.app.state.settings).build_t2va(
            payload.prompt,
            seed=payload.seed,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            filename_prefix=payload.filename_prefix,
            sigma_points=payload.sigma_points,
            acceleration=payload.acceleration,
        )
        bindings = {
            "PROMPT": {"node_id": "8", "input": "prompt"},
            "SEED": {"node_id": "5", "input": "noise_seed"},
            "FRAME_COUNT": {"node_id": "8", "input": "length"},
            "OUTPUT_PREFIX": {"node_id": "14", "input": "filename_prefix"},
        }
        contract = {"capability": "H3_T2VA_CANDIDATE", "requires_explicit_validation": True, "local_only": True}
        runtime_contract = {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK", "candidate": True}
        version = workflow_service(request).register_package(payload.code, payload.title, workflow, contract, bindings, runtime_contract)
        return {"workflow_version": version, "candidate_assets": H3WorkflowFactory(request.app.state.settings).candidate_assets()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-packages:h3-i2v-candidate", status_code=201, operation_id="registerH3I2VCandidateWorkflow")
async def register_h3_i2v_candidate(payload: H3I2VCandidateWorkflowRequest, request: Request) -> dict[str, object]:
    try:
        factory = H3WorkflowFactory(request.app.state.settings)
        workflow = factory.build_fl2va(
            payload.prompt,
            first_frame=payload.first_frame,
            seed=payload.seed,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            filename_prefix=payload.filename_prefix,
            sigma_points=payload.sigma_points,
            acceleration=payload.acceleration,
        )
        bindings = {
            "FIRST_FRAME": {"node_id": "5", "input": "image"},
            "PROMPT": {"node_id": "7", "input": "prompt"},
            "SEED": {"node_id": "8", "input": "noise_seed"},
            "FRAME_COUNT": {"node_id": "7", "input": "length"},
            "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"},
        }
        contract = {"capability": "H3_FL2VA_I2V_CANDIDATE", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}, "requires_explicit_validation": True, "local_only": True}
        runtime_contract = {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK", "candidate": True}
        version = workflow_service(request).register_package(payload.code, payload.title, workflow, contract, bindings, runtime_contract)
        return {"workflow_version": version, "candidate_assets": factory.candidate_assets()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/h3/candidate-runtime", operation_id="getH3CandidateRuntimeLayout")
async def h3_candidate_runtime(request: Request) -> dict[str, object]:
    try:
        factory = H3WorkflowFactory(request.app.state.settings)
        return {"runtime": factory.runtime_layout(), "candidate_assets": factory.candidate_assets()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/workflow-versions/{version_id}", operation_id="getWorkflowVersion")
async def get_version(version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"workflow_version": workflow_service(request).get_version(version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/workflow-versions", operation_id="listWorkflowVersions")
async def list_versions(request: Request) -> dict[str, object]:
    return {"items": workflow_service(request).list_versions(), "runtime_contacted": False}


@router.post("/workflow-versions/{version_id}:compile", operation_id="compileWorkflowInputs")
async def compile_inputs(version_id: str, payload: WorkflowCompileRequest, request: Request) -> dict[str, object]:
    try:
        return {"compiled": workflow_service(request).compile_semantic_inputs(version_id, payload.semantic_inputs)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-versions/{version_id}:validate-local", operation_id="validateWorkflowLocal")
async def validate_local(version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"validation": workflow_service(request).validate_against_comfy(version_id, comfy_client(request))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-versions/{version_id}:publish", operation_id="publishWorkflowVersion")
async def publish(version_id: str, payload: WorkflowPublishRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow_version": workflow_service(request).publish(version_id, payload.validation_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-versions/{version_id}:rollback", operation_id="rollbackWorkflowVersion")
async def rollback(version_id: str, payload: WorkflowPublishRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow_version": workflow_service(request).rollback(version_id, payload.validation_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/workflow-versions/{version_id}:revoke", operation_id="revokeWorkflowVersion")
async def revoke(version_id: str, payload: WorkflowRevokeRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow_version": workflow_service(request).revoke(version_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/comfy/system-stats", operation_id="getComfySystemStats")
async def system_stats(request: Request) -> dict[str, object]:
    try:
        return {"system": comfy_client(request).system_stats()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/comfy/queue", operation_id="getComfyQueue")
async def comfy_queue(request: Request) -> dict[str, object]:
    try:
        return {"queue": comfy_client(request).queue()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy:interrupt", operation_id="interruptComfy")
async def interrupt(request: Request) -> dict[str, object]:
    try:
        return {"result": comfy_client(request).interrupt()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy/jobs:submit-next", operation_id="submitNextComfyJob")
async def submit_next(payload: ComfyWorkerRequest, request: Request) -> dict[str, object]:
    try:
        return {"submission": comfy_service(request).submit_next(payload.worker_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy/attempts/{attempt_id}:poll", operation_id="pollComfyAttempt")
async def poll_attempt(attempt_id: str, payload: ComfyWorkerRequest, request: Request) -> dict[str, object]:
    try:
        return {"poll": comfy_service(request).poll_attempt(attempt_id, payload.worker_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy/attempts/{attempt_id}:interrupt", operation_id="interruptComfyAttempt")
async def interrupt_attempt(attempt_id: str, payload: ComfyWorkerRequest, request: Request) -> dict[str, object]:
    try:
        return {"result": comfy_service(request).interrupt_attempt(attempt_id, payload.worker_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy/attempts/{attempt_id}:recover", operation_id="recoverComfyAttempt")
async def recover_attempt(attempt_id: str, payload: ComfyWorkerRequest, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.connect() as connection:
            row = connection.execute("SELECT provider_job_id FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None or not row["provider_job_id"]:
            raise DomainRuleError("PROVIDER_JOB_ID_REQUIRED", "Attempt 没有可恢复的 provider_job_id")
        return {"result": comfy_service(request).recover_attempt(attempt_id, str(row["provider_job_id"]))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
