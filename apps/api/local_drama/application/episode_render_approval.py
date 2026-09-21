"""Canonical latest-render approval checks used by delivery entry points."""

from __future__ import annotations

import sqlite3
from typing import Any

from local_drama.domain.errors import DomainRuleError


def latest_episode_render(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any] | None:
    """Return the current composed render, never an unadopted derivative."""

    row = connection.execute(
        """SELECT erv.id, erv.episode_id, erv.revision, erv.integrity_status,
        erv.created_at
        FROM episode_render_versions erv
        WHERE erv.episode_id=? AND erv.render_kind='COMPOSE'
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
    connection: sqlite3.Connection,
    render: dict[str, Any],
    *,
    target_version_id: str | None = None,
) -> dict[str, Any]:
    """Require the passed render to be current and validly approved.

    Delivery must never build from an older render, even when that older
    render still has a historical APPROVED decision.  The returned decision
    is the exact immutable approval used in the delivery fingerprint.
    """

    episode_id = str(render["episode_id"])
    latest = latest_episode_render(connection, episode_id)
    render_kind = str(render.get("render_kind") or "COMPOSE")
    if render_kind == "SUPER_RESOLUTION":
        if latest is None or str(latest["id"]) != str(render.get("parent_render_version_id") or ""):
            raise DomainRuleError(
                "UPSCALE_SOURCE_STALE",
                "超分成片的合成根已经更新，不能创建新交付",
                {"episode_id": episode_id, "render_id": str(render["id"])},
            )
        machine = connection.execute(
            """SELECT status FROM machine_check_runs
            WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (render["id"],),
        ).fetchone()
        if machine is None or str(machine["status"]) != "PASS":
            raise DomainRuleError("UPSCALE_QC_FAILED", "超分成片未通过当前机器 QC，不能创建交付")
        selection = connection.execute(
            """SELECT * FROM episode_delivery_selections
            WHERE episode_id=? AND selected_render_id=? AND (? IS NULL OR target_slot=?)
            ORDER BY updated_at DESC,id DESC LIMIT 1""",
            (episode_id, render["id"], target_version_id, target_version_id),
        ).fetchone()
        if selection is None:
            raise DomainRuleError(
                "DELIVERY_SELECTION_REQUIRED",
                "超分成片必须先明确采用到当前交付目标",
                {"episode_id": episode_id, "render_id": str(render["id"]), "target_version_id": target_version_id},
            )
    elif latest is None or str(latest["id"]) != str(render["id"]):
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
