from __future__ import annotations

from typing import Any

from local_drama.application.ports.episode_production_commands import (
    EpisodeProductionStarterPort,
    EpisodeProductionTransitionPort,
)


class EpisodeProductionCommandService:
    def __init__(
        self,
        starter: EpisodeProductionStarterPort,
        transitions: EpisodeProductionTransitionPort,
    ) -> None:
        self.starter = starter
        self.transitions = transitions

    @staticmethod
    def _fact(run: dict[str, Any], outcome: str, *, affected: int = 0) -> dict[str, Any]:
        return {
            "run": {
                "id": str(run["id"]),
                "episode_id": str(run["episode_id"]),
                "project_id": str(run["project_id"]),
                "status": str(run["status"]),
                "revision": int(run["revision"]),
                "updated_at": run.get("updated_at"),
                "outcome": outcome,
                "affected_job_count": affected,
                "idempotent_replay": bool(run.get("idempotent_replay", False)),
            }
        }

    def start(self, episode_id: str, command: dict[str, Any]) -> dict[str, Any]:
        options = {key: value for key, value in command.items() if key != "idempotency_key"}
        run = self.starter.start(
            episode_id,
            idempotency_key=str(command["idempotency_key"]),
            **options,
        )
        return self._fact(run, "STARTED")

    def transition(self, run_id: str, action: str, command: dict[str, Any]) -> dict[str, Any]:
        run = self.transitions.transition(
            run_id,
            action=action,
            expected_revision=int(command["expected_revision"]),
            idempotency_key=str(command["idempotency_key"]),
            reason=command.get("reason") or command.get("note"),
            actor="local-user",
        )
        return self._fact(run, action, affected=int(run.get("affected_job_count", 0)))
