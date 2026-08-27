"""Read boundary for the canonical Episode Production projection."""

from __future__ import annotations

from typing import Any, Protocol


class EpisodeProductionReadPort(Protocol):
    def overview_facts(self, episode_id: str) -> dict[str, Any]: ...

    def shot_facts(
        self,
        episode_id: str,
        *,
        cursor: int,
        limit: int,
        states: set[str],
    ) -> dict[str, Any]: ...

    def changes(
        self,
        episode_id: str,
        *,
        after: int,
        limit: int,
    ) -> dict[str, Any]: ...
