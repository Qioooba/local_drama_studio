"""Post / Edit query and immutable command boundary."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.edit import EpisodeEditPort


class EpisodeEditService:
    def __init__(self, repository: EpisodeEditPort) -> None:
        self.repository = repository

    def workspace(self, episode_id: str, *, history_limit: int = 20) -> dict[str, Any]:
        return {
            "workspace": self.repository.workspace(episode_id, history_limit=history_limit),
            "read_only": True,
            "request_shape": "episode_edit_workspace_v2",
        }

    def create_draft(self, episode_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"timeline": self.repository.create_draft(episode_id, command, actor="local-user")}

    def freeze(self, timeline_revision_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"timeline": self.repository.freeze(timeline_revision_id, command, actor="local-user")}
