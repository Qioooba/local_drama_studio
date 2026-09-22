"""Shared project-hierarchy ownership verification.

A route that names more than one entity in its URL (``project + episode``,
``episode + shot group``, ...) must prove *inside the same write transaction*
that the child belongs to the parent before it performs any INSERT/UPDATE.
This module is the single implementation of that rule, so a route never has to
grow its own special case and a URL parent id can never be dropped silently.

Every helper raises the unified :class:`DomainRuleError` contract:

* the parent (or the child) does not exist -> ``*_NOT_FOUND`` (HTTP 404)
* the child exists but belongs to another parent -> ``*_MISMATCH`` (HTTP 409)
"""

from __future__ import annotations

import sqlite3
from typing import cast

from local_drama.domain.errors import DomainRuleError


def _fetchone(connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...]) -> sqlite3.Row | None:
    return cast(sqlite3.Row | None, connection.execute(sql, parameters).fetchone())


def require_project(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = _fetchone(connection, "SELECT id, code, status FROM projects WHERE id=?", (project_id,))
    if row is None:
        raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
    return row


def require_episode(connection: sqlite3.Connection, project_id: str, episode_id: str) -> sqlite3.Row:
    """Return the episode row after proving it belongs to ``project_id``."""
    require_project(connection, project_id)
    row = _fetchone(
        connection,
        "SELECT e.id AS episode_id, s.project_id AS project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
        (episode_id,),
    )
    if row is None:
        raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
    if str(row["project_id"]) != str(project_id):
        raise DomainRuleError(
            "EPISODE_PROJECT_MISMATCH",
            "分集不属于 URL 指定的项目",
            {"project_id": project_id, "episode_id": episode_id, "episode_project_id": str(row["project_id"])},
        )
    return row


def require_scene(connection: sqlite3.Connection, project_id: str, scene_id: str) -> sqlite3.Row:
    """Return the scene row after proving it belongs to ``project_id``."""
    require_project(connection, project_id)
    row = _fetchone(connection, "SELECT id, project_id FROM scenes WHERE id=?", (scene_id,))
    if row is None:
        raise DomainRuleError("SCENE_NOT_FOUND", "母本场次不存在", {"scene_id": scene_id})
    if str(row["project_id"]) != str(project_id):
        raise DomainRuleError(
            "SCENE_EPISODE_PROJECT_MISMATCH",
            "母本场次与分集必须属于同一项目",
            {"project_id": project_id, "scene_id": scene_id, "scene_project_id": str(row["project_id"])},
        )
    return row


def require_shot(connection: sqlite3.Connection, project_id: str, shot_id: str) -> sqlite3.Row:
    """Return the shot row after proving it belongs to ``project_id``."""
    require_project(connection, project_id)
    row = _fetchone(
        connection,
        """SELECT sh.id AS shot_id, s.project_id AS project_id FROM shots sh
        JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE sh.id=?""",
        (shot_id,),
    )
    if row is None:
        raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
    if str(row["project_id"]) != str(project_id):
        raise DomainRuleError(
            "SHOT_PROJECT_MISMATCH",
            "镜头不属于 URL 指定的项目",
            {"project_id": project_id, "shot_id": shot_id, "shot_project_id": str(row["project_id"])},
        )
    return row


def require_shot_group(connection: sqlite3.Connection, project_id: str, group_id: str) -> sqlite3.Row:
    """Return the shot-group row after proving it belongs to ``project_id``."""
    require_project(connection, project_id)
    row = _fetchone(
        connection,
        """SELECT g.id AS group_id, g.episode_id AS episode_id, s.project_id AS project_id FROM shot_groups g
        JOIN episodes e ON e.id=g.episode_id JOIN seasons s ON s.id=e.season_id WHERE g.id=?""",
        (group_id,),
    )
    if row is None:
        raise DomainRuleError("SHOT_GROUP_NOT_FOUND", "镜头组不存在", {"group_id": group_id})
    if str(row["project_id"]) != str(project_id):
        raise DomainRuleError(
            "SHOT_GROUP_PROJECT_MISMATCH",
            "镜头组不属于 URL 指定的项目",
            {"project_id": project_id, "group_id": group_id, "group_project_id": str(row["project_id"])},
        )
    return row
