"""Validate that V2 capability scopes name real, mutually compatible business entities."""

from __future__ import annotations

from collections.abc import Mapping

from local_drama.domain.errors import DomainRuleError


def validate_assignment_scope(connection, *, scope_type: str, scope_id: str) -> None:
    """Require a non-system assignment target to exist before it is persisted.

    Capability assignments intentionally use a polymorphic scope instead of a
    database foreign key.  Keeping the ownership check here preserves that
    schema while still preventing dangling or cross-project configuration.
    """
    if scope_type == "SYSTEM":
        return
    _project_id_for_scope(connection, scope_type=scope_type, scope_id=scope_id)


def validate_scope_context(connection, scoped_ids: Mapping[str, str]) -> None:
    """Ensure all supplied resolution IDs belong to the same project."""
    owners = {
        scope_type: _project_id_for_scope(connection, scope_type=scope_type, scope_id=scope_id)
        for scope_type, scope_id in scoped_ids.items()
        if scope_type != "SYSTEM"
    }
    if len(set(owners.values())) > 1:
        raise DomainRuleError(
            "MP_ASSIGNMENT_SCOPE_CONTEXT_MISMATCH",
            "Capability 解析中的项目、集、镜头或角色不属于同一项目。",
            {"scope_types": sorted(owners)},
        )


def _project_id_for_scope(connection, *, scope_type: str, scope_id: str) -> str:
    queries = {
        "PROJECT": ("SELECT id AS project_id FROM projects WHERE id=?", (scope_id,)),
        "EPISODE": (
            """SELECT season.project_id FROM episodes episode
            JOIN seasons season ON season.id=episode.season_id WHERE episode.id=?""",
            (scope_id,),
        ),
        "SHOT": (
            """SELECT season.project_id FROM shots shot
            JOIN episodes episode ON episode.id=shot.episode_id
            JOIN seasons season ON season.id=episode.season_id WHERE shot.id=?""",
            (scope_id,),
        ),
        "CHARACTER": (
            "SELECT project_id FROM story_assets WHERE id=? AND kind='CHARACTER' AND status='ACTIVE'",
            (scope_id,),
        ),
    }
    query = queries.get(scope_type)
    if query is None:
        raise DomainRuleError("MP_ASSIGNMENT_SCOPE_INVALID", "CapabilityAssignment 的 scope_type 无效。")
    row = connection.execute(*query).fetchone()
    if row is None:
        raise DomainRuleError(
            "MP_ASSIGNMENT_SCOPE_NOT_FOUND",
            "CapabilityAssignment 的范围对象不存在、不是可用角色，或已不属于当前项目。",
            {"scope_type": scope_type, "scope_id": scope_id},
        )
    return str(row["project_id"])
