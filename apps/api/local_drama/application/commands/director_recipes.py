"""Commands for immutable, declarative Director Recipes."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from local_drama.application.ports.director_recipes import DirectorRecipeRepository
from local_drama.domain.errors import DomainRuleError

TOP_LEVEL_KEYS = {"aspect_ratio", "shot_planning", "asset_policy", "generation", "qc_policy_ref"}
FORBIDDEN_EXECUTION_KEYS = {
    "shell", "python", "command", "commands", "executor", "exec", "code", "script", "subprocess",
    "powershell", "bash", "cmd", "runtime_code", "entrypoint",
}


def canonical_recipe(recipe: dict[str, Any]) -> str:
    return json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def recipe_hash(recipe: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_recipe(recipe).encode("utf-8")).hexdigest()


def _object(value: object, field: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainRuleError("DIRECTOR_RECIPE_INVALID", f"{field} 必须是声明式对象")
    extras = sorted(set(map(str, value)) - allowed)
    if extras:
        raise DomainRuleError("DIRECTOR_RECIPE_FIELD_UNSUPPORTED", f"{field} 包含不支持字段", {"field": field, "fields": extras})
    return value


def validate_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, dict):
        raise DomainRuleError("DIRECTOR_RECIPE_INVALID", "Recipe 必须是 JSON 对象")

    def reject_execution(value: object, path: str = "recipe") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                token = str(key).strip().casefold()
                if token in FORBIDDEN_EXECUTION_KEYS:
                    raise DomainRuleError("DIRECTOR_RECIPE_EXECUTABLE_FORBIDDEN", "Recipe 只能声明策略，禁止 shell/python/command/executor code", {"path": f"{path}.{key}"})
                reject_execution(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                reject_execution(item, f"{path}[{index}]")

    reject_execution(recipe)
    extras = sorted(set(recipe) - TOP_LEVEL_KEYS)
    missing = sorted(TOP_LEVEL_KEYS - set(recipe))
    if extras or missing:
        raise DomainRuleError("DIRECTOR_RECIPE_SHAPE_INVALID", "Recipe 顶层字段不完整或不受支持", {"missing": missing, "unsupported": extras})
    aspect_ratio = recipe["aspect_ratio"]
    if not isinstance(aspect_ratio, str) or not re.fullmatch(r"[1-9]\d*:[1-9]\d*", aspect_ratio):
        raise DomainRuleError("DIRECTOR_RECIPE_ASPECT_RATIO_INVALID", "aspect_ratio 必须是 W:H")
    shot = _object(recipe["shot_planning"], "shot_planning", {"avg_duration_ms", "dialogue_coverage"})
    if not isinstance(shot.get("avg_duration_ms"), int) or not 250 <= int(shot["avg_duration_ms"]) <= 120_000:
        raise DomainRuleError("DIRECTOR_RECIPE_SHOT_PLANNING_INVALID", "avg_duration_ms 必须在 250—120000")
    if not isinstance(shot.get("dialogue_coverage"), str) or not str(shot["dialogue_coverage"]).strip():
        raise DomainRuleError("DIRECTOR_RECIPE_SHOT_PLANNING_INVALID", "dialogue_coverage 必须显式声明")
    assets = _object(recipe["asset_policy"], "asset_policy", {"character_required_refs"})
    refs = assets.get("character_required_refs")
    if not isinstance(refs, list) or not refs or len(refs) > 20 or any(not isinstance(item, str) or not item.strip() for item in refs):
        raise DomainRuleError("DIRECTOR_RECIPE_ASSET_POLICY_INVALID", "character_required_refs 必须是 1—20 个声明式引用类型")
    generation = _object(recipe["generation"], "generation", {"image", "video"})
    for kind in ("image", "video"):
        spec = _object(generation.get(kind), f"generation.{kind}", {"capability"})
        if not isinstance(spec.get("capability"), str) or not spec["capability"].strip():
            raise DomainRuleError("DIRECTOR_RECIPE_GENERATION_INVALID", f"generation.{kind}.capability 必须声明")
    qc = _object(recipe["qc_policy_ref"], "qc_policy_ref", {"policy_version_id"})
    if not isinstance(qc.get("policy_version_id"), str) or not qc["policy_version_id"].strip():
        raise DomainRuleError("DIRECTOR_RECIPE_QC_POLICY_REF_INVALID", "qc_policy_ref.policy_version_id 必须声明")
    return json.loads(canonical_recipe(recipe))


class DirectorRecipeCommandService:
    def __init__(self, repository: DirectorRecipeRepository) -> None:
        self.repository = repository

    def create(self, *, project_id: str, code: str, title: str, recipe: dict[str, Any], reason: str = "", actor: str = "local-user") -> dict[str, Any]:
        if not self.repository.project_exists(project_id):
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        normalized_code = code.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,79}", normalized_code):
            raise DomainRuleError("DIRECTOR_RECIPE_CODE_INVALID", "Recipe code 必须是 2—80 位小写 ASCII")
        normalized = validate_recipe(recipe)
        return self.repository.create_recipe(project_id=project_id, code=normalized_code, title=title.strip(), recipe=normalized, recipe_hash=recipe_hash(normalized), reason=reason.strip(), actor=actor)

    def create_version(self, *, project_id: str, recipe_id: str, recipe: dict[str, Any], reason: str = "", actor: str = "local-user") -> dict[str, Any]:
        normalized = validate_recipe(recipe)
        return self.repository.create_version(project_id=project_id, recipe_id=recipe_id, recipe=normalized, recipe_hash=recipe_hash(normalized), reason=reason.strip(), actor=actor)

    def bind(self, *, project_id: str, recipe_version_id: str, reason: str = "", expected_revision: int | None = None, actor: str = "local-user") -> dict[str, Any]:
        return self.repository.bind(project_id=project_id, recipe_version_id=recipe_version_id, reason=reason.strip(), actor=actor, expected_revision=expected_revision)
