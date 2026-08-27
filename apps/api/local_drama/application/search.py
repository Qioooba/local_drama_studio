"""Application facade for bounded local entity search."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.search import SearchPort


class SearchService:
    def __init__(self, repository: SearchPort) -> None:
        self.repository = repository

    def rebuild(self) -> int:
        return self.repository.rebuild()

    def search(
        self, query: str, project_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self.repository.search(query, project_id, max(1, min(limit, 200)))
