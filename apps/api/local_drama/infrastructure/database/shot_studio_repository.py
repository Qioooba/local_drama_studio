"""SQLite-backed Shot Studio aggregate facts.

SQL ownership lives in infrastructure.  The application layer consumes this
repository through ``ShotStudioReadPort`` and owns projection semantics.
"""

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


def _json(value: object, default: object) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class SqliteShotStudioReadRepository:
    """One bounded SQLite read repository over canonical production facts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def studio_facts(self, episode_id: str, shot_id: str, nav_radius: int = 12) -> dict[str, Any]:
        radius = max(2, min(int(nav_radius), 25))
        with self.database.connect() as connection:
            context = connection.execute(
                """SELECT p.id AS project_id,p.code AS project_code,p.title AS project_title,p.aspect_ratio,
                e.id AS episode_id,e.code AS episode_code,e.title AS episode_title,e.production_status
                FROM projects p JOIN seasons se ON se.project_id=p.id JOIN episodes e ON e.season_id=se.id
                WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if context is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
            project_id = str(context["project_id"])

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
                connection,
                episode_id,
                shot_id,
                shot["scene_id"],
                source_context,
                revision_fields,
            )
            review_summary, qc_summary = self._quality(connection, media["current_media"])
            preferences = self._preferences(connection, project_id, episode_id, shot_id)
            dialogue = self._dialogue(connection, project_id, shot_id)
            generation_intents = [
                dict(row)
                for row in connection.execute(
                    """SELECT id,project_id,owner_type,owner_id,purpose,creative_goal,status,created_at,0 AS idempotent_replay
                    FROM generation_intents WHERE owner_type='SHOT' AND owner_id=?
                    ORDER BY created_at DESC,id DESC LIMIT 20""",
                    (shot_id,),
                ).fetchall()
            ]
            blockers = self._blockers(connection, project_id, shot, revision_fields, frame_bridge, media, preferences)

        return {
            "project": {
                "id": context["project_id"],
                "code": context["project_code"],
                "name": context["project_title"],
                "aspect_ratio": context["aspect_ratio"],
            },
            "episode": {
                "id": context["episode_id"],
                "code": context["episode_code"],
                "title": context["episode_title"],
                "status": context["production_status"],
                **dict(episode_summary),
            },
            "shot_nav": nav,
            "current_shot": {
                "shot": {
                    "id": shot["id"],
                    "code": shot["code"],
                    "order_key": shot["order_key"],
                    "target_duration_ms": shot["target_duration_ms"],
                    "shot_type": shot["shot_type"],
                    "status": shot["status"],
                    "revision": shot["revision"],
                    "scene_id": shot["scene_id"],
                    "scene_code": shot["scene_code"],
                    "scene_title": shot["scene_title"],
                    "group_id": shot["group_id"],
                    "group_code": shot["group_code"],
                    "group_title": shot["group_title"],
                },
                "current_revision": None
                if shot["current_revision_id"] is None
                else {
                    "id": shot["current_revision_id"],
                    "revision_no": shot["revision_no"],
                    "is_frozen": bool(shot["is_frozen"]),
                    "fields": revision_fields,
                },
                "source_context": source_context,
                "intent_suggestions": intent_suggestions,
                "assets": assets,
                "asset_states": asset_states,
                "selected_variant": media["selected_variant"],
                "current_media": media["current_media"],
                "candidates": media["candidates"],
                "frame_bridge": frame_bridge,
                "dialogue": dialogue,
                "qc_summary": qc_summary,
                "review_summary": review_summary,
                "generation_preferences": preferences,
                "generation_intents": generation_intents,
                "active_jobs": active_jobs,
                "blockers": blockers,
            },
        }

    def continuity_facts(self, shot_id: str) -> dict[str, Any]:
        """Project the current shot and its immediate neighbours from canonical heads."""
        facet_aliases = {
            "appearance": ("appearance", "character_appearance"),
            "costume": ("costume", "wardrobe"),
            "props": ("props", "prop"),
            "lighting": ("lighting", "light"),
            "spatial_direction": ("spatial_direction", "screen_direction"),
            "continuity": ("continuity", "continuity_refs"),
        }
        with self.database.connect() as connection:
            current = connection.execute(
                "SELECT id,episode_id FROM shots WHERE id=? AND archived_at IS NULL",
                (shot_id,),
            ).fetchone()
            if current is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            rows = connection.execute(
                """SELECT s.id,s.code,s.order_key,s.status,s.target_duration_ms,s.current_revision_id,
                sr.revision_no,sr.is_frozen,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id""",
                (current["episode_id"],),
            ).fetchall()
            current_index = next(index for index, row in enumerate(rows) if str(row["id"]) == shot_id)
            window_rows = [
                rows[index]
                for index in (current_index - 1, current_index, current_index + 1)
                if 0 <= index < len(rows)
            ]
            window_ids = [str(row["id"]) for row in window_rows]
            placeholders = ",".join("?" for _ in window_ids)
            media_rows = connection.execute(
                f"""SELECT ws.shot_id AS owner_id,ma.id AS media_asset_id,ma.purpose,ma.media_kind,
                CASE WHEN ma.approved_version_id=ws.media_version_id THEN 'APPROVED' ELSE 'SELECTED' END selection_state,
                mv.id AS media_version_id,mv.version_no,mv.stage,mv.integrity_status
                FROM shot_working_media_slots ws
                JOIN media_versions mv ON mv.id=ws.media_version_id
                JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE ws.shot_id IN ({placeholders})
                ORDER BY ws.shot_id,CASE ws.slot_type WHEN 'KEYFRAME' THEN 1 ELSE 2 END,ws.id""",
                window_ids,
            ).fetchall()
            transition_rows = connection.execute(
                f"""SELECT id,from_shot_id,to_shot_id,constraint_type,enforcement,
                compatibility_status,is_stale,stale_reason,from_anchor_id,to_anchor_id,boundary_revision
                FROM shot_transition_constraints
                WHERE from_shot_id IN ({placeholders}) AND to_shot_id IN ({placeholders})
                ORDER BY from_shot_id,to_shot_id,id""",
                [*window_ids, *window_ids],
            ).fetchall()

        media_by_shot: dict[str, list[dict[str, Any]]] = {item: [] for item in window_ids}
        for media in media_rows:
            fact = dict(media)
            media_by_shot[str(fact.pop("owner_id"))].append(fact)

        def project(row: sqlite3.Row, position: str) -> dict[str, Any]:
            fields = _json(row["fields_json"], {})
            facets = {
                facet: next(
                    (fields[key] for key in aliases if fields.get(key) not in (None, "", [])),
                    None,
                )
                for facet, aliases in facet_aliases.items()
            }
            return {
                "position": position,
                "id": str(row["id"]),
                "code": str(row["code"]),
                "order_key": str(row["order_key"]),
                "status": str(row["status"]),
                "target_duration_ms": int(row["target_duration_ms"]),
                "revision": {
                    "id": str(row["current_revision_id"]) if row["current_revision_id"] else None,
                    "revision_no": int(row["revision_no"]) if row["revision_no"] is not None else None,
                    "is_frozen": bool(row["is_frozen"]),
                },
                "facets": facets,
                "missing_facets": [key for key, value in facets.items() if value is None],
                "references": media_by_shot[str(row["id"])],
            }

        shots: dict[str, dict[str, Any] | None] = {
            "previous": project(rows[current_index - 1], "previous") if current_index > 0 else None,
            "current": project(rows[current_index], "current"),
            "next": project(rows[current_index + 1], "next") if current_index + 1 < len(rows) else None,
        }
        transitions = []
        for row in transition_rows:
            fact = dict(row)
            fact["is_stale"] = bool(fact["is_stale"])
            transitions.append(fact)
        return {
            "episode_id": str(current["episode_id"]),
            "selected_shot_id": shot_id,
            "shots": shots,
            "transitions": transitions,
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
              (SELECT ws.media_version_id FROM shot_working_media_slots ws
               JOIN media_versions selected_mv ON selected_mv.id=ws.media_version_id
               WHERE ws.shot_id=s.id AND ws.slot_type='VIDEO'
                 AND selected_mv.mime_type LIKE 'video/%' LIMIT 1) AS current_video_media_version_id,
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
            ), ranked_thumbs AS (
              SELECT ws.shot_id AS owner_id,ws.media_version_id,
              ROW_NUMBER() OVER (
                PARTITION BY ws.shot_id ORDER BY CASE ws.slot_type WHEN 'VIDEO' THEN 2 ELSE 1 END DESC
              ) AS slot_rank
              FROM shot_working_media_slots ws
            ), thumbs AS (
              SELECT owner_id,media_version_id FROM ranked_thumbs WHERE slot_rank=1
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
            items.append(
                {
                    "id": row["id"],
                    "code": row["code"],
                    "order_key": row["order_key"],
                    "scene_id": row["scene_id"],
                    "scene_code": row["scene_code"],
                    "scene_title": row["scene_title"],
                    "group_id": row["group_id"],
                    "group_code": row["group_code"],
                    "group_title": row["group_title"],
                    "thumbnail_media_version_id": row["thumbnail_media_version_id"],
                    "current_video_media_version_id": row["current_video_media_version_id"],
                    "status": row["status"],
                    "continuity_status": row["continuity_status"],
                    "job_status": row["job_status"],
                }
            )
        start = int(rows[0]["idx"]) if rows else 0
        end = int(rows[-1]["idx"]) + 1 if rows else 0
        return {
            "items": items,
            "total": total,
            "selected_index": selected_index,
            "window_start": start,
            "window_end": end,
            "has_previous": start > 0,
            "has_next": end < total,
        }

    def _media_and_candidates(self, connection: sqlite3.Connection, shot_id: str) -> dict[str, Any]:
        rows = connection.execute(
            """WITH current_slots AS (
              SELECT media_version_id,slot_type,updated_at
              FROM shot_working_media_slots WHERE shot_id=?
            )
            SELECT gv.id,gv.intent_id,gv.variant_no,gv.variant_type,gv.parent_variant_id,gv.branch_reason,
            gv.seed_policy,gv.explicit_seed,gv.capability_profile_version_id,
            gv.status,gv.is_stale,gv.stale_reason,gi.purpose,ma.id AS media_asset_id,ma.media_kind,
            mv.id AS media_version_id,mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,mv.duration_ms,
            mv.integrity_status,mv.created_at,
            EXISTS(SELECT 1 FROM media_cache_entries mce WHERE mce.media_version_id=mv.id
              AND mce.cache_kind='THUMBNAIL' AND mce.status='READY' AND mce.rel_path LIKE '%/medium/%') AS thumbnail_ready,
            CASE WHEN current_slot.media_version_id IS NOT NULL THEN 1 ELSE 0 END AS selected,
            current_slot.slot_type,current_slot.updated_at AS current_selection_at,
            CASE WHEN ma.approved_version_id=mv.id THEN 1 ELSE 0 END AS approved
            FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
            LEFT JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
            LEFT JOIN media_versions mv ON mv.media_asset_id=ma.id
            LEFT JOIN current_slots current_slot ON current_slot.media_version_id=mv.id
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
            candidate["thumbnail_ready"] = bool(candidate["thumbnail_ready"])
            if candidate["media_version_id"] is not None:
                candidates.append(candidate)

        candidate_versions = {str(c["media_version_id"]) for c in candidates if c["media_version_id"] is not None}
        lab_rows = connection.execute(
            """SELECT p.id AS promotion_id,p.created_at AS promoted_at,mv.id AS media_version_id,
            mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,mv.duration_ms,mv.integrity_status,
            ma.id AS media_asset_id,ma.media_kind,ma.purpose,ma.approved_version_id,
            EXISTS(SELECT 1 FROM media_cache_entries mce WHERE mce.media_version_id=mv.id
              AND mce.cache_kind='THUMBNAIL' AND mce.status='READY' AND mce.rel_path LIKE '%/medium/%') AS thumbnail_ready
            FROM visual_lab_promotions p
            JOIN media_versions mv ON mv.id=p.source_media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            WHERE p.target_type='SHOT_CANDIDATE' AND p.target_id=?""",
            (shot_id,),
        ).fetchall()
        for lab_row in lab_rows:
            media_version_id = str(lab_row["media_version_id"])
            if media_version_id in candidate_versions:
                continue
            candidate = dict(lab_row)
            target_version_id = str(candidate.pop("media_version_id"))
            candidate["id"] = f"lab-{candidate.pop('promotion_id')}"
            candidate["intent_id"] = "visual-lab"
            candidate["variant_no"] = 0
            candidate["variant_type"] = "VISUAL_LAB"
            candidate["parent_variant_id"] = None
            candidate["branch_reason"] = None
            candidate["seed_policy"] = None
            candidate["explicit_seed"] = None
            candidate["capability_profile_version_id"] = None
            candidate["status"] = "CANDIDATE"
            candidate["is_stale"] = False
            candidate["stale_reason"] = None
            candidate["media_version_id"] = target_version_id
            candidate["selected"] = False
            candidate["approved"] = str(candidate.get("approved_version_id") or "") == target_version_id
            candidate["thumbnail_ready"] = bool(candidate["thumbnail_ready"])
            candidate["candidate_source"] = "VISUAL_LAB"
            candidate.pop("approved_version_id", None)
            candidates.append(candidate)
            candidate_versions.add(media_version_id)

        selected_candidates = [candidate for candidate in candidates if candidate["selected"]]
        selected_candidate = max(
            selected_candidates,
            key=lambda candidate: (str(candidate.get("current_selection_at") or ""), str(candidate["media_version_id"])),
            default=None,
        )
        if selected_candidate is not None:
            selected_variant = {
                "id": selected_candidate["id"],
                "intent_id": selected_candidate["intent_id"],
                "variant_no": selected_candidate["variant_no"],
                "status": selected_candidate["status"],
                "is_stale": selected_candidate["is_stale"],
                "media_version_id": selected_candidate["media_version_id"],
            }
            current_media = selected_candidate
        for candidate in candidates:
            candidate.pop("slot_type", None)
            candidate.pop("current_selection_at", None)

        shot_media = connection.execute(
            """SELECT ma.id AS media_asset_id,ma.purpose,ma.media_kind,
            mv.id AS media_version_id,mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,mv.duration_ms,mv.integrity_status,
            EXISTS(SELECT 1 FROM media_cache_entries mce WHERE mce.media_version_id=mv.id
              AND mce.cache_kind='THUMBNAIL' AND mce.status='READY' AND mce.rel_path LIKE '%/medium/%') AS thumbnail_ready,
            CASE WHEN ma.approved_version_id=mv.id THEN 1 ELSE 0 END approved,1 AS selected
            FROM shot_working_media_slots ws
            JOIN media_versions mv ON mv.id=ws.media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            WHERE ws.shot_id=?
            ORDER BY CASE ws.slot_type WHEN 'VIDEO' THEN 2 ELSE 1 END DESC,ws.updated_at DESC LIMIT 1""",
            (shot_id,),
        ).fetchone()
        if current_media is None and shot_media is not None:
            current_media = dict(shot_media)
            current_media["approved"] = bool(current_media["approved"])
            current_media["thumbnail_ready"] = bool(current_media["thumbnail_ready"])
        return {
            "candidates": candidates,
            "variant_ids": variant_ids,
            "selected_variant": selected_variant,
            "current_media": current_media,
        }

    @staticmethod
    def _dialogue(connection: sqlite3.Connection, project_id: str, shot_id: str) -> dict[str, Any]:
        line_rows = connection.execute(
            """SELECT dl.id,dl.code,dl.speaker,dl.revision,
            dtr.id AS text_revision_id,dtr.revision_no,dtr.text,dtr.pronunciation_json,dtr.text_hash,dtr.created_at AS text_created_at
            FROM dialogue_lines dl
            JOIN dialogue_text_revisions dtr ON dtr.dialogue_line_id=dl.id
            WHERE dl.shot_id=? AND dtr.revision_no=(
              SELECT MAX(latest.revision_no) FROM dialogue_text_revisions latest WHERE latest.dialogue_line_id=dl.id
            )
            ORDER BY dl.code,dl.created_at,dl.id""",
            (shot_id,),
        ).fetchall()
        if not line_rows:
            return {"lines": [], "total": 0}

        candidate_rows = connection.execute(
            """SELECT tc.id,tc.dialogue_text_revision_id,tc.voice_profile_version_id,tc.media_version_id,
            tc.emotion,tc.speech_rate,tc.seed,tc.model_ref,tc.candidate_kind,tc.status,tc.created_at,
            dtr.dialogue_line_id,
            CASE WHEN dtr.id=(SELECT latest.id FROM dialogue_text_revisions latest
              WHERE latest.dialogue_line_id=dtr.dialogue_line_id ORDER BY latest.revision_no DESC LIMIT 1) THEN 0 ELSE 1 END AS is_stale,
            CASE WHEN tc.id=(SELECT dcs.tts_candidate_id FROM dialogue_candidate_selections dcs
              WHERE dcs.dialogue_line_id=dtr.dialogue_line_id ORDER BY dcs.created_at DESC,dcs.id DESC LIMIT 1) THEN 1 ELSE 0 END AS selected
            FROM tts_candidates tc
            JOIN dialogue_text_revisions dtr ON dtr.id=tc.dialogue_text_revision_id
            JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id
            WHERE dl.shot_id=?
            ORDER BY tc.created_at DESC,tc.id DESC""",
            (shot_id,),
        ).fetchall()
        candidates_by_line: dict[str, list[dict[str, Any]]] = {}
        for row in candidate_rows:
            item = dict(row)
            line_id = str(item.pop("dialogue_line_id"))
            item["is_stale"] = bool(item["is_stale"])
            item["selected"] = bool(item["selected"])
            candidates_by_line.setdefault(line_id, []).append(item)

        selection_rows = connection.execute(
            """SELECT dcs.id,dcs.dialogue_line_id,dcs.tts_candidate_id,dcs.source_text_revision_id,
            dcs.created_at,tc.media_version_id,
            CASE WHEN dcs.source_text_revision_id=(SELECT latest.id FROM dialogue_text_revisions latest
              WHERE latest.dialogue_line_id=dcs.dialogue_line_id ORDER BY latest.revision_no DESC LIMIT 1) THEN 0 ELSE 1 END AS is_stale
            FROM dialogue_candidate_selections dcs
            JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
            JOIN dialogue_lines dl ON dl.id=dcs.dialogue_line_id
            WHERE dl.shot_id=? AND dcs.id=(SELECT latest_selection.id FROM dialogue_candidate_selections latest_selection
              WHERE latest_selection.dialogue_line_id=dcs.dialogue_line_id ORDER BY latest_selection.created_at DESC,latest_selection.id DESC LIMIT 1)""",
            (shot_id,),
        ).fetchall()
        selections = {str(row["dialogue_line_id"]): {**dict(row), "is_stale": bool(row["is_stale"])} for row in selection_rows}
        for selection in selections.values():
            selection.pop("dialogue_line_id", None)

        voice_rows = connection.execute(
            """SELECT a.id AS character_asset_id,a.code AS character_code,a.name AS character_name,
            cvb.voice_profile_version_id,vpv.code AS voice_code,vpv.title AS voice_title,vpv.voice_ref,
            vpv.provider_profile_version_id
            FROM shot_asset_bindings sab
            JOIN story_assets a ON a.id=sab.asset_id AND a.kind='CHARACTER'
            JOIN character_voice_bindings cvb ON cvb.character_asset_id=a.id AND cvb.project_id=?
            JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id AND vpv.status='ACTIVE'
            WHERE sab.shot_id=?
            ORDER BY a.code,a.id""",
            (project_id, shot_id),
        ).fetchall()
        voices: dict[str, dict[str, Any]] = {}
        for row in voice_rows:
            item = dict(row)
            voices[str(item["character_code"]).casefold()] = item
            voices[str(item["character_name"]).casefold()] = item

        lines: list[dict[str, Any]] = []
        for row in line_rows:
            line_id = str(row["id"])
            lines.append(
                {
                    "id": line_id,
                    "code": row["code"],
                    "speaker": row["speaker"],
                    "revision": row["revision"],
                    "current_text": {
                        "id": row["text_revision_id"],
                        "revision_no": row["revision_no"],
                        "text": row["text"],
                        "pronunciation": _json(row["pronunciation_json"], {}),
                        "text_hash": row["text_hash"],
                        "created_at": row["text_created_at"],
                    },
                    "voice_binding": voices.get(str(row["speaker"]).casefold()),
                    "candidates": candidates_by_line.get(line_id, [])[:20],
                    "working_selection": selections.get(line_id),
                }
            )
        return {"lines": lines, "total": len(lines)}

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
            FROM story_asset_states WHERE id IN ({marks}) ORDER BY story_asset_id,code""",
            state_ids,
        ).fetchall()
        projected = []
        for row in states:
            item = dict(row)
            item["state"] = _json(item.pop("state_json"), {})
            projected.append(item)
        return assets, projected

    def _source_context(
        self,
        connection: sqlite3.Connection,
        episode_id: str,
        scene_id: str | None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        if not scene_id or not _table_exists(connection, "episode_scene_ranges"):
            return {"scene_id": scene_id, "source_range": None, "source_text": fields.get("source_text") or fields.get("source_passage")}
        row = connection.execute(
            """SELECT sc.id AS scene_id,sc.code AS scene_code,sc.title AS scene_title,sc.location,sc.time_of_day,sc.revision AS scene_revision,
            esr.ordinal,esr.source_start,esr.source_end,esr.source_label
            FROM scenes sc LEFT JOIN episode_scene_ranges esr ON esr.scene_id=sc.id AND esr.episode_id=?
            WHERE sc.id=?""",
            (episode_id, scene_id),
        ).fetchone()
        return {
            "scene_id": scene_id,
            "source_range": dict(row) if row else None,
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
            scene_label = " · ".join(str(value).strip() for value in (scene.get("scene_code"), scene.get("scene_title")) if value) or "当前场景"
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
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
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
                ORDER BY revision DESC,updated_at DESC,id DESC LIMIT 1""",
                (from_row["id"], to_row["id"]),
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
                "transition_id": constraint["id"],
                "boundary_revision": int(constraint["boundary_revision"]),
                "from_shot_id": from_row["id"],
                "from_shot_code": from_row["code"],
                "to_shot_id": to_row["id"],
                "to_shot_code": to_row["code"],
                "enforcement": constraint["enforcement"],
                "compatibility": constraint["compatibility_status"],
                "stale": bool(constraint["is_stale"]),
                "stale_reason": constraint["stale_reason"],
                "previous_end": previous_end,
                "current_start": current_start,
                "inheritance_recommended": inheritance_recommended,
                "inheritance_reason": inheritance_reason,
            }

        previous = boundary(by_delta.get(-1), by_delta.get(0))
        following = boundary(by_delta.get(0), by_delta.get(1))
        states = [item for item in (previous, following) if item]
        stale = any(
            item["stale"] or (item["previous_end"] and item["previous_end"]["stale"]) or (item["current_start"] and item["current_start"]["stale"])
            for item in states
        )
        statuses = {str(item["compatibility"]).upper() for item in states}
        compatibility = (
            "STALE"
            if stale
            else "CONFLICT"
            if statuses & {"BLOCKED", "CONFLICT", "INCOMPATIBLE"}
            else "ATTENTION"
            if statuses & {"WARNING", "ATTENTION"}
            else "OK"
            if states
            else "MISSING"
        )
        return {
            "previous": previous,
            "current_start": previous["current_start"] if previous else None,
            "current_end": following["previous_end"] if following else None,
            "next": following,
            "compatibility": compatibility,
            "stale": stale,
        }

    @staticmethod
    def _anchor(
        connection: sqlite3.Connection, anchor_id: str | None, constraint: sqlite3.Row, inherited_from: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not anchor_id:
            return None
        row = connection.execute(
            """SELECT fa.*,mv.rel_path FROM frame_anchors fa
            JOIN media_versions mv ON mv.id=fa.extracted_media_version_id WHERE fa.id=?""",
            (anchor_id,),
        ).fetchone()
        if row is None:
            return None
        inherited = bool(inherited_from and inherited_from["media_version_id"] == row["extracted_media_version_id"])
        source = (
            "INHERITED"
            if inherited
            else "GENERATED"
            if str(row["role_hint"] or "").upper() == "GENERATED"
            else "EXTRACTED"
            if row["extraction_method"]
            else "EXPLICIT"
        )
        conflict = str(constraint["compatibility_status"]).upper() in {"BLOCKED", "CONFLICT", "INCOMPATIBLE"}
        status = (
            "STALE"
            if row["is_stale"] or constraint["is_stale"]
            else "CONFLICT"
            if conflict
            else "LOCKED"
            if str(constraint["enforcement"]).upper() in {"HARD", "LOCKED"}
            else "AUTO_INHERITED"
            if inherited
            else "GENERATED"
            if source == "GENERATED"
            else "EXPLICIT"
        )
        return {
            "anchor_id": row["id"],
            "source_media_version_id": row["source_media_version_id"],
            "media_version_id": row["extracted_media_version_id"],
            "rel_path": row["rel_path"],
            "role_hint": row["role_hint"],
            "inherited_from_anchor_id": inherited_from["anchor_id"] if inherited and inherited_from else None,
            "source": source,
            "status": status,
            "stale": bool(row["is_stale"] or constraint["is_stale"]),
            "stale_reason": row["stale_reason"] or constraint["stale_reason"],
        }

    def _active_jobs(self, connection: sqlite3.Connection, shot_id: str, variant_ids: list[str]) -> list[dict[str, Any]]:
        ids = [shot_id, *variant_ids]
        marks = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT id,type,subject_type,subject_id,state,channel,priority,progress_json,progress_updated_at,started_at,
            last_error_code,last_error_detail_redacted,created_at,updated_at FROM jobs
            WHERE subject_id IN ({marks}) AND state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
            ORDER BY priority,created_at""",
            ids,
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
            FROM review_decisions WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC,id DESC""",
            (media_id,),
        ).fetchall()
        run = connection.execute(
            """SELECT id,policy_version,status,created_at,updated_at FROM machine_check_runs
            WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
            (media_id,),
        ).fetchone()
        results = (
            []
            if run is None
            else [
                {**dict(row), "details": _json(row["details_json"], {})}
                for row in connection.execute(
                    "SELECT item_id,result,details_json FROM machine_check_results WHERE run_id=? ORDER BY item_id", (run["id"],)
                ).fetchall()
            ]
        )
        return (
            {"subject_id": media_id, "count": len(reviews), "latest": dict(reviews[0]) if reviews else None},
            {"subject_id": media_id, "latest_run": dict(run) if run else None, "results": results},
        )

    @staticmethod
    def _preferences(connection: sqlite3.Connection, project_id: str, episode_id: str, shot_id: str) -> dict[str, Any]:
        if not _table_exists(connection, "generation_preference_sets"):
            return {"resolutions": [], "available": False}
        repository = SqliteGenerationPreferenceRepository(connection)
        query = GenerationPreferenceQueryService(repository)
        resolutions = []
        for capability in ("IMAGE_CHARACTER", "VIDEO_I2V", "VIDEO_FIRST_LAST_FRAME"):
            resolution = query.resolve(project_id=project_id, episode_id=episode_id, shot_id=shot_id, capability=capability)
            profile_version_id = resolution.get("profile_version_id")
            profile_fact = resolution.get("profile")
            if profile_version_id and isinstance(profile_fact, dict):
                stored_profile = repository.profile(str(profile_version_id))
                profile_fact["capability_contract"] = stored_profile.get("capability_contract", {}) if stored_profile else {}
            resolutions.append(resolution)
        return {"resolutions": resolutions, "available": True}

    @staticmethod
    def _blockers(
        connection: sqlite3.Connection,
        project_id: str,
        shot: sqlite3.Row,
        fields: dict[str, Any],
        frame_bridge: dict[str, Any],
        media: dict[str, Any],
        preferences: dict[str, Any],
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
                    f"{resolution['capability']}：{resolution['blocked_reason']}",
                    "GENERATION",
                )
        return blockers
