"""Transactional SQLite repository for immutable Review frame annotations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

_CATEGORIES = {"IDENTITY", "MOTION", "ARTIFACT", "FLICKER", "AUDIO_SYNC", "SUBTITLE", "CONTINUITY", "OTHER"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SqliteReviewAnnotationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _target(connection: sqlite3.Connection, target_kind: str, target_id: str) -> dict[str, Any]:
        if target_kind != "MEDIA_VERSION":
            raise DomainRuleError("REVIEW_ANNOTATION_TARGET_UNSUPPORTED", "逐帧批注只支持视频媒体目标")
        row = connection.execute(
            """SELECT mv.id,ma.id AS media_asset_id,ma.project_id,ma.revision AS subject_revision,
            ma.media_kind,mv.integrity_status,mv.duration_ms
            FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
            (target_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("REVIEW_TARGET_NOT_FOUND", "审核目标不存在", {"target_id": target_id})
        target = dict(row)
        if str(target["media_kind"]) != "VIDEO":
            raise DomainRuleError("REVIEW_ANNOTATION_REQUIRES_VIDEO", "逐帧批注只支持视频媒体目标")
        return target

    def list_page(self, target_kind: str, target_id: str, *, cursor: int, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._target(connection, target_kind, target_id)
            total = int(connection.execute(
                "SELECT COUNT(*) FROM video_review_annotations WHERE media_version_id=?", (target_id,)
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT id,media_version_id,timecode_ms,category,comment,snapshot_media_version_id,
                rework_job_id,created_at,created_by FROM video_review_annotations
                WHERE media_version_id=? ORDER BY timecode_ms,created_at,id LIMIT ? OFFSET ?""",
                (target_id, limit, cursor),
            ).fetchall()
        items = [
            {**dict(row), "target_kind": "MEDIA_VERSION", "target_id": str(row["media_version_id"]), "idempotent_replay": False}
            for row in rows
        ]
        for item in items:
            item.pop("media_version_id", None)
        return {
            "items": items, "cursor": cursor, "limit": limit, "total": total,
            "next_cursor": cursor + limit if cursor + limit < total else None,
        }

    def create(self, target_kind: str, target_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key = str(command["idempotency_key"]).strip()
        scope = f"review-annotation:create:{target_kind}:{target_id}"
        canonical = {name: value for name, value in command.items() if name != "idempotency_key"}
        payload_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        now = _now()
        with self.database.transaction() as connection:
            replay = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if replay is not None:
                if str(replay["payload_hash"]) != payload_hash:
                    raise DomainRuleError("REVIEW_IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 idempotency_key 的批注请求内容不一致")
                return {**json.loads(str(replay["response_json"])), "idempotent_replay": True}
            target = self._target(connection, target_kind, target_id)
            expected = int(command["expected_revision"])
            actual = int(target["subject_revision"])
            if expected != actual:
                raise DomainRuleError(
                    "REVIEW_REVISION_CONFLICT", "审核目标已变化，请刷新后重试",
                    {"expected_revision": expected, "actual_revision": actual},
                )
            if str(target["integrity_status"]) != "VERIFIED":
                raise DomainRuleError("REVIEW_ANNOTATION_MEDIA_NOT_VERIFIED", "只有完整性已验证的视频可以批注")
            duration_ms = int(target["duration_ms"] or 0)
            timecode_ms = int(command["timecode_ms"])
            if duration_ms <= 0:
                raise DomainRuleError("VIDEO_DURATION_UNVERIFIED", "视频时长未核验，不能保存逐帧批注")
            if timecode_ms >= duration_ms:
                raise DomainRuleError(
                    "REVIEW_ANNOTATION_TIMECODE_OUT_OF_RANGE", "批注时间必须位于视频时长范围内",
                    {"timecode_ms": timecode_ms, "duration_ms": duration_ms},
                )
            category = str(command["category"]).strip().upper()
            if category not in _CATEGORIES:
                raise DomainRuleError("REVIEW_ANNOTATION_CATEGORY_INVALID", "逐帧批注分类无效")
            comment = str(command["comment"]).strip()
            if not comment:
                raise DomainRuleError("REVIEW_ANNOTATION_COMMENT_REQUIRED", "逐帧批注必须填写内容")
            snapshot_id = command.get("snapshot_media_version_id")
            if snapshot_id:
                snapshot = connection.execute(
                    """SELECT mv.id,mv.parent_version_id,ma.project_id,ma.media_kind,
                    EXISTS(SELECT 1 FROM frame_anchors fa WHERE fa.source_media_version_id=?
                      AND fa.extracted_media_version_id=mv.id) AS is_extracted_frame
                    FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
                    (target_id, snapshot_id),
                ).fetchone()
                if snapshot is None or str(snapshot["project_id"]) != str(target["project_id"]) or str(snapshot["media_kind"]) != "IMAGE" or (
                    str(snapshot["parent_version_id"] or "") != target_id and not bool(snapshot["is_extracted_frame"])
                ):
                    raise DomainRuleError("REVIEW_ANNOTATION_SNAPSHOT_INVALID", "截图必须由当前视频派生且属于同一项目")
            rework_job_id = command.get("rework_job_id")
            if rework_job_id:
                job = connection.execute("SELECT project_id FROM jobs WHERE id=?", (rework_job_id,)).fetchone()
                if job is None or str(job["project_id"]) != str(target["project_id"]):
                    raise DomainRuleError("REVIEW_ANNOTATION_REWORK_JOB_INVALID", "返工任务必须属于同一项目")
            annotation_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO video_review_annotations
                (id,media_version_id,timecode_ms,category,comment,snapshot_media_version_id,rework_job_id,
                 created_at,created_by,schema_version) VALUES (?,?,?,?,?,?,?,?,?,'v2')""",
                (annotation_id, target_id, timecode_ms, category, comment, snapshot_id, rework_job_id, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'reviewer','REVIEW_ANNOTATION_CREATED','media_version',?,?,?,?,?)""",
                (actor, target_id, actual, actual, "创建逐帧批注", _json({"annotation_id": annotation_id, "timecode_ms": timecode_ms, "category": category})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('ReviewAnnotationChanged',?,'MEDIA_VERSION',?,?)""",
                (target["project_id"], target_id, _json({"annotation_id": annotation_id, "timecode_ms": timecode_ms, "category": category})),
            )
            result = {
                "id": annotation_id, "target_kind": "MEDIA_VERSION", "target_id": target_id,
                "timecode_ms": timecode_ms, "category": category, "comment": comment,
                "snapshot_media_version_id": snapshot_id, "rework_job_id": rework_job_id,
                "created_at": now, "created_by": actor, "idempotent_replay": False,
            }
            connection.execute(
                """INSERT INTO command_idempotencies
                (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)""",
                (scope, key, payload_hash, _json(result)),
            )
            return result
