from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.diagnostics import GpuRuntimeStatusResponse
from local_drama.application.diagnostics import DiagnosticService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("", operation_id="getDiagnostics")
async def diagnostics(request: Request) -> dict[str, object]:
    """Blueprint-compatible read-only alias for the latest local run."""
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).latest()}


@router.post("/runs", operation_id="runDiagnostics")
async def run_diagnostics(request: Request) -> dict[str, object]:
    service = DiagnosticService(request.app.state.database, request.app.state.settings)
    return {"run": await run_in_threadpool(service.run)}


@router.get("/latest", operation_id="getLatestDiagnostics")
async def latest_diagnostics(request: Request) -> dict[str, object]:
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).latest()}


@router.get("/gpu-runtime", operation_id="getGpuRuntimeStatus", response_model=GpuRuntimeStatusResponse)
async def gpu_runtime_status(request: Request) -> dict[str, object]:
    """Return durable single-card ownership without contacting model runtimes."""

    return {
        "gpu_runtime": GpuRuntimeCoordinator(
            request.app.state.database,
            request.app.state.settings,
        ).status()
    }


@router.post("/{check_id}:dry-run-fix", operation_id="dryRunDiagnosticFix")
async def dry_run_fix(check_id: str, request: Request) -> dict[str, object]:
    try:
        return {"preview": DiagnosticService(request.app.state.database, request.app.state.settings).dry_run_fix(check_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
