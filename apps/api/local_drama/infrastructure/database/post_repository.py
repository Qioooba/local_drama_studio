"""Canonical SQLite projections for Post navigation and Review targets."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class SqlitePostReadRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,se.project_id FROM episodes e
            JOIN seasons se ON se.id=e.season_id WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return dict(row)

    def overview_facts(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            review_rows = self._review_rows(connection, episode_id, set(), True)
            timeline = connection.execute(
                """SELECT id,revision_no,status FROM timeline_revisions
                WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            frozen = connection.execute(
                """SELECT id FROM timeline_revisions WHERE episode_id=? AND status='FROZEN'
                ORDER BY revision_no DESC,id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            timeline_count = int(connection.execute(
                "SELECT COUNT(*) FROM timeline_revisions WHERE episode_id=?", (episode_id,)
            ).fetchone()[0])
            subtitle = connection.execute(
                "SELECT id FROM subtitle_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            subtitle_count = int(connection.execute(
                "SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (episode_id,)
            ).fetchone()[0])
            audio = connection.execute(
                """SELECT
                (SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?) AS dialogue_lines,
                (SELECT COUNT(*) FROM dialogue_candidate_selections dcs
                 JOIN dialogue_lines dl ON dl.id=dcs.dialogue_line_id WHERE dl.episode_id=?) AS adopted_tts,
                (SELECT COUNT(*) FROM audio_bindings WHERE episode_id=?) AS bindings,
                (SELECT COUNT(*) FROM audio_bindings WHERE episode_id=?
                 AND source_license_status IN ('VERIFIED_LOCAL','USER_OWNED','PUBLIC_DOMAIN')) AS verified_licenses""",
                (episode_id, episode_id, episode_id, episode_id),
            ).fetchone()
            render = connection.execute(
                """SELECT id,revision,integrity_status FROM episode_render_versions
                WHERE episode_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            render_count = int(connection.execute(
                "SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=?", (episode_id,)
            ).fetchone()[0])
            verified_render_count = int(connection.execute(
                "SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=? AND integrity_status='VERIFIED'",
                (episode_id,),
            ).fetchone()[0])
            package = connection.execute(
                """SELECT dp.id,dp.status FROM delivery_packages dp
                JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                WHERE erv.episode_id=? ORDER BY dp.created_at DESC,dp.id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            package_count = int(connection.execute(
                """SELECT COUNT(*) FROM delivery_packages dp JOIN episode_render_versions erv
                ON erv.id=dp.episode_render_version_id WHERE erv.episode_id=?""",
                (episode_id,),
            ).fetchone()[0])

        pending = sum(self._is_pending(row) for row in review_rows)
        blocked = sum(bool(row["blocker_codes"]) for row in review_rows)
        stale = sum(bool(row["latest_decision_stale"]) for row in review_rows)
        approved_render = next((row["target_id"] for row in review_rows
                                if row["target_kind"] == "EPISODE_RENDER_VERSION"
                                and row["latest_decision"] == "APPROVED"
                                and not row["latest_decision_stale"]), None)
        audio_missing = int(audio["dialogue_lines"]) > int(audio["adopted_tts"])
        license_missing = int(audio["verified_licenses"]) < int(audio["bindings"])
        blockers: list[dict[str, str]] = []
        if pending:
            blockers.append(self._blocker("REVIEW_TARGETS_PENDING", "仍有待处理的审核目标。", "REVIEW", "OPEN_REVIEW"))
        if audio_missing:
            blockers.append(self._blocker("DIALOGUE_AUDIO_INCOMPLETE", "部分台词尚未采用工作语音。", "POST_AUDIO", "OPEN_DIALOGUE_REFERENCE"))
        if license_missing:
            blockers.append(self._blocker("AUDIO_LICENSE_EVIDENCE_MISSING", "配乐或音效授权证据不完整。", "POST_AUDIO", "OPEN_AUDIO_EVIDENCE"))
        if frozen is None:
            blockers.append(self._blocker("FROZEN_TIMELINE_REQUIRED", "尚未冻结可供合成的时间线 revision。", "POST_EDIT", "FREEZE_TIMELINE"))
        if render is not None and str(render["integrity_status"]) != "VERIFIED":
            blockers.append(self._blocker("RENDER_INTEGRITY_FAILED", "最新整集成片未通过文件完整性验证。", "DELIVERY", "VERIFY_RENDER"))
        if render is not None and approved_render != str(render["id"]):
            blockers.append(self._blocker("LATEST_RENDER_APPROVAL_REQUIRED", "最新整集成片尚未获得有效人工批准。", "REVIEW", "REVIEW_RENDER"))

        review_state = "ATTENTION" if pending or blocked or stale else "READY" if review_rows else "EMPTY"
        audio_state = "BLOCKED" if license_missing else "ATTENTION" if audio_missing else "READY" if int(audio["dialogue_lines"]) or int(audio["bindings"]) else "EMPTY"
        edit_state = "READY" if frozen is not None else "ATTENTION" if timeline is not None else "EMPTY"
        delivery_state = "READY" if package is not None and str(package["status"]) == "VERIFIED" else "BLOCKED" if render is not None and (str(render["integrity_status"]) != "VERIFIED" or approved_render != str(render["id"])) else "ATTENTION" if render is not None else "EMPTY"
        if review_state == "ATTENTION":
            next_action = "OPEN_REVIEW"
        elif audio_state in {"BLOCKED", "ATTENTION"}:
            next_action = "OPEN_POST_AUDIO"
        elif frozen is None:
            next_action = "OPEN_POST_EDIT"
        elif render is None:
            next_action = "RENDER_EPISODE"
        elif approved_render != str(render["id"]):
            next_action = "REVIEW_RENDER"
        else:
            next_action = "OPEN_DELIVERY"
        return {
            "episode_id": episode_id,
            "project_id": str(episode["project_id"]),
            "episode_code": str(episode["code"]),
            "episode_title": episode.get("title"),
            "next_action": next_action,
            "review": {"state": review_state, "target_count": len(review_rows), "pending_count": pending,
                       "blocked_count": blocked, "stale_count": stale, "approved_render_id": approved_render},
            "audio": {"state": audio_state, "dialogue_line_count": int(audio["dialogue_lines"]),
                      "adopted_tts_count": int(audio["adopted_tts"]), "binding_count": int(audio["bindings"]),
                      "verified_license_count": int(audio["verified_licenses"])},
            "edit": {"state": edit_state, "timeline_revision_count": timeline_count,
                     "latest_timeline_id": str(timeline["id"]) if timeline else None,
                     "latest_timeline_revision_no": int(timeline["revision_no"]) if timeline else None,
                     "latest_timeline_status": str(timeline["status"]) if timeline else None,
                     "frozen_timeline_id": str(frozen["id"]) if frozen else None,
                     "subtitle_revision_count": subtitle_count,
                     "latest_subtitle_id": str(subtitle["id"]) if subtitle else None},
            "delivery": {"state": delivery_state, "render_count": render_count,
                         "verified_render_count": verified_render_count,
                         "latest_render_id": str(render["id"]) if render else None,
                         "latest_render_revision": int(render["revision"]) if render else None,
                         "latest_render_integrity": str(render["integrity_status"]) if render else None,
                         "package_count": package_count,
                         "latest_package_id": str(package["id"]) if package else None,
                         "latest_package_status": str(package["status"]) if package else None},
            "blockers": blockers,
            "allowed_actions": ["OPEN_REVIEW", "OPEN_POST_AUDIO", "OPEN_POST_EDIT", "OPEN_DELIVERY"],
        }

    def review_target_facts(self, episode_id: str, *, cursor: int, limit: int,
                            target_kinds: set[str], include_resolved: bool,
                            target_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._episode(connection, episode_id)
            rows = self._review_rows(connection, episode_id, target_kinds, include_resolved, target_id=target_id)
        selected = rows[cursor: cursor + limit]
        return {
            "items": selected,
            "cursor": cursor,
            "limit": limit,
            "total": len(rows),
            "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
            "target_kinds": sorted(target_kinds),
            "include_resolved": include_resolved,
        }

    @staticmethod
    def _is_pending(row: dict[str, Any]) -> bool:
        return row["latest_decision"] not in {"APPROVED", "REJECTED"} or bool(row["latest_decision_stale"])

    @classmethod
    def _include_review_target(cls, row: dict[str, Any], *, include_resolved: bool,
                               target_id: str | None) -> bool:
        """Keep deep links addressable while the default queue stays pending-only."""
        if target_id is not None:
            return row["target_id"] == target_id
        return include_resolved or cls._is_pending(row)

    @staticmethod
    def _blocker(code: str, message: str, owner: str, action: str) -> dict[str, str]:
        return {"code": code, "message": message, "owner_route": owner, "repair_action": action}

    def _review_rows(self, connection: sqlite3.Connection, episode_id: str,
                     target_kinds: set[str], include_resolved: bool,
                     *, target_id: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not target_kinds or "MEDIA_VERSION" in target_kinds:
            media = connection.execute(
                """SELECT mv.id AS target_id,ma.project_id,? AS episode_id,
                COALESCE(direct_shot.id,generation_shot.id,dialogue_shot.id) AS shot_id,
                COALESCE(direct_shot.code,generation_shot.code,dialogue_shot.code,mv.id) AS label,
                ma.media_kind,mv.stage,mv.duration_ms,ma.revision AS subject_revision,mv.integrity_status,mv.created_at,
                COALESCE(mc.status,'NOT_RUN') AS machine_status,
                CASE WHEN ma.media_kind='AUDIO' AND EXISTS (
                  SELECT 1 FROM dialogue_candidate_selections dcs
                  JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
                  WHERE tc.media_version_id=mv.id
                    AND dcs.id=(SELECT latest.id FROM dialogue_candidate_selections latest
                      WHERE latest.dialogue_line_id=dcs.dialogue_line_id
                      ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
                ) THEN 1 ELSE 0 END AS is_adopted,
                template.id AS template_version_id,template.code AS template_code,template.items_json AS template_items_json,
                rd.id AS latest_decision_id,rd.decision AS latest_decision,rd.revision AS latest_decision_revision,
                COALESCE(rd.is_stale,0) AS latest_decision_stale
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                LEFT JOIN shots direct_shot ON ma.owner_type='SHOT' AND ma.owner_id=direct_shot.id
                LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                LEFT JOIN shots generation_shot ON gi.owner_type='SHOT' AND gi.owner_id=generation_shot.id
                LEFT JOIN dialogue_text_revisions dtr ON ma.owner_type='DIALOGUE_TEXT_REVISION' AND ma.owner_id=dtr.id
                LEFT JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id
                LEFT JOIN shots dialogue_shot ON dialogue_shot.id=dl.shot_id
                LEFT JOIN review_decisions rd ON rd.id=(SELECT r.id FROM review_decisions r
                  WHERE r.subject_type='MEDIA_VERSION' AND r.subject_id=mv.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1)
                LEFT JOIN machine_check_runs mc ON mc.id=(SELECT m.id FROM machine_check_runs m
                  WHERE m.subject_type='MEDIA_VERSION' AND m.subject_id=mv.id ORDER BY m.created_at DESC,m.id DESC LIMIT 1)
                JOIN review_templates template ON template.id=(SELECT rt.id FROM review_templates rt
                  WHERE rt.code=CASE WHEN ma.media_kind='AUDIO' THEN 'audio_mix'
                    WHEN ma.media_kind='VIDEO' AND mv.stage='FORMAL' THEN 'formal_video'
                    WHEN ma.media_kind='VIDEO' THEN 'proxy_video' ELSE 'image_asset' END
                  ORDER BY rt.version_no DESC,rt.id DESC LIMIT 1)
                WHERE COALESCE(direct_shot.episode_id,generation_shot.episode_id,dl.episode_id,
                  CASE WHEN ma.owner_type='EPISODE' THEN ma.owner_id END)=?
                ORDER BY mv.created_at,mv.id""",
                (episode_id, episode_id),
            ).fetchall()
            for raw in media:
                row = dict(raw)
                template_items = json.loads(str(row.pop("template_items_json")))
                blockers = []
                if str(row["integrity_status"]) != "VERIFIED":
                    blockers.append("MEDIA_INTEGRITY_REQUIRED")
                if str(row["stage"]) == "FORMAL" and str(row["machine_status"]) != "PASS":
                    blockers.append("MACHINE_QC_REQUIRED")
                if bool(row["latest_decision_stale"]):
                    blockers.append("PREVIOUS_DECISION_STALE")
                allowed_actions = ["SUBMIT_REVIEW_DECISION"]
                if str(row["media_kind"]) == "VIDEO" and str(row["integrity_status"]) == "VERIFIED" and int(row["duration_ms"] or 0) > 0:
                    allowed_actions.append("CREATE_FRAME_ANNOTATION")
                item = {**row, "target_kind": "MEDIA_VERSION", "latest_decision_stale": bool(row["latest_decision_stale"]),
                        "template_items": template_items,
                        "blocker_codes": blockers, "allowed_actions": allowed_actions}
                if self._include_review_target(item, include_resolved=include_resolved, target_id=target_id):
                    rows.append(item)
        if not target_kinds or "EPISODE_RENDER_VERSION" in target_kinds:
            renders = connection.execute(
                """SELECT erv.id AS target_id,se.project_id,erv.episode_id,NULL AS shot_id,
                e.code || ' 整集成片' AS label,NULL AS media_kind,'FORMAL' AS stage,
                NULL AS duration_ms,erv.revision AS subject_revision,erv.integrity_status,NULL AS machine_status,
                template.id AS template_version_id,template.code AS template_code,template.items_json AS template_items_json,
                rd.id AS latest_decision_id,rd.decision AS latest_decision,rd.revision AS latest_decision_revision,
                COALESCE(rd.is_stale,0) AS latest_decision_stale,erv.created_at
                FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons se ON se.id=e.season_id
                JOIN review_templates template ON template.id=(SELECT rt.id FROM review_templates rt
                  WHERE rt.code='episode_render' ORDER BY rt.version_no DESC,rt.id DESC LIMIT 1)
                LEFT JOIN review_decisions rd ON rd.id=(SELECT r.id FROM review_decisions r
                  WHERE r.subject_type='EPISODE_RENDER_VERSION' AND r.subject_id=erv.id
                  ORDER BY r.created_at DESC,r.id DESC LIMIT 1)
                WHERE erv.episode_id=? ORDER BY erv.created_at,erv.id""",
                (episode_id,),
            ).fetchall()
            for raw in renders:
                row = dict(raw)
                template_items = json.loads(str(row.pop("template_items_json")))
                blockers = []
                if str(row["integrity_status"]) != "VERIFIED":
                    blockers.append("RENDER_INTEGRITY_REQUIRED")
                if bool(row["latest_decision_stale"]):
                    blockers.append("PREVIOUS_DECISION_STALE")
                item = {**row, "target_kind": "EPISODE_RENDER_VERSION", "latest_decision_stale": bool(row["latest_decision_stale"]),
                        "template_items": template_items,
                        "blocker_codes": blockers, "allowed_actions": ["SUBMIT_REVIEW_DECISION"]}
                if self._include_review_target(item, include_resolved=include_resolved, target_id=target_id):
                    rows.append(item)
        # Review operators need the newest pending material first.  The list is
        # still bounded by the caller, so ordering here is what makes the first
        # page useful when a project has more targets than the page limit.  Keep
        # the target id as a deterministic tie-breaker for equal timestamps;
        # target_id is also sufficient for the separate deep-link lookup below.
        rows.sort(key=lambda item: (str(item["created_at"]), str(item["target_id"])), reverse=True)
        return rows
