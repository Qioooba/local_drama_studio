from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from local_drama.application.commands.director_recipes import DirectorRecipeCommandService
from local_drama.application.commands.qc_policies import QcPolicyCommandService
from local_drama.application.generation import GenerationService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.director_recipe_repository import SqliteDirectorRecipeRepository
from local_drama.infrastructure.database.qc_policy_repository import SqliteQcPolicyRepository
from local_drama.main import create_app
from tests.test_generation_variants import _image, _plan, _project, _published_profile


def _policy(database, project_id: str) -> dict:
    with database.transaction() as connection:
        return QcPolicyCommandService(SqliteQcPolicyRepository(connection)).put(
            project_id=project_id, owner_type="PROJECT", owner_id=project_id, stage="VIDEO",
            policy={"technical": "STRICT"}, max_auto_rerolls=1,
            auto_reroll_categories=["TECHNICAL"], reason="recipe test",
        )


def _definition(policy_version_id: str, *, duration: int = 3500) -> dict:
    return {
        "aspect_ratio": "9:16",
        "shot_planning": {"avg_duration_ms": duration, "dialogue_coverage": "MEDIUM_CLOSEUP_BIASED"},
        "asset_policy": {"character_required_refs": ["HERO", "FRONT", "LEFT", "RIGHT"]},
        "generation": {"image": {"capability": "IMAGE_CHARACTER"}, "video": {"capability": "I2V"}},
        "qc_policy_ref": {"policy_version_id": policy_version_id},
    }


def test_recipe_versions_are_immutable_and_project_upgrade_is_explicit(workspace, database) -> None:
    project = _project(workspace, database, "director_recipe_versions")
    project_id = str(project["id"])
    policy = _policy(database, project_id)
    with database.transaction() as connection:
        commands = DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection))
        created = commands.create(project_id=project_id, code="vertical_drama", title="竖屏漫剧", recipe=_definition(str(policy["policy_version_id"])))
        v1 = created["versions"][0]
        bound_v1 = commands.bind(project_id=project_id, recipe_version_id=str(v1["id"]))
        v2 = commands.create_version(project_id=project_id, recipe_id=str(created["id"]), recipe=_definition(str(policy["policy_version_id"]), duration=4200), reason="slower pacing")
    assert v1["version_no"] == 1 and v2["version_no"] == 2
    assert v1["recipe"]["shot_planning"]["avg_duration_ms"] == 3500
    with database.connect() as connection:
        current = SqliteDirectorRecipeRepository(connection).current_binding(project_id)
    assert current is not None and current["recipe_version_id"] == bound_v1["recipe_version_id"] == v1["id"]
    with database.transaction() as connection:
        upgraded = DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection)).bind(
            project_id=project_id, recipe_version_id=str(v2["id"]), expected_revision=int(current["revision"]), reason="explicit upgrade",
        )
    assert upgraded["recipe_version_id"] == v2["id"]
    with database.connect() as connection:
        frozen_v1 = connection.execute("SELECT recipe_json,recipe_hash,is_frozen FROM director_recipe_versions WHERE id=?", (v1["id"],)).fetchone()
    assert frozen_v1["recipe_hash"] == v1["recipe_hash"] and frozen_v1["is_frozen"] == 1


@pytest.mark.parametrize("path", ["shell", "python", "command", "executor", "code"])
def test_recipe_rejects_executable_fields(workspace, database, path: str) -> None:
    project = _project(workspace, database, f"recipe_reject_{path}")
    policy = _policy(database, str(project["id"]))
    recipe = _definition(str(policy["policy_version_id"]))
    recipe["generation"]["video"][path] = "do-dangerous-work"
    with database.transaction() as connection, pytest.raises(DomainRuleError) as error:
        DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection)).create(
            project_id=str(project["id"]), code=f"bad_{path}", title="bad", recipe=recipe,
        )
    assert error.value.code == "DIRECTOR_RECIPE_EXECUTABLE_FORBIDDEN"


def test_generation_plan_and_new_variant_freeze_current_recipe_without_mutating_old_variant(workspace, database) -> None:
    project = _project(workspace, database, "director_recipe_generation")
    project_id = str(project["id"])
    policy = _policy(database, project_id)
    with database.transaction() as connection:
        commands = DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection))
        created = commands.create(project_id=project_id, code="recipe_generation", title="Generation recipe", recipe=_definition(str(policy["policy_version_id"])))
        v1 = created["versions"][0]
        commands.bind(project_id=project_id, recipe_version_id=str(v1["id"]))
    profile_id = _published_profile(workspace, database)
    media_id = _image(workspace, database, project_id, "director-recipe.png")
    generation = GenerationService(database, workspace)
    intent1 = generation.create_intent(project_id, "PROJECT", project_id, "RECIPE_TEST", "freeze v1")
    plan1 = _plan(profile_id, media_id)
    preflight1 = generation.preflight_variant(str(intent1["id"]), plan1)
    assert preflight1["dependencies"]["director_recipe_version_id"] == v1["id"]
    submitted1 = generation.submit_confirmed_variant(str(intent1["id"]), plan1, str(preflight1["plan_hash"]), "director-recipe-v1")
    assert submitted1["variant"]["director_recipe_version_id"] == v1["id"]
    assert submitted1["job"]["input_snapshot"]["director_recipe"] == {"version_id": v1["id"], "recipe_hash": v1["recipe_hash"]}

    v2_recipe = copy.deepcopy(_definition(str(policy["policy_version_id"])))
    v2_recipe["shot_planning"]["avg_duration_ms"] = 5000
    with database.transaction() as connection:
        commands = DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection))
        v2 = commands.create_version(project_id=project_id, recipe_id=str(created["id"]), recipe=v2_recipe)
        commands.bind(project_id=project_id, recipe_version_id=str(v2["id"]), expected_revision=1)
    intent2 = generation.create_intent(project_id, "PROJECT", project_id, "RECIPE_TEST", "freeze v2")
    plan2 = _plan(profile_id, media_id, seed=8)
    preflight2 = generation.preflight_variant(str(intent2["id"]), plan2)
    submitted2 = generation.submit_confirmed_variant(str(intent2["id"]), plan2, str(preflight2["plan_hash"]), "director-recipe-v2")
    assert submitted2["variant"]["director_recipe_version_id"] == v2["id"]
    assert generation.get_variant(str(submitted1["variant"]["id"]))["director_recipe_version_id"] == v1["id"]


def test_director_recipe_api_create_list_bind(workspace, database) -> None:
    project = _project(workspace, database, "director_recipe_api")
    project_id = str(project["id"])
    policy = _policy(database, project_id)
    with TestClient(create_app(workspace)) as client:
        created = client.post(f"/api/v1/projects/{project_id}/director-recipes", json={"code": "api_recipe", "title": "API recipe", "recipe": _definition(str(policy["policy_version_id"]))})
        assert created.status_code == 201, created.text
        recipe = created.json()["recipe"]
        version = recipe["versions"][0]
        bound = client.put(f"/api/v1/projects/{project_id}/director-recipe-binding", json={"recipe_version_id": version["id"]})
        assert bound.status_code == 200, bound.text
        assert bound.json()["binding"]["recipe_hash"] == version["recipe_hash"]
        listed = client.get(f"/api/v1/projects/{project_id}/director-recipes")
        assert listed.status_code == 200 and [item["code"] for item in listed.json()["items"]] == ["api_recipe"]
