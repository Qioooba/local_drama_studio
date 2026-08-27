from __future__ import annotations

from typing import Any, Protocol


class ReviewAnnotationPort(Protocol):
    def list_page(self, target_kind: str, target_id: str, *, cursor: int, limit: int) -> dict[str, Any]: ...

    def create(self, target_kind: str, target_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]: ...
