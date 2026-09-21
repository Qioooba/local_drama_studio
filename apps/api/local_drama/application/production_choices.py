from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.reviews import ReviewService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError

from .keyframe_references import approved_keyframes_for_shots
from .production_sessions import ProductionSessionService


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def session_keyframe_for_shot(
    connection: sqlite3.Connection,
    production_session_id: str,
    shot_id: str,
) -> dict[str, Any] | None:
    """Resolve a verified session-only keyframe without implying approval."""
    row = connection.execute(
        """SELECT pc.id AS production_choice_id,pc.candidate_id AS media_version_id,
                  pc.choice_type,pc.selection_state,pc.score,pc.reason_json,
                  mv.integrity_status,mv.created_at AS media_created_at,
                  ma.id AS media_asset_id,ma.project_id,ma.owner_type,ma.owner_id,
                  psi.episode_id
           FROM production_choices pc
           JOIN production_sessions ps ON ps.id=pc.session_id
           JOIN production_session_items psi
             ON psi.session_id=ps.id AND psi.episode_id=pc.episode_id
           JOIN shots sh ON sh.id=pc.target_id AND sh.episode_id=psi.episode_id
           JOIN media_versions mv ON mv.id=pc.candidate_id
           JOIN media_assets ma ON ma.id=mv.media_asset_id AND ma.project_id=ps.project_id
           JOIN shot_keyframe_generation_batch_items bi
             ON bi.shot_id=sh.id AND bi.media_version_id=mv.id
            AND bi.frame_role='FIRST_FRAME' AND bi.status='SUCCEEDED'
           WHERE pc.session_id=? AND pc.target_kind='SHOT' AND pc.target_id=?
             AND pc.slot_role='FIRST_FRAME'
             AND pc.choice_type='MACHINE' AND pc.selection_state='TEMPORARY'
             AND mv.integrity_status='VERIFIED' AND mv.stage='KEYFRAME' AND ma.media_kind='IMAGE'
           ORDER BY pc.updated_at DESC,pc.id DESC LIMIT 1""",
        (production_session_id, shot_id),
    ).fetchone()
    if row is None:
        return None
    fact = dict(row)
    fact["selection_authority"] = "MACHINE_TEMPORARY"
    fact["human_approved"] = False
    return fact


def session_keyframes_for_shots(
    connection: sqlite3.Connection,
    production_session_id: str,
    shot_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    return {
        shot_id: fact
        for shot_id in dict.fromkeys(str(value) for value in shot_ids)
        if (fact := session_keyframe_for_shot(connection, production_session_id, shot_id)) is not None
    }


class ProductionChoiceService:
    """Own machine-temporary choices inside one durable production session."""

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _reroll_candidates(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        episode_id: str,
        shot_id: str,
        slot_role: str,
    ) -> list[dict[str, Any]]:
        if slot_role == "FIRST_FRAME":
            rows = connection.execute(
                """SELECT DISTINCT mv.id AS candidate_id,mv.media_asset_id,mv.created_at,
                                  bi.candidate_index,bi.batch_id
                   FROM shot_keyframe_generation_batch_items bi
                   JOIN shot_keyframe_generation_batches b ON b.id=bi.batch_id
                   JOIN media_versions mv ON mv.id=bi.media_version_id
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                   WHERE b.project_id=? AND b.episode_id=? AND bi.shot_id=?
                     AND bi.frame_role='FIRST_FRAME' AND bi.status='SUCCEEDED'
                     AND mv.integrity_status='VERIFIED' AND mv.stage='KEYFRAME'
                     AND ma.media_kind='IMAGE'
                   ORDER BY b.created_at,bi.candidate_index,mv.id""",
                (project_id, episode_id, shot_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT DISTINCT mv.id AS candidate_id,mv.media_asset_id,mv.created_at,
                                  NULL AS candidate_index,NULL AS batch_id
                   FROM media_versions mv
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                   LEFT JOIN generation_variants gv
                     ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
                   LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                   WHERE ma.project_id=? AND ma.media_kind='VIDEO' AND mv.stage='FORMAL'
                     AND mv.integrity_status='VERIFIED'
                     AND ((ma.owner_type='SHOT' AND ma.owner_id=?)
                       OR (ma.owner_type='GENERATION_VARIANT' AND gi.owner_type='SHOT'
                           AND gi.owner_id=? AND gi.project_id=?))
                     AND (SELECT mcr.status FROM machine_check_runs mcr
                          WHERE mcr.subject_type='MEDIA_VERSION' AND mcr.subject_id=mv.id
                          ORDER BY mcr.created_at DESC,mcr.id DESC LIMIT 1)='PASS'
                   ORDER BY mv.created_at,mv.id""",
                (project_id, shot_id, shot_id, project_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def reroll(
        self,
        production_session_id: str,
        production_choice_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Move a temporary choice to the next verified candidate.

        The order is deterministic so replay and audits are stable.  A video
        reroll changes only the session-scoped temporary choice, marks the
        latest timeline stale, and arms the session runner for a crash-resumable
        RECOMPOSE_ONLY pass.  It never adopts the candidate as a global or
        human selection.
        """

        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "换候选必须提供有效 Idempotency-Key")
        expected_session_revision = int(command["expected_session_revision"])
        expected_choice_revision = int(command["expected_choice_revision"])
        payload = {
            "production_choice_id": production_choice_id,
            "expected_session_revision": expected_session_revision,
            "expected_choice_revision": expected_choice_revision,
        }
        payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        scope = f"production-session:reroll:{production_session_id}:{production_choice_id}"
        actor = str(command.get("actor") or "local-user")
        now = _now()
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
                        "相同 Idempotency-Key 不能换到不同候选",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["idempotent_replay"] = True
                replay["session"] = ProductionSessionService._session_view(
                    connection, production_session_id
                )
                return replay
            session = connection.execute(
                "SELECT * FROM production_sessions WHERE id=?",
                (production_session_id,),
            ).fetchone()
            if session is None:
                raise DomainRuleError("PRODUCTION_SESSION_NOT_FOUND", "生产会话不存在")
            if int(session["revision"]) != expected_session_revision:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新后重试",
                    {
                        "expected_revision": expected_session_revision,
                        "actual_revision": int(session["revision"]),
                    },
                )
            if str(session["status"]) not in {"WAITING_REVIEW", "WAITING_USER"}:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVIEW_NOT_READY",
                    "只有等待人工审核的会话可以换候选",
                    {"status": str(session["status"])},
                )
            choice = connection.execute(
                """SELECT pc.*,psi.progress_json,psi.state AS item_state,
                          psi.current_stage AS item_current_stage
                   FROM production_choices pc
                   JOIN production_session_items psi ON psi.id=pc.session_item_id
                   WHERE pc.id=? AND pc.session_id=?""",
                (production_choice_id, production_session_id),
            ).fetchone()
            if choice is None:
                raise DomainRuleError("PRODUCTION_CHOICE_NOT_FOUND", "会话候选选择不存在")
            if str(choice["item_state"]) != "WAITING" or str(choice["item_current_stage"]) != "WAITING_REVIEW":
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVIEW_NOT_READY",
                    "当前分集尚未进入人工审核，不能换候选",
                    {
                        "item_state": str(choice["item_state"]),
                        "current_stage": str(choice["item_current_stage"]),
                    },
                )
            if int(choice["revision"]) != expected_choice_revision:
                raise DomainRuleError(
                    "PRODUCTION_CHOICE_REVISION_CONFLICT",
                    "候选选择已变化，请刷新后重试",
                    {
                        "expected_revision": expected_choice_revision,
                        "actual_revision": int(choice["revision"]),
                    },
                )
            if str(choice["choice_type"]) != "MACHINE" or str(choice["selection_state"]) != "TEMPORARY":
                raise DomainRuleError(
                    "PRODUCTION_CHOICE_ALREADY_CONFIRMED",
                    "已人工确认或已撤销的选择不能自动换候选",
                )
            slot_role = str(choice["slot_role"])
            if slot_role not in {"FIRST_FRAME", "VIDEO"}:
                raise DomainRuleError(
                    "PRODUCTION_CHOICE_REROLL_UNSUPPORTED",
                    "当前选择类型不支持换候选",
                    {"slot_role": slot_role},
                )
            candidates = self._reroll_candidates(
                connection,
                project_id=str(session["project_id"]),
                episode_id=str(choice["episode_id"]),
                shot_id=str(choice["target_id"]),
                slot_role=slot_role,
            )
            candidate_ids = [str(item["candidate_id"]) for item in candidates]
            current_id = str(choice["candidate_id"])
            alternatives = [candidate_id for candidate_id in candidate_ids if candidate_id != current_id]
            if not alternatives:
                raise DomainRuleError(
                    "PRODUCTION_CHOICE_ALTERNATIVE_REQUIRED",
                    "当前镜头没有其他已验证候选；请先生成新候选",
                    {"slot_role": slot_role, "candidate_count": len(candidate_ids)},
                )
            if current_id in candidate_ids:
                current_index = candidate_ids.index(current_id)
                next_id = next(
                    (
                        candidate_ids[(current_index + offset) % len(candidate_ids)]
                        for offset in range(1, len(candidate_ids) + 1)
                        if candidate_ids[(current_index + offset) % len(candidate_ids)] != current_id
                    ),
                    alternatives[0],
                )
            else:
                next_id = alternatives[0]
            next_position = candidate_ids.index(next_id) + 1
            reason = {
                "schema_version": "localdrama.production-choice-reason.v1",
                "policy": "NEXT_VERIFIED_CANDIDATE_V1",
                "previous_candidate_id": current_id,
                "candidate_position": next_position,
                "candidate_count": len(candidate_ids),
                "human_approved": False,
            }
            connection.execute(
                """UPDATE production_choices SET candidate_id=?,score=?,reason_json=?,
                   choice_type='MACHINE',selection_state='TEMPORARY',human_review_decision_id=NULL,
                   updated_at=?,created_by=?,revision=revision+1 WHERE id=?""",
                (
                    next_id,
                    max(0.0, 1.0 - (next_position - 1) * 0.01),
                    _json(reason),
                    now,
                    actor,
                    production_choice_id,
                ),
            )
            progress = _json_object(choice["progress_json"])
            progress["session_run_attempt"] = int(progress.get("session_run_attempt") or 1) + 1
            progress["reroll"] = {
                "production_choice_id": production_choice_id,
                "slot_role": slot_role,
                "previous_candidate_id": current_id,
                "candidate_id": next_id,
            }
            if slot_role == "VIDEO":
                latest_timeline = connection.execute(
                    """SELECT id FROM timeline_revisions WHERE episode_id=?
                       AND json_extract(input_snapshot_json,'$.production_session_id')=?
                       ORDER BY revision_no DESC,id DESC LIMIT 1""",
                    (choice["episode_id"], production_session_id),
                ).fetchone()
                if latest_timeline is not None:
                    connection.execute(
                        "UPDATE timeline_revisions SET status='STALE',updated_at=?,revision=revision+1 WHERE id=?",
                        (now, latest_timeline["id"]),
                    )
                progress["requested_operation"] = "RECOMPOSE_ONLY"
                next_state = "PENDING"
                next_stage = "TIMELINE_PREVIEW"
            else:
                progress["requested_operation"] = "FULL_REBUILD"
                next_state = "BLOCKED"
                next_stage = "VIDEO"
            connection.execute(
                """UPDATE production_session_items
                   SET state=?,current_stage=?,progress_json=?,last_error_code=?,last_error_message=?,
                       updated_at=?,revision=revision+1 WHERE id=?""",
                (
                    next_state,
                    next_stage,
                    _json(progress),
                    None if slot_role == "VIDEO" else "SESSION_KEYFRAME_REROLL_REBUILD_REQUIRED",
                    None if slot_role == "VIDEO" else "关键帧已换选，需要重新生成对应视频",
                    now,
                    choice["session_item_id"],
                ),
            )
            connection.execute(
                """UPDATE production_sessions SET status=?,current_stage=?,finished_at=NULL,
                   updated_at=?,revision=revision+1 WHERE id=?""",
                (
                    "RUNNING" if slot_role == "VIDEO" else "WAITING_REVIEW",
                    next_stage,
                    now,
                    production_session_id,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_CHOICE_REROLLED','production_choice',?,?,?)""",
                (actor, production_choice_id, "换选下一个已验证候选", _json(reason)),
            )
            updated = connection.execute(
                "SELECT * FROM production_choices WHERE id=?",
                (production_choice_id,),
            ).fetchone()
            result = {
                "choice": {
                    "id": str(updated["id"]),
                    "candidate_id": str(updated["candidate_id"]),
                    "slot_role": str(updated["slot_role"]),
                    "choice_type": str(updated["choice_type"]),
                    "selection_state": str(updated["selection_state"]),
                    "reason": reason,
                    "revision": int(updated["revision"]),
                },
                "session": ProductionSessionService._session_view(connection, production_session_id),
                "outcome": "PREVIEW_REBUILD_QUEUED" if slot_role == "VIDEO" else "VIDEO_REBUILD_REQUIRED",
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (scope, key, payload_hash, _json(result)),
            )
            return result

    def record_video_choice(
        self,
        production_session_id: str,
        episode_id: str,
        shot_id: str,
        media_version_id: str,
        machine_check_run_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            fact = connection.execute(
                """SELECT ps.id AS session_id,psi.id AS item_id,ps.project_id,mv.id AS media_version_id,
                          mcr.status AS machine_check_status
                   FROM production_sessions ps
                   JOIN production_session_items psi
                     ON psi.session_id=ps.id AND psi.episode_id=?
                   JOIN shots sh ON sh.id=? AND sh.episode_id=psi.episode_id
                   JOIN media_versions mv ON mv.id=? AND mv.integrity_status='VERIFIED'
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                     AND ma.project_id=ps.project_id AND ma.media_kind='VIDEO'
                   LEFT JOIN generation_variants gv
                     ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
                   LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                   JOIN machine_check_runs mcr ON mcr.id=? AND mcr.subject_id=mv.id
                   WHERE ps.id=?
                     AND ((ma.owner_type='SHOT' AND ma.owner_id=sh.id)
                       OR (ma.owner_type='GENERATION_VARIANT' AND gi.owner_type='SHOT'
                           AND gi.owner_id=sh.id AND gi.project_id=ps.project_id))""",
                (
                    episode_id,
                    shot_id,
                    media_version_id,
                    machine_check_run_id,
                    production_session_id,
                ),
            ).fetchone()
            if fact is None or str(fact["machine_check_status"]) != "PASS":
                raise DomainRuleError(
                    "PRODUCTION_SESSION_VIDEO_CHOICE_INVALID",
                    "机器临时视频选择必须属于本会话镜头，并具有 PASS 的机器检查证据",
                    {
                        "production_session_id": production_session_id,
                        "episode_id": episode_id,
                        "shot_id": shot_id,
                        "media_version_id": media_version_id,
                        "machine_check_run_id": machine_check_run_id,
                    },
                )
            reason = {
                "schema_version": "localdrama.production-choice-reason.v1",
                "policy": "MACHINE_QC_PASS_V1",
                "machine_check_run_id": machine_check_run_id,
                "human_approved": False,
            }
            choice_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO production_choices
                   (id,session_id,session_item_id,episode_id,target_kind,target_id,slot_role,
                    candidate_id,choice_type,selection_state,score,reason_json,
                    created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?, 'SHOT',?,'VIDEO',?,'MACHINE','TEMPORARY',1.0,?, ?,?,?,1,'production-session.v1')
                   ON CONFLICT(session_id,target_kind,target_id,slot_role) DO UPDATE SET
                     candidate_id=excluded.candidate_id,choice_type='MACHINE',selection_state='TEMPORARY',
                     score=excluded.score,reason_json=excluded.reason_json,human_review_decision_id=NULL,
                     updated_at=excluded.updated_at,revision=production_choices.revision+1""",
                (
                    choice_id,
                    production_session_id,
                    fact["item_id"],
                    episode_id,
                    shot_id,
                    media_version_id,
                    _json(reason),
                    now,
                    now,
                    actor,
                ),
            )
            row = connection.execute(
                """SELECT * FROM production_choices
                   WHERE session_id=? AND target_kind='SHOT' AND target_id=? AND slot_role='VIDEO'""",
                (production_session_id, shot_id),
            ).fetchone()
            return {
                "id": str(row["id"]),
                "media_version_id": str(row["candidate_id"]),
                "selection_authority": "MACHINE_TEMPORARY",
                "machine_check_run_id": machine_check_run_id,
                "human_approved": False,
            }

    def record_tts_choice(
        self,
        production_session_id: str,
        episode_id: str,
        dialogue_line_id: str,
        tts_candidate_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        """Record verified dialogue audio for this session without adopting it globally."""

        if self.settings is None:
            raise DomainRuleError(
                "PRODUCTION_CHOICE_SETTINGS_REQUIRED",
                "生产会话对白选择缺少媒体质检配置",
            )
        with self.database.connect() as connection:
            candidate = connection.execute(
                """SELECT tc.id,tc.media_version_id,tc.dialogue_text_revision_id,
                          mv.integrity_status,ma.media_kind
                   FROM tts_candidates tc
                   JOIN media_versions mv ON mv.id=tc.media_version_id
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                   WHERE tc.id=?""",
                (tts_candidate_id,),
            ).fetchone()
        if candidate is None:
            raise DomainRuleError("TTS_CANDIDATE_NOT_FOUND", "TTS 候选不存在")
        media_version_id = str(candidate["media_version_id"])
        if str(candidate["integrity_status"]) != "VERIFIED" or str(candidate["media_kind"]) != "AUDIO":
            raise DomainRuleError(
                "PRODUCTION_SESSION_TTS_CHOICE_INVALID",
                "会话临时对白选择必须引用已验证音频",
            )
        machine_check = ReviewService(self.database, self.settings).machine_check(
            media_version_id,
            actor=actor,
        )
        if str(machine_check.get("status") or "") != "PASS":
            raise DomainRuleError(
                "PRODUCTION_SESSION_TTS_CHOICE_INVALID",
                "会话临时对白选择必须通过机器质检",
                {"media_version_id": media_version_id},
            )
        machine_check_run_id = str(machine_check["id"])
        now = _now()
        with self.database.transaction() as connection:
            fact = connection.execute(
                """SELECT ps.project_id,psi.id AS item_id,dtr.id AS latest_text_revision_id,
                          tc.dialogue_text_revision_id,tc.media_version_id
                   FROM production_sessions ps
                   JOIN production_session_items psi
                     ON psi.session_id=ps.id AND psi.episode_id=?
                   JOIN dialogue_lines dl ON dl.id=? AND dl.episode_id=psi.episode_id
                   JOIN dialogue_text_revisions dtr ON dtr.id=(
                     SELECT latest.id FROM dialogue_text_revisions latest
                     WHERE latest.dialogue_line_id=dl.id
                     ORDER BY latest.revision_no DESC,latest.id DESC LIMIT 1)
                   JOIN tts_candidates tc ON tc.id=?
                     AND tc.dialogue_text_revision_id=dtr.id
                   JOIN media_versions mv ON mv.id=tc.media_version_id
                     AND mv.integrity_status='VERIFIED'
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                     AND ma.project_id=ps.project_id AND ma.media_kind='AUDIO'
                   JOIN machine_check_runs mcr ON mcr.id=?
                     AND mcr.subject_type='MEDIA_VERSION'
                     AND mcr.subject_id=mv.id AND mcr.status='PASS'
                   WHERE ps.id=?""",
                (
                    episode_id,
                    dialogue_line_id,
                    tts_candidate_id,
                    machine_check_run_id,
                    production_session_id,
                ),
            ).fetchone()
            if fact is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_TTS_CHOICE_INVALID",
                    "TTS 候选必须属于本会话分集、当前对白文本和同一项目",
                    {
                        "production_session_id": production_session_id,
                        "episode_id": episode_id,
                        "dialogue_line_id": dialogue_line_id,
                        "tts_candidate_id": tts_candidate_id,
                    },
                )
            reason = {
                "schema_version": "localdrama.production-choice-reason.v1",
                "policy": "MACHINE_QC_PASS_TTS_V1",
                "tts_candidate_id": tts_candidate_id,
                "text_revision_id": str(fact["latest_text_revision_id"]),
                "media_version_id": str(fact["media_version_id"]),
                "machine_check_run_id": machine_check_run_id,
                "human_approved": False,
            }
            choice_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO production_choices
                   (id,session_id,session_item_id,episode_id,target_kind,target_id,slot_role,
                    candidate_id,choice_type,selection_state,score,reason_json,
                    created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?, 'DIALOGUE_LINE',?,'TTS_AUDIO',?,'MACHINE','TEMPORARY',1.0,?, ?,?,?,1,'production-session.v1')
                   ON CONFLICT(session_id,target_kind,target_id,slot_role) DO UPDATE SET
                     candidate_id=excluded.candidate_id,choice_type='MACHINE',selection_state='TEMPORARY',
                     score=excluded.score,reason_json=excluded.reason_json,human_review_decision_id=NULL,
                     updated_at=excluded.updated_at,revision=production_choices.revision+1""",
                (
                    choice_id,
                    production_session_id,
                    fact["item_id"],
                    episode_id,
                    dialogue_line_id,
                    media_version_id,
                    _json(reason),
                    now,
                    now,
                    actor,
                ),
            )
            row = connection.execute(
                """SELECT id,revision FROM production_choices
                   WHERE session_id=? AND target_kind='DIALOGUE_LINE'
                     AND target_id=? AND slot_role='TTS_AUDIO'""",
                (production_session_id, dialogue_line_id),
            ).fetchone()
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_TTS_CHOICE','production_session',?,?,?)""",
                (
                    actor,
                    production_session_id,
                    "记录生产会话机器临时对白选择",
                    _json(
                        {
                            "episode_id": episode_id,
                            "dialogue_line_id": dialogue_line_id,
                            "tts_candidate_id": tts_candidate_id,
                            "media_version_id": media_version_id,
                            "machine_check_run_id": machine_check_run_id,
                            "human_approval_written": False,
                        }
                    ),
                ),
            )
        return {
            "id": str(row["id"]),
            "revision": int(row["revision"]),
            "dialogue_line_id": dialogue_line_id,
            "tts_candidate_id": tts_candidate_id,
            "media_version_id": media_version_id,
            "selection_authority": "MACHINE_TEMPORARY",
            "machine_check_run_id": machine_check_run_id,
            "human_approved": False,
        }

    def ensure_keyframes(
        self,
        production_session_id: str,
        episode_id: str,
        *,
        actor: str = "production-session-runner",
    ) -> tuple[dict[str, Any], int]:
        now = _now()
        with self.database.transaction() as connection:
            session = connection.execute(
                """SELECT ps.id,ps.project_id,ps.status,psi.id AS item_id
                   FROM production_sessions ps
                   JOIN production_session_items psi ON psi.session_id=ps.id
                   WHERE ps.id=? AND psi.episode_id=?""",
                (production_session_id, episode_id),
            ).fetchone()
            if session is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_MISMATCH",
                    "生产会话不包含当前分集",
                    {"production_session_id": production_session_id, "episode_id": episode_id},
                )
            if str(session["status"]) in {"CANCELLED", "COMPLETED"}:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_TERMINAL",
                    "生产会话已结束，不能再创建机器临时选择",
                    {"status": str(session["status"])},
                )
            shots = connection.execute(
                """SELECT id,code FROM shots WHERE episode_id=? AND archived_at IS NULL
                   ORDER BY CAST(order_key AS REAL),code""",
                (episode_id,),
            ).fetchall()
            shot_ids = [str(row["id"]) for row in shots]
            approved = approved_keyframes_for_shots(
                connection,
                shot_ids,
                project_id=str(session["project_id"]),
            )
            selected: list[dict[str, Any]] = []
            missing: list[dict[str, Any]] = []
            for shot in shots:
                shot_id = str(shot["id"])
                if shot_id in approved:
                    selected.append(
                        {
                            "shot_id": shot_id,
                            "shot_code": str(shot["code"]),
                            "media_version_id": str(approved[shot_id]["media_version_id"]),
                            "selection_authority": "HUMAN_APPROVED",
                            "production_choice_id": None,
                        }
                    )
                    continue
                candidate = connection.execute(
                    """SELECT bi.media_version_id,bi.candidate_index,bi.batch_id,mv.created_at,
                              mv.integrity_status,ma.media_kind
                       FROM shot_keyframe_generation_batch_items bi
                       JOIN shot_keyframe_generation_batches b ON b.id=bi.batch_id
                       JOIN media_versions mv ON mv.id=bi.media_version_id
                       JOIN media_assets ma ON ma.id=mv.media_asset_id
                       WHERE b.episode_id=? AND b.project_id=? AND bi.shot_id=?
                         AND bi.frame_role='FIRST_FRAME' AND bi.status='SUCCEEDED'
                         AND mv.integrity_status='VERIFIED' AND mv.stage='KEYFRAME' AND ma.media_kind='IMAGE'
                       ORDER BY b.created_at DESC,b.id DESC,bi.candidate_index,bi.id LIMIT 1""",
                    (episode_id, session["project_id"], shot_id),
                ).fetchone()
                if candidate is None:
                    missing.append({"shot_id": shot_id, "shot_code": str(shot["code"])})
                    continue
                choice_id = str(uuid.uuid4())
                candidate_index = int(candidate["candidate_index"])
                score = max(0.0, 1.0 - (candidate_index - 1) * 0.01)
                reason = {
                    "schema_version": "localdrama.production-choice-reason.v1",
                    "policy": "FIRST_VERIFIED_DETERMINISTIC_V1",
                    "integrity_status": "VERIFIED",
                    "candidate_index": candidate_index,
                    "batch_id": str(candidate["batch_id"]),
                    "human_approved": False,
                }
                connection.execute(
                    """INSERT INTO production_choices
                       (id,session_id,session_item_id,episode_id,target_kind,target_id,slot_role,
                        candidate_id,choice_type,selection_state,score,reason_json,
                        created_at,updated_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?, 'SHOT',?,'FIRST_FRAME',?,'MACHINE','TEMPORARY',?,?, ?,?,?,1,'production-session.v1')
                       ON CONFLICT(session_id,target_kind,target_id,slot_role) DO UPDATE SET
                         candidate_id=excluded.candidate_id,choice_type='MACHINE',selection_state='TEMPORARY',
                         score=excluded.score,reason_json=excluded.reason_json,human_review_decision_id=NULL,
                         updated_at=excluded.updated_at,revision=production_choices.revision+1""",
                    (
                        choice_id,
                        production_session_id,
                        session["item_id"],
                        episode_id,
                        shot_id,
                        str(candidate["media_version_id"]),
                        score,
                        _json(reason),
                        now,
                        now,
                        actor,
                    ),
                )
                stored = connection.execute(
                    """SELECT id FROM production_choices
                       WHERE session_id=? AND target_kind='SHOT' AND target_id=? AND slot_role='FIRST_FRAME'""",
                    (production_session_id, shot_id),
                ).fetchone()
                selected.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "media_version_id": str(candidate["media_version_id"]),
                        "selection_authority": "MACHINE_TEMPORARY",
                        "production_choice_id": str(stored["id"]),
                        "score": score,
                        "reason": reason,
                    }
                )
            status = "PASS" if shots and not missing else "NEEDS_HITL"
            report = {
                "status": status,
                "machine_check": {
                    "status": status,
                    "code": "SESSION_KEYFRAMES_SELECTED" if status == "PASS" else "SESSION_KEYFRAME_CANDIDATE_REQUIRED",
                    "human_approval_written": False,
                    "selected_count": len(selected),
                    "missing_shots": missing,
                },
                "produced": {
                    "items": selected,
                    "production_session_id": production_session_id,
                },
                "summary": (
                    "生产会话已记录机器临时关键帧选择，最终仍需人工审核"
                    if status == "PASS"
                    else "部分镜头没有已验证关键帧候选，需要修复后重试"
                ),
            }
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_SESSION_KEYFRAME_CHOICES','production_session',?,?,?)""",
                (
                    actor,
                    production_session_id,
                    "记录生产会话机器临时关键帧选择",
                    _json(
                        {
                            "episode_id": episode_id,
                            "selected_count": len(selected),
                            "missing_count": len(missing),
                            "human_approval_written": False,
                        }
                    ),
                ),
            )
            return report, 0
