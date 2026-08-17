"""G11 P0-1: character appearance anchors for shots and generation prompt injection.

Shared by two consumers so the executed anchor text is byte-identical to what
the preview endpoint shows:

* ``PromptAnchorService.shot_prompt_anchor`` — read-only preview for the UI
  (GET /shots/{shot_id}/prompt-anchor).
* ``generation._character_anchor_lines`` — injection into the ephemeral job
  input snapshot at ``submit_confirmed_variant`` time.

Anchors are derived from the *current* DB binding state at submit time and
frozen into the job snapshot + audit trail; they never mutate
``parameter_set`` / ``plan_hash``, preserving EXACT_REPLAY semantics.
"""

from __future__ import annotations

from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def character_anchor_rows(connection: Any, shot_id: str) -> list[dict[str, Any]]:
    """Return ACTIVE CHARACTER story assets bound to a shot, ordered deterministically.

    Ordering is by ``role_in_shot`` then asset ``name`` (both stable keys) so a
    given binding state always yields the same anchor text.
    """
    rows = connection.execute(
        """SELECT a.id AS asset_id, a.name, a.description, a.canonical_media_version_id
        FROM shot_asset_bindings b JOIN story_assets a ON a.id = b.asset_id
        WHERE b.shot_id = ? AND a.kind = 'CHARACTER' AND a.status = 'ACTIVE'
        ORDER BY b.role_in_shot, a.name""",
        (shot_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def character_anchor_line(name: str, description: str, canonical_media_version_id: str | None) -> str:
    """Format one anchor line: ``角色锚点 · {name}：{description}（参考图 {id}）``."""
    line = f"角色锚点 · {name}"
    if description:
        line += f"：{description}"
    if canonical_media_version_id:
        line += f"（参考图 {canonical_media_version_id}）"
    return line


def character_anchor_text(rows: list[dict[str, Any]]) -> str:
    """Join anchor rows into the exact multi-line text injected into PROMPT."""
    return "\n".join(
        character_anchor_line(
            str(item["name"]), str(item.get("description") or ""), item.get("canonical_media_version_id") or None
        )
        for item in rows
    )


class PromptAnchorService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def shot_prompt_anchor(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM shots WHERE id=?", (shot_id,)).fetchone() is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            rows = character_anchor_rows(connection, shot_id)
        characters = [
            {
                "asset_id": str(item["asset_id"]),
                "name": str(item["name"]),
                "description": str(item["description"] or ""),
                "canonical_media_version_id": str(item["canonical_media_version_id"]) if item["canonical_media_version_id"] else None,
            }
            for item in rows
        ]
        return {"shot_id": shot_id, "anchor": character_anchor_text(rows), "characters": characters}
