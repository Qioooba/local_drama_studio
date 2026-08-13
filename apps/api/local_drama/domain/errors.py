from __future__ import annotations


class DomainRuleError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
        *,
        suggested_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.suggested_action = suggested_action
