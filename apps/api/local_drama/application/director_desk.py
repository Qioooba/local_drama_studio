"""Director Desk aggregate projection over existing production facts."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import missing_shot_fields
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database

ACTIVE_JOB_STATES = ("QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED")


def _json(value: object, default: object) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


class DirectorDeskReadModelService:
    """One bounded read facade. It never writes or caches a second authority."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, project_id: str, episode_id: str, shot_id: str | None, nav_radius: int = 12) -> dict[str, Any]:
        radius = max(2, min(int(nav_radius), 25))
        with self.database.connect() as connection:
            context = connection.execute(
                """SELECT p.id AS project_id,p.code AS project_code,p.title AS project_title,p.aspect_ratio,
                e.id AS episode_id,e.code AS episode_code,e.title AS episode_title,e.production_status
                FROM projects p JOIN seasons se ON se.project_id=p.id JOIN episodes e ON e.season_id=se.id
                WHERE p.id=? AND e.id=?""",
                (project_id, episode_id),
            ).fetchone()
            if context is None:
                raise DomainRuleError(
                    "EPISODE_NOT_FOUND", "分集不存在或不属于当前项目", {"project_id": project_id, "episode_id": episode_id}
                )

            if shot_id is None:
                raise DomainRuleError(
                    "DIRECTOR_SHOT_REQUIRED",
                    "进入导演台前必须显式选择镜头",
                    {"project_id": project_id, "episode_id": episode_id},
                )

            shot = connection.execute(
                """SELECT s.*,sr.revision_no,sr.fields_json,sr.is_frozen,
                sc.code AS scene_code,sc.title AS scene_title,
                (SELECT g.id FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
                 WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_id,
                (SELECT g.code FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
                 WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_code,
                (SELECT g.title FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
                 WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_title
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                LEFT JOIN scenes sc ON sc.id=s.scene_id
                WHERE s.id=? AND s.episode_id=? AND s.archived_at IS NULL""",
                (shot_id, episode_id),
            ).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在或不属于当前分集", {"shot_id": shot_id})

            episode_summary = connection.execute(
                """SELECT COUNT(*) AS shot_count,
                COALESCE(SUM(CASE WHEN status='APPROVED' THEN 1 ELSE 0 END),0) AS approved_count,
                COALESCE(SUM(CASE WHEN status='BLOCKED' THEN 1 ELSE 0 END),0) AS blocked_count
                FROM shots WHERE episode_id=? AND archived_at IS NULL""",
                (episode_id,),
            ).fetchone()
            nav = self._navigator(connection, episode_id, shot_id, radius)
            revision_fields = _json(shot["fields_json"], {})
            media = self._media_and_candidates(connection, shot_id)
            frame_bridge = self._frame_bridge(connection, episode_id, shot_id)
            assets, asset_states = self._assets(connection, episode_id, shot_id)
            active_jobs = self._active_jobs(connection, shot_id, media["variant_ids"])
            source_context = self._source_context(connection, episode_id, shot["scene_id"], revision_fields)
            review_summary, qc_summary = self._quality(connection, media["current_media"])
            preferences = self._preferences(connection, project_id, episode_id, shot_id)
            blockers = self._blockers(
                connection, project_id, shot, revision_fields, frame_bridge, media, preferences
            )

        return {
            "project": {
                "id": context["project_id"], "code": context["project_code"],
                "name": context["project_title"], "aspect_ratio": context["aspect_ratio"],
            },
            "episode": {
                "id": context["episode_id"], "code": context["episode_code"], "title": context["episode_title"],
                "status": context["production_status"], **dict(episode_summary),
            },
            "shot_nav": nav,
            "current_shot": {
                "shot": {
                    "id": shot["id"], "code": shot["code"], "order_key": shot["order_key"],
                    "target_duration_ms": shot["target_duration_ms"], "shot_type": shot["shot_type"],
                    "status": shot["status"], "revision": shot["revision"],
                    "scene_id": shot["scene_id"], "scene_code": shot["scene_code"], "scene_title": shot["scene_title"],
                    "group_id": shot["group_id"], "group_code": shot["group_code"], "group_title": shot["group_title"],
                },
                "current_revision": None if shot["current_revision_id"] is None else {
                    "id": shot["current_revision_id"], "revision_no": shot["revision_no"],
                    "is_frozen": bool(shot["is_frozen"]), "fields": revision_fields,
                },
                "source_context": source_context,
                "assets": assets,
                "asset_states": asset_states,
                "selected_variant": media["selected_variant"],
                "current_media": media["current_media"],
                "candidates": media["candidates"],
                "frame_bridge": frame_bridge,
                "qc_summary": qc_summary,
                "review_summary": review_summary,
                "generation_preferences": preferences,
                "active_jobs": active_jobs,
                "blockers": blockers,
            },
            "permissions": {"can_edit": True, "can_generate": True, "can_approve": True},
            "read_only": True,
            "request_shape": "bounded_director_desk_read_model",
        }

    def _navigator(self, connection: sqlite3.Connection, episode_id: str, shot_id: str, radius: int) -> dict[str, Any]:
        rows = connection.execute(
            """WITH ordered AS (
              SELECT s.id,s.code,s.order_key,s.status,s.current_revision_id,s.scene_id,
              sc.code AS scene_code,sc.title AS scene_title,
              (SELECT g.id FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
               WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_id,
              (SELECT g.code FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
               WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_code,
              (SELECT g.title FROM shot_group_members gm JOIN shot_groups g ON g.id=gm.group_id
               WHERE gm.shot_id=s.id AND g.status='ACTIVE' ORDER BY g.order_key,g.code,g.id LIMIT 1) AS group_title,
              ROW_NUMBER() OVER (ORDER BY CAST(s.order_key AS REAL),s.code,s.id)-1 AS idx,
              COUNT(*) OVER () AS total
              FROM shots s LEFT JOIN scenes sc ON sc.id=s.scene_id
              WHERE s.episode_id=? AND s.archived_at IS NULL
            ), selected AS (SELECT idx FROM ordered WHERE id=?), windowed AS (
              SELECT * FROM ordered WHERE idx BETWEEN MAX(0,(SELECT idx FROM selected)-?) AND (SELECT idx FROM selected)+?
            ), job_subjects AS (
              SELECT j.subject_id AS shot_id,j.state FROM jobs j
              WHERE j.subject_type='SHOT' AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
              UNION ALL
              SELECT gi.owner_id AS shot_id,j.state FROM jobs j
              JOIN generation_variants gv ON gv.id=j.subject_id JOIN generation_intents gi ON gi.id=gv.intent_id
              WHERE j.subject_type='GENERATION_VARIANT' AND gi.owner_type='SHOT'
              AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
            ), job_state AS (
              SELECT shot_id,MAX(CASE state WHEN 'RUNNING' THEN 4 WHEN 'CLAIMED' THEN 3 WHEN 'QUEUED' THEN 2 ELSE 1 END) rank
              FROM job_subjects GROUP BY shot_id
            ), thumbs AS (
              SELECT ma.owner_id,COALESCE(ma.approved_version_id,ma.selected_version_id) media_version_id
              FROM media_assets ma WHERE ma.owner_type='SHOT' AND COALESCE(ma.approved_version_id,ma.selected_version_id) IS NOT NULL
              GROUP BY ma.owner_id
            ), continuity AS (
              SELECT shot_id,
              CASE WHEN MAX(is_stale)=1 THEN 'STALE'
                   WHEN MAX(is_conflict)=1 THEN 'CONFLICT'
                   WHEN MAX(is_attention)=1 THEN 'ATTENTION' ELSE 'OK' END continuity_status
              FROM (
                SELECT to_shot_id shot_id,is_stale,
                CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END is_conflict,
                CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END is_attention
                FROM shot_transition_constraints
                UNION ALL
                SELECT from_shot_id,is_stale,
                CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END,
                CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END
                FROM shot_transition_constraints
              ) GROUP BY shot_id
            )
            SELECT w.*,t.media_version_id AS thumbnail_media_version_id,COALESCE(c.continuity_status,'MISSING') continuity_status,
            CASE j.rank WHEN 4 THEN 'RUNNING' WHEN 3 THEN 'CLAIMED' WHEN 2 THEN 'QUEUED' WHEN 1 THEN 'CANCEL_REQUESTED' END job_status
            FROM windowed w LEFT JOIN thumbs t ON t.owner_id=w.id LEFT JOIN continuity c ON c.shot_id=w.id LEFT JOIN job_state j ON j.shot_id=w.id
            ORDER BY w.idx""",
            (episode_id, shot_id, radius, radius),
        ).fetchall()
        selected_index = next(int(row["idx"]) for row in rows if row["id"] == shot_id)
        total = int(rows[0]["total"]) if rows else 0
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append({
                "id": row["id"], "code": row["code"], "order_key": row["order_key"],
                "scene_id": row["scene_id"], "scene_code": row["scene_code"], "scene_title": row["scene_title"],
                "group_id": row["group_id"], "group_code": row["group_code"], "group_title": row["group_title"],
                "thumbnail_media_version_id": row["thumbnail_media_version_id"], "status": row["status"],
                "continuity_status": row["continuity_status"], "job_status": row["job_status"],
            })
        start = int(rows[0]["idx"]) if rows else 0
        end = int(rows[-1]["idx"]) + 1 if rows else 0
        return {
            "items": items, "total": total, "selected_index": selected_index, "window_start": start,
            "window_end": end, "has_previous": start > 0, "has_next": end < total,
        }

    def _media_and_candidates(self, connection: sqlite3.Connection, shot_id: str) -> dict[str, Any]:
        rows = connection.execute(
            """WITH ranked_selections AS (
              SELECT se.media_version_id,se.selection_type,se.created_at,se.id,
              ROW_NUMBER() OVER (
                PARTITION BY se.selection_type ORDER BY se.created_at DESC,se.id DESC
              ) AS selection_rank
              FROM selections se
              JOIN media_versions selected_mv ON selected_mv.id=se.media_version_id
              JOIN media_assets selected_ma ON selected_ma.id=selected_mv.media_asset_id
              JOIN generation_variants selected_gv
                ON selected_ma.owner_type='GENERATION_VARIANT' AND selected_ma.owner_id=selected_gv.id
              JOIN generation_intents selected_gi ON selected_gi.id=selected_gv.intent_id
              WHERE selected_gi.owner_type='SHOT' AND selected_gi.owner_id=?
            )
            SELECT gv.id,gv.intent_id,gv.variant_no,gv.variant_type,gv.parent_variant_id,gv.branch_reason,
            gv.status,gv.is_stale,gv.stale_reason,gi.purpose,ma.id AS media_asset_id,ma.media_kind,
            mv.id AS media_version_id,mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,mv.duration_ms,
            mv.integrity_status,mv.created_at,
            CASE WHEN current_selection.media_version_id IS NOT NULL THEN 1 ELSE 0 END AS selected,
            current_selection.selection_type,current_selection.created_at AS current_selection_at,
            CASE WHEN ma.approved_version_id=mv.id THEN 1 ELSE 0 END AS approved
            FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
            LEFT JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
            LEFT JOIN media_versions mv ON mv.media_asset_id=ma.id
            LEFT JOIN ranked_selections current_selection
              ON current_selection.selection_rank=1 AND current_selection.media_version_id=mv.id
              AND current_selection.selection_type=CASE
                WHEN mv.stage='FORMAL' THEN 'FORMAL_SELECTION'
                WHEN mv.stage='PROXY' THEN 'PROXY_WINNER'
                WHEN mv.stage='KEYFRAME' AND ma.media_kind='IMAGE' THEN 'KEYFRAME'
                ELSE NULL END
            WHERE gi.owner_type='SHOT' AND gi.owner_id=?
            ORDER BY gv.created_at DESC,gv.variant_no DESC,mv.version_no DESC""",
            (shot_id, shot_id),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        variant_ids: list[str] = []
        selected_variant: dict[str, Any] | None = None
        current_media: dict[str, Any] | None = None
        for row in rows:
            candidate = dict(row)
            if str(candidate["id"]) not in variant_ids:
                variant_ids.append(str(candidate["id"]))
            candidate["is_stale"] = bool(candidate["is_stale"])
            candidate["selected"] = bool(candidate["selected"])
            candidate["approved"] = bool(candidate["approved"])
            if candidate["media_version_id"] is not None:
                candidates.append(candidate)

        selected_candidates = [candidate for candidate in candidates if candidate["selected"]]
        selected_candidate = max(
            selected_candidates,
            key=lambda candidate: (str(candidate.get("current_selection_at") or ""), str(candidate["media_version_id"])),
            default=None,
        )
        if selected_candidate is None:
            selected_candidate = next((candidate for candidate in candidates if candidate["approved"]), None)
        if selected_candidate is not None:
            selected_variant = {
                "id": selected_candidate["id"], "intent_id": selected_candidate["intent_id"],
                "variant_no": selected_candidate["variant_no"], "status": selected_candidate["status"],
                "is_stale": selected_candidate["is_stale"], "media_version_id": selected_candidate["media_version_id"],
            }
            current_media = selected_candidate
        for candidate in candidates:
            candidate.pop("selection_type", None)
            candidate.pop("current_selection_at", None)

        shot_media = connection.execute(
            """SELECT ma.id AS media_asset_id,ma.purpose,ma.media_kind,
            mv.id AS media_version_id,mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,mv.duration_ms,mv.integrity_status,
            CASE WHEN ma.approved_version_id=mv.id THEN 1 ELSE 0 END approved
            FROM media_assets ma JOIN media_versions mv ON mv.id=COALESCE(ma.approved_version_id,ma.selected_version_id)
            WHERE ma.owner_type='SHOT' AND ma.owner_id=?
            ORDER BY approved DESC,mv.updated_at DESC LIMIT 1""",
            (shot_id,),
        ).fetchone()
        if current_media is None and shot_media is not None:
            current_media = dict(shot_media)
            current_media["approved"] = bool(current_media["approved"])
        return {
            "candidates": candidates,
            "variant_ids": variant_ids,
            "selected_variant": selected_variant,
            "current_media": current_media,
        }

    def _assets(self, connection: sqlite3.Connection, episode_id: str, shot_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not _table_exists(connection, "story_assets"):
            return [], []
        if not _table_exists(connection, "story_asset_states"):
            rows = connection.execute(
                """SELECT sab.id AS binding_id,sab.role_in_shot,NULL AS asset_state_id,
                a.id,a.kind,a.code,a.name,a.description,a.canonical_media_version_id,a.status,
                NULL AS effective_state_id
                FROM shot_asset_bindings sab JOIN story_assets a ON a.id=sab.asset_id
                WHERE sab.shot_id=? ORDER BY a.kind,a.code,sab.role_in_shot""",
                (shot_id,),
            ).fetchall()
            return [dict(row) for row in rows], []
        rows = connection.execute(
            """SELECT sab.id AS binding_id,sab.role_in_shot,sab.asset_state_id,a.id,a.kind,a.code,a.name,a.description,
            a.canonical_media_version_id,a.status,
            COALESCE(sab.asset_state_id,easb.asset_state_id) AS effective_state_id
            FROM shot_asset_bindings sab JOIN story_assets a ON a.id=sab.asset_id
            LEFT JOIN episode_asset_state_bindings easb ON easb.episode_id=? AND easb.story_asset_id=a.id
            WHERE sab.shot_id=? ORDER BY a.kind,a.code,sab.role_in_shot""",
            (episode_id, shot_id),
        ).fetchall()
        assets = [dict(row) for row in rows]
        state_ids = [str(row["effective_state_id"]) for row in rows if row["effective_state_id"]]
        if not state_ids:
            return assets, []
        marks = ",".join("?" for _ in state_ids)
        states = connection.execute(
            f"""SELECT id,story_asset_id,code,label,state_kind,description,state_json,status,revision
            FROM story_asset_states WHERE id IN ({marks}) ORDER BY story_asset_id,code""", state_ids
        ).fetchall()
        projected = []
        for row in states:
            item = dict(row)
            item["state"] = _json(item.pop("state_json"), {})
            projected.append(item)
        return assets, projected

    def _source_context(
        self, connection: sqlite3.Connection, episode_id: str, scene_id: str | None, fields: dict[str, Any],
    ) -> dict[str, Any]:
        if not scene_id or not _table_exists(connection, "episode_scene_ranges"):
            return {"scene_id": scene_id, "source_range": None, "source_text": fields.get("source_text") or fields.get("source_passage")}
        row = connection.execute(
            """SELECT sc.id AS scene_id,sc.code AS scene_code,sc.title AS scene_title,sc.location,sc.time_of_day,
            esr.ordinal,esr.source_start,esr.source_end,esr.source_label
            FROM scenes sc LEFT JOIN episode_scene_ranges esr ON esr.scene_id=sc.id AND esr.episode_id=?
            WHERE sc.id=?""", (episode_id, scene_id)
        ).fetchone()
        return {
            "scene_id": scene_id, "source_range": dict(row) if row else None,
            "source_text": fields.get("source_text") or fields.get("source_passage"),
        }

    def _frame_bridge(self, connection: sqlite3.Connection, episode_id: str, shot_id: str) -> dict[str, Any]:
        adjacent = connection.execute(
            """WITH ordered AS (SELECT id,code,ROW_NUMBER() OVER (ORDER BY CAST(order_key AS REAL),code,id) idx
            FROM shots WHERE episode_id=? AND archived_at IS NULL),
            current AS (SELECT idx FROM ordered WHERE id=?) SELECT id,code,idx-(SELECT idx FROM current) delta FROM ordered
            WHERE idx BETWEEN (SELECT idx FROM current)-1 AND (SELECT idx FROM current)+1 ORDER BY idx""",
            (episode_id, shot_id),
        ).fetchall()
        by_delta = {int(row["delta"]): row for row in adjacent}

        def boundary(from_row: sqlite3.Row | None, to_row: sqlite3.Row | None) -> dict[str, Any] | None:
            if from_row is None or to_row is None:
                return None
            constraint = connection.execute(
                """SELECT * FROM shot_transition_constraints WHERE from_shot_id=? AND to_shot_id=?
                ORDER BY revision DESC,updated_at DESC,id DESC LIMIT 1""", (from_row["id"], to_row["id"])
            ).fetchone()
            if constraint is None:
                return None
            previous_end = self._anchor(connection, constraint["from_anchor_id"], constraint)
            current_start = self._anchor(connection, constraint["to_anchor_id"], constraint, inherited_from=previous_end)
            return {
                "transition_id": constraint["id"], "boundary_revision": int(constraint["boundary_revision"]),
                "from_shot_id": from_row["id"], "from_shot_code": from_row["code"],
                "to_shot_id": to_row["id"], "to_shot_code": to_row["code"], "enforcement": constraint["enforcement"],
                "compatibility": constraint["compatibility_status"], "stale": bool(constraint["is_stale"]),
                "stale_reason": constraint["stale_reason"], "previous_end": previous_end, "current_start": current_start,
            }

        previous = boundary(by_delta.get(-1), by_delta.get(0))
        following = boundary(by_delta.get(0), by_delta.get(1))
        states = [item for item in (previous, following) if item]
        stale = any(item["stale"] or (item["previous_end"] and item["previous_end"]["stale"]) or (item["current_start"] and item["current_start"]["stale"]) for item in states)
        statuses = {str(item["compatibility"]).upper() for item in states}
        compatibility = "STALE" if stale else "CONFLICT" if statuses & {"BLOCKED", "CONFLICT", "INCOMPATIBLE"} else "ATTENTION" if statuses & {"WARNING", "ATTENTION"} else "OK" if states else "MISSING"
        return {
            "previous": previous, "current_start": previous["current_start"] if previous else None,
            "current_end": following["previous_end"] if following else None, "next": following,
            "compatibility": compatibility, "stale": stale,
        }

    @staticmethod
    def _anchor(connection: sqlite3.Connection, anchor_id: str | None, constraint: sqlite3.Row, inherited_from: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not anchor_id:
            return None
        row = connection.execute(
            """SELECT fa.*,mv.rel_path FROM frame_anchors fa
            JOIN media_versions mv ON mv.id=fa.extracted_media_version_id WHERE fa.id=?""", (anchor_id,)
        ).fetchone()
        if row is None:
            return None
        inherited = bool(inherited_from and inherited_from["media_version_id"] == row["extracted_media_version_id"])
        source = "INHERITED" if inherited else "GENERATED" if str(row["role_hint"] or "").upper() == "GENERATED" else "EXTRACTED" if row["extraction_method"] else "EXPLICIT"
        conflict = str(constraint["compatibility_status"]).upper() in {"BLOCKED", "CONFLICT", "INCOMPATIBLE"}
        status = "STALE" if row["is_stale"] or constraint["is_stale"] else "CONFLICT" if conflict else "LOCKED" if str(constraint["enforcement"]).upper() in {"HARD", "LOCKED"} else "AUTO_INHERITED" if inherited else "GENERATED" if source == "GENERATED" else "EXPLICIT"
        return {
            "anchor_id": row["id"], "source_media_version_id": row["source_media_version_id"],
            "media_version_id": row["extracted_media_version_id"], "rel_path": row["rel_path"], "role_hint": row["role_hint"],
            "inherited_from_anchor_id": inherited_from["anchor_id"] if inherited and inherited_from else None,
            "source": source, "status": status, "stale": bool(row["is_stale"] or constraint["is_stale"]),
            "stale_reason": row["stale_reason"] or constraint["stale_reason"],
        }

    def _active_jobs(self, connection: sqlite3.Connection, shot_id: str, variant_ids: list[str]) -> list[dict[str, Any]]:
        ids = [shot_id, *variant_ids]
        marks = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT id,type,subject_type,subject_id,state,channel,priority,progress_json,progress_updated_at,started_at,
            last_error_code,last_error_detail_redacted,created_at,updated_at FROM jobs
            WHERE subject_id IN ({marks}) AND state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
            ORDER BY priority,created_at""", ids
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["progress"] = _json(item.pop("progress_json"), {})
            items.append(item)
        return items

    @staticmethod
    def _quality(connection: sqlite3.Connection, current_media: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
        media_id = str(current_media["media_version_id"]) if current_media and current_media.get("media_version_id") else None
        if media_id is None:
            return ({"subject_id": None, "count": 0, "latest": None}, {"subject_id": None, "latest_run": None, "results": []})
        reviews = connection.execute(
            """SELECT id,decision,subject_revision,is_stale,stale_reason,comment,created_at,created_by
            FROM review_decisions WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC,id DESC""", (media_id,)
        ).fetchall()
        run = connection.execute(
            """SELECT id,policy_version,status,created_at,updated_at FROM machine_check_runs
            WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC,id DESC LIMIT 1""", (media_id,)
        ).fetchone()
        results = [] if run is None else [
            {**dict(row), "details": _json(row["details_json"], {})}
            for row in connection.execute("SELECT item_id,result,details_json FROM machine_check_results WHERE run_id=? ORDER BY item_id", (run["id"],)).fetchall()
        ]
        return (
            {"subject_id": media_id, "count": len(reviews), "latest": dict(reviews[0]) if reviews else None},
            {"subject_id": media_id, "latest_run": dict(run) if run else None, "results": results},
        )

    @staticmethod
    def _preferences(connection: sqlite3.Connection, project_id: str, episode_id: str, shot_id: str) -> dict[str, Any]:
        if not _table_exists(connection, "generation_preference_sets"):
            return {"resolutions": [], "available": False}
        query = GenerationPreferenceQueryService(SqliteGenerationPreferenceRepository(connection))
        resolutions = []
        for capability in ("IMAGE_CHARACTER", "VIDEO_I2V", "VIDEO_FIRST_LAST_FRAME"):
            resolutions.append(query.resolve(project_id=project_id, episode_id=episode_id, shot_id=shot_id, capability=capability))
        return {"resolutions": resolutions, "available": True}

    @staticmethod
    def _blockers(
        connection: sqlite3.Connection, project_id: str, shot: sqlite3.Row, fields: dict[str, Any],
        frame_bridge: dict[str, Any], media: dict[str, Any], preferences: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []

        def add(code: str, message: str, scope: str = "SHOT", blocking: bool = True) -> None:
            blockers.append({"code": code, "message": message, "scope": scope, "blocking": blocking})

        bindings = connection.execute(
            """SELECT
            EXISTS(SELECT 1 FROM project_profile_bindings WHERE project_id=? AND status IN ('ACTIVE','SELECTED_CANDIDATE')) profile_bound,
            EXISTS(SELECT 1 FROM project_plan_bindings WHERE project_id=?) plan_bound,
            EXISTS(SELECT 1 FROM delivery_targets WHERE project_id=? AND status='ACTIVE') delivery_bound""",
            (project_id, project_id, project_id),
        ).fetchone()
        if not bindings["profile_bound"]:
            add("PROFILE_NOT_BOUND", "项目尚未绑定执行 Profile", "PROJECT")
        if not bindings["plan_bound"]:
            add("PRODUCTION_PLAN_NOT_BOUND", "项目尚未绑定生产计划", "PROJECT")
        if not bindings["delivery_bound"]:
            add("DELIVERY_TARGET_NOT_BOUND", "项目尚未绑定交付目标", "PROJECT", False)
        missing = missing_shot_fields(fields)
        if missing:
            add("DIRECTOR_FIELDS_MISSING", "镜头导演字段不完整：" + ", ".join(missing))
        if shot["status"] not in {"READY", "GENERATING", "REVIEW", "APPROVED"}:
            add("SHOT_NOT_PRODUCTION_READY", "镜头尚未标记为可生产")
        if frame_bridge["stale"]:
            add("FRAME_BRIDGE_STALE", "首尾帧来源已变化，需要重新继承", "CONTINUITY")
        if frame_bridge["compatibility"] == "CONFLICT":
            add("FRAME_BRIDGE_CONFLICT", "相邻镜头连续性约束冲突", "CONTINUITY")
        if not media["candidates"] and media["current_media"] is None:
            add("NO_MEDIA_CANDIDATES", "当前镜头还没有媒体候选", "MEDIA", False)
        for resolution in preferences.get("resolutions", []):
            if resolution.get("blocked_reason"):
                add(
                    "GENERATION_CAPABILITY_UNAVAILABLE",
                    f"{resolution['capability']}：{resolution['blocked_reason']}", "GENERATION",
                )
        return blockers
