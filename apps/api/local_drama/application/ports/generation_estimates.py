"""Read port for bounded local generation-attempt history."""

from __future__ import annotations

from typing import Any, Protocol


class GenerationEstimateHistoryPort(Protocol):
    def load_successful_attempts(self, *, profile_version_id: str, limit: int) -> dict[str, Any]: ...
