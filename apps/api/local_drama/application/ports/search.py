"""Read/write boundary for the local search projection."""

from __future__ import annotations

from typing import Any, Protocol


class SearchPort(Protocol):
    def rebuild(self) -> int: ...

    def search(
        self, query: str, project_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...
