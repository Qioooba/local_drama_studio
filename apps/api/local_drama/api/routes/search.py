from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.application.search import SearchService
from local_drama.infrastructure.database.search_repository import SqliteSearchRepository

router = APIRouter(prefix="/search", tags=["search"])


def service(request: Request) -> SearchService:
    return SearchService(SqliteSearchRepository(request.app.state.database))


@router.get("", operation_id="searchAll")
async def search(request: Request, q: str, project_id: str | None = None, limit: int = 50) -> dict[str, object]:
    return {"items": service(request).search(q, project_id, limit)}


@router.post(":rebuild", operation_id="rebuildSearch")
async def rebuild_search(request: Request) -> dict[str, object]:
    return {"indexed_count": service(request).rebuild()}
