from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from local_drama.api.contract_version import API_CONTRACT_VERSION
from local_drama.application.diagnostics import _probe_loopback
from local_drama.application.worker_sessions import ACTIVE_SESSION_STATES, WorkerSessionService
from local_drama.infrastructure.database.readiness import InitializationReport, SchemaReadiness, inspect_schema_readiness
from local_drama.infrastructure.filesystem.path_policy import client_is_server_loopback

router = APIRouter(tags=["health"])


class HealthCheck(BaseModel):
    status: str
    checks: dict[str, str]
    # Structured, machine-readable explanation of a non-healthy readiness
    # verdict.  Optional so existing complete-database responses are unchanged.
    reasons: list[str] = []
    initialization: dict[str, str] = {}


def _writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".local-drama-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "ok"
    except OSError:
        return "not_writable"


def _client_capabilities(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    loopback = client_is_server_loopback(request.client.host if request.client else None)
    return {
        "network_mode": str(settings.network_mode),
        "trusted_lan_unauthenticated": bool(settings.trusted_lan_unauthenticated),
        "security_warning": "TRUSTED_LAN_NO_LOGIN" if settings.is_lan_service else None,
        "client_location": "SERVER_LOOPBACK" if loopback else "REMOTE_BROWSER",
        "server_file_dialogs": bool(request.app.state.platform.file_picker.available and loopback),
        "browser_uploads": True,
        "browser_downloads": True,
        "model_library_roots": [str(path) for path in settings.model_library_roots],
        "upload_limits_mb": settings.uploads.model_dump(),
    }


@router.get("/health/live", response_model=HealthCheck, operation_id="healthLive")
async def live(request: Request) -> HealthCheck:
    network_mode = str(request.app.state.settings.network_mode)
    return HealthCheck(status="HEALTHY", checks={"process": "ok", "mode": network_mode, "network_mode": network_mode})


@router.get("/session/bootstrap", operation_id="bootstrapLocalSession")
async def bootstrap_local_session(request: Request) -> dict[str, object]:
    """Return the per-process token to a same-origin local client.

    No CORS allow header is emitted, so an unrelated webpage cannot read this
    response even though it may attempt a simple cross-origin GET.
    """
    return {
        "token": str(request.app.state.instance_session_token),
        "mode": str(request.app.state.settings.network_mode),
        "capabilities": _client_capabilities(request),
    }


@router.get("/system/client-capabilities", operation_id="getClientCapabilities")
async def client_capabilities(request: Request) -> dict[str, object]:
    return {"capabilities": _client_capabilities(request)}


def _schema_readiness(request: Request) -> SchemaReadiness | None:
    """Return the startup-cached schema verdict, or inspect once if uncached.

    ``ready`` must not run an expensive full-database ``integrity_check`` on
    every request.  The verdict is computed once during startup (or after
    migration/maintenance) and only re-inspected when an app was built without
    running the lifespan, which happens in a few test harnesses.
    """
    report = getattr(request.app.state, "readiness", None)
    if isinstance(report, InitializationReport):
        return report.schema
    database = getattr(request.app.state, "database", None)
    if database is None:
        return None
    try:
        return inspect_schema_readiness(database.path)
    except Exception:
        return None


_DATABASE_STATUS_BY_STATE: dict[str, str] = {
    "ready": "ok",
    "not_applicable": "schema_not_initialized",
    "no_database": "not_configured_until_g2",
    "unreadable": "unreadable",
    "schema_incomplete": "schema_incomplete",
    "revision_mismatch": "migration_pending",
}


def _database_status(schema: SchemaReadiness | None) -> str:
    if schema is None:
        return "unreadable"
    return _DATABASE_STATUS_BY_STATE.get(schema.state, "unreadable")


@router.get("/health/ready", response_model=HealthCheck, operation_id="healthReady")
async def ready(request: Request) -> JSONResponse:
    """Report business readiness, not merely process liveness.

    A database that cannot serve business requests (absent, unreadable,
    missing core tables/columns, or not at the release migration head) turns
    into an application-level readiness blocker with HTTP 503 and a structured
    reason.  ``/health/live`` keeps meaning only "the process is alive".
    """
    settings = request.app.state.settings
    report = getattr(request.app.state, "readiness", None)
    schema = _schema_readiness(request)
    checks = {
        "mode": "ok" if settings.mode == "LOCAL_ONLY" else "invalid",
        "data_root": _writable(settings.data_root),
        "projects_root": _writable(settings.projects_root),
        "work_root": _writable(settings.work_root),
        "cache_root": _writable(settings.cache_root),
        "database": _database_status(schema),
    }
    reasons: list[str] = []
    if schema is None:
        reasons.append("DATABASE_READINESS_UNKNOWN")
    elif not schema.ready:
        reasons.append(schema.reason)
    initialization: dict[str, str] = {}
    if isinstance(report, InitializationReport):
        for step in report.steps:
            initialization[step.name] = step.status
        reasons.extend(f"REQUIRED_INIT_FAILED:{name}" for name in report.blocked_by)
    healthy = all(value == "ok" for value in checks.values()) and not reasons
    payload = HealthCheck(
        status="HEALTHY" if healthy else "NOT_READY",
        checks=checks,
        reasons=reasons,
        initialization=initialization,
    )
    return JSONResponse(status_code=200 if healthy else 503, content=payload.model_dump())


@router.get("/health/dependencies", response_model=HealthCheck, operation_id="healthDependencies")
async def dependencies(request: Request) -> HealthCheck:
    settings = request.app.state.settings
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if settings.allows_private_network:
        comfy_probe, comfy_observed = _probe_loopback(
            settings.comfy_base_url,
            allow_private_network=True,
        )
    else:
        # Keep the local-only probe seam intentionally simple: tests and
        # embedded hosts have historically replaced this one-argument call.
        comfy_probe, comfy_observed = _probe_loopback(settings.comfy_base_url)
    comfy_status = "ready" if comfy_probe == "PASS" else f"blocked:{comfy_observed.get('reason', 'unavailable')}"
    database_status = "not_configured"
    profile_status = "not_configured"
    worker_status = "not_running"
    schema = _schema_readiness(request)
    if schema is not None and schema.ready and schema.database_exists:
        database = request.app.state.database
        database_status = "ok"
        try:
            with database.connect() as connection:
                profiles = connection.execute("SELECT COUNT(*) FROM execution_profile_versions").fetchone()[0]
            profile_status = "synced_candidates" if profiles else "not_synced"
            sessions = WorkerSessionService(database, settings).list_sessions(limit=20)
            active = next(
                (
                    item for item in sessions
                    if item["effective_status"] in ACTIVE_SESSION_STATES
                    and item["compatible"]
                ),
                None,
            )
            if active is not None:
                worker_status = f"ready:{','.join(active['supported_channels'])}"
        except Exception:
            database_status = "unreadable"
    dependencies_ready = (
        bool(ffmpeg)
        and database_status == "ok"
        and comfy_probe == "PASS"
        and worker_status.startswith("ready:")
    )
    return HealthCheck(
        status="HEALTHY" if dependencies_ready else "DEGRADED",
        checks={
            "ffmpeg": "discovered" if ffmpeg else "not_found",
            "database": database_status,
            "comfy_designer": comfy_status,
            "production_profiles": profile_status,
            "worker_supervisor": worker_status,
            "network_scope": str(settings.network_mode),
        },
    )


@router.get("/system/contract", operation_id="systemContract")
async def contract(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "api_contract_version": API_CONTRACT_VERSION,
        "mode": settings.mode,
        "network_mode": str(settings.network_mode),
        "database_authority": "sqlite_wal_after_g2",
        "media_authority": "project_filesystem",
        "profile_registry": "manifest_backed_candidates_after_g3",
        "remote_provider": "disabled",
        "legacy_migration": "deferred_to_g11",
    }
