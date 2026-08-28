from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import VALID_SHOT_TRANSITIONS, require_transition
from local_drama.infrastructure.database.sqlite import Database

if TYPE_CHECKING:
    from local_drama.application.shot_studio_commands import ShotStudioCommandService


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SqliteShotStudioCommandRepository:
    """Single SQLite write authority for Shot Studio commands."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def camera_profile(self, profile_version_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status,parameter_schema_json FROM execution_profile_versions WHERE id=?",
                (profile_version_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def current_draft(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.id,s.status,s.current_revision_id,s.revision,
                sr.revision_no,sr.fields_json,sr.is_frozen
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.id=?""",
                (shot_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        if row["current_revision_id"] is None:
            raise DomainRuleError("SHOT_REVISION_REQUIRED", "镜头就绪前必须先保存导演意图")
        return {
            "id": str(row["current_revision_id"]),
            "shot_id": shot_id,
            "revision_no": int(row["revision_no"]),
            "fields": json.loads(str(row["fields_json"])),
            "is_frozen": bool(row["is_frozen"]),
        }

    def save_draft(
        self,
        shot_id: str,
        fields: dict[str, Any],
        *,
        freeze: bool,
        expected_revision_no: int | None,
        actor: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        revision_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            shot = self._shot(connection, shot_id)
            current = self._current_revision_no(connection, shot_id)
            self._assert_revision(shot_id, current, expected_revision_no)
            revision_no = current + 1
            connection.execute(
                """INSERT INTO shot_revisions
                (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by)
                VALUES (?,?,?,?,?,?,?,?)""",
                (revision_id, shot_id, revision_no, _json(fields), int(freeze), now, now, actor),
            )
            connection.execute(
                "UPDATE shots SET current_revision_id=?,status='DIRECTED',revision=revision+1,updated_at=? WHERE id=?",
                (revision_id, now, shot_id),
            )
            self._record_revision(connection, shot, revision_id, revision_no, freeze, actor)
            self._mark_reviews_stale(connection, shot_id, actor, now)
        return {
            "shot_revision": self._revision_result(revision_id, shot_id, revision_no, fields, freeze),
            "shot": self._shot_result(shot_id),
        }

    def mark_ready(
        self,
        shot_id: str,
        *,
        fields: dict[str, Any] | None,
        freeze: bool,
        expected_revision_no: int | None,
        actor: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        revision_id: str | None = None
        revision_no: int | None = None
        with self.database.transaction() as connection:
            shot = self._shot(connection, shot_id)
            require_transition(VALID_SHOT_TRANSITIONS, str(shot["status"]), "READY", "Shot")
            current = self._current_revision_no(connection, shot_id)
            self._assert_revision(shot_id, current, expected_revision_no)
            if fields is not None:
                revision_id = str(uuid.uuid4())
                revision_no = current + 1
                connection.execute(
                    """INSERT INTO shot_revisions
                    (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (revision_id, shot_id, revision_no, _json(fields), int(freeze), now, now, actor),
                )
                connection.execute(
                    "UPDATE shots SET current_revision_id=?,status='READY',revision=revision+1,updated_at=? WHERE id=?",
                    (revision_id, now, shot_id),
                )
                self._record_revision(connection, shot, revision_id, revision_no, freeze, actor)
                self._mark_reviews_stale(connection, shot_id, actor, now)
            else:
                revision_id = str(shot["current_revision_id"])
                connection.execute(
                    "UPDATE shots SET status='READY',revision=revision+1,updated_at=? WHERE id=?",
                    (now, shot_id),
                )
            ready_event = {
                "shot_id": shot_id,
                "revision_id": revision_id,
                "from_status": str(shot["status"]),
                "to_status": "READY",
                "atomic_save": fields is not None,
            }
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'director','SHOT_MARKED_PRODUCTION_READY','shot',?,'镜头已标记 Production Ready',?)""",
                (actor, shot_id, _json(ready_event)),
            )
            connection.execute(
                """INSERT INTO outbox_events
                (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('SHOT_PRODUCTION_READY',?,'SHOT',?,?)""",
                (shot["project_id"], shot_id, _json(ready_event)),
            )
        result: dict[str, Any] = {"shot": self._shot_result(shot_id)}
        if fields is not None and revision_id is not None and revision_no is not None:
            result["shot_revision"] = self._revision_result(revision_id, shot_id, revision_no, fields, freeze)
        else:
            result["shot_revision"] = self.current_draft(shot_id)
        return result

    def adopt_working_version(self, media_version_id: str, *, actor: str) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            media = connection.execute(
                """SELECT mv.id,mv.stage,mv.integrity_status,ma.id AS media_asset_id,
                ma.media_kind,ma.project_id,ma.revision,
                CASE
                  WHEN ma.owner_type='SHOT' THEN ma.owner_id
                  WHEN ma.owner_type='GENERATION_VARIANT' THEN gi.owner_id
                  ELSE NULL
                END AS shot_id,
                CASE
                  WHEN ma.owner_type='SHOT' THEN 'SHOT'
                  WHEN ma.owner_type='GENERATION_VARIANT' THEN gi.owner_type
                  ELSE ma.owner_type
                END AS resolved_owner_type
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
                LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                WHERE mv.id=?""",
                (media_version_id,),
            ).fetchone()
            if media is None:
                raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "媒体版本不存在")
            media = dict(media)
            if not media["shot_id"]:
                lab_promotion = connection.execute(
                    """SELECT target_id FROM visual_lab_promotions
                    WHERE source_media_version_id=? AND target_type='SHOT_CANDIDATE'
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (media_version_id,),
                ).fetchone()
                if lab_promotion is not None:
                    media["shot_id"] = lab_promotion["target_id"]
                    media["resolved_owner_type"] = "SHOT"
            if str(media["resolved_owner_type"] or "") != "SHOT" or not media["shot_id"]:
                raise DomainRuleError("SHOT_MEDIA_REQUIRED", "Shot Studio 只能采用属于镜头的媒体候选")
            stage = str(media["stage"])
            kind = str(media["media_kind"])
            if stage == "FORMAL":
                raise DomainRuleError("FORMAL_VERSION_REVIEW_ONLY", "正式版本只能在审核工作区完成选择")
            if kind == "IMAGE" and stage == "KEYFRAME":
                selection_type = "KEYFRAME"
                slot_type = "KEYFRAME"
            elif kind == "VIDEO" and stage == "PROXY":
                selection_type = "PROXY_WINNER"
                slot_type = "VIDEO"
            else:
                raise DomainRuleError(
                    "UNSUPPORTED_WORKING_VERSION",
                    "Shot Studio 仅能采用 KEYFRAME 图片或 PROXY 视频作为工作版本",
                    {"media_kind": kind, "stage": stage},
                )
            existing = connection.execute(
                """SELECT id,media_version_id,revision FROM shot_working_media_slots
                WHERE shot_id=? AND slot_type=?""",
                (media["shot_id"], slot_type),
            ).fetchone()
            replayed = existing is not None and str(existing["media_version_id"]) == media_version_id
            if replayed:
                slot_id = str(existing["id"])
            else:
                if existing is None:
                    slot_id = str(uuid.uuid4())
                    before_revision = 0
                    after_revision = 1
                    connection.execute(
                        """INSERT INTO shot_working_media_slots
                        (id,shot_id,slot_type,media_version_id,adopted_at,created_at,updated_at,
                         created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,1,'v1')""",
                        (slot_id, media["shot_id"], slot_type, media_version_id, now, now, now, actor),
                    )
                else:
                    slot_id = str(existing["id"])
                    before_revision = int(existing["revision"])
                    after_revision = before_revision + 1
                    connection.execute(
                        """UPDATE shot_working_media_slots SET media_version_id=?,adopted_from_selection_id=NULL,
                        adopted_at=?,updated_at=?,created_by=?,revision=revision+1 WHERE id=?""",
                        (media_version_id, now, now, actor, slot_id),
                    )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,
                     summary,metadata_redacted_json)
                    VALUES (?,'director','SHOT_WORKING_VERSION_ADOPTED','shot_working_media_slot',?,?,?,?,?)""",
                    (
                        actor,
                        slot_id,
                        before_revision,
                        after_revision,
                        "镜头工作版本已采用",
                        _json(
                            {
                                "shot_id": media["shot_id"],
                                "slot_type": slot_type,
                                "media_version_id": media_version_id,
                                "selection_type": selection_type,
                            }
                        ),
                    ),
                )
                connection.execute(
                    """INSERT INTO outbox_events
                    (type,project_id,subject_type,subject_id,payload_json)
                    VALUES ('SHOT_WORKING_MEDIA_ADOPTED',?,'SHOT',?,?)""",
                    (
                        media["project_id"],
                        media["shot_id"],
                        _json(
                            {
                                "shot_id": media["shot_id"],
                                "slot_id": slot_id,
                                "slot_type": slot_type,
                                "media_version_id": media_version_id,
                            }
                        ),
                    ),
                )
        return {
            "id": slot_id,
            "shot_id": str(media["shot_id"]),
            "media_asset_id": str(media["media_asset_id"]),
            "media_version_id": media_version_id,
            "slot_type": slot_type,
            "selection_type": selection_type,
            "status": "ADOPTED",
            "replayed": replayed,
        }

    @staticmethod
    def _shot(connection: Any, shot_id: str) -> Any:
        row = connection.execute(
            """SELECT s.*,se.project_id FROM shots s JOIN episodes e ON e.id=s.episode_id
            JOIN seasons se ON se.id=e.season_id WHERE s.id=?""",
            (shot_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        return row

    @staticmethod
    def _current_revision_no(connection: Any, shot_id: str) -> int:
        return int(
            connection.execute(
                "SELECT COALESCE(MAX(revision_no),0) FROM shot_revisions WHERE shot_id=?",
                (shot_id,),
            ).fetchone()[0]
        )

    @staticmethod
    def _assert_revision(shot_id: str, current: int, expected: int | None) -> None:
        if expected is not None and current != expected:
            raise DomainRuleError(
                "REVISION_CONFLICT",
                "镜头导演意图已被其他编辑更新，请刷新后重试",
                {
                    "shot_id": shot_id,
                    "expected_revision_no": expected,
                    "current_revision_no": current,
                },
            )

    @staticmethod
    def _record_revision(
        connection: Any,
        shot: Any,
        revision_id: str,
        revision_no: int,
        freeze: bool,
        actor: str,
    ) -> None:
        event = {
            "shot_id": shot["id"],
            "revision_id": revision_id,
            "revision_no": revision_no,
            "schema_version": "director-intent.v3",
            "is_frozen": bool(freeze),
        }
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'director','SHOT_REVISION_CREATED','shot',?,'镜头导演意图 revision 已创建',?)""",
            (actor, shot["id"], _json(event)),
        )
        connection.execute(
            """INSERT INTO outbox_events
            (type,project_id,subject_type,subject_id,payload_json)
            VALUES ('SHOT_REVISION_CREATED',?,'SHOT',?,?)""",
            (shot["project_id"], shot["id"], _json(event)),
        )

    @staticmethod
    def _mark_reviews_stale(connection: Any, shot_id: str, actor: str, now: str) -> None:
        result = connection.execute(
            """UPDATE review_decisions SET is_stale=1,stale_reason='shot_revision_changed',
            updated_at=?,revision=revision+1 WHERE subject_type='MEDIA_VERSION' AND subject_id IN
            (SELECT mv.id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
             WHERE ma.owner_id=?)""",
            (now, shot_id),
        )
        if result.rowcount:
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'system','REVIEWS_MARKED_STALE','owner',?,'上游 revision 变更，审核标记 stale',?)""",
                (actor, shot_id, _json({"count": result.rowcount, "reason": "shot_revision_changed"})),
            )

    @staticmethod
    def _revision_result(
        revision_id: str,
        shot_id: str,
        revision_no: int,
        fields: dict[str, Any],
        freeze: bool,
    ) -> dict[str, Any]:
        return {
            "id": revision_id,
            "shot_id": shot_id,
            "revision_no": revision_no,
            "fields": fields,
            "is_frozen": freeze,
        }

    def _shot_result(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id,status,current_revision_id,revision,updated_at FROM shots WHERE id=?",
                (shot_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        return dict(row)


def shot_studio_command_service(database: Database) -> ShotStudioCommandService:
    """Compose the application command owner at an infrastructure boundary."""
    from local_drama.application.shot_studio_commands import ShotStudioCommandService

    return ShotStudioCommandService(SqliteShotStudioCommandRepository(database))
