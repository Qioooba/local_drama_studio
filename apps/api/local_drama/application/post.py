"""Typed Post workspace queries."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.post import PostReadPort


class PostQueryService:
    def __init__(self, reader: PostReadPort) -> None:
        self.reader = reader

    def overview(self, episode_id: str) -> dict[str, Any]:
        return {
            "overview": self.reader.overview_facts(episode_id),
            "read_only": True,
            "request_shape": "episode_post_overview_v2",
        }

    def review_targets(
        self,
        episode_id: str,
        *,
        cursor: int,
        limit: int,
        target_kinds: set[str],
        include_resolved: bool,
    ) -> dict[str, Any]:
        facts = self.reader.review_target_facts(
            episode_id,
            cursor=max(0, cursor),
            limit=max(1, min(limit, 100)),
            target_kinds=target_kinds,
            include_resolved=include_resolved,
        )
        return {
            **facts,
            "read_only": True,
            "request_shape": "bounded_episode_review_targets_v2",
        }
