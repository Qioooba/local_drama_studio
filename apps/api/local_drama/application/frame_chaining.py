"""Automated frame chaining and shot continuity bridge engine.

When Shot N video is rendered and adopted as a working version, this service automatically
bridges the tail frame to Shot N+1 within the same scene, enabling smooth character,
lighting, and compositional continuity without requiring manual frame selection.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.infrastructure.database.sqlite import Database

logger = logging.getLogger(__name__)


class FrameChainingService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.bridge_commands = FrameBridgeCommandService(database)

    def auto_chain_shot_tail_to_next(
        self,
        from_shot_id: str,
        *,
        tail_media_version_id: str,
        actor: str = "auto-chaining",
    ) -> dict[str, Any]:
        """Automatically bridge Shot N tail frame into Shot N+1 starting anchor if same scene."""
        with self.database.connect() as connection:
            from_shot = connection.execute(
                """SELECT s.id, s.episode_id, s.scene_id, s.order_key, s.code, se.project_id
                FROM shots s
                JOIN episodes e ON e.id = s.episode_id
                JOIN seasons se ON se.id = e.season_id
                WHERE s.id = ? AND s.archived_at IS NULL""",
                (from_shot_id,),
            ).fetchone()
            if from_shot is None:
                return {"chained": False, "reason": "FROM_SHOT_NOT_FOUND"}

            # Find next shot in the episode by order_key
            next_shot = connection.execute(
                """SELECT id, episode_id, scene_id, order_key, code
                FROM shots
                WHERE episode_id = ? AND CAST(order_key AS REAL) > CAST(? AS REAL) AND archived_at IS NULL
                ORDER BY CAST(order_key AS REAL) ASC, code ASC LIMIT 1""",
                (from_shot["episode_id"], from_shot["order_key"]),
            ).fetchone()
            if next_shot is None:
                return {"chained": False, "reason": "NO_SUCCESSOR_SHOT"}

            # Check for scene cut
            is_same_scene = str(from_shot["scene_id"] or "") == str(next_shot["scene_id"] or "")
            if not is_same_scene:
                logger.info(
                    "Scene cut detected between %s and %s; skipping auto frame chaining",
                    from_shot["code"],
                    next_shot["code"],
                )
                return {
                    "chained": False,
                    "reason": "SCENE_CUT_DETECTED",
                    "from_shot_code": from_shot["code"],
                    "to_shot_code": next_shot["code"],
                }

            # Find existing transition constraint or create one
            transition = connection.execute(
                """SELECT id, boundary_revision, from_anchor_id, to_anchor_id
                FROM shot_transition_constraints
                WHERE from_shot_id = ? AND to_shot_id = ?""",
                (from_shot["id"], next_shot["id"]),
            ).fetchone()

        transition_id = str(transition["id"]) if transition else None
        boundary_revision = int(transition["boundary_revision"]) if transition else 1

        if transition is None:
            # Create the transition constraint
            transition_id = str(uuid.uuid4())
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO shot_transition_constraints
                    (id, from_shot_id, to_shot_id, constraint_type, enforcement, compatibility_status, boundary_revision,
                     revision, created_at, updated_at, created_by, schema_version)
                    VALUES (?, ?, ?, 'START_FROM_PREVIOUS_LAST', 'ADVISORY', 'PENDING_REVIEW', 1, 1, datetime('now'), datetime('now'), ?, 'v2')""",
                    (transition_id, from_shot["id"], next_shot["id"], actor),
                )

        # Register or find anchor for the tail frame
        anchor_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            media_row = connection.execute(
                "SELECT sha256 FROM media_versions WHERE id = ?",
                (tail_media_version_id,),
            ).fetchone()
            sha256_val = str(media_row["sha256"]) if media_row else ""

            connection.execute(
                """INSERT INTO frame_anchors
                (id, source_media_version_id, source_time_us, source_frame_index,
                 extracted_media_version_id, role_hint, sha256, created_at, updated_at,
                 created_by, revision, schema_version, is_stale, requested_time_us,
                 resolved_time_us, source_sha256, extraction_method)
                VALUES (?, ?, 0, 0, ?, 'LAST_FRAME', ?, datetime('now'), datetime('now'),
                 ?, 1, 'v2', 0, 0, 0, ?, 'AUTO_EXTRACTED_TAIL_FRAME')""",
                (
                    anchor_id,
                    tail_media_version_id,
                    tail_media_version_id,
                    sha256_val,
                    actor,
                    sha256_val,
                ),
            )
            # Update transition constraint from_anchor_id
            connection.execute(
                """UPDATE shot_transition_constraints
                SET from_anchor_id = ?, updated_at = datetime('now')
                WHERE id = ?""",
                (anchor_id, transition_id),
            )

        # Inherit to next shot
        idempotency_key = f"auto-chain-{from_shot_id}-{next_shot['id']}-{anchor_id[:8]}"
        try:
            inherited = self.bridge_commands.inherit(
                transition_id,
                expected_boundary_revision=boundary_revision,
                source_anchor_id=anchor_id,
                idempotency_key=idempotency_key,
                actor=actor,
            )
            return {
                "chained": True,
                "transition_id": transition_id,
                "from_shot_id": str(from_shot["id"]),
                "to_shot_id": str(next_shot["id"]),
                "from_shot_code": str(from_shot["code"]),
                "to_shot_code": str(next_shot["code"]),
                "anchor_id": anchor_id,
                "inherited": inherited,
            }
        except Exception as err:
            logger.warning("Auto-chain inherit failed: %s", err)
            return {
                "chained": False,
                "reason": f"INHERIT_FAILED: {err}",
                "transition_id": transition_id,
            }
