from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.comfy_lab import ComfyLabCaptureRequest, ComfyLabConfigureRequest, ComfyLabDiscoverRequest, ComfyLabTestRunRequest
from local_drama.application.comfy_lab import ComfyLabService
from local_drama.application.errors import api_error_from_domain
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


@router.post("/comfy-lab/test-runs", operation_id="createComfyLabTestRun")
async def test_run(payload: ComfyLabTestRunRequest, request: Request) -> dict[str, object]:
    try:
        return {"test_run": service(request).test_run(payload.workflow, execute=payload.execute)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
