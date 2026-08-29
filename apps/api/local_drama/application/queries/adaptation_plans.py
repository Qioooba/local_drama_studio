"""Read queries for the Adaptation Planning bounded context."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.adaptation_planning import AdaptationPlanningRepository


class AdaptationPlanQueryService:
    def __init__(self, repository: AdaptationPlanningRepository) -> None:
        self.repository = repository

    def source_versions(self, *, project_id: str) -> dict[str, Any]:
        return {"items": self.repository.list_source_versions(project_id=project_id)}

    def plans(self, *, project_id: str) -> dict[str, Any]:
        return {"items": self.repository.list_plans(project_id=project_id)}

    def workspace(self, *, plan_id: str) -> dict[str, Any]:
        return self.repository.workspace(plan_id=plan_id)
