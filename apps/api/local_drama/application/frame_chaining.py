"""Explicit, idempotent frame chaining over existing bridge records."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.application.ports.database import DatabaseUnitOfWork


class FrameChainingService:
    """Apply an authorized tail IMAGE to an existing continuity intent."""

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database
        self.bridge_commands = FrameBridgeCommandService(database)

    def auto_chain_shot_tail_to_next(
        self,
        from_shot_id: str,
        *,
        tail_media_version_id: str,
        actor: str = "auto-chaining",
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            from_shot = connection.execute(
                """SELECT id,episode_id,scene_id,order_key,code FROM shots
                WHERE id=? AND archived_at IS NULL""",
                (from_shot_id,),
            ).fetchone()
            if from_shot is None:
                return {"chained": False, "reason": "FROM_SHOT_NOT_FOUND"}
            next_shot = connection.execute(
                """SELECT id,scene_id,code FROM shots
                WHERE episode_id=? AND CAST(order_key AS REAL)>CAST(? AS REAL)
                AND archived_at IS NULL ORDER BY CAST(order_key AS REAL),code LIMIT 1""",
                (from_shot["episode_id"], from_shot["order_key"]),
            ).fetchone()
            if next_shot is None:
                return {"chained": False, "reason": "NO_SUCCESSOR_SHOT"}
            from_scene = str(from_shot["scene_id"] or "").strip()
            to_scene = str(next_shot["scene_id"] or "").strip()
            if not from_scene or not to_scene:
                return {"chained": False, "reason": "SCENE_ID_UNKNOWN"}
            if from_scene != to_scene:
                return {
                    "chained": False,
                    "reason": "SCENE_CUT_DETECTED",
                    "from_shot_code": str(from_shot["code"]),
                    "to_shot_code": str(next_shot["code"]),
                }
            transition = connection.execute(
                """SELECT * FROM shot_transition_constraints
                WHERE from_shot_id=? AND to_shot_id=? AND is_stale=0
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (from_shot_id, next_shot["id"]),
            ).fetchone()
            if transition is None:
                return {"chained": False, "reason": "EXPLICIT_TRANSITION_REQUIRED"}
            if str(transition["constraint_type"]) not in {
                "START_FROM_PREVIOUS_LAST",
                "LAST_TO_FIRST",
                "SHARED_BOUNDARY_FRAME",
            }:
                return {"chained": False, "reason": "TRANSITION_DOES_NOT_INHERIT_TAIL"}
            media = connection.execute(
                """SELECT mv.sha256,mv.integrity_status,ma.media_kind
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE mv.id=?""",
                (tail_media_version_id,),
            ).fetchone()
            if media is None:
                return {"chained": False, "reason": "TAIL_MEDIA_NOT_FOUND"}
            if str(media["media_kind"]) != "IMAGE" or str(media["integrity_status"]) != "VERIFIED":
                return {"chained": False, "reason": "VERIFIED_TAIL_IMAGE_REQUIRED"}
            if FrameBridgeCommandService._media_shot_id(connection, tail_media_version_id) != from_shot_id:
                return {"chained": False, "reason": "TAIL_MEDIA_SHOT_MISMATCH"}
            anchor = connection.execute(
                """SELECT * FROM frame_anchors
                WHERE extracted_media_version_id=? AND role_hint='LAST_FRAME' AND is_stale=0
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (tail_media_version_id,),
            ).fetchone()
            transition_id = str(transition["id"])
            boundary_revision = int(transition["boundary_revision"])
            next_shot_id = str(next_shot["id"])
            next_shot_code = str(next_shot["code"])
            from_shot_code = str(from_shot["code"])
            source_sha256 = str(media["sha256"])

        event_key = hashlib.sha256(
            f"{transition_id}:{from_shot_id}:{next_shot_id}:{tail_media_version_id}:{source_sha256}".encode()
        ).hexdigest()
        idempotency_key = f"auto-chain:{event_key}"
        with self.database.connect() as connection:
            replay = connection.execute(
                """SELECT response_json FROM command_idempotencies
                WHERE scope=? AND idempotency_key=?""",
                (f"frame-bridge:{transition_id}:INHERIT", idempotency_key),
            ).fetchone()
        if replay is not None:
            inherited = json.loads(str(replay["response_json"]))
            inherited["idempotent_replay"] = True
            return self._result(
                transition_id=transition_id,
                from_shot_id=from_shot_id,
                to_shot_id=next_shot_id,
                from_shot_code=from_shot_code,
                to_shot_code=next_shot_code,
                source_anchor_id=str(inherited.get("from_anchor_id") or ""),
                tail_media_version_id=tail_media_version_id,
                source_sha256=source_sha256,
                inherited=inherited,
            )

        if anchor is None:
            source_anchor_id = str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO frame_anchors
                    (id,source_media_version_id,source_time_us,source_frame_index,extracted_media_version_id,
                     role_hint,sha256,created_at,updated_at,created_by,revision,schema_version,is_stale,
                     requested_time_us,resolved_time_us,source_sha256,extraction_method)
                    VALUES (?,?,0,0,?,'LAST_FRAME',?,?,?,?,1,'v2',0,0,0,?,'STATIC_IMAGE_REFERENCE')""",
                    (
                        source_anchor_id,
                        tail_media_version_id,
                        tail_media_version_id,
                        source_sha256,
                        now,
                        now,
                        actor,
                        source_sha256,
                    ),
                )
        else:
            source_anchor_id = str(anchor["id"])

        try:
            inherited = self.bridge_commands.inherit(
                transition_id,
                expected_boundary_revision=boundary_revision,
                source_anchor_id=source_anchor_id,
                idempotency_key=idempotency_key,
                actor=actor,
            )
        except Exception as error:
            return {
                "chained": False,
                "reason": "INHERIT_FAILED",
                "error_code": getattr(error, "code", type(error).__name__),
                "transition_id": transition_id,
            }
        return self._result(
            transition_id=transition_id,
            from_shot_id=from_shot_id,
            to_shot_id=next_shot_id,
            from_shot_code=from_shot_code,
            to_shot_code=next_shot_code,
            source_anchor_id=source_anchor_id,
            tail_media_version_id=tail_media_version_id,
            source_sha256=source_sha256,
            inherited=inherited,
        )

    @staticmethod
    def _result(
        *,
        transition_id: str,
        from_shot_id: str,
        to_shot_id: str,
        from_shot_code: str,
        to_shot_code: str,
        source_anchor_id: str,
        tail_media_version_id: str,
        source_sha256: str,
        inherited: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "chained": True,
            "transition_id": transition_id,
            "from_shot_id": from_shot_id,
            "to_shot_id": to_shot_id,
            "from_shot_code": from_shot_code,
            "to_shot_code": to_shot_code,
            "anchor_id": source_anchor_id,
            "source_media_version_id": tail_media_version_id,
            "source_sha256": source_sha256,
            "source_position": "LAST_FRAME",
            "inherited": inherited,
        }
