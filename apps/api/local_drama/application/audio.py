"""Post / Audio query and command boundary."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.audio import AudioWorkspacePort


class AudioWorkspaceService:
    def __init__(self, repository: AudioWorkspacePort) -> None:
        self.repository = repository

    def workspace(self, episode_id: str) -> dict[str, Any]:
        return {"workspace": self.repository.workspace(episode_id), "read_only": True, "request_shape": "episode_audio_workspace_v2"}

    def create_track(self, episode_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"track": self.repository.create_track(episode_id, command, actor="local-user")}

    def update_track(self, binding_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"track": self.repository.update_track(binding_id, command, actor="local-user")}

    def remove_track(self, binding_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"track": self.repository.remove_track(binding_id, command, actor="local-user")}
