"""Read boundary for creator-facing Post and Review projections."""

from __future__ import annotations

from typing import Any, Protocol


class PostReadPort(Protocol):
    def overview_facts(self, episode_id: str) -> dict[str, Any]: ...

    def review_target_facts(
        self,
        episode_id: str,
        *,
        cursor: int,
        limit: int,
        target_kinds: set[str],
        include_resolved: bool,
    ) -> dict[str, Any]: ...
