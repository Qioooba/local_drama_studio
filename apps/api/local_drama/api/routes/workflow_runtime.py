from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Request

from local_drama.api.schemas.workflow_runtime import (
    RuntimeEnvironmentCreateRequest,
    RuntimeEnvironmentVersionRequest,
    RuntimeStartRequest,
    RuntimeStopRequest,
    WorkflowAppContractRequest,
    WorkflowRuntimeBindRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.workflow_runtime import WorkflowRuntimeService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["workflow-runtime"])
T = TypeVar("T")


def _service(request: Request) -> WorkflowRuntimeService:
    return WorkflowRuntimeService(request.app.state.database, request.app.state.settings)


def _guard(call: Callable[[], T]) -> T:
    try:
        return call()
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/runtime-environments", operation_id="listRuntimeEnvironments")
async def list_environments(request: Request) -> dict[str, object]:
    return {"items": _service(request).list_environments()}


@router.post("/runtime-environments", status_code=201, operation_id="createRuntimeEnvironment")
async def create_environment(payload: RuntimeEnvironmentCreateRequest, request: Request) -> dict[str, object]:
    return {"runtime_environment": _guard(lambda: _service(request).create_environment(payload.code, payload.title, payload.manifest))}


@router.get("/runtime-environments/{environment_id}", operation_id="getRuntimeEnvironment")
async def get_environment(environment_id: str, request: Request) -> dict[str, object]:
    return _guard(lambda: _service(request).get_environment(environment_id))


@router.post("/runtime-environments/{environment_id}/versions", status_code=201, operation_id="createRuntimeEnvironmentVersion")
async def create_environment_version(environment_id: str, payload: RuntimeEnvironmentVersionRequest, request: Request) -> dict[str, object]:
    return {"runtime_environment_version": _guard(lambda: _service(request).add_environment_version(environment_id, payload.manifest))}


@router.post("/runtime-environment-versions/{version_id}:validate", operation_id="validateRuntimeEnvironmentVersion")
async def validate_environment(version_id: str, request: Request) -> dict[str, object]:
    return {"validation": _guard(lambda: _service(request).validate_environment(version_id))}


@router.post("/runtime-environment-versions/{version_id}:publish", operation_id="publishRuntimeEnvironmentVersion")
async def publish_environment(version_id: str, request: Request) -> dict[str, object]:
    return {"runtime_environment_version": _guard(lambda: _service(request).publish_environment(version_id))}


@router.get("/runtime-environment-versions/{version_id}/instance", operation_id="getRuntimeInstanceStatus")
async def runtime_status(version_id: str, request: Request) -> dict[str, object]:
    return _guard(lambda: _service(request).runtime_status(version_id))


@router.post("/runtime-environment-versions/{version_id}/instance:start", operation_id="startRuntimeInstance")
async def runtime_start(version_id: str, payload: RuntimeStartRequest, request: Request) -> dict[str, object]:
    return _guard(lambda: _service(request).start_runtime(version_id, payload.instance_kind))


@router.post("/runtime-environment-versions/{version_id}/instance:stop", operation_id="stopRuntimeInstance")
async def runtime_stop(version_id: str, payload: RuntimeStopRequest, request: Request) -> dict[str, object]:
    return _guard(lambda: _service(request).stop_runtime(version_id, payload.expected_instance_id))


@router.post("/workflow-versions/{workflow_version_id}/app-contracts", status_code=201, operation_id="createWorkflowAppContract")
async def create_contract(workflow_version_id: str, payload: WorkflowAppContractRequest, request: Request) -> dict[str, object]:
    return {"app_contract": _guard(lambda: _service(request).create_contract(workflow_version_id, payload.capability, payload.contract, payload.bindings, payload.semantic_phases))}


@router.get("/workflow-app-contracts/{contract_id}", operation_id="getWorkflowAppContract")
async def get_contract(contract_id: str, request: Request) -> dict[str, object]:
    return {"app_contract": _guard(lambda: _service(request).get_contract(contract_id))}


@router.post("/workflow-app-contracts/{contract_id}:publish", operation_id="publishWorkflowAppContract")
async def publish_contract(contract_id: str, request: Request) -> dict[str, object]:
    return {"app_contract": _guard(lambda: _service(request).publish_contract(contract_id))}


@router.put("/workflow-versions/{workflow_version_id}/runtime-binding", operation_id="bindWorkflowRuntime")
async def bind_runtime(workflow_version_id: str, payload: WorkflowRuntimeBindRequest, request: Request) -> dict[str, object]:
    return {"binding": _guard(lambda: _service(request).bind(workflow_version_id, payload.contract_version_id, payload.runtime_environment_version_id))}
