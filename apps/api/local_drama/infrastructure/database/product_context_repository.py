from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
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
                "SELECT id,code,title,status,revision FROM projects WHERE id=?", (project_id,),
            ).fetchone()
            if project_row is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            catalog_rows = connection.execute(
                """SELECT s.id AS season_id,s.code AS season_code,s.title AS season_title,s.number AS season_number,
                s.display_order AS season_display_order,e.id AS episode_id,e.code AS episode_code,e.title AS episode_title,
                e.number AS episode_number,e.display_order AS episode_display_order,e.production_status,e.target_duration_ms
                FROM seasons s LEFT JOIN episodes e ON e.season_id=s.id WHERE s.project_id=?
                ORDER BY s.display_order,s.number,s.id,e.display_order,e.number,e.id""",
                (project_id,),
            ).fetchall()
            counts = connection.execute(
                """WITH target(project_id,observed_at) AS (VALUES (?,?)) SELECT
                (SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=target.project_id) AS episode_count,
                (SELECT COUNT(*) FROM project_plan_bindings b JOIN production_plan_versions v ON v.id=b.production_plan_version_id WHERE b.project_id=target.project_id AND v.status='ACTIVE') AS production_plan_count,
                (SELECT COUNT(*) FROM project_profile_bindings b JOIN execution_profile_versions v ON v.id=b.execution_profile_version_id WHERE b.project_id=target.project_id AND b.status='ACTIVE' AND v.status='PUBLISHED') AS published_profile_binding_count,
                (SELECT COUNT(*) FROM script_breakdown_drafts WHERE project_id=target.project_id AND status IN ('DRAFT_READY','APPLIED')) AS reviewable_story_draft_count,
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
                season["episodes"].append({"id": row["episode_id"], "code": row["episode_code"], "title": row["episode_title"], "number": row["episode_number"], "display_order": row["episode_display_order"], "production_status": row["production_status"], "target_duration_ms": row["target_duration_ms"]})
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
