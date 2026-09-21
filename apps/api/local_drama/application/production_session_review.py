from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError

from .episode_render_approval import require_latest_episode_render_approval
from .production_sessions import ProductionSessionService


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProductionSessionReviewService:
    """Bounded, read-only review projection for a production session.

    The projection keeps machine choices and human approval separate.  It also
    compares each session video choice with the exact media version referenced
    by the latest frozen timeline, so the operator never approves a candidate
    that is absent from the preview/render inputs.
    """

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    @staticmethod
    def repair_plan(
        *,
        item_state: str,
        current_stage: str,
        blockers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Describe the smallest existing retry operation that can repair an item."""

        codes = {str(item.get("code") or "") for item in blockers}
        prerequisites: list[dict[str, str]] = []
        if "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED" in codes:
            prerequisites.append(
                {
                    "action": "REVIEW_ASSET_IDENTITIES",
                    "message": "先确认本次生产实际使用的机器临时资产",
                }
            )
        if any(code.startswith("PRODUCTION_SESSION_") and code.endswith("_BUDGET_EXHAUSTED") for code in codes):
            prerequisites.append(
                {
                    "action": "EXTEND_BUDGET",
                    "message": "先提高已耗尽的生产预算",
                }
            )

        preview_codes = {
            "TIMELINE_PREVIEW_MISSING",
            "TIMELINE_NOT_FROZEN",
            "EPISODE_PREVIEW_RENDER_MISSING",
            "SESSION_TIMELINE_CHOICE_MISMATCH",
        }
        strategy: str | None = None
        effects: list[str] = []
        summary = "当前问题需要先完成人工处理"
        if "SESSION_VIDEO_CHOICES_MISSING" in codes:
            strategy = "FULL_EPISODE"
            effects = [
                "REUSE_VERIFIED_INPUTS",
                "FILL_MISSING_GENERATION",
                "REBUILD_TIMELINE",
                "RENDER_PREVIEW",
            ]
            summary = "复用已验证素材，从缺失的视频候选开始补齐本集并重建预览"
        elif codes & preview_codes or current_stage in {"TIMELINE_PREVIEW", "WAITING_REVIEW"}:
            strategy = "RECOMPOSE_ONLY"
            effects = ["KEEP_GENERATED_MEDIA", "REBUILD_TIMELINE", "RENDER_PREVIEW"]
            summary = "保留现有画面和视频，只重建时间线与预览成片"
        elif item_state in {"BLOCKED", "FAILED"}:
            strategy = "RETRY_FAILED_STAGE"
            effects = ["REUSE_SUCCEEDED_OUTPUTS", "RETRY_FAILED_STAGE"]
            summary = "保留已成功产物，从失败阶段继续"
        elif not blockers:
            summary = "当前没有需要返工的问题"

        return {
            "recommended_strategy": strategy,
            "summary": summary,
            "effects": effects,
            "prerequisites": prerequisites,
            "can_retry_now": strategy is not None and not prerequisites,
            "read_only": True,
            "mutated": False,
        }

    @staticmethod
    def allowed_actions(
        *,
        review_status: str,
        repair_plan: dict[str, Any],
    ) -> list[str]:
        """Expose only actions that are valid for the projected review state."""

        if review_status == "READY_FOR_HUMAN_REVIEW":
            return ["CONFIRM_CHOICES"]
        if review_status != "BLOCKED":
            return []

        actions: list[str] = []
        for prerequisite in repair_plan.get("prerequisites", []):
            action = str(prerequisite.get("action") or "")
            if action and action not in actions:
                actions.append(action)
        if repair_plan.get("can_retry_now"):
            actions.append("REQUEST_LOCAL_RETRY")
        return actions

    @staticmethod
    def _session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT id,project_id,status,revision FROM production_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError(
                "PRODUCTION_SESSION_NOT_FOUND",
                "生产会话不存在",
                {"session_id": session_id},
            )
        return row

    @staticmethod
    def _choices(
        connection: sqlite3.Connection,
        session_id: str,
        episode_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT pc.*,sh.code AS shot_code,mv.media_asset_id,mv.stage,mv.mime_type,
                      mv.integrity_status,mv.sha256,mv.duration_ms,ma.media_kind,
                      rd.decision AS human_decision,
                      available.id AS available_human_approval_id,
                      (SELECT mcr.status FROM machine_check_runs mcr
                       WHERE mcr.subject_type='MEDIA_VERSION' AND mcr.subject_id=pc.candidate_id
                       ORDER BY mcr.created_at DESC,mcr.id DESC LIMIT 1) AS machine_check_status
               FROM production_choices pc
               LEFT JOIN shots sh ON pc.target_kind='SHOT' AND sh.id=pc.target_id
               LEFT JOIN media_versions mv ON mv.id=pc.candidate_id
               LEFT JOIN media_assets ma ON ma.id=mv.media_asset_id
               LEFT JOIN review_decisions rd ON rd.id=pc.human_review_decision_id
               LEFT JOIN review_decisions available ON available.id=(
                 SELECT approval.id FROM review_decisions approval
                 WHERE approval.subject_type='MEDIA_VERSION'
                   AND approval.subject_id=pc.candidate_id
                   AND approval.decision='APPROVED' AND approval.is_stale=0
                 ORDER BY approval.created_at DESC,approval.id DESC LIMIT 1
               )
               WHERE pc.session_id=? AND pc.episode_id=?
               ORDER BY CAST(COALESCE(sh.order_key,'0') AS REAL),sh.code,pc.slot_role,pc.id""",
            (session_id, episode_id),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "target_kind": str(row["target_kind"]),
                "target_id": str(row["target_id"]),
                "shot_code": str(row["shot_code"]) if row["shot_code"] is not None else None,
                "slot_role": str(row["slot_role"]),
                "candidate_id": str(row["candidate_id"]),
                "choice_type": str(row["choice_type"]),
                "selection_state": str(row["selection_state"]),
                "revision": int(row["revision"]),
                "selection_authority": (
                    "HUMAN_CONFIRMED"
                    if str(row["choice_type"]) == "HUMAN"
                    and str(row["selection_state"]) == "CONFIRMED"
                    else "MACHINE_TEMPORARY"
                ),
                "score": float(row["score"]) if row["score"] is not None else None,
                "reason": _json_object(row["reason_json"]),
                "human_review_decision_id": (
                    str(row["human_review_decision_id"])
                    if row["human_review_decision_id"] is not None
                    else None
                ),
                "human_decision": (
                    str(row["human_decision"]) if row["human_decision"] is not None else None
                ),
                "available_human_approval_id": (
                    str(row["available_human_approval_id"])
                    if row["available_human_approval_id"] is not None
                    else None
                ),
                "machine_check_status": (
                    str(row["machine_check_status"])
                    if row["machine_check_status"] is not None
                    else None
                ),
                "media": {
                    "media_asset_id": (
                        str(row["media_asset_id"]) if row["media_asset_id"] is not None else None
                    ),
                    "media_kind": str(row["media_kind"]) if row["media_kind"] is not None else None,
                    "stage": str(row["stage"]) if row["stage"] is not None else None,
                    "mime_type": str(row["mime_type"]) if row["mime_type"] is not None else None,
                    "integrity_status": (
                        str(row["integrity_status"])
                        if row["integrity_status"] is not None
                        else None
                    ),
                    "sha256": str(row["sha256"]) if row["sha256"] is not None else None,
                    "duration_ms": int(row["duration_ms"]) if row["duration_ms"] is not None else None,
                },
            }
            for row in rows
        ]

    @staticmethod
    def _asset_inputs(
        connection: sqlite3.Connection,
        session_id: str,
        episode_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT i.id,i.asset_proposal_id,i.story_asset_id,i.state,i.revision,
                      i.input_json,p.kind,p.name,p.status AS proposal_status,
                      p.resolved_asset_id,p.revision AS proposal_revision,
                      a.code AS asset_code,a.name AS asset_name,a.status AS asset_status
               FROM production_session_asset_inputs i
               JOIN story_asset_proposals p ON p.id=i.asset_proposal_id
               JOIN story_assets a ON a.id=i.story_asset_id
               WHERE i.session_id=? AND i.episode_id=? AND i.state='ACTIVE'
               ORDER BY p.created_at,p.id""",
            (session_id, episode_id),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            proposal_status = str(row["proposal_status"])
            resolved_asset_id = (
                str(row["resolved_asset_id"])
                if row["resolved_asset_id"] is not None
                else None
            )
            if (
                proposal_status in {"ACCEPTED_NEW", "ACCEPTED_MERGE"}
                and resolved_asset_id == str(row["story_asset_id"])
                and str(row["asset_status"]) == "ACTIVE"
            ):
                review_status = "CONFIRMED"
            elif proposal_status == "PENDING":
                review_status = "PENDING"
            else:
                review_status = "MISMATCH"
            items.append(
                {
                    "id": str(row["id"]),
                    "asset_proposal_id": str(row["asset_proposal_id"]),
                    "story_asset_id": str(row["story_asset_id"]),
                    "kind": str(row["kind"]),
                    "name": str(row["name"]),
                    "asset_code": str(row["asset_code"]),
                    "asset_name": str(row["asset_name"]),
                    "proposal_status": proposal_status,
                    "resolved_asset_id": resolved_asset_id,
                    "review_status": review_status,
                    "selection_authority": "MACHINE_TEMPORARY",
                    "human_approved": review_status == "CONFIRMED",
                    "revision": int(row["revision"]),
                    "proposal_revision": int(row["proposal_revision"]),
                    "input": _json_object(row["input_json"]),
                }
            )
        return items

    @staticmethod
    def _timeline(
        connection: sqlite3.Connection,
        episode_id: str,
        session_id: str,
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        row = connection.execute(
            """SELECT id,revision_no,status,revision_hash,created_at
               FROM timeline_revisions WHERE episode_id=?
                 AND json_extract(input_snapshot_json,'$.production_session_id')=?
               ORDER BY revision_no DESC,id DESC LIMIT 1""",
            (episode_id, session_id),
        ).fetchone()
        if row is None:
            return None, {}
        items = connection.execute(
            """SELECT media_version_id,parameters_json,start_us,end_us
               FROM timeline_items
               WHERE timeline_revision_id=? AND UPPER(track_type)='VIDEO'
               ORDER BY start_us,id""",
            (row["id"],),
        ).fetchall()
        video_by_shot: dict[str, str] = {}
        for item in items:
            parameters = _json_object(item["parameters_json"])
            shot_id = str(parameters.get("shot_id") or "")
            media_version_id = str(item["media_version_id"] or "")
            if shot_id and media_version_id:
                video_by_shot[shot_id] = media_version_id
        return (
            {
                "id": str(row["id"]),
                "revision_no": int(row["revision_no"]),
                "status": str(row["status"]),
                "revision_hash": str(row["revision_hash"]),
                "created_at": str(row["created_at"]),
                "video_item_count": len(items),
            },
            video_by_shot,
        )

    @staticmethod
    def _latest_outputs(
        connection: sqlite3.Connection,
        episode_id: str,
        timeline_revision_id: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not timeline_revision_id:
            return None, None
        render = connection.execute(
            """SELECT erv.id,erv.episode_id,erv.timeline_revision_id,erv.rel_path,erv.sha256,
                      erv.integrity_status,erv.duration_ms,erv.mime_type,erv.created_at,
                      erv.render_kind,erv.parent_render_version_id,erv.revision,
                      approval.id AS available_human_approval_id,
                      approval.decision AS human_decision,
                      approval.is_stale AS human_decision_stale,
                      approval.subject_revision AS human_approval_subject_revision,
                      CASE WHEN erv.id=(
                        SELECT latest.id FROM episode_render_versions latest
                        WHERE latest.episode_id=erv.episode_id AND latest.render_kind='COMPOSE'
                        ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1
                      ) THEN 1 ELSE 0 END AS is_latest_compose
               FROM episode_render_versions erv
               LEFT JOIN review_decisions approval ON approval.id=(
                 SELECT rd.id FROM review_decisions rd
                 WHERE rd.subject_type='EPISODE_RENDER_VERSION' AND rd.subject_id=erv.id
                 ORDER BY rd.created_at DESC,rd.id DESC LIMIT 1
               )
               WHERE erv.episode_id=? AND erv.timeline_revision_id=?
                 AND erv.render_kind='COMPOSE'
               ORDER BY erv.created_at DESC,erv.id DESC LIMIT 1""",
            (episode_id, timeline_revision_id),
        ).fetchone()
        delivery = connection.execute(
            """SELECT dp.id,dp.episode_render_version_id,dp.status,dp.manifest_sha256,
                      dp.human_review_status,dp.platform_review_status,dp.created_at
               FROM delivery_packages dp
               JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
               WHERE erv.episode_id=? AND erv.timeline_revision_id=?
               ORDER BY dp.created_at DESC,dp.id DESC LIMIT 1""",
            (episode_id, timeline_revision_id),
        ).fetchone()
        render_view = dict(render) if render is not None else None
        if render_view is not None:
            render_view["human_approval_current"] = bool(
                render_view.get("available_human_approval_id")
                and str(render_view.get("human_decision") or "") == "APPROVED"
                and int(render_view.get("human_decision_stale") or 0) == 0
                and int(render_view.get("human_approval_subject_revision") or -1)
                == int(render_view.get("revision") or 0)
                and int(render_view.get("is_latest_compose") or 0) == 1
            )
        return render_view, (dict(delivery) if delivery is not None else None)

    def inspect(self, session_id: str, *, cursor: int, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM production_session_items WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """SELECT psi.*,e.code AS episode_code,e.title AS episode_title
                   FROM production_session_items psi JOIN episodes e ON e.id=psi.episode_id
                   WHERE psi.session_id=? ORDER BY psi.ordinal LIMIT ? OFFSET ?""",
                (session_id, limit, cursor),
            ).fetchall()
            items: list[dict[str, Any]] = []
            aggregate = {
                "ready_for_human_review": 0,
                "generating": 0,
                "blocked": 0,
                "reviewed": 0,
                "temporary_choice_count": 0,
                "confirmed_choice_count": 0,
                "timeline_mismatch_count": 0,
            }
            for row in rows:
                episode_id = str(row["episode_id"])
                choices = self._choices(connection, session_id, episode_id)
                asset_inputs = self._asset_inputs(connection, session_id, episode_id)
                timeline, timeline_videos = self._timeline(connection, episode_id, session_id)
                render, delivery = self._latest_outputs(
                    connection,
                    episode_id,
                    str(timeline["id"]) if timeline is not None else None,
                )
                video_choices = [choice for choice in choices if choice["slot_role"] == "VIDEO"]
                confirmed = [
                    choice
                    for choice in choices
                    if choice["choice_type"] == "HUMAN"
                    and choice["selection_state"] == "CONFIRMED"
                    and choice["human_review_decision_id"]
                ]
                mismatches: list[dict[str, Any]] = []
                for choice in video_choices:
                    timeline_media = timeline_videos.get(str(choice["target_id"]))
                    if timeline_media != choice["candidate_id"]:
                        mismatches.append(
                            {
                                "shot_id": choice["target_id"],
                                "shot_code": choice["shot_code"],
                                "session_media_version_id": choice["candidate_id"],
                                "timeline_media_version_id": timeline_media,
                            }
                        )
                blockers: list[dict[str, Any]] = []
                item_state = str(row["state"])
                if item_state in {"BLOCKED", "FAILED"}:
                    blockers.append(
                        {
                            "code": str(row["last_error_code"] or "PRODUCTION_ITEM_BLOCKED"),
                            "message": str(row["last_error_message"] or "本集生产未完成"),
                        }
                    )
                if item_state in {"WAITING", "COMPLETED"}:
                    if not video_choices:
                        blockers.append(
                            {"code": "SESSION_VIDEO_CHOICES_MISSING", "message": "本集没有会话视频选择证据"}
                        )
                    if timeline is None:
                        blockers.append(
                            {"code": "TIMELINE_PREVIEW_MISSING", "message": "本集还没有可审核时间线"}
                        )
                    elif str(timeline["status"]) != "FROZEN":
                        blockers.append(
                            {"code": "TIMELINE_NOT_FROZEN", "message": "最新时间线尚未冻结"}
                        )
                    if render is None or str(render["integrity_status"]) != "VERIFIED":
                        blockers.append(
                            {"code": "EPISODE_PREVIEW_RENDER_MISSING", "message": "本集还没有已验证预览成片"}
                        )
                if mismatches:
                    blockers.append(
                        {
                            "code": "SESSION_TIMELINE_CHOICE_MISMATCH",
                            "message": f"{len(mismatches)} 个镜头的会话选择与时间线引用不一致",
                        }
                    )
                pending_asset_inputs = [
                    asset_input
                    for asset_input in asset_inputs
                    if asset_input["review_status"] != "CONFIRMED"
                ]
                if pending_asset_inputs:
                    blockers.append(
                        {
                            "code": "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED",
                            "message": f"{len(pending_asset_inputs)} 个机器临时资产身份仍需人工确认",
                        }
                    )
                all_confirmed = bool(choices) and len(confirmed) == len(choices)
                if blockers:
                    review_status = "BLOCKED"
                elif all_confirmed:
                    review_status = "REVIEWED"
                elif item_state in {"WAITING", "COMPLETED"}:
                    review_status = "READY_FOR_HUMAN_REVIEW"
                else:
                    review_status = "GENERATING"
                aggregate[review_status.lower()] += 1
                aggregate["temporary_choice_count"] += sum(
                    1 for choice in choices if choice["selection_state"] == "TEMPORARY"
                )
                aggregate["confirmed_choice_count"] += len(confirmed)
                aggregate["timeline_mismatch_count"] += len(mismatches)
                repair_plan = self.repair_plan(
                    item_state=item_state,
                    current_stage=str(row["current_stage"]),
                    blockers=blockers,
                )
                items.append(
                    {
                        "session_item_id": str(row["id"]),
                        "episode_id": episode_id,
                        "episode_code": str(row["episode_code"]),
                        "episode_title": (
                            str(row["episode_title"]) if row["episode_title"] is not None else None
                        ),
                        "ordinal": int(row["ordinal"]),
                        "item_revision": int(row["revision"]),
                        "item_state": item_state,
                        "current_stage": str(row["current_stage"]),
                        "review_status": review_status,
                        "choices": choices,
                        "asset_inputs": asset_inputs,
                        "timeline": timeline,
                        "timeline_choice_consistency": {
                            "status": "MATCH" if not mismatches else "MISMATCH",
                            "session_video_choice_count": len(video_choices),
                            "timeline_video_item_count": len(timeline_videos),
                            "mismatches": mismatches,
                        },
                        "preview_render": render,
                        "delivery": delivery,
                        "blockers": blockers,
                        "repair_plan": repair_plan,
                        "allowed_actions": self.allowed_actions(
                            review_status=review_status,
                            repair_plan=repair_plan,
                        ),
                    }
                )
        next_cursor = cursor + len(items) if cursor + len(items) < total else None
        return {
            "session_id": str(session["id"]),
            "project_id": str(session["project_id"]),
            "session_status": str(session["status"]),
            "session_revision": int(session["revision"]),
            "summary": {"episode_count": total, "page_episode_count": len(items), **aggregate},
            "items": items,
            "cursor": cursor,
            "limit": limit,
            "total": total,
            "next_cursor": next_cursor,
            "read_only": True,
            "human_approval_written": False,
            "request_shape": "bounded_production_session_review_v2",
        }

    def confirm_episode(
        self,
        session_id: str,
        episode_id: str,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Bind existing, explicit human approvals to exact session choices.

        This command deliberately does not create review decisions.  The caller
        must first complete the normal media review checklist, then submit the
        returned decision ids here.  That keeps bulk confirmation auditable and
        prevents an unattended worker from manufacturing human approval.
        """

        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "确认分集选择必须提供有效 Idempotency-Key",
            )
        approvals = [
            {
                "production_choice_id": str(item["production_choice_id"]),
                "review_decision_id": str(item["review_decision_id"]),
            }
            for item in command.get("approvals") or []
        ]
        approvals.sort(key=lambda item: item["production_choice_id"])
        payload = {
            "session_id": session_id,
            "episode_id": episode_id,
            "expected_revision": int(command["expected_revision"]),
            "approvals": approvals,
        }
        payload_hash = _digest(payload)
        scope = f"production-session:confirm-episode:{session_id}:{episode_id}"
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
                        "相同 Idempotency-Key 不能确认不同的审核结果",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["idempotent_replay"] = True
                return replay

            session = self._session(connection, session_id)
            if int(session["revision"]) != int(command["expected_revision"]):
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVISION_CONFLICT",
                    "生产会话已变化，请刷新待审页后重试",
                    {
                        "expected_revision": int(command["expected_revision"]),
                        "actual_revision": int(session["revision"]),
                    },
                )
            item = connection.execute(
                """SELECT * FROM production_session_items
                   WHERE session_id=? AND episode_id=?""",
                (session_id, episode_id),
            ).fetchone()
            if item is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_MISMATCH",
                    "生产会话不包含当前分集",
                    {"session_id": session_id, "episode_id": episode_id},
                )
            if str(item["state"]) not in {"WAITING", "COMPLETED"}:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_REVIEW_NOT_READY",
                    "本集尚未进入人工审核阶段",
                    {"item_state": str(item["state"]), "current_stage": str(item["current_stage"])},
                )
            choices = connection.execute(
                """SELECT pc.*,mv.integrity_status
                   FROM production_choices pc
                   JOIN media_versions mv ON mv.id=pc.candidate_id
                   WHERE pc.session_id=? AND pc.episode_id=? AND pc.selection_state!='REVOKED'
                   ORDER BY pc.id""",
                (session_id, episode_id),
            ).fetchall()
            if not choices:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_CHOICES_REQUIRED",
                    "本集没有可确认的会话选择",
                )
            asset_inputs = self._asset_inputs(connection, session_id, episode_id)
            unresolved_asset_inputs = [
                item
                for item in asset_inputs
                if item["review_status"] != "CONFIRMED"
            ]
            if unresolved_asset_inputs:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED",
                    "必须先人工确认本次生产使用的临时资产身份",
                    {
                        "items": [
                            {
                                "asset_proposal_id": item["asset_proposal_id"],
                                "story_asset_id": item["story_asset_id"],
                                "review_status": item["review_status"],
                            }
                            for item in unresolved_asset_inputs
                        ]
                    },
                )
            approval_by_choice = {item["production_choice_id"]: item["review_decision_id"] for item in approvals}
            choice_ids = {str(row["id"]) for row in choices}
            if set(approval_by_choice) != choice_ids:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_APPROVALS_INCOMPLETE",
                    "必须为本集每个有效会话选择提供对应的人工批准",
                    {
                        "missing_choice_ids": sorted(choice_ids - set(approval_by_choice)),
                        "unexpected_choice_ids": sorted(set(approval_by_choice) - choice_ids),
                    },
                )
            timeline, timeline_videos = self._timeline(connection, episode_id, session_id)
            if timeline is None or str(timeline["status"]) != "FROZEN":
                raise DomainRuleError(
                    "PRODUCTION_SESSION_TIMELINE_REQUIRED",
                    "确认前必须存在最新冻结时间线",
                )
            render, _delivery = self._latest_outputs(
                connection,
                episode_id,
                str(timeline["id"]),
            )
            if (
                render is None
                or str(render["integrity_status"]) != "VERIFIED"
                or str(render["timeline_revision_id"]) != str(timeline["id"])
            ):
                raise DomainRuleError(
                    "PRODUCTION_SESSION_PREVIEW_REQUIRED",
                    "确认前必须生成基于最新冻结时间线的已验证预览成片",
                )
            render_approval = require_latest_episode_render_approval(connection, render)
            confirmed: list[dict[str, str]] = []
            for choice in choices:
                choice_id = str(choice["id"])
                candidate_id = str(choice["candidate_id"])
                if str(choice["integrity_status"]) != "VERIFIED":
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_CHOICE_UNVERIFIED",
                        "不能确认完整性未验证的候选",
                        {"production_choice_id": choice_id, "candidate_id": candidate_id},
                    )
                if str(choice["slot_role"]) == "VIDEO" and timeline_videos.get(str(choice["target_id"])) != candidate_id:
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_TIMELINE_CHOICE_MISMATCH",
                        "会话视频选择与预览时间线引用不一致",
                        {
                            "production_choice_id": choice_id,
                            "candidate_id": candidate_id,
                            "timeline_candidate_id": timeline_videos.get(str(choice["target_id"])),
                        },
                    )
                decision_id = approval_by_choice[choice_id]
                decision = connection.execute(
                    """SELECT id,subject_type,subject_id,decision,is_stale
                       FROM review_decisions WHERE id=?""",
                    (decision_id,),
                ).fetchone()
                if (
                    decision is None
                    or str(decision["subject_type"]) != "MEDIA_VERSION"
                    or str(decision["subject_id"]) != candidate_id
                    or str(decision["decision"]) != "APPROVED"
                    or int(decision["is_stale"] or 0) != 0
                ):
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_HUMAN_APPROVAL_INVALID",
                        "人工批准必须有效且精确对应当前候选",
                        {
                            "production_choice_id": choice_id,
                            "candidate_id": candidate_id,
                            "review_decision_id": decision_id,
                        },
                    )
                connection.execute(
                    """UPDATE production_choices
                       SET choice_type='HUMAN',selection_state='CONFIRMED',human_review_decision_id=?,
                           updated_at=?,created_by=?,revision=revision+1
                       WHERE id=?""",
                    (decision_id, now, actor, choice_id),
                )
                confirmed.append(
                    {
                        "production_choice_id": choice_id,
                        "candidate_id": candidate_id,
                        "review_decision_id": decision_id,
                    }
                )
            connection.execute(
                """UPDATE production_session_items
                   SET state='COMPLETED',current_stage='COMPLETED',progress_json=?,
                       last_error_code=NULL,last_error_message=NULL,updated_at=?,revision=revision+1
                   WHERE id=?""",
                (
                    json.dumps(
                        {
                            "completion_percent": 100,
                            "human_review_confirmed": True,
                            "confirmed_choice_count": len(confirmed),
                            "timeline_revision_id": str(timeline["id"]),
                            "preview_render_id": str(render["id"]),
                            "preview_render_review_decision_id": str(render_approval["id"]),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                    item["id"],
                ),
            )
            counts = {
                str(row["state"]).lower(): int(row["count"])
                for row in connection.execute(
                    """SELECT state,COUNT(*) AS count FROM production_session_items
                       WHERE session_id=? GROUP BY state""",
                    (session_id,),
                ).fetchall()
            }
            total = sum(counts.values())
            counters = {
                "total": total,
                "pending": counts.get("pending", 0),
                "running": counts.get("running", 0),
                "waiting": counts.get("waiting", 0),
                "blocked": counts.get("blocked", 0),
                "failed": counts.get("failed", 0),
                "completed": counts.get("completed", 0),
                "cancelled": counts.get("cancelled", 0),
            }
            session_status = "COMPLETED" if counters["completed"] == total else str(session["status"])
            current_stage = "COMPLETED" if session_status == "COMPLETED" else "WAITING_REVIEW"
            connection.execute(
                """UPDATE production_sessions
                   SET status=?,current_stage=?,counters_json=?,finished_at=?,updated_at=?,revision=revision+1
                   WHERE id=?""",
                (
                    session_status,
                    current_stage,
                    json.dumps(counters, sort_keys=True, separators=(",", ":")),
                    now if session_status == "COMPLETED" else None,
                    now,
                    session_id,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'reviewer','PRODUCTION_SESSION_EPISODE_CONFIRMED','production_session',?,?,?)""",
                (
                    actor,
                    session_id,
                    "确认一键生产分集的精确候选与预览",
                    json.dumps(
                        {
                            "episode_id": episode_id,
                            "confirmed": confirmed,
                            "timeline_revision_id": str(timeline["id"]),
                            "preview_render_id": str(render["id"]),
                            "preview_render_review_decision_id": str(render_approval["id"]),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                   VALUES ('production.session.episode_confirmed',?,'production_session',?,?)""",
                (
                    session["project_id"],
                    session_id,
                    json.dumps(
                        {"episode_id": episode_id, "status": session_status},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            result = {
                "session": ProductionSessionService._session_view(connection, session_id),
                "episode_id": episode_id,
                "confirmed_choice_count": len(confirmed),
                "preview_render_review_decision_id": str(render_approval["id"]),
                "outcome": "SESSION_COMPLETED" if session_status == "COMPLETED" else "EPISODE_CONFIRMED",
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (
                    scope,
                    key,
                    payload_hash,
                    json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            return result
