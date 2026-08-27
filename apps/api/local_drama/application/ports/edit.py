from __future__ import annotations

from typing import Any, Protocol


class EpisodeEditPort(Protocol):
    def workspace(self, episode_id: str, *, history_limit: int = 20) -> dict[str, Any]: ...

    def create_draft(self, episode_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]: ...

    def freeze(self, timeline_revision_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]: ...
