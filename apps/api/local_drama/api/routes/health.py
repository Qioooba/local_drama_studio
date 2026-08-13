from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

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


@router.get("/health/live", response_model=HealthCheck, operation_id="healthLive")
async def live() -> HealthCheck:
    return HealthCheck(status="HEALTHY", checks={"process": "ok", "mode": "LOCAL_ONLY"})


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
    ffmpeg = os.environ.get("LOCAL_DRAMA_FFMPEG") or shutil.which("ffmpeg")
    database = request.app.state.database
    database_status = "not_configured" if not database.exists else "unreadable"
    profile_status = "not_configured"
    if database.exists:
        try:
            with database.connect() as connection:
                version = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                profiles = connection.execute("SELECT COUNT(*) FROM execution_profile_versions").fetchone()[0]
                database_status = "ok" if version else "migration_pending"
                profile_status = "synced_candidates" if profiles else "not_synced"
        except sqlite3.Error:
            database_status = "unreadable"
    return HealthCheck(
        status="HEALTHY" if ffmpeg and database_status == "ok" else "DEGRADED",
        checks={
            "ffmpeg": "discovered" if ffmpeg else "not_found",
            "database": database_status,
            "comfy_designer": "loopback_only_not_started",
            "production_profiles": profile_status,
            "network_scope": settings.mode,
        },
    )


@router.get("/system/contract", operation_id="systemContract")
async def contract(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "mode": settings.mode,
        "database_authority": "sqlite_wal_after_g2",
        "media_authority": "project_filesystem",
        "profile_registry": "manifest_backed_candidates_after_g3",
        "remote_provider": "disabled",
        "legacy_migration": "deferred_to_g11",
    }
