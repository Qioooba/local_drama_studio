"""Canonical latest-render approval checks used by delivery entry points."""

from __future__ import annotations

import sqlite3
from typing import Any

from local_drama.domain.errors import DomainRuleError


def latest_episode_render(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any] | None:
    """Return the current render for an episode using a deterministic order."""

    row = connection.execute(
        """SELECT erv.id, erv.episode_id, erv.revision, erv.integrity_status,
        erv.created_at
        FROM episode_render_versions erv
        WHERE erv.episode_id=?
        ORDER BY erv.created_at DESC, erv.id DESC LIMIT 1""",
        (episode_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def latest_episode_render_approval(
    connection: sqlite3.Connection, render_id: str,
) -> dict[str, Any] | None:
    """Return the latest decision for one immutable episode render."""

    row = connection.execute(
        """SELECT id, decision, is_stale, subject_revision
        FROM review_decisions
        WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
        ORDER BY created_at DESC, id DESC LIMIT 1""",
        (render_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def require_latest_episode_render_approval(
    connection: sqlite3.Connection, render: dict[str, Any],
) -> dict[str, Any]:
    """Require the passed render to be current and validly approved.

    Delivery must never build from an older render, even when that older
    render still has a historical APPROVED decision.  The returned decision
    is the exact immutable approval used in the delivery fingerprint.
    """

    episode_id = str(render["episode_id"])
    latest = latest_episode_render(connection, episode_id)
    if latest is None or str(latest["id"]) != str(render["id"]):
        raise DomainRuleError(
            "EPISODE_RENDER_APPROVAL_REQUIRED",
            "只有最新、未过期的整集批准版本才能创建交付候选",
            {
                "episode_id": episode_id,
                "render_id": str(render["id"]),
                "latest_render_id": str(latest["id"]) if latest else None,
            },
        )
    approval = latest_episode_render_approval(connection, str(render["id"]))
    if approval is None or str(approval["decision"]) != "APPROVED" or int(approval["is_stale"] or 0) != 0:
        raise DomainRuleError(
            "EPISODE_RENDER_APPROVAL_REQUIRED",
            "只有最新、未过期的整集批准版本才能创建交付候选",
            {"episode_id": episode_id, "render_id": str(render["id"])},
        )
    if int(approval["subject_revision"]) != int(render["revision"]):
        raise DomainRuleError(
            "EPISODE_RENDER_APPROVAL_STALE",
            "整集批准基于旧 revision，不能创建交付候选",
            {
                "render_id": str(render["id"]),
                "approval_subject_revision": int(approval["subject_revision"]),
                "render_revision": int(render["revision"]),
            },
        )
    return approval
