from __future__ import annotations

import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes.adapters import router as adapters_router
from .api.routes.canvas import router as canvas_router
from .api.routes.capacity import router as capacity_router
from .api.routes.configuration import router as configuration_router
from .api.routes.diagnostics import router as diagnostics_router
from .api.routes.experiments import router as experiments_router
from .api.routes.gates import router as gates_router
from .api.routes.health import router as health_router
from .api.routes.imports import router as imports_router
from .api.routes.jobs import router as jobs_router
from .api.routes.llm import router as llm_router
from .api.routes.media import router as media_router
from .api.routes.production import router as production_router
from .api.routes.profiles import router as profiles_router
from .api.routes.projects import router as project_router
from .api.routes.prompts import router as prompts_router
from .api.routes.reviews import router as reviews_router
from .api.routes.search import router as search_router
from .api.routes.timeline import router as timeline_router
from .api.routes.variants import router as variants_router
from .api.routes.workflows import router as workflows_router
from .application.local_llm import LocalLLMService
from .application.profiles import ProfileService
from .application.reviews import ReviewService
from .config import Settings
from .errors import ApiError, api_error_handler
from .infrastructure.database.sqlite import Database
from .infrastructure.manifest import ManifestValidationError
from .middleware import LocalOriginMiddleware, RequestContextMiddleware


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
            app.state.llm_sync = LocalLLMService(app.state.database, settings).sync_candidate(actor="startup")
            app.state.review_templates = ReviewService(app.state.database, settings).ensure_templates(actor="startup")
        else:
            app.state.manifest_sync = None
    except (sqlite3.Error, ManifestValidationError):
        app.state.manifest_sync = None
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
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
    app.state.database = Database(resolved.database_path)
    app.state.instance_session_token = secrets.token_urlsafe(32)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(LocalOriginMiddleware, allowed_origins=resolved.allowed_origins)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(canvas_router, prefix="/api/v1")
    app.include_router(capacity_router, prefix="/api/v1")
    app.include_router(adapters_router, prefix="/api/v1")
    app.include_router(project_router, prefix="/api/v1")
    app.include_router(configuration_router, prefix="/api/v1")
    app.include_router(diagnostics_router, prefix="/api/v1")
    app.include_router(experiments_router, prefix="/api/v1")
    app.include_router(gates_router, prefix="/api/v1")
    app.include_router(imports_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(llm_router, prefix="/api/v1")
    app.include_router(media_router, prefix="/api/v1")
    app.include_router(production_router, prefix="/api/v1")
    app.include_router(profiles_router, prefix="/api/v1")
    app.include_router(prompts_router, prefix="/api/v1")
    app.include_router(reviews_router, prefix="/api/v1")
    app.include_router(workflows_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(timeline_router, prefix="/api/v1")
    app.include_router(variants_router, prefix="/api/v1")
    return app


app = create_app()
