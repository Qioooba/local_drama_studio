"""Read boundary for the creator-facing Shot Studio aggregate."""

from __future__ import annotations

from typing import Any, Protocol


class ShotStudioReadPort(Protocol):
    def studio_facts(
        self, episode_id: str, shot_id: str, nav_radius: int = 12
    ) -> dict[str, Any]: ...

    def continuity_facts(self, shot_id: str) -> dict[str, Any]: ...
