from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.comfy_lab import (
    ComfyLabCaptureRequest,
    ComfyLabConfigureRequest,
    ComfyLabDiscoverRequest,
    ComfyLabPromoteRequest,
    ComfyLabTestRunRequest,
)
from local_drama.application.comfy_lab import ComfyLabService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["comfy-lab"])


def service(request: Request) -> ComfyLabService:
    return ComfyLabService(request.app.state.settings)


@router.get("/comfy-lab/status", operation_id="getComfyLabStatus")
async def status(request: Request) -> dict[str, object]:
    return {"status": service(request).status()}


@router.get("/comfy-lab/session", operation_id="getComfyLabSession")
async def session(request: Request) -> dict[str, object]:
    return service(request).session()


@router.post("/comfy-lab:discover", operation_id="discoverComfyLab")
async def discover(payload: ComfyLabDiscoverRequest, request: Request) -> dict[str, object]:
    try:
        return {"discovery": service(request).discover(apply=payload.apply)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/comfy-lab/configuration", operation_id="configureComfyLab")
async def configure(payload: ComfyLabConfigureRequest, request: Request) -> dict[str, object]:
    try:
        return {"configuration": service(request).configure(payload.python_path, payload.root_path, payload.port)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab:start", operation_id="startComfyLab")
async def start(request: Request) -> dict[str, object]:
    try:
        return {"status": service(request).start()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab:stop", operation_id="stopComfyLab")
async def stop(request: Request) -> dict[str, object]:
    try:
        return {"status": service(request).stop()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab:restart", operation_id="restartComfyLab")
async def restart(request: Request) -> dict[str, object]:
    try:
        return {"status": service(request).restart()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab/workflow:capture", status_code=201, operation_id="captureComfyLabWorkflow")
async def capture(payload: ComfyLabCaptureRequest, request: Request) -> dict[str, object]:
    try:
        return {"capture": service(request).capture(payload.workflow, payload.title)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/comfy-lab/captures", operation_id="listComfyLabCaptures")
async def list_captures(request: Request) -> dict[str, object]:
    return {"items": service(request).list_captures(), "runtime_contacted": False}


@router.get("/comfy-lab/captures/{capture_id}", operation_id="getComfyLabCapture")
async def get_capture(capture_id: str, request: Request) -> dict[str, object]:
    try:
        return {"capture": service(request).get_capture(capture_id), "runtime_contacted": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab/captures/{capture_id}:promote", status_code=201, operation_id="promoteComfyLabCapture")
async def promote_capture(capture_id: str, payload: ComfyLabPromoteRequest, request: Request) -> dict[str, object]:
    try:
        capture = service(request).promotable_capture(capture_id)
        contract = {**payload.contract, "source": {"kind": "COMFY_LAB_CAPTURE", "capture_id": capture_id, "content_hash": capture["content_hash"]}, "requires_explicit_validation": True, "local_only": True}
        version = WorkflowService(request.app.state.database, request.app.state.settings).register_package(
            payload.code, payload.title, capture["workflow"], contract, payload.node_bindings, payload.runtime_contract
        )
        return {"workflow_version": version, "source_capture_id": capture_id}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/comfy-lab/test-runs", operation_id="createComfyLabTestRun")
async def test_run(payload: ComfyLabTestRunRequest, request: Request) -> dict[str, object]:
    try:
        return {"test_run": service(request).test_run(payload.workflow, capture_id=payload.capture_id, execute=payload.execute)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
