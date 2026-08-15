from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.diagnostics import DiagnosticService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("", operation_id="getDiagnostics")
async def diagnostics(request: Request) -> dict[str, object]:
    """Blueprint-compatible read-only alias for the latest local run."""
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).latest()}


@router.post("/runs", operation_id="runDiagnostics")
async def run_diagnostics(request: Request) -> dict[str, object]:
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).run()}


@router.get("/latest", operation_id="getLatestDiagnostics")
async def latest_diagnostics(request: Request) -> dict[str, object]:
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).latest()}


@router.post("/{check_id}:dry-run-fix", operation_id="dryRunDiagnosticFix")
async def dry_run_fix(check_id: str, request: Request) -> dict[str, object]:
    try:
        return {"preview": DiagnosticService(request.app.state.database, request.app.state.settings).dry_run_fix(check_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
