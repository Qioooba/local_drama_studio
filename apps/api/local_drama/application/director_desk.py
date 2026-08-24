"""Director Desk aggregate projection over existing production facts."""

from __future__ import annotations

import hashlib
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

    def timeline_selections(self, project_id: str, episode_id: str, limit: int = 500) -> dict[str, Any]:
        """Return the episode's timeline selection facts in one bounded read.

        Timeline composition needs only ordered shot labels, the current selected
        video and continuity status. Reusing the full Director Desk projection
        page by page caused up to ten sequential aggregate requests.
        """
        bounded_limit = max(1, min(int(limit), 500))
        with self.database.connect() as connection:
            context = connection.execute(
                """SELECT 1 FROM projects p JOIN seasons se ON se.project_id=p.id
                JOIN episodes e ON e.season_id=se.id WHERE p.id=? AND e.id=?""",
                (project_id, episode_id),
            ).fetchone()
            if context is None:
                raise DomainRuleError(
                    "EPISODE_NOT_FOUND", "分集不存在或不属于当前项目",
                    {"project_id": project_id, "episode_id": episode_id},
                )
            total = int(connection.execute(
                "SELECT COUNT(*) FROM shots WHERE episode_id=? AND archived_at IS NULL", (episode_id,),
            ).fetchone()[0])
            rows = connection.execute(
                """WITH continuity AS (
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
                SELECT s.id,s.code,s.order_key,s.status,s.target_duration_ms,
                (SELECT se.media_version_id
                 FROM selections se
                 JOIN media_versions mv ON mv.id=se.media_version_id
                 JOIN media_assets ma ON ma.id=mv.media_asset_id
                 LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                 LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                 WHERE ma.media_kind='VIDEO' AND mv.mime_type LIKE 'video/%'
                   AND se.selection_type IN ('FORMAL_SELECTION','PROXY_WINNER')
                   AND ((ma.owner_type='SHOT' AND ma.owner_id=s.id)
                     OR (gi.owner_type='SHOT' AND gi.owner_id=s.id))
                 ORDER BY CASE se.selection_type WHEN 'FORMAL_SELECTION' THEN 2 ELSE 1 END DESC,
                          se.created_at DESC,se.id DESC LIMIT 1) current_video_media_version_id,
                COALESCE(c.continuity_status,'MISSING') continuity_status
                FROM shots s LEFT JOIN continuity c ON c.shot_id=s.id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id LIMIT ?""",
                (episode_id, bounded_limit),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows], "total": total,
            "has_more": total > len(rows), "limit": bounded_limit,
            "read_only": True, "request_shape": "bounded_timeline_selection_read_model",
        }

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
            intent_suggestions = self._intent_suggestions(
                connection, episode_id, shot_id, shot["scene_id"], source_context, revision_fields,
            )
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
                "intent_suggestions": intent_suggestions,
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
              (SELECT se.media_version_id
               FROM selections se
               JOIN media_versions selected_mv ON selected_mv.id=se.media_version_id
               JOIN media_assets selected_ma ON selected_ma.id=selected_mv.media_asset_id
               LEFT JOIN generation_variants selected_gv
                 ON selected_ma.owner_type='GENERATION_VARIANT' AND selected_ma.owner_id=selected_gv.id
               LEFT JOIN generation_intents selected_gi ON selected_gi.id=selected_gv.intent_id
               WHERE selected_ma.media_kind='VIDEO' AND selected_mv.mime_type LIKE 'video/%'
                 AND se.selection_type IN ('FORMAL_SELECTION','PROXY_WINNER')
                 AND ((selected_ma.owner_type='SHOT' AND selected_ma.owner_id=s.id)
                   OR (selected_gi.owner_type='SHOT' AND selected_gi.owner_id=s.id))
               ORDER BY CASE se.selection_type WHEN 'FORMAL_SELECTION' THEN 2 ELSE 1 END DESC,
                        se.created_at DESC,se.id DESC LIMIT 1) AS current_video_media_version_id,
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
                "thumbnail_media_version_id": row["thumbnail_media_version_id"],
                "current_video_media_version_id": row["current_video_media_version_id"], "status": row["status"],
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
            gv.seed_policy,gv.explicit_seed,gv.capability_profile_version_id,
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
            """SELECT sc.id AS scene_id,sc.code AS scene_code,sc.title AS scene_title,sc.location,sc.time_of_day,sc.revision AS scene_revision,
            esr.ordinal,esr.source_start,esr.source_end,esr.source_label
            FROM scenes sc LEFT JOIN episode_scene_ranges esr ON esr.scene_id=sc.id AND esr.episode_id=?
            WHERE sc.id=?""", (episode_id, scene_id)
        ).fetchone()
        return {
            "scene_id": scene_id, "source_range": dict(row) if row else None,
            "source_text": fields.get("source_text") or fields.get("source_passage"),
        }

    def _intent_suggestions(
        self,
        connection: sqlite3.Connection,
        episode_id: str,
        shot_id: str,
        scene_id: str | None,
        source_context: dict[str, Any],
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Return grounded, opt-in Director suggestions without mutating authority."""
        scene = source_context.get("source_range") if isinstance(source_context.get("source_range"), dict) else {}
        suggestion_sources = fields.get("suggestion_sources") if isinstance(fields.get("suggestion_sources"), dict) else {}
        scene_parts = [str(value).strip() for value in (scene.get("location"), scene.get("time_of_day")) if value]
        environment = None
        if scene_parts:
            scene_label = " · ".join(
                str(value).strip() for value in (scene.get("scene_code"), scene.get("scene_title")) if value
            ) or "当前场景"
            source_revision = str(scene.get("scene_revision") or "")
            adopted = suggestion_sources.get("environment") if isinstance(suggestion_sources.get("environment"), dict) else {}
            stale = bool(adopted and str(adopted.get("source_revision") or "") != source_revision)
            environment = {
                "value": "；".join(scene_parts),
                "source_label": scene_label,
                "source_kind": "SCENE",
                "source_revision": source_revision,
                "stale": stale,
                "stale_reason": "场景资料已更新，请重新采用并复核环境。" if stale else None,
            }

        previous = connection.execute(
            """WITH ordered AS (
              SELECT s.id,s.code,s.scene_id,sr.id AS revision_id,sr.revision_no,sr.fields_json,
                     ROW_NUMBER() OVER (ORDER BY CAST(s.order_key AS REAL),s.code,s.id) AS idx
              FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
              WHERE s.episode_id=? AND s.archived_at IS NULL
            ), current AS (SELECT idx FROM ordered WHERE id=?)
            SELECT id,code,scene_id,revision_id,revision_no,fields_json FROM ordered
            WHERE idx=(SELECT idx-1 FROM current)""",
            (episode_id, shot_id),
        ).fetchone()
        continuity: dict[str, Any] | None = None
        if previous is not None:
            source_revision = str(previous["revision_id"] or "")
            adopted = suggestion_sources.get("continuity") if isinstance(suggestion_sources.get("continuity"), dict) else {}
            stale = bool(adopted and str(adopted.get("source_revision") or "") != source_revision)
            same_scene = bool(scene_id and previous["scene_id"] == scene_id)
            if not same_scene:
                continuity = {
                    "value": None,
                    "source_label": str(previous["code"]),
                    "source_kind": "PREVIOUS_SHOT",
                    "eligible": False,
                    "reason": "上一镜不在同一场景，未自动建议继承。",
                    "source_revision": source_revision,
                    "stale": stale,
                    "stale_reason": "上一镜版本已更新，原连续性继承需要重新复核。" if stale else None,
                }
            else:
                previous_fields = _json(previous["fields_json"], {})
                performance = previous_fields.get("performance") if isinstance(previous_fields.get("performance"), dict) else {}
                inherited_parts = []
                if previous_fields.get("continuity"):
                    inherited_parts.append(str(previous_fields["continuity"]).strip())
                if previous_fields.get("environment"):
                    inherited_parts.append(f"环境：{str(previous_fields['environment']).strip()}")
                if performance.get("blocking_summary"):
                    inherited_parts.append(f"站位：{str(performance['blocking_summary']).strip()}")
                inherited_parts = list(dict.fromkeys(item for item in inherited_parts if item))
                continuity = {
                    "value": "；".join(inherited_parts) or None,
                    "source_label": str(previous["code"]),
                    "source_kind": "PREVIOUS_SHOT",
                    "eligible": bool(inherited_parts),
                    "reason": None if inherited_parts else "同场上一镜尚未记录可继承的环境或连续性状态。",
                    "source_revision": source_revision,
                    "stale": stale,
                    "stale_reason": "上一镜版本已更新，原连续性继承需要重新复核。" if stale else None,
                }
        elif fields.get("continuity") is None:
            continuity = {
                "value": None,
                "source_label": None,
                "source_kind": "PREVIOUS_SHOT",
                "eligible": False,
                "reason": "当前是本集第一镜，没有上一镜状态。",
                "source_revision": "",
                "stale": False,
                "stale_reason": None,
            }
        script = self._script_intent_suggestion(connection, episode_id, shot_id, scene_id, suggestion_sources)
        return {"environment": environment, "continuity": continuity, "script": script}

    @staticmethod
    def _script_intent_suggestion(
        connection: sqlite3.Connection,
        episode_id: str,
        shot_id: str,
        scene_id: str | None,
        suggestion_sources: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not scene_id or not _table_exists(connection, "script_breakdown_scene_applications"):
            return None
        application = connection.execute(
            """SELECT a.scene_no,a.breakdown_draft_revision_id,d.draft_json AS model_draft_json,
            r.draft_json AS revision_draft_json,sd.title AS source_document_title
            FROM script_breakdown_scene_applications a
            JOIN script_breakdown_drafts d ON d.id=a.breakdown_draft_id
            LEFT JOIN script_breakdown_draft_revisions r ON r.id=a.breakdown_draft_revision_id
            JOIN source_document_versions sdv ON sdv.id=d.source_document_version_id
            JOIN source_documents sd ON sd.id=sdv.source_document_id
            WHERE a.episode_id=? AND a.created_scene_id=?
            ORDER BY a.created_at DESC,a.id DESC LIMIT 1""",
            (episode_id, scene_id),
        ).fetchone()
        if application is None:
            return None
        ordinal_row = connection.execute(
            """WITH ordered AS (
              SELECT id,ROW_NUMBER() OVER (ORDER BY CAST(order_key AS REAL),code,id) AS ordinal
              FROM shots WHERE episode_id=? AND scene_id=? AND archived_at IS NULL
            ) SELECT ordinal FROM ordered WHERE id=?""",
            (episode_id, scene_id, shot_id),
        ).fetchone()
        if ordinal_row is None:
            return None
        payload = _json(application["revision_draft_json"] or application["model_draft_json"], {})
        scenes = payload.get("scenes") if isinstance(payload, dict) else None
        scene = next(
            (item for item in (scenes or []) if isinstance(item, dict) and int(item.get("scene_no", 0)) == int(application["scene_no"])),
            None,
        )
        if not isinstance(scene, dict):
            return None
        shot_no = int(ordinal_row["ordinal"])
        shot = next(
            (item for item in (scene.get("shots") or []) if isinstance(item, dict) and int(item.get("shot_no", 0)) == shot_no),
            None,
        )
        if not isinstance(shot, dict):
            return None
        action = str(shot.get("action") or "").strip()
        visual = str(shot.get("visual") or "").strip()
        summary = str(scene.get("summary") or "").strip()
        dialogue = shot.get("dialogue")
        if not any((action, visual, summary, dialogue)):
            return None
        fingerprint_payload = {
            "scene_no": int(application["scene_no"]),
            "shot_no": shot_no,
            "action": action,
            "visual": visual,
            "summary": summary,
            "dialogue": dialogue,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        adopted = suggestion_sources.get("script") if isinstance(suggestion_sources.get("script"), dict) else {}
        stale = bool(adopted and str(adopted.get("source_fingerprint") or "") != fingerprint)
        creative_intent = "；".join(dict.fromkeys(part for part in (visual, summary) if part))
        return {
            "subject_action": action,
            "creative_intent": creative_intent,
            "dialogue": dialogue,
            "source_label": f"{application['source_document_title']} · 场 {application['scene_no']} 镜 {shot_no}",
            "source_kind": "APPLIED_BREAKDOWN_DRAFT",
            "source_revision_id": application["breakdown_draft_revision_id"],
            "source_fingerprint": fingerprint,
            "stale": stale,
            "stale_reason": "剧本拆解来源已变化，请重新采用并复核镜头意图。" if stale else None,
        }

    def _frame_bridge(self, connection: sqlite3.Connection, episode_id: str, shot_id: str) -> dict[str, Any]:
        adjacent = connection.execute(
            """WITH ordered AS (SELECT id,code,scene_id,ROW_NUMBER() OVER (ORDER BY CAST(order_key AS REAL),code,id) idx
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
            same_scene = bool(from_row["scene_id"] and from_row["scene_id"] == to_row["scene_id"])
            conflict = str(constraint["compatibility_status"]).upper() in {"BLOCKED", "CONFLICT", "INCOMPATIBLE"}
            stale = bool(constraint["is_stale"] or (previous_end and previous_end["stale"]) or (current_start and current_start["stale"]))
            inheritance_recommended = bool(same_scene and previous_end and not current_start and not stale and not conflict)
            if not same_scene:
                inheritance_reason = "相邻镜头不属于同一场景，不建议默认继承；仍可由导演显式选择。"
            elif not previous_end:
                inheritance_reason = "同场上一镜尚无可继承尾帧。"
            elif stale:
                inheritance_reason = "同场来源已失效，请先刷新尾帧事实。"
            elif conflict:
                inheritance_reason = "同场边界存在冲突，不建议继承。"
            elif current_start:
                inheritance_reason = "本镜已有首帧；如需替换请显式重新继承。"
            else:
                inheritance_reason = "同场连续镜且上一镜尾帧有效，建议继承后复核再锁定。"
            return {
                "transition_id": constraint["id"], "boundary_revision": int(constraint["boundary_revision"]),
                "from_shot_id": from_row["id"], "from_shot_code": from_row["code"],
                "to_shot_id": to_row["id"], "to_shot_code": to_row["code"], "enforcement": constraint["enforcement"],
                "compatibility": constraint["compatibility_status"], "stale": bool(constraint["is_stale"]),
                "stale_reason": constraint["stale_reason"], "previous_end": previous_end, "current_start": current_start,
                "inheritance_recommended": inheritance_recommended, "inheritance_reason": inheritance_reason,
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
