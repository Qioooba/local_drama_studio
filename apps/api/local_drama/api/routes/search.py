from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.read_models import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", operation_id="searchAll")
async def search(request: Request, q: str, project_id: str | None = None, limit: int = 50) -> dict[str, object]:
    return {"items": SearchService(request.app.state.database).search(q, project_id, limit)}


@router.post(":rebuild", operation_id="rebuildSearch")
async def rebuild_search(request: Request) -> dict[str, object]:
    return {"indexed_count": SearchService(request.app.state.database).rebuild()}
