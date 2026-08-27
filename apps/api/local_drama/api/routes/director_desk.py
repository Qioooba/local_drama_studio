from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.application.errors import api_error_from_domain
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["director-desk"])


@router.get(
    "/projects/{project_id}/episodes/{episode_id}/timeline-selections",
    operation_id="getEpisodeTimelineSelections",
)
async def get_episode_timeline_selections(
    project_id: str,
    episode_id: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, object]:
    try:
        return TimelineService(request.app.state.database, request.app.state.settings).timeline_selections(
            project_id=project_id, episode_id=episode_id, limit=limit,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error

