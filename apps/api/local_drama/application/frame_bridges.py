"""Semantic Frame Bridge commands over existing continuity facts.

The current linkage remains ``shot_transition_constraints`` and immutable frame
history remains ``frame_anchors``.  This service deliberately introduces no
second bridge state table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.application.ports.frame_bridges import FrameBridgeUnitOfWork
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FrameBridgeCommandService:
    """Fine-grained writes for the Director Desk continuity projection."""

    def __init__(self, database: FrameBridgeUnitOfWork) -> None:
        self.database = database

    def inherit(
        self,
        transition_id: str,
        *,
        expected_boundary_revision: int,
        source_anchor_id: str | None = None,
        lock: bool | None = None,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Copy the previous end frame into a new current-start anchor.

        A new anchor is intentional: re-inherit advances the constraint pointer
        while the stale predecessor and every old inherited anchor stay
        queryable as production history.
        """
        command_payload = {
            "expected_boundary_revision": expected_boundary_revision,
            "source_anchor_id": source_anchor_id,
            "lock": lock,
        }
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, transition_id, "INHERIT", idempotency_key, command_payload)
            if replay is not None:
                return replay
            transition = self._transition(connection, transition_id)
            self._expect_revision(transition, expected_boundary_revision)
            selected_source_id = source_anchor_id or transition["from_anchor_id"]
            if not selected_source_id:
                raise DomainRuleError(
                    "FRAME_BRIDGE_SOURCE_REQUIRED",
                    "上一镜尚无可继承的尾帧，请先选择或提取尾帧",
                    {"transition_id": transition_id},
                )
            source = self._anchor(connection, str(selected_source_id))
            self._require_fresh_anchor(source, side="from")
            self._require_anchor_shot(connection, source, str(transition["from_shot_id"]), side="from")

            inherited_id = str(uuid.uuid4())
            now = _now()
            connection.execute(
                """INSERT INTO frame_anchors
                (id,source_media_version_id,source_time_us,source_frame_index,extracted_media_version_id,
                 role_hint,sha256,approval_id,created_at,updated_at,created_by,revision,schema_version,
                 is_stale,stale_reason,requested_time_us,resolved_time_us,source_sha256,extraction_method)
                VALUES (?,?,?,?,?,'FIRST_FRAME',?,?,?,?,?,1,'v2',0,NULL,?,?,?,'INHERITED_FRAME_ANCHOR')""",
                (
                    inherited_id,
                    source["source_media_version_id"],
                    source["source_time_us"],
                    source["source_frame_index"],
                    source["extracted_media_version_id"],
                    source["sha256"],
                    source["approval_id"],
                    now,
                    now,
                    actor,
                    source["requested_time_us"],
                    source["resolved_time_us"],
                    source["source_sha256"],
                ),
            )
            enforcement = str(transition["enforcement"])
            if lock is not None:
                enforcement = "HARD" if lock else "ADVISORY"
            cursor = connection.execute(
                """UPDATE shot_transition_constraints
                SET from_anchor_id=?,to_anchor_id=?,enforcement=?,compatibility_status='PENDING_REVIEW',
                    is_stale=0,stale_reason=NULL,boundary_revision=boundary_revision+1,
                    revision=revision+1,updated_at=?
                WHERE id=? AND boundary_revision=?""",
                (
                    source["id"],
                    inherited_id,
                    enforcement,
                    now,
                    transition_id,
                    expected_boundary_revision,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_revision_conflict(connection, transition_id, expected_boundary_revision)
            self._audit(
                connection,
                actor,
                "FRAME_BRIDGE_INHERITED",
                transition_id,
                "继承上一镜尾帧为当前镜首帧",
                {
                    "source_anchor_id": source["id"],
                    "inherited_anchor_id": inherited_id,
                    "replaced_to_anchor_id": transition["to_anchor_id"],
                    "before_boundary_revision": expected_boundary_revision,
                    "after_boundary_revision": expected_boundary_revision + 1,
                },
            )
            return self._finish_command(
                connection,
                transition_id,
                "INHERIT",
                idempotency_key,
                command_payload,
                inherited_from_anchor_id=str(source["id"]),
            )

    def set_current_frame(
        self,
        transition_id: str,
        *,
        expected_boundary_revision: int,
        media_version_id: str | None = None,
        frame_anchor_id: str | None = None,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if (media_version_id is None) == (frame_anchor_id is None):
            raise DomainRuleError(
                "FRAME_BRIDGE_CANDIDATE_REQUIRED",
                "必须且只能提供 media_version_id 或 frame_anchor_id",
            )
        command_payload = {
            "expected_boundary_revision": expected_boundary_revision,
            "media_version_id": media_version_id,
            "frame_anchor_id": frame_anchor_id,
        }
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, transition_id, "SET_CURRENT_FRAME", idempotency_key, command_payload)
            if replay is not None:
                return replay
            transition = self._transition(connection, transition_id)
            self._expect_revision(transition, expected_boundary_revision)
            if frame_anchor_id:
                anchor = self._anchor(connection, frame_anchor_id)
                self._require_fresh_anchor(anchor, side="to")
                self._require_anchor_shot(connection, anchor, str(transition["to_shot_id"]), side="to")
                new_anchor_id = str(anchor["id"])
            else:
                assert media_version_id is not None
                media = self._media(connection, media_version_id)
                if str(media["media_kind"]) != "IMAGE":
                    raise DomainRuleError("FRAME_BRIDGE_CANDIDATE_INVALID", "当前候选帧必须是 IMAGE MediaVersion")
                if str(media["integrity_status"]) != "VERIFIED":
                    raise DomainRuleError(
                        "FRAME_BRIDGE_CANDIDATE_INTEGRITY_REQUIRED",
                        "当前候选帧尚未通过内容完整性校验",
                        {"integrity_status": media["integrity_status"]},
                    )
                owner_shot_id = self._media_shot_id(connection, media_version_id)
                if owner_shot_id != str(transition["to_shot_id"]):
                    raise DomainRuleError(
                        "FRAME_BRIDGE_CANDIDATE_SHOT_MISMATCH",
                        "候选帧不属于当前镜头",
                        {"expected_shot_id": transition["to_shot_id"], "actual_shot_id": owner_shot_id},
                    )
                new_anchor_id = str(uuid.uuid4())
                now = _now()
                connection.execute(
                    """INSERT INTO frame_anchors
                    (id,source_media_version_id,source_time_us,source_frame_index,extracted_media_version_id,
                     role_hint,sha256,approval_id,created_at,updated_at,created_by,revision,schema_version,
                     is_stale,stale_reason,requested_time_us,resolved_time_us,source_sha256,extraction_method)
                    VALUES (?,?,0,0,?,'FIRST_FRAME',?,NULL,?,?,?,1,'v2',0,NULL,0,0,?,NULL)""",
                    (new_anchor_id, media_version_id, media_version_id, media["sha256"], now, now, actor, media["sha256"]),
                )
            now = _now()
            cursor = connection.execute(
                """UPDATE shot_transition_constraints
                SET to_anchor_id=?,compatibility_status='PENDING_REVIEW',is_stale=0,stale_reason=NULL,
                    boundary_revision=boundary_revision+1,revision=revision+1,updated_at=?
                WHERE id=? AND boundary_revision=?""",
                (new_anchor_id, now, transition_id, expected_boundary_revision),
            )
            if cursor.rowcount != 1:
                self._raise_revision_conflict(connection, transition_id, expected_boundary_revision)
            self._audit(
                connection,
                actor,
                "FRAME_BRIDGE_CURRENT_FRAME_SET",
                transition_id,
                "设置当前镜头候选首帧",
                {
                    "anchor_id": new_anchor_id,
                    "media_version_id": media_version_id,
                    "replaced_to_anchor_id": transition["to_anchor_id"],
                    "before_boundary_revision": expected_boundary_revision,
                },
            )
            return self._finish_command(
                connection, transition_id, "SET_CURRENT_FRAME", idempotency_key, command_payload
            )

    def set_source_frame(
        self,
        transition_id: str,
        *,
        expected_boundary_revision: int,
        frame_anchor_id: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Bind an extracted tail frame to the outgoing side of a boundary."""
        command_payload = {
            "expected_boundary_revision": expected_boundary_revision,
            "frame_anchor_id": frame_anchor_id,
        }
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, transition_id, "SET_SOURCE_FRAME", idempotency_key, command_payload)
            if replay is not None:
                return replay
            transition = self._transition(connection, transition_id)
            self._expect_revision(transition, expected_boundary_revision)
            anchor = self._anchor(connection, frame_anchor_id)
            self._require_fresh_anchor(anchor, side="from")
            self._require_anchor_shot(connection, anchor, str(transition["from_shot_id"]), side="from")
            if str(anchor["role_hint"] or "").upper() != "LAST_FRAME":
                raise DomainRuleError(
                    "FRAME_BRIDGE_SOURCE_ROLE_INVALID",
                    "镜头桥尾帧来源必须是 LAST_FRAME FrameAnchor",
                    {"anchor_id": frame_anchor_id, "role_hint": anchor["role_hint"]},
                )
            now = _now()
            cursor = connection.execute(
                """UPDATE shot_transition_constraints
                SET from_anchor_id=?,compatibility_status='PENDING_REVIEW',is_stale=0,stale_reason=NULL,
                    boundary_revision=boundary_revision+1,revision=revision+1,updated_at=?
                WHERE id=? AND boundary_revision=?""",
                (frame_anchor_id, now, transition_id, expected_boundary_revision),
            )
            if cursor.rowcount != 1:
                self._raise_revision_conflict(connection, transition_id, expected_boundary_revision)
            self._audit(
                connection,
                actor,
                "FRAME_BRIDGE_SOURCE_FRAME_SET",
                transition_id,
                "设置当前镜头真实尾帧来源",
                {
                    "anchor_id": frame_anchor_id,
                    "replaced_from_anchor_id": transition["from_anchor_id"],
                    "before_boundary_revision": expected_boundary_revision,
                    "after_boundary_revision": expected_boundary_revision + 1,
                },
            )
            return self._finish_command(
                connection, transition_id, "SET_SOURCE_FRAME", idempotency_key, command_payload
            )

    def set_locked(
        self,
        transition_id: str,
        *,
        expected_boundary_revision: int,
        locked: bool,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        command_payload = {"expected_boundary_revision": expected_boundary_revision, "locked": locked}
        command = "LOCK" if locked else "UNLOCK"
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, transition_id, command, idempotency_key, command_payload)
            if replay is not None:
                return replay
            transition = self._transition(connection, transition_id)
            self._expect_revision(transition, expected_boundary_revision)
            if locked and int(transition["is_stale"]):
                raise DomainRuleError("FRAME_BRIDGE_STALE", "失效的 Frame Bridge 不能锁定，请先重新继承")
            enforcement = "HARD" if locked else "ADVISORY"
            cursor = connection.execute(
                """UPDATE shot_transition_constraints SET enforcement=?,boundary_revision=boundary_revision+1,
                revision=revision+1,updated_at=? WHERE id=? AND boundary_revision=?""",
                (enforcement, _now(), transition_id, expected_boundary_revision),
            )
            if cursor.rowcount != 1:
                self._raise_revision_conflict(connection, transition_id, expected_boundary_revision)
            self._audit(
                connection,
                actor,
                "FRAME_BRIDGE_LOCKED" if locked else "FRAME_BRIDGE_UNLOCKED",
                transition_id,
                "锁定 Frame Bridge" if locked else "解锁 Frame Bridge",
                {"before_boundary_revision": expected_boundary_revision, "enforcement": enforcement},
            )
            return self._finish_command(connection, transition_id, command, idempotency_key, command_payload)

    @staticmethod
    def _idempotency_scope(transition_id: str, command: str) -> str:
        return f"frame-bridge:{transition_id}:{command}"

    @classmethod
    def _idempotent_replay(
        cls,
        connection: sqlite3.Connection,
        transition_id: str,
        command: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError(
                "FRAME_BRIDGE_IDEMPOTENCY_KEY_INVALID",
                "Frame Bridge idempotency_key 必须为 1—200 字符",
            )
        payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        row = connection.execute(
            "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
            (cls._idempotency_scope(transition_id, command), key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise DomainRuleError(
                "FRAME_BRIDGE_IDEMPOTENCY_MISMATCH",
                "相同 Frame Bridge idempotency_key 的请求内容不一致",
                {"transition_id": transition_id, "command": command},
            )
        result = cast(dict[str, Any], json.loads(str(row["response_json"])))
        result["idempotent_replay"] = True
        return result

    @classmethod
    def _finish_command(
        cls,
        connection: sqlite3.Connection,
        transition_id: str,
        command: str,
        idempotency_key: str,
        payload: dict[str, Any],
        *,
        inherited_from_anchor_id: str | None = None,
    ) -> dict[str, Any]:
        result = cls._result(
            connection,
            transition_id,
            inherited_from_anchor_id=inherited_from_anchor_id,
        )
        result["idempotent_replay"] = False
        payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
            (
                cls._idempotency_scope(transition_id, command),
                idempotency_key.strip(),
                payload_hash,
                _json(result),
            ),
        )
        scope = connection.execute(
            """SELECT se.project_id,s.episode_id,t.from_shot_id,t.to_shot_id
            FROM shot_transition_constraints t
            JOIN shots s ON s.id=t.to_shot_id
            JOIN episodes e ON e.id=s.episode_id
            JOIN seasons se ON se.id=e.season_id
            WHERE t.id=?""",
            (transition_id,),
        ).fetchone()
        assert scope is not None
        connection.execute(
            """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
            VALUES ('FrameBridgeChanged',?,'SHOT_TRANSITION_CONSTRAINT',?,?)""",
            (
                scope["project_id"],
                transition_id,
                _json(
                    {
                        "command": command,
                        "episode_id": scope["episode_id"],
                        "from_shot_id": scope["from_shot_id"],
                        "to_shot_id": scope["to_shot_id"],
                        "boundary_revision": result["boundary_revision"],
                    }
                ),
            ),
        )
        return result

    @staticmethod
    def _transition(connection: sqlite3.Connection, transition_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM shot_transition_constraints WHERE id=?", (transition_id,)).fetchone()
        if row is None:
            raise DomainRuleError("FRAME_BRIDGE_NOT_FOUND", "Frame Bridge 不存在", {"transition_id": transition_id})
        return cast(sqlite3.Row, row)

    @staticmethod
    def _expect_revision(transition: sqlite3.Row, expected: int) -> None:
        actual = int(transition["boundary_revision"])
        if actual != expected:
            raise DomainRuleError(
                "REVISION_CONFLICT",
                "Frame Bridge 已被其他操作更新，请刷新后重试",
                {"expected_boundary_revision": expected, "actual_boundary_revision": actual},
            )

    @staticmethod
    def _raise_revision_conflict(connection: sqlite3.Connection, transition_id: str, expected: int) -> None:
        row = connection.execute(
            "SELECT boundary_revision FROM shot_transition_constraints WHERE id=?", (transition_id,)
        ).fetchone()
        raise DomainRuleError(
            "REVISION_CONFLICT",
            "Frame Bridge 已被其他操作更新，请刷新后重试",
            {"expected_boundary_revision": expected, "actual_boundary_revision": int(row[0]) if row else None},
        )

    @staticmethod
    def _anchor(connection: sqlite3.Connection, anchor_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM frame_anchors WHERE id=?", (anchor_id,)).fetchone()
        if row is None:
            raise DomainRuleError("FRAME_ANCHOR_NOT_FOUND", "FrameAnchor 不存在", {"anchor_id": anchor_id})
        return cast(sqlite3.Row, row)

    @staticmethod
    def _require_fresh_anchor(anchor: sqlite3.Row, *, side: str) -> None:
        if int(anchor["is_stale"]):
            raise DomainRuleError(
                "FRAME_ANCHOR_STALE",
                "不能绑定已失效的 FrameAnchor",
                {"side": side, "anchor_id": anchor["id"], "stale_reason": anchor["stale_reason"]},
            )

    @classmethod
    def _require_anchor_shot(
        cls, connection: sqlite3.Connection, anchor: sqlite3.Row, expected_shot_id: str, *, side: str
    ) -> None:
        actual = cls._media_shot_id(connection, str(anchor["source_media_version_id"]))
        if actual != expected_shot_id:
            raise DomainRuleError(
                "FRAME_ANCHOR_SHOT_MISMATCH",
                "FrameAnchor 不属于连续性边界对应镜头",
                {"side": side, "expected_shot_id": expected_shot_id, "actual_shot_id": actual},
            )

    @staticmethod
    def _media(connection: sqlite3.Connection, media_version_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT mv.*,ma.media_kind FROM media_versions mv
            JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
            (media_version_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "MediaVersion 不存在")
        return cast(sqlite3.Row, row)

    @classmethod
    def _media_shot_id(cls, connection: sqlite3.Connection, media_version_id: str) -> str | None:
        row = connection.execute(
            """SELECT ma.owner_type,ma.owner_id FROM media_versions mv
            JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
            (media_version_id,),
        ).fetchone()
        if row is None:
            return None
        owner_type, owner_id = str(row["owner_type"]), str(row["owner_id"])
        if owner_type == "SHOT":
            return owner_id
        if owner_type == "GENERATION_VARIANT":
            owner = connection.execute(
                """SELECT gi.owner_type,gi.owner_id FROM generation_variants gv
                JOIN generation_intents gi ON gi.id=gv.intent_id WHERE gv.id=?""",
                (owner_id,),
            ).fetchone()
            return str(owner["owner_id"]) if owner and owner["owner_type"] == "SHOT" else None
        if owner_type == "MEDIA_VERSION" and owner_id != media_version_id:
            return cls._media_shot_id(connection, owner_id)
        return None

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        actor: str,
        action: str,
        subject_id: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'producer',?,'shot_transition_constraint',?,?,?)""",
            (actor, action, subject_id, summary, _json(metadata)),
        )

    @staticmethod
    def _result(
        connection: sqlite3.Connection, transition_id: str, *, inherited_from_anchor_id: str | None = None
    ) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM shot_transition_constraints WHERE id=?", (transition_id,)).fetchone()
        assert row is not None
        result = dict(row)
        result["is_stale"] = bool(result["is_stale"])
        result["locked"] = str(result["enforcement"]).upper() in {"HARD", "LOCKED"}
        if inherited_from_anchor_id:
            result["inherited_from_anchor_id"] = inherited_from_anchor_id
        return result
