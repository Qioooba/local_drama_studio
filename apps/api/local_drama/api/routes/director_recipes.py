from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Request

from local_drama.api.schemas.director_recipes import DirectorRecipeBindingRequest, DirectorRecipeCreateRequest, DirectorRecipeVersionRequest
from local_drama.application.commands.director_recipes import DirectorRecipeCommandService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.queries.director_recipes import DirectorRecipeQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.director_recipe_repository import SqliteDirectorRecipeRepository

router = APIRouter(tags=["director-recipes"])
T = TypeVar("T")


def _query(request: Request, fn: Callable[[DirectorRecipeQueryService], T]) -> T:
    with request.app.state.database.connect() as connection:
        return fn(DirectorRecipeQueryService(SqliteDirectorRecipeRepository(connection)))


def _command(request: Request, fn: Callable[[DirectorRecipeCommandService], T]) -> T:
    with request.app.state.database.transaction() as connection:
        return fn(DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection)))


@router.get("/projects/{project_id}/director-recipes", operation_id="listDirectorRecipes")
async def list_recipes(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": _query(request, lambda service: service.list(project_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/director-recipes/{recipe_id}", operation_id="getDirectorRecipe")
async def get_recipe(project_id: str, recipe_id: str, request: Request) -> dict[str, object]:
    try:
        return {"recipe": _query(request, lambda service: service.get(project_id, recipe_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/director-recipes", status_code=201, operation_id="createDirectorRecipe")
async def create_recipe(project_id: str, payload: DirectorRecipeCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"recipe": _command(request, lambda service: service.create(project_id=project_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/director-recipes/{recipe_id}/versions", status_code=201, operation_id="createDirectorRecipeVersion")
async def create_version(project_id: str, recipe_id: str, payload: DirectorRecipeVersionRequest, request: Request) -> dict[str, object]:
    try:
        return {"version": _command(request, lambda service: service.create_version(project_id=project_id, recipe_id=recipe_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/director-recipe-binding", operation_id="getProjectDirectorRecipeBinding")
async def get_binding(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"binding": _query(request, lambda service: service.current(project_id))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/projects/{project_id}/director-recipe-binding", operation_id="bindProjectDirectorRecipe")
async def bind_recipe(project_id: str, payload: DirectorRecipeBindingRequest, request: Request) -> dict[str, object]:
    try:
        return {"binding": _command(request, lambda service: service.bind(project_id=project_id, **payload.model_dump()))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
