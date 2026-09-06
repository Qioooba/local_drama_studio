"""Resolve story breakdowns through the same model preferences as the UI."""

from __future__ import annotations

import sqlite3
from typing import Any

from local_drama.application.ports.override_schema import effective_schema
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.capabilities import normalize_capability
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository


def resolve_breakdown_execution(
    connection: sqlite3.Connection,
    project_id: str,
    episode_id: str | None = None,
    requested_profile_version_id: str | None = None,
) -> dict[str, Any]:
    repository = SqliteGenerationPreferenceRepository(connection)
    resolution = GenerationPreferenceQueryService(repository).resolve(
        project_id=project_id, episode_id=episode_id, capability="LLM_STORY_PARSE",
    )
    profile_id = requested_profile_version_id or resolution.get("profile_version_id")
    if not profile_id:
        raise DomainRuleError(
            "EPISODE_BREAKDOWN_MODEL_REQUIRED",
            "当前项目没有可用的故事拆解能力，请在生成偏好中配置模型。",
            {"reason": resolution.get("blocked_reason")},
        )
    profile = repository.profile(str(profile_id))
    if not profile or profile.get("status") != "PUBLISHED" or normalize_capability(str(profile.get("capability"))) != "LLM_STORY_PARSE":
        raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "故事拆解必须使用已发布的故事解析能力。")
    if profile_id == resolution.get("profile_version_id"):
        settings = dict(resolution.get("effective_settings") or {})
    else:
        fields = effective_schema(profile).get("fields", {})
        settings = {key: field["default"] for key, field in fields.items() if isinstance(field, dict) and "default" in field}
        bundle = profile.get("model_bundle") or {}
        if isinstance(bundle.get("defaults"), dict):
            settings.update(bundle["defaults"])
    return {
        "profile_version_id": str(profile_id),
        "inference_options": {key: settings[key] for key in ("temperature", "top_p", "max_tokens") if key in settings},
    }
