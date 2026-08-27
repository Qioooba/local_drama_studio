"""Formal human-review commands owned exclusively by the Review workspace."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.review_decisions import ReviewDecisionCommandPort


class ReviewDecisionCommandService:
    def __init__(self, commands: ReviewDecisionCommandPort) -> None:
        self.commands = commands

    def create(self, command: dict[str, Any]) -> dict[str, Any]:
        return {"decision": self.commands.create(command, actor="local-user")}

    def revoke(self, decision_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"decision": self.commands.revoke(decision_id, command, actor="local-user")}
