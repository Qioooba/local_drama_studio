"""Transactional SQLite implementation of typed Review decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class SqliteReviewDecisionCommandRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _replay(connection: sqlite3.Connection, scope: str, key: str,
                payload_hash: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise DomainRuleError(
                "REVIEW_IDEMPOTENCY_PAYLOAD_MISMATCH",
                "相同 idempotency_key 的审核请求内容不一致",
            )
        return {**json.loads(str(row["response_json"])), "idempotent_replay": True}

    @staticmethod
    def _store(connection: sqlite3.Connection, scope: str, key: str,
               payload_hash: str, response: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO command_idempotencies
            (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)""",
            (scope, key, payload_hash, _json(response)),
        )

    @staticmethod
    def _target(connection: sqlite3.Connection, kind: str, target_id: str) -> dict[str, Any]:
        if kind == "MEDIA_VERSION":
            row = connection.execute(
                """SELECT mv.id,ma.id AS asset_id,ma.project_id,ma.owner_type,ma.owner_id,
                ma.media_kind,mv.stage,mv.integrity_status,ma.revision AS subject_revision,
                ma.approved_version_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE mv.id=?""",
                (target_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT erv.id,NULL AS asset_id,se.project_id,'EPISODE' AS owner_type,
                erv.episode_id AS owner_id,NULL AS media_kind,'FORMAL' AS stage,
                erv.integrity_status,erv.revision AS subject_revision,NULL AS approved_version_id
                FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE erv.id=?""",
                (target_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("REVIEW_TARGET_NOT_FOUND", "审核目标不存在", {"target_kind": kind, "target_id": target_id})
        return dict(row)

    @staticmethod
    def _expected_template_code(target: dict[str, Any], kind: str) -> str:
        if kind == "EPISODE_RENDER_VERSION":
            return "episode_render"
        if str(target["media_kind"]) == "AUDIO":
            return "audio_mix"
        if str(target["media_kind"]) == "VIDEO":
            return "formal_video" if str(target["stage"]) == "FORMAL" else "proxy_video"
        return "image_asset"

    def create(self, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        kind = str(command["target_kind"])
        target_id = str(command["target_id"])
        key = str(command["idempotency_key"]).strip()
        scope = f"review-decision:create:{kind}:{target_id}"
        canonical = {name: value for name, value in command.items() if name != "idempotency_key"}
        payload_hash = _hash(canonical)
        decision = str(command["decision"])
        if decision == "REJECTED" and not str(command.get("comment") or "").strip():
            raise DomainRuleError("REVIEW_COMMENT_REQUIRED", "拒绝审核必须填写原因")
        now = _now()
        with self.database.transaction() as connection:
            replay = self._replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            target = self._target(connection, kind, target_id)
            expected = int(command["expected_revision"])
            actual = int(target["subject_revision"])
            if actual != expected:
                raise DomainRuleError(
                    "REVIEW_REVISION_CONFLICT", "审核目标已变化，请刷新后重试",
                    {"expected_revision": expected, "actual_revision": actual},
                )
            template = connection.execute(
                "SELECT id,code,subject_type,items_json FROM review_templates WHERE id=?",
                (command["template_version_id"],),
            ).fetchone()
            expected_code = self._expected_template_code(target, kind)
            if template is None or str(template["code"]) != expected_code or str(template["subject_type"]) != kind:
                raise DomainRuleError(
                    "REVIEW_TEMPLATE_MISMATCH", "审核模板与目标类型不匹配",
                    {"expected_template_code": expected_code},
                )
            checks = list(command["checks"])
            submitted = {str(item["item_id"]) for item in checks}
            required = {str(item["id"]) for item in json.loads(str(template["items_json"])) if item.get("required", True)}
            missing = sorted(required - submitted)
            if missing:
                raise DomainRuleError("REVIEW_CHECKS_INCOMPLETE", "审核检查项不完整", {"missing": missing})
            failures = sorted(str(item["item_id"]) for item in checks if str(item["result"]) == "FAIL")
            if decision == "APPROVED" and failures:
                raise DomainRuleError("REVIEW_CHECK_FAILED", "存在未通过检查项，不能批准", {"failed": failures})
            if decision == "APPROVED" and str(target["integrity_status"]) != "VERIFIED":
                raise DomainRuleError("REVIEW_TARGET_INTEGRITY_REQUIRED", "审核目标必须先通过完整性验证")
            if decision == "APPROVED" and kind == "MEDIA_VERSION" and (
                str(target["stage"]) == "FORMAL" or str(target["media_kind"]) == "AUDIO"
            ):
                machine = connection.execute(
                    """SELECT status FROM machine_check_runs WHERE subject_type='MEDIA_VERSION' AND subject_id=?
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (target_id,),
                ).fetchone()
                if machine is None or str(machine["status"]) != "PASS":
                    raise DomainRuleError("MACHINE_QC_REQUIRED", "正式媒体或音频必须先通过机器检查")
            decision_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,subject_revision,
                 is_stale,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,0,?,?,?,1,'v2')""",
                (decision_id, kind, target_id, command["template_version_id"], decision,
                 command.get("comment"), actual, now, now, actor),
            )
            for item in checks:
                connection.execute(
                    """INSERT INTO review_checks
                    (id,review_decision_id,item_id,result,comment) VALUES (?,?,?,?,?)""",
                    (str(uuid.uuid4()), decision_id, item["item_id"], item["result"], item.get("comment")),
                )
            if decision == "APPROVED" and kind == "MEDIA_VERSION":
                connection.execute(
                    """UPDATE media_assets SET approved_version_id=?,revision=revision+1,updated_at=?
                    WHERE id=?""",
                    (target_id, now, target["asset_id"]),
                )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'reviewer','REVIEW_DECISION_CREATED','review_decision',?,?,?,?,?)""",
                (actor, decision_id, None, 1, f"审核 {decision}", _json({"target_kind": kind, "target_id": target_id})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('ReviewDecisionChanged',?,'REVIEW_DECISION',?,?)""",
                (target["project_id"], decision_id, _json({"target_kind": kind, "target_id": target_id, "decision": decision})),
            )
            result = {
                "id": decision_id, "target_kind": kind, "target_id": target_id, "decision": decision,
                "subject_revision": actual, "revision": 1, "is_stale": False,
                "created_at": now, "updated_at": now, "idempotent_replay": False,
            }
            self._store(connection, scope, key, payload_hash, result)
            return result

    def revoke(self, decision_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key = str(command["idempotency_key"]).strip()
        scope = f"review-decision:revoke:{decision_id}"
        canonical = {name: value for name, value in command.items() if name != "idempotency_key"}
        payload_hash = _hash(canonical)
        now = _now()
        with self.database.transaction() as connection:
            replay = self._replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            row = connection.execute("SELECT * FROM review_decisions WHERE id=?", (decision_id,)).fetchone()
            if row is None:
                raise DomainRuleError("REVIEW_DECISION_NOT_FOUND", "审核决定不存在", {"decision_id": decision_id})
            actual = int(row["revision"])
            expected = int(command["expected_revision"])
            if actual != expected:
                raise DomainRuleError(
                    "REVIEW_DECISION_REVISION_CONFLICT", "审核决定已变化，请刷新后重试",
                    {"expected_revision": expected, "actual_revision": actual},
                )
            if str(row["decision"]) == "VOIDED":
                raise DomainRuleError("REVIEW_DECISION_ALREADY_REVOKED", "审核决定已经撤回")
            connection.execute(
                """UPDATE review_decisions SET decision='VOIDED',is_stale=1,stale_reason=?,
                updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                (str(command["reason"]), now, decision_id, actual),
            )
            project = self._project_for_decision(connection, str(row["subject_type"]), str(row["subject_id"]))
            if str(row["decision"]) == "APPROVED" and str(row["subject_type"]) == "MEDIA_VERSION":
                connection.execute(
                    """UPDATE media_assets SET approved_version_id=NULL,revision=revision+1,updated_at=?
                    WHERE approved_version_id=?""",
                    (now, row["subject_id"]),
                )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'reviewer','REVIEW_DECISION_REVOKED','review_decision',?,?,?,?,?)""",
                (actor, decision_id, actual, actual + 1, "撤回审核决定", _json({"reason": command["reason"]})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('ReviewDecisionChanged',?,'REVIEW_DECISION',?,?)""",
                (project, decision_id, _json({"target_kind": row["subject_type"], "target_id": row["subject_id"], "decision": "VOIDED"})),
            )
            result = {
                "id": decision_id, "target_kind": str(row["subject_type"]), "target_id": str(row["subject_id"]),
                "decision": "VOIDED", "subject_revision": int(row["subject_revision"]),
                "revision": actual + 1, "is_stale": True, "created_at": str(row["created_at"]),
                "updated_at": now, "idempotent_replay": False,
            }
            self._store(connection, scope, key, payload_hash, result)
            return result

    @staticmethod
    def _project_for_decision(connection: sqlite3.Connection, kind: str, target_id: str) -> str:
        if kind == "MEDIA_VERSION":
            row = connection.execute(
                """SELECT ma.project_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE mv.id=?""", (target_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT se.project_id FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE erv.id=?""", (target_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("REVIEW_TARGET_NOT_FOUND", "审核目标不存在")
        return str(row["project_id"])
