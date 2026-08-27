"""Typed, bounded frame annotations owned by the Review workspace."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.review_annotations import ReviewAnnotationPort


class ReviewAnnotationService:
    def __init__(self, repository: ReviewAnnotationPort) -> None:
        self.repository = repository

    def list_page(self, target_kind: str, target_id: str, *, cursor: int, limit: int) -> dict[str, Any]:
        return {
            **self.repository.list_page(target_kind, target_id, cursor=cursor, limit=limit),
            "read_only": True,
            "request_shape": "bounded_review_annotations_v2",
        }

    def create(self, target_kind: str, target_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return {"annotation": self.repository.create(target_kind, target_id, command, actor="local-user")}
