from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from .api.routes.adaptation_plans import router as adaptation_plans_router
from .api.routes.adapters import router as adapters_router
from .api.routes.asset_bible import router as asset_bible_router
from .api.routes.asset_proposals import router as asset_proposals_router
from .api.routes.audio_v2 import router as audio_v2_router
from .api.routes.audit import router as audit_router
from .api.routes.automation import router as automation_router
from .api.routes.automation_workflows import router as automation_workflows_router
from .api.routes.beat_replan import router as beat_replan_router
from .api.routes.capacity import router as capacity_router
from .api.routes.character_identity_packs import router as character_identity_packs_router
from .api.routes.comfy_lab import router as comfy_lab_router
from .api.routes.configuration import router as configuration_router
from .api.routes.creative_entries import router as creative_entries_router
from .api.routes.diagnostics import router as diagnostics_router
from .api.routes.dialogue import router as dialogue_router
from .api.routes.director_desk import router as director_desk_router
from .api.routes.director_recipes import router as director_recipes_router
from .api.routes.edit_v2 import router as edit_v2_router
from .api.routes.effective_configuration import router as effective_configuration_router
from .api.routes.episode_production_v2 import router as episode_production_v2_router
from .api.routes.experiments import router as experiments_router
from .api.routes.gates import router as gates_router
from .api.routes.generation_estimates import router as generation_estimates_router
from .api.routes.generation_preferences import router as generation_preferences_router
from .api.routes.health import router as health_router
from .api.routes.imports import router as imports_router
from .api.routes.jobs import router as jobs_router
from .api.routes.llm import router as llm_router
from .api.routes.media import router as media_router
from .api.routes.model_platform_v2 import router as model_platform_v2_router
from .api.routes.one_sentence_video_runs import router as one_sentence_video_runs_router
from .api.routes.pipeline import router as pipeline_router
from .api.routes.platform import router as platform_router
from .api.routes.post_v2 import router as post_v2_router
from .api.routes.product_context_v2 import router as product_context_v2_router
from .api.routes.profiles import router as profiles_router
from .api.routes.project_packages import router as project_packages_router
from .api.routes.projects import router as project_router
from .api.routes.prompts import router as prompts_router
from .api.routes.provider_connections import router as provider_connections_router
from .api.routes.provider_events import router as provider_events_router
from .api.routes.qc_policies import router as qc_policies_router
from .api.routes.quick_generation_presets import router as quick_generation_presets_router
from .api.routes.quick_generations import output_router as quick_generation_outputs_router
from .api.routes.quick_generations import router as quick_generations_router
from .api.routes.reviews import router as reviews_router
from .api.routes.search import router as search_router
from .api.routes.shot_editing import router as shot_editing_router
from .api.routes.shot_groups import router as shot_groups_router
from .api.routes.shot_studio_v2 import router as shot_studio_v2_router
from .api.routes.story_assets import router as story_assets_router
from .api.routes.timeline import router as timeline_router
from .api.routes.variants import router as variants_router
from .api.routes.visual_labs import router as visual_labs_router
from .api.routes.workflow_runtime import router as workflow_runtime_router
from .api.routes.workflows import router as workflows_router
from .application.profiles import ProfileService
from .application.reviews import ReviewService
from .application.worker_sessions import WorkerSupervisor
from .config import Settings
from .domain.errors import DomainRuleError
from .errors import ApiError, api_error_handler, validation_error_handler
from .infrastructure.database.sqlite import Database
from .infrastructure.manifest import ManifestValidationError
from .logging_setup import configure_logging, get_logger
from .middleware import ApiContractMiddleware, LocalOriginMiddleware, RequestContextMiddleware
from .platform import create_platform_services

_LOGGER = get_logger("main")


def _start_embedded_worker(app: FastAPI, settings: Settings) -> tuple[threading.Event, threading.Thread] | None:
    """Run a durable queue consumer only for a directly launched API.

    Runtime Host keeps the production two-process topology and sets the guard
    below. A direct API process otherwise owns the worker so a creator never
    has to open a terminal to continue a queued project task.
    """
    if os.environ.get("LOCAL_DRAMA_EMBEDDED_WORKER") != "1":
        return None
    stop_requested = threading.Event()
    worker_id = f"embedded-api-{settings.instance_id}-{os.getpid()}"

    def run() -> None:
        try:
            WorkerSupervisor(app.state.database, settings).run_until_idle(
                worker_id,
                channels=list(settings.worker_channels),
                max_jobs=None,
                idle_poll_seconds=1.0,
                should_stop=stop_requested.is_set,
            )
        except BaseException:
            _LOGGER.exception("embedded_worker_stopped worker_id=%s", worker_id)

    thread = threading.Thread(target=run, name=f"local-drama-{worker_id}", daemon=True)
    thread.start()
    _LOGGER.info("embedded_worker_started worker_id=%s", worker_id)
    return stop_requested, thread


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.ensure_roots()
    app.state.database = Database(settings.database_path)
    try:
        if app.state.database.exists:
            with app.state.database.connect() as connection:
                connection.execute("SELECT 1 FROM local_runtimes LIMIT 1")
            app.state.manifest_sync = ProfileService(app.state.database, settings.manifest_path).sync_manifest(actor="startup")
            app.state.llm_sync = None
            app.state.review_templates = ReviewService(app.state.database, settings).ensure_templates(actor="startup")
        else:
            app.state.manifest_sync = None
            app.state.llm_sync = None
    except (sqlite3.Error, ManifestValidationError, DomainRuleError) as error:
        _LOGGER.error(
            "api.startup_sync_failed error_type=%s error_code=%s error=%s",
            type(error).__name__,
            getattr(error, "code", ""),
            str(error)[:300],
        )
        app.state.manifest_sync = None
        app.state.llm_sync = None
    worker = _start_embedded_worker(app, settings)
    app.state.embedded_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            stop_requested, thread = worker
            stop_requested.set()
            thread.join(timeout=5.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    configure_logging(resolved, process_role="api")
    app = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        description="LOCAL_ONLY local AI short-drama production workbench API",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.platform = create_platform_services(resolved)
    app.state.database = Database(resolved.database_path)
    app.state.instance_session_token = secrets.token_urlsafe(32)
    if resolved.is_lan_service:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Contract-Version", "X-Local-Instance-Token", "X-Request-Id", "X-Trace-Id", "Idempotency-Key"],
            expose_headers=["X-API-Contract-Version", "X-Local-Instance-Token", "ETag", "Content-Range", "Accept-Ranges", "Last-Modified", "X-Request-Id", "X-Trace-Id"],
            max_age=600,
        )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        LocalOriginMiddleware,
        allowed_origins=resolved.allowed_origins,
        allow_same_origin_writes=resolved.is_lan_service,
    )
    app.add_middleware(ApiContractMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(platform_router, prefix="/api/v1")
    app.include_router(comfy_lab_router, prefix="/api/v1")
    app.include_router(capacity_router, prefix="/api/v1")
    app.include_router(adapters_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(automation_router, prefix="/api/v1")
    app.include_router(automation_workflows_router, prefix="/api/v1")
    app.include_router(beat_replan_router, prefix="/api/v1")
    app.include_router(project_router, prefix="/api/v1")
    app.include_router(project_packages_router, prefix="/api/v1")
    app.include_router(configuration_router, prefix="/api/v1")
    app.include_router(creative_entries_router, prefix="/api/v1")
    app.include_router(diagnostics_router, prefix="/api/v1")
    app.include_router(director_desk_router, prefix="/api/v1")
    app.include_router(director_recipes_router, prefix="/api/v1")
    app.include_router(dialogue_router, prefix="/api/v1")
    app.include_router(experiments_router, prefix="/api/v1")
    app.include_router(gates_router, prefix="/api/v1")
    app.include_router(generation_preferences_router, prefix="/api/v1")
    app.include_router(generation_estimates_router, prefix="/api/v1")
    app.include_router(imports_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(llm_router, prefix="/api/v1")
    app.include_router(media_router, prefix="/api/v1")
    app.include_router(one_sentence_video_runs_router, prefix="/api/v1")
    app.include_router(pipeline_router, prefix="/api/v1")
    app.include_router(quick_generations_router, prefix="/api/v1")
    app.include_router(quick_generation_outputs_router, prefix="/api/v1")
    app.include_router(quick_generation_presets_router, prefix="/api/v1")
    app.include_router(provider_events_router, prefix="/api/v1")
    app.include_router(post_v2_router, prefix="/api/v2")
    app.include_router(audio_v2_router, prefix="/api/v2")
    app.include_router(edit_v2_router, prefix="/api/v2")
    app.include_router(profiles_router, prefix="/api/v1")
    app.include_router(provider_connections_router, prefix="/api/v1")
    app.include_router(effective_configuration_router, prefix="/api/v1")
    app.include_router(prompts_router, prefix="/api/v1")
    app.include_router(qc_policies_router, prefix="/api/v1")
    app.include_router(reviews_router, prefix="/api/v1")
    app.include_router(workflows_router, prefix="/api/v1")
    app.include_router(workflow_runtime_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(shot_editing_router, prefix="/api/v1")
    app.include_router(shot_groups_router, prefix="/api/v1")
    app.include_router(timeline_router, prefix="/api/v1")
    app.include_router(variants_router, prefix="/api/v1")
    app.include_router(visual_labs_router, prefix="/api/v1")
    app.include_router(story_assets_router, prefix="/api/v1")
    app.include_router(asset_bible_router, prefix="/api/v1")
    app.include_router(asset_proposals_router, prefix="/api/v1")
    app.include_router(character_identity_packs_router, prefix="/api/v1")
    app.include_router(product_context_v2_router, prefix="/api/v2")
    app.include_router(model_platform_v2_router, prefix="/api/v2")
    app.include_router(adaptation_plans_router, prefix="/api/v2")
    app.include_router(shot_studio_v2_router, prefix="/api/v2")
    app.include_router(episode_production_v2_router, prefix="/api/v2")
    _mount_frontend(app, resolved)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist_root = settings.resolved_frontend_dist_root
    if dist_root is None:
        return
    resolved_dist = dist_root.resolve()
    assets_root = resolved_dist / "assets"
    if assets_root.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_root), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "NOT_FOUND", "message": "接口不存在", "details": {}, "retryable": False}},
            )
        candidate = (resolved_dist / full_path).resolve()
        if candidate.is_relative_to(resolved_dist) and candidate.is_file() and not candidate.is_symlink():
            return FileResponse(candidate)
        return FileResponse(resolved_dist / "index.html")


app = create_app()
