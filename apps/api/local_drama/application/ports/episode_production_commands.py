from __future__ import annotations

from typing import Any, Protocol


class EpisodeProductionStarterPort(Protocol):
    def start(
        self,
        episode_id: str,
        *,
        idempotency_key: str,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024,
        front_half_only: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]: ...


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
