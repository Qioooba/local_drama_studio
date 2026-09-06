from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.episode_production_repository import SqliteEpisodeProductionReadRepository
from local_drama.infrastructure.database.sqlite import Database


class SqliteProductContextReadRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def app_context(self, project_id: str | None, episode_id: str | None) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = None
            episode = None
            if project_id:
                row = connection.execute("SELECT id,code,title,status FROM projects WHERE id=?", (project_id,)).fetchone()
                if row is None:
                    raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
                project = dict(row)
            if episode_id:
                row = connection.execute(
                    """SELECT e.id,e.code,e.title,e.production_status,s.id AS season_id,s.code AS season_code,
                    s.title AS season_title,s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
                    (episode_id,),
                ).fetchone()
                if row is None:
                    raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
                if project_id and str(row["project_id"]) != project_id:
                    raise DomainRuleError("EPISODE_PROJECT_MISMATCH", "分集不属于当前项目", {"project_id": project_id, "episode_id": episode_id})
                episode = dict(row)
                if project is None:
                    project_row = connection.execute("SELECT id,code,title,status FROM projects WHERE id=?", (row["project_id"],)).fetchone()
                    project = dict(project_row) if project_row else None
            now = datetime.now(UTC).isoformat()
            active_workers = int(connection.execute(
                "SELECT COUNT(*) FROM worker_sessions WHERE status IN ('STARTING','RUNNING','BACKING_OFF','DRAINING') AND lease_expires_at>?",
                (now,),
            ).fetchone()[0])
            task_scope = project_id or (str(episode["project_id"]) if episode else None)
            task_count = int(connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE (? IS NULL OR project_id=?) AND state IN ('QUEUED','RUNNING','FAILED')",
                (task_scope, task_scope),
            ).fetchone()[0])
        return {"project": project, "episode": episode, "task_summary": {"active_worker_count": active_workers, "attention_job_count": task_count}}

    def project_overview_facts(self, project_id: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            project_row = connection.execute(
                "SELECT id,code,title,status,revision,target_duration_ms FROM projects WHERE id=?", (project_id,),
            ).fetchone()
            if project_row is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            catalog_rows = connection.execute(
                """WITH latest_verified_renders AS (
                    SELECT erv.id,erv.episode_id,
                    ROW_NUMBER() OVER (PARTITION BY erv.episode_id ORDER BY erv.created_at DESC,erv.id DESC) AS render_rank
                    FROM episode_render_versions erv
                    WHERE erv.integrity_status='VERIFIED'
                ), latest_render_reviews AS (
                    SELECT rd.subject_id,rd.decision,rd.is_stale,
                    ROW_NUMBER() OVER (PARTITION BY rd.subject_id ORDER BY rd.created_at DESC,rd.id DESC) AS review_rank
                    FROM review_decisions rd
                    WHERE rd.subject_type='EPISODE_RENDER_VERSION'
                ), latest_render_packages AS (
                    SELECT dp.episode_render_version_id,dp.status,dp.human_review_status,
                    ROW_NUMBER() OVER (PARTITION BY dp.episode_render_version_id ORDER BY dp.created_at DESC,dp.id DESC) AS package_rank
                    FROM delivery_packages dp
                )
                SELECT s.id AS season_id,s.code AS season_code,s.title AS season_title,s.number AS season_number,
                s.display_order AS season_display_order,e.id AS episode_id,e.code AS episode_code,e.title AS episode_title,
                e.number AS episode_number,e.display_order AS episode_display_order,e.production_status,e.target_duration_ms,
                latest_render.id AS preview_render_id,
                latest_review.decision AS preview_render_review_decision,
                COALESCE(latest_review.is_stale,0) AS preview_render_review_stale,
                latest_package.status AS preview_delivery_status,
                latest_package.human_review_status AS preview_delivery_human_review_status,
                (SELECT ws.media_version_id FROM shot_working_media_slots ws
                 JOIN shots preview_shot ON preview_shot.id=ws.shot_id
                 WHERE preview_shot.episode_id=e.id
                 ORDER BY preview_shot.order_key,CASE ws.slot_type WHEN 'VIDEO' THEN 0 ELSE 1 END,ws.updated_at DESC
                 LIMIT 1) AS preview_media_version_id
                FROM seasons s LEFT JOIN episodes e ON e.season_id=s.id
                LEFT JOIN latest_verified_renders latest_render ON latest_render.episode_id=e.id AND latest_render.render_rank=1
                LEFT JOIN latest_render_reviews latest_review ON latest_review.subject_id=latest_render.id AND latest_review.review_rank=1
                LEFT JOIN latest_render_packages latest_package ON latest_package.episode_render_version_id=latest_render.id AND latest_package.package_rank=1
                WHERE s.project_id=?
                ORDER BY s.display_order,s.number,s.id,e.display_order,e.number,e.id""",
                (project_id,),
            ).fetchall()
            counts = connection.execute(
                """WITH target(project_id,observed_at) AS (VALUES (?,?)),
                published_project_capabilities(capability) AS (
                    SELECT UPPER(binding.capability)
                    FROM project_profile_bindings binding
                    JOIN execution_profile_versions profile ON profile.id=binding.execution_profile_version_id
                    JOIN target ON target.project_id=binding.project_id
                    WHERE binding.status='ACTIVE'
                      AND profile.status='PUBLISHED'
                      AND UPPER(binding.capability)=UPPER(profile.capability)
                    UNION
                    SELECT UPPER(preference.capability)
                    FROM generation_preference_sets preference
                    JOIN generation_preference_versions preference_version ON preference_version.id=preference.current_version_id
                    JOIN target ON target.project_id=preference.project_id
                    LEFT JOIN execution_profile_versions explicit_profile ON explicit_profile.id=preference_version.execution_profile_version_id
                    WHERE preference.owner_type='PROJECT'
                      AND preference.owner_id=preference.project_id
                      AND preference.status='ACTIVE'
                      AND (
                        (preference_version.resolution_mode='EXPLICIT'
                         AND explicit_profile.status='PUBLISHED'
                         AND UPPER(explicit_profile.capability)=UPPER(preference.capability))
                        OR
                        (preference_version.resolution_mode='AUTO'
                         AND EXISTS (
                            SELECT 1 FROM execution_profile_versions automatic_profile
                            WHERE automatic_profile.status='PUBLISHED'
                              AND UPPER(automatic_profile.capability)=UPPER(preference.capability)
                         ))
                      )
                ) SELECT
                (SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=target.project_id) AS episode_count,
                (SELECT COUNT(*) FROM project_plan_bindings b JOIN production_plan_versions v ON v.id=b.production_plan_version_id WHERE b.project_id=target.project_id AND v.status='ACTIVE') AS production_plan_count,
                (SELECT COUNT(*) FROM published_project_capabilities) AS published_profile_binding_count,
                ((SELECT COUNT(*) FROM script_breakdown_drafts WHERE project_id=target.project_id AND status IN ('DRAFT_READY','APPLIED')) +
                 (SELECT COUNT(*) FROM pipeline_runs WHERE project_id=target.project_id AND state='SUCCEEDED')) AS reviewable_story_draft_count,
                (SELECT COUNT(*) FROM story_assets WHERE project_id=target.project_id AND status='ACTIVE') AS active_story_asset_count,
                (SELECT COUNT(*) FROM generation_intents gi JOIN shots sh ON gi.owner_type='SHOT' AND gi.owner_id=sh.id JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE gi.project_id=target.project_id AND s.project_id=target.project_id) AS shot_intent_count,
                (SELECT COUNT(*) FROM jobs j JOIN generation_variants gv ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id JOIN generation_intents gi ON gi.id=gv.intent_id AND gi.owner_type='SHOT' JOIN shots sh ON sh.id=gi.owner_id JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE j.project_id=target.project_id AND s.project_id=target.project_id) AS shot_generation_job_count
                FROM target""",
                (project_id, now),
            ).fetchone()
            activity_rows = connection.execute(
                "SELECT event_id,type,subject_type,subject_id,occurred_at FROM outbox_events WHERE project_id=? ORDER BY event_id DESC LIMIT 8",
                (project_id,),
            ).fetchall()
        seasons: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        for row in catalog_rows:
            season_id = str(row["season_id"])
            season = by_id.get(season_id)
            if season is None:
                season = {"id": season_id, "code": row["season_code"], "title": row["season_title"], "number": row["season_number"], "display_order": row["season_display_order"], "episodes": []}
                seasons.append(season)
                by_id[season_id] = season
            if row["episode_id"] is not None:
                season["episodes"].append({
                    "id": row["episode_id"],
                    "code": row["episode_code"],
                    "title": row["episode_title"],
                    "number": row["episode_number"],
                    "display_order": row["episode_display_order"],
                    "production_status": row["production_status"],
                    "target_duration_ms": row["target_duration_ms"],
                    "preview_render_id": row["preview_render_id"],
                    "preview_media_version_id": row["preview_media_version_id"],
                    "_render_review_decision": row["preview_render_review_decision"],
                    "_render_review_stale": bool(row["preview_render_review_stale"]),
                    "_delivery_status": row["preview_delivery_status"],
                    "_delivery_human_review_status": row["preview_delivery_human_review_status"],
                })
        production_reader = SqliteEpisodeProductionReadRepository(self.database)
        for season in seasons:
            for episode in season["episodes"]:
                # Only episodes with historical render/delivery evidence need
                # the additional current-production validity check.  This
                # keeps the project overview bounded while ensuring a stale
                # historical package cannot masquerade as current delivery.
                if not (episode.get("preview_render_id") or episode.get("_delivery_status")):
                    continue
                episode["_production_attention"] = self._episode_requires_update(
                    production_reader,
                    str(episode["id"]),
                )
        milestone_keys = (
            "episode_count", "production_plan_count", "published_profile_binding_count", "reviewable_story_draft_count",
            "active_story_asset_count", "shot_intent_count", "shot_generation_job_count",
        )
        return {
            "project": dict(project_row),
            "seasons": seasons,
            "milestones": {key: {"ready": int(counts[key]) > 0, "count": int(counts[key])} for key in milestone_keys},
            "recent_activity": [dict(row) for row in activity_rows],
        }

    def _episode_requires_update(
        self,
        production_reader: SqliteEpisodeProductionReadRepository,
        episode_id: str,
    ) -> bool:
        """Return whether current production facts invalidate old delivery.

        This delegates to the canonical episode-production projection so the
        home page observes the same dependency, timeline, and plan freshness
        rules as the production workspace.  On malformed/incomplete facts we
        fail closed: a historical delivery is never presented as current.
        """
        try:
            overview = production_reader.overview_facts(episode_id)
        except (DomainRuleError, sqlite3.DatabaseError):
            return True
        if bool(overview.get("replan_required")):
            return True
        if int(overview.get("shot_count") or 0) == 0:
            return True
        if int(overview.get("active_job_count") or 0) > 0:
            return True
        state_counts = overview.get("state_counts")
        if isinstance(state_counts, dict):
            attention_states = {"BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE", "RUNNING", "EMPTY"}
            if any(int(state_counts.get(state) or 0) > 0 for state in attention_states):
                return True
        else:
            # An incomplete projection is not enough evidence to keep an old
            # delivery green; fail closed when the canonical state summary is
            # absent or malformed.
            return True

        # A complete shot projection can still coexist with an old render or
        # delivery package.  Compare those immutable records with the latest
        # episode timeline so a new timeline revision (or a pending render)
        # cannot leave the historical delivery card green.
        try:
            with self.database.connect() as connection:
                latest_timeline = connection.execute(
                    "SELECT id,status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1",
                    (episode_id,),
                ).fetchone()
                latest_render = connection.execute(
                    "SELECT id,timeline_revision_id,integrity_status FROM episode_render_versions WHERE episode_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (episode_id,),
                ).fetchone()
                latest_delivery = connection.execute(
                    """SELECT dp.episode_render_version_id,dp.status
                    FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                    WHERE erv.episode_id=? ORDER BY dp.created_at DESC,dp.id DESC LIMIT 1""",
                    (episode_id,),
                ).fetchone()
        except sqlite3.DatabaseError:
            return True
        if latest_render is None or str(latest_render["integrity_status"]) != "VERIFIED":
            return True
        if latest_timeline is None or str(latest_timeline["status"]) != "FROZEN":
            return True
        if str(latest_render["timeline_revision_id"]) != str(latest_timeline["id"]):
            return True
        if latest_delivery is not None and (
            str(latest_delivery["status"]) != "VERIFIED"
            or str(latest_delivery["episode_render_version_id"]) != str(latest_render["id"])
        ):
            return True
        return False
