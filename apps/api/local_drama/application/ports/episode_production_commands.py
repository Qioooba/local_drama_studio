from __future__ import annotations

from typing import Any, Protocol


class EpisodeProductionStarterPort(Protocol):
    def start(self, episode_id: str, *, idempotency_key: str, **options: Any) -> dict[str, Any]: ...


class EpisodeProductionTransitionPort(Protocol):
    def transition(
        self,
        run_id: str,
        *,
        action: str,
        expected_revision: int,
        idempotency_key: str,
        reason: str | None,
        actor: str,
    ) -> dict[str, Any]: ...
