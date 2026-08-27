from __future__ import annotations

import ipaddress
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from local_drama.application.diagnostics import _probe_loopback
from local_drama.application.worker_sessions import ACTIVE_SESSION_STATES, WorkerSessionService

router = APIRouter(tags=["health"])


class HealthCheck(BaseModel):
    status: str
    checks: dict[str, str]


def _writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".local-drama-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "ok"
    except OSError:
        return "not_writable"


def _client_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() in {"localhost", "testclient"}


def _client_capabilities(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "network_mode": str(settings.network_mode),
        "client_location": "SERVER_LOOPBACK" if _client_is_loopback(request) else "REMOTE_BROWSER",
        "server_file_dialogs": bool(request.app.state.platform.file_picker.available and _client_is_loopback(request)),
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


@router.get("/health/ready", response_model=HealthCheck, operation_id="healthReady")
async def ready(request: Request) -> HealthCheck:
    settings = request.app.state.settings
    database = request.app.state.database
    database_status = "not_configured_until_g2"
    if database.exists:
        try:
            with database.connect() as connection:
                version = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                database_status = "ok" if version else "migration_pending"
        except sqlite3.Error:
            database_status = "unreadable"
    checks = {
        "mode": "ok" if settings.mode == "LOCAL_ONLY" else "invalid",
        "data_root": _writable(settings.data_root),
        "projects_root": _writable(settings.projects_root),
        "work_root": _writable(settings.work_root),
        "cache_root": _writable(settings.cache_root),
        "database": database_status,
    }
    status = "HEALTHY" if all(value == "ok" for value in checks.values()) else "NOT_READY"
    return HealthCheck(status=status, checks=checks)


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
    database = request.app.state.database
    database_status = "not_configured" if not database.exists else "unreadable"
    profile_status = "not_configured"
    worker_status = "not_running"
    if database.exists:
        try:
            with database.connect() as connection:
                version = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                profiles = connection.execute("SELECT COUNT(*) FROM execution_profile_versions").fetchone()[0]
                database_status = "ok" if version else "migration_pending"
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
        except sqlite3.Error:
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
        "mode": settings.mode,
        "network_mode": str(settings.network_mode),
        "database_authority": "sqlite_wal_after_g2",
        "media_authority": "project_filesystem",
        "profile_registry": "manifest_backed_candidates_after_g3",
        "remote_provider": "disabled",
        "legacy_migration": "deferred_to_g11",
    }
