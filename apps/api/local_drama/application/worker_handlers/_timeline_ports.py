"""Timeline capabilities consumed by worker job handlers.

Narrow port implemented by ``application.timeline.TimelineService``; handler
modules depend on this protocol instead of constructing the service, which
keeps service construction out of extracted handlers and lets the runner wire
cancellation/progress callbacks at dispatch time.
"""

from __future__ import annotations

from typing import Any, Protocol


class EpisodeTimelineJobPort(Protocol):
    """Timeline operations required by EPISODE_COMPOSE handlers."""

    def preflight_episode_render(self, timeline_revision_id: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def render_episode(
        self,
        timeline_revision_id: str,
        *,
        force_rerender: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class SegmentedEpisodeTimelineJobPort(Protocol):
    """Timeline operations required by SEGMENTED_EPISODE_COMPOSE handlers."""

    def preflight_segmented_episode_render(
        self, timeline_revision_id: str, segments: list[dict[str, Any]]
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def render_segmented_episode(
        self,
        timeline_revision_id: str,
        segments: list[dict[str, Any]],
        *,
        force_rerender: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class VideoEnhancementJobPort(Protocol):
    """Timeline operations required by VIDEO_ENHANCEMENT handlers."""

    def plan_enhancement(
        self,
        input_media_version_id: str,
        recipe_id: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def run_enhancement(
        self,
        input_media_version_id: str,
        recipe_id: str,
        plan_hash: str,
        parameters: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...
