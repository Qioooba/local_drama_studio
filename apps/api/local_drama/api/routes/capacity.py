from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.capacity import CapacitySnapshotService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["capacity"])


@router.get("/capacity/snapshot", operation_id="getCapacitySnapshot")
async def get_capacity_snapshot(request: Request, project_id: str | None = None) -> dict[str, object]:
    try:
        return {"snapshot": CapacitySnapshotService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
