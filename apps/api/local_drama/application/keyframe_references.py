"""Shared read-side resolution for approved shot keyframes.

Keyframe media can be stored in two valid ownership shapes:

* a legacy ``SHOT`` media asset whose ``owner_id`` is the shot id; or
* a generated image whose media asset belongs to a ``GENERATION_VARIANT`` and
  whose generation intent owns the shot.

Production checks must resolve both shapes from the same project/shot scope.
This module intentionally performs no writes and treats a non-stale human
approval as part of the definition of an approved keyframe.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable


def approved_keyframes_for_shots(
    connection: sqlite3.Connection,
    shot_ids: Iterable[str],
    *,
    project_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the newest approved keyframe for each requested shot.

    The join deliberately follows ``GENERATION_VARIANT -> generation_intents
    -> SHOT`` for generated keyframes and independently supports the legacy
    direct ``SHOT`` owner.  Joining through the real shot and season/project
    rows prevents a candidate from another shot or project from satisfying a
    production check.
    """

    normalized_ids = tuple(dict.fromkeys(str(shot_id).strip() for shot_id in shot_ids if str(shot_id).strip()))
    if not normalized_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    params: list[Any] = [*normalized_ids]
    project_clause = ""
    if project_id is not None:
        project_clause = " AND se.project_id=?"
        params.append(str(project_id))

    rows = connection.execute(
        f"""SELECT s.id AS shot_id,
                mv.id AS media_version_id,
                ma.id AS media_asset_id,
                ma.project_id,
                ma.owner_type,
                ma.owner_id,
                rd.id AS review_decision_id,
                rd.revision AS review_revision,
                rd.is_stale,
                mv.created_at AS media_created_at
        FROM media_assets ma
        JOIN media_versions mv ON mv.id=ma.approved_version_id
        LEFT JOIN generation_variants gv
          ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
        LEFT JOIN generation_intents gi
          ON gi.id=gv.intent_id AND gi.owner_type='SHOT'
        JOIN shots s ON (
            (ma.owner_type='SHOT' AND ma.owner_id=s.id)
            OR (ma.owner_type='GENERATION_VARIANT' AND gi.owner_id=s.id)
        )
        JOIN episodes e ON e.id=s.episode_id
        JOIN seasons se ON se.id=e.season_id
        JOIN review_decisions rd
          ON rd.subject_type='MEDIA_VERSION' AND rd.subject_id=mv.id
         AND rd.decision='APPROVED' AND rd.is_stale=0
        WHERE s.id IN ({placeholders})
          AND ma.project_id=se.project_id
          AND (ma.owner_type='SHOT' OR gi.project_id=se.project_id)
          AND ma.purpose='KEYFRAME'
          AND ma.media_kind='IMAGE'
          AND mv.stage='KEYFRAME'
          AND mv.integrity_status='VERIFIED'
          {project_clause}
        ORDER BY s.id,mv.created_at DESC,mv.id DESC,rd.created_at DESC,rd.id DESC""",
        params,
    ).fetchall()

    resolved: dict[str, dict[str, Any]] = {}
    for row in rows:
        shot_id = str(row["shot_id"])
        # Multiple non-stale approvals can exist for the same immutable
        # version.  The query is newest-first, so retain one deterministic
        # fact per shot without leaking another candidate into the caller.
        if shot_id not in resolved:
            resolved[shot_id] = dict(row)
    return resolved


def approved_keyframe_for_shot(
    connection: sqlite3.Connection,
    shot_id: str,
    *,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve one shot's approved keyframe through the shared query."""

    return approved_keyframes_for_shots(connection, (shot_id,), project_id=project_id).get(str(shot_id))
