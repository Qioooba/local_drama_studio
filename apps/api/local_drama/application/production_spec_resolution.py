"""Shared read-only selection of the effective VIDEO execution profile.

Production specs are project-owned, but the VIDEO graph they resolve against
must follow the same preference inheritance rules as generation itself.  In
particular, a project-level AUTO preference can select a published global
profile without creating a ``project_profile_bindings`` row.  Keeping that
selection here prevents configuration, Shot Studio and Timeline from drifting
into three subtly different notions of the active VIDEO workflow.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.production_spec import canonical_production_plan, resolve_production_spec
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository

VIDEO_CAPABILITIES = ("VIDEO_FIRST_LAST_FRAME", "VIDEO_I2V")


def effective_video_profile(connection: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    """Return the effective published VIDEO profile, or ``None`` when blocked.

    A project profile binding remains an explicit legacy override and is
    preferred when present.  Otherwise the canonical generation-preference
    resolver handles project AUTO/EXPLICIT inheritance and global AUTO
    selection.  If an explicit preference is unavailable, stop rather than
    silently switching to a different VIDEO capability.
    """

    repository = SqliteGenerationPreferenceRepository(connection)
    for capability in VIDEO_CAPABILITIES:
        # Existing project bindings are explicit production configuration and
        # should retain precedence over an older preference row.
        binding = connection.execute(
            """SELECT epv.id
            FROM project_profile_bindings ppb
            JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
            WHERE ppb.project_id=? AND ppb.status IN ('ACTIVE','SELECTED_CANDIDATE')
              AND epv.status='PUBLISHED' AND UPPER(epv.capability)=?
            ORDER BY epv.updated_at DESC, epv.version_no DESC LIMIT 1""",
            (project_id, capability),
        ).fetchone()
        if binding is not None:
            profile = repository.profile(str(binding["id"]))
            if profile is not None and profile.get("workflow_version_id"):
                return profile

        resolution = GenerationPreferenceQueryService(repository).resolve(
            project_id=project_id,
            capability=capability,
        )
        preference = resolution.get("preference")
        if resolution.get("blocked_reason") and preference is not None:
            # Explicitly selected unavailable profiles are fail-closed.  Do
            # not reinterpret the user's choice as another capability.
            continue
        profile_id = resolution.get("profile_version_id")
        if not profile_id:
            continue
        profile = repository.profile(str(profile_id))
        if profile is not None and profile.get("workflow_version_id"):
            return profile
    return None


def project_production_spec(connection: Any, project_id: str, plan: dict[str, Any] | None) -> dict[str, Any]:
    """Build the read-only production-spec projection from canonical bindings."""

    if plan is None:
        return {
            "status": "BLOCKED",
            "delivery": None,
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": "PRODUCTION_PLAN_NOT_BOUND", "message": "请在生产设置中确认并保存当前项目的画幅、分辨率和帧率"}],
            "warnings": [],
        }
    try:
        canonical_plan = canonical_production_plan(plan)
    except DomainRuleError as error:
        return {
            "status": "BLOCKED",
            "delivery": None,
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": error.code, "message": error.message}],
            "warnings": [],
        }
    profile = effective_video_profile(connection, project_id)
    if profile is None:
        return {
            "status": "BLOCKED",
            "delivery": canonical_plan["presentation"],
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": "VIDEO_PROFILE_NOT_BOUND", "message": "请先绑定已发布的 VIDEO workflow/profile，才能解析实际生成规格"}],
            "warnings": [],
        }
    workflow = connection.execute(
        "SELECT status,content_json,node_bindings_json FROM workflow_versions WHERE id=?",
        (profile["workflow_version_id"],),
    ).fetchone()
    if workflow is None or str(workflow["status"]) != "PUBLISHED":
        return {
            "status": "BLOCKED",
            "delivery": canonical_plan["presentation"],
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": "VIDEO_WORKFLOW_NOT_PUBLISHED", "message": "当前 VIDEO Profile 没有可解析的已发布 workflow"}],
            "warnings": [],
        }
    try:
        bindings = json.loads(str(workflow["node_bindings_json"] or "{}"))
        content = json.loads(str(workflow["content_json"] or "{}"))
    except (TypeError, ValueError):
        return {
            "status": "BLOCKED",
            "delivery": canonical_plan["presentation"],
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": "VIDEO_WORKFLOW_INVALID", "message": "当前 VIDEO workflow 的内容或语义绑定不是有效 JSON"}],
            "warnings": [],
        }
    if not isinstance(bindings, dict) or not isinstance(content, dict):
        return {
            "status": "BLOCKED",
            "delivery": canonical_plan["presentation"],
            "generation": {"mode": "BLOCKED", "actual": None, "semantic_inputs": {}, "upscale": None},
            "blockers": [{"code": "VIDEO_WORKFLOW_INVALID", "message": "当前 VIDEO workflow 的内容或语义绑定结构无效"}],
            "warnings": [],
        }
    return resolve_production_spec(canonical_plan, bindings, content)

