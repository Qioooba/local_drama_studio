from __future__ import annotations

from typing import Any, Protocol


class ReviewDecisionCommandPort(Protocol):
    def create(self, command: dict[str, Any], *, actor: str) -> dict[str, Any]: ...

    def revoke(self, decision_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]: ...
