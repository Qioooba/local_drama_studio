"""Typed creator-facing Episode Production queries.

The application service deliberately knows nothing about SQLite.  Its reader
port exposes a projection built exclusively from canonical production facts.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.episode_production import EpisodeProductionReadPort


class EpisodeProductionQueryService:
    def __init__(self, reader: EpisodeProductionReadPort) -> None:
        self.reader = reader

    def overview(self, episode_id: str) -> dict[str, Any]:
        return {
            "overview": self.reader.overview_facts(episode_id),
            "read_only": True,
            "request_shape": "episode_production_overview_v2",
        }

    def shots(
        self,
        episode_id: str,
        *,
        cursor: int,
        limit: int,
        states: set[str],
    ) -> dict[str, Any]:
        facts = self.reader.shot_facts(
            episode_id,
            cursor=max(0, cursor),
            limit=max(1, min(limit, 100)),
            states=states,
        )
        return {
            **facts,
            "read_only": True,
            "request_shape": "bounded_episode_production_shots_v2",
        }

    def changes(self, episode_id: str, *, after: int, limit: int) -> dict[str, Any]:
        facts = self.reader.changes(
            episode_id,
            after=max(0, after),
            limit=max(1, min(limit, 100)),
        )
        return {
            **facts,
            "read_only": True,
            "request_shape": "bounded_episode_production_changes_v2",
        }
