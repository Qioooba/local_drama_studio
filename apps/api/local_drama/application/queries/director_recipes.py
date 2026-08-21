from __future__ import annotations

from typing import Any

from local_drama.application.ports.director_recipes import DirectorRecipeRepository
from local_drama.domain.errors import DomainRuleError


class DirectorRecipeQueryService:
    def __init__(self, repository: DirectorRecipeRepository) -> None:
        self.repository = repository

    def list(self, project_id: str) -> list[dict[str, Any]]:
        if not self.repository.project_exists(project_id):
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return self.repository.list_recipes(project_id)

    def get(self, project_id: str, recipe_id: str) -> dict[str, Any]:
        item = self.repository.get_recipe(project_id, recipe_id)
        if item is None:
            raise DomainRuleError("DIRECTOR_RECIPE_NOT_FOUND", "Director Recipe 不存在")
        return item

    def current(self, project_id: str) -> dict[str, Any] | None:
        if not self.repository.project_exists(project_id):
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return self.repository.current_binding(project_id)
