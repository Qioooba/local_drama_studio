from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.diagnostics import DiagnosticService

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.post("/runs", operation_id="runDiagnostics")
async def run_diagnostics(request: Request) -> dict[str, object]:
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).run()}


@router.get("/latest", operation_id="getLatestDiagnostics")
async def latest_diagnostics(request: Request) -> dict[str, object]:
    return {"run": DiagnosticService(request.app.state.database, request.app.state.settings).latest()}
