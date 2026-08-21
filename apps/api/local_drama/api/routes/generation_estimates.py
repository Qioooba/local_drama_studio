from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.generation_estimates import GenerationEstimateResponse
from local_drama.application.queries.generation_estimates import GenerationEstimateService
from local_drama.infrastructure.database.generation_estimate_repository import SqliteGenerationEstimateRepository

router = APIRouter(tags=["generation-estimates"])


@router.get("/generation-estimates", response_model=GenerationEstimateResponse, operation_id="getGenerationEstimate")
async def get_generation_estimate(
    request: Request,
    profile_version_id: str = Query(min_length=1),
    width: int | None = Query(default=None, gt=0),
    height: int | None = Query(default=None, gt=0),
    duration_seconds: float | None = Query(default=None, gt=0),
    frame_count: int | None = Query(default=None, gt=0),
    steps: int | None = Query(default=None, gt=0),
    gpu_class: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=3, le=500),
) -> dict[str, object]:
    with request.app.state.database.connect() as connection:
        connection.execute("PRAGMA query_only=ON")
        return GenerationEstimateService(SqliteGenerationEstimateRepository(connection)).estimate(
            profile_version_id=profile_version_id, width=width, height=height,
            duration_seconds=duration_seconds, frame_count=frame_count, steps=steps,
            gpu_class=gpu_class, limit=limit,
        )
