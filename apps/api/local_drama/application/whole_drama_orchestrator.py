"""Whole-drama automated orchestration engine.

Coordinates end-to-end multi-episode drama production:
1. Asset readiness and canonical reference verification.
2. Cross-episode shot plan auto-healing & production-readiness confirmation.
3. Automated multi-episode run dispatch (keyframes -> video -> TTS -> timeline assembly -> render -> delivery).
4. Whole-drama status tracking, pause/resume, and idempotency control.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.episode_shot_ready import EpisodeShotReadyService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

logger = logging.getLogger(__name__)


class WholeDramaOrchestratorService:
    def __init__(self, database: Database, settings: Any) -> None:
        self.database = database
        self.settings = settings
        self.run_service = EpisodeProductionRunService(database, settings)
        self.shot_ready_service = EpisodeShotReadyService(database)
        self.automation = AutomationWorkflowService(database)

    def _get_project_and_episodes(self, project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.database.connect() as conn:
            project = conn.execute(
                "SELECT id, code, title, root_rel FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})

            episodes = conn.execute(
                """SELECT e.id, e.code, e.title, e.production_status, e.narrative_status,
                          e.revision, s.id AS season_id
                FROM episodes e
                JOIN seasons s ON s.id = e.season_id
                WHERE s.project_id = ?
                ORDER BY s.display_order ASC, e.display_order ASC, e.code ASC""",
                (project_id,),
            ).fetchall()

        return dict(project), [dict(ep) for ep in episodes]

    def inspect(self, project_id: str) -> dict[str, Any]:
        """Inspect the current production state across all episodes of a drama."""
        project, episodes = self._get_project_and_episodes(project_id)

        episode_reports: list[dict[str, Any]] = []
        has_running = False
        all_completed = len(episodes) > 0

        with self.database.connect() as conn:
            for ep in episodes:
                ep_id = str(ep["id"])

                # Shot statistics
                shot_stats = conn.execute(
                    """SELECT
                        COUNT(*) as total_shots,
                        SUM(CASE WHEN status = 'READY' THEN 1 ELSE 0 END) as ready_shots,
                        SUM(CASE WHEN status IN ('GENERATING', 'REVIEW', 'APPROVED') THEN 1 ELSE 0 END) as advanced_shots,
                        SUM(CASE WHEN status IN ('DRAFT', 'DIRECTED') THEN 1 ELSE 0 END) as draft_shots
                    FROM shots
                    WHERE episode_id = ? AND archived_at IS NULL""",
                    (ep_id,),
                ).fetchone()

                # Working slots
                working_slots = conn.execute(
                    """SELECT
                        SUM(CASE WHEN slot_type = 'KEYFRAME' THEN 1 ELSE 0 END) as keyframes_count,
                        SUM(CASE WHEN slot_type = 'VIDEO' THEN 1 ELSE 0 END) as videos_count
                    FROM shot_working_media_slots sws
                    JOIN shots s ON s.id = sws.shot_id
                    WHERE s.episode_id = ? AND s.archived_at IS NULL""",
                    (ep_id,),
                ).fetchone()

                # Dialogue
                total_lines = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM dialogue_lines WHERE episode_id = ?",
                        (ep_id,),
                    ).fetchone()[0]
                )
                voiced_lines = int(
                    conn.execute(
                        """SELECT COUNT(DISTINCT dl.id) FROM dialogue_lines dl
                        JOIN dialogue_candidate_selections dcs ON dcs.dialogue_line_id = dl.id
                        WHERE dl.episode_id = ?""",
                        (ep_id,),
                    ).fetchone()[0]
                )

                # Timeline status
                timeline = conn.execute(
                    """SELECT id, revision_no, status FROM timeline_revisions
                    WHERE episode_id = ? ORDER BY revision_no DESC LIMIT 1""",
                    (ep_id,),
                ).fetchone()

                # Latest workflow run
                workflow_run = conn.execute(
                    """SELECT r.id, r.status
                    FROM automation_workflow_runs r
                    JOIN automation_workflows w ON w.id = r.workflow_id
                    WHERE r.project_id = ?
                    AND (json_extract(w.definition_json, '$.nodes[0].metadata.episode_id') = ?
                         OR w.code LIKE ?)
                    ORDER BY r.updated_at DESC, r.id DESC LIMIT 1""",
                    (project_id, ep_id, f"%{ep_id.replace('-', '')[:16]}%"),
                ).fetchone()

                total_shots = int(shot_stats["total_shots"] or 0) if shot_stats else 0
                ready_shots = int(shot_stats["ready_shots"] or 0) if shot_stats else 0
                draft_shots = int(shot_stats["draft_shots"] or 0) if shot_stats else 0
                keyframes_count = int(working_slots["keyframes_count"] or 0) if working_slots else 0
                videos_count = int(working_slots["videos_count"] or 0) if working_slots else 0
                run_status = str(workflow_run["status"]) if workflow_run else "NOT_STARTED"

                if run_status in ("RUNNING", "WAITING_ON_TASK"):
                    has_running = True
                if run_status != "SUCCEEDED":
                    all_completed = False

                ep_report = {
                    "episode_id": ep_id,
                    "code": ep["code"],
                    "title": ep["title"],
                    "production_status": ep["production_status"],
                    "total_shots": total_shots,
                    "ready_shots": ready_shots,
                    "draft_shots": draft_shots,
                    "keyframes_count": keyframes_count,
                    "videos_count": videos_count,
                    "dialogue_lines": total_lines,
                    "voiced_lines": voiced_lines,
                    "timeline_status": str(timeline["status"]) if timeline else "NONE",
                    "workflow_run_id": str(workflow_run["id"]) if workflow_run else None,
                    "workflow_run_status": run_status,
                }
                episode_reports.append(ep_report)

        summary_status = "COMPLETED" if all_completed else "RUNNING" if has_running else "READY"

        return {
            "project_id": project_id,
            "project_code": project["code"],
            "project_title": project["title"],
            "overall_status": summary_status,
            "total_episodes": len(episodes),
            "episodes": episode_reports,
        }

    def prepare_all_episodes(
        self,
        project_id: str,
        *,
        actor: str = "whole-drama-orchestrator",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Auto-heal and confirm production readiness across all episodes."""
        project, episodes = self._get_project_and_episodes(project_id)
        results: list[dict[str, Any]] = []

        for ep in episodes:
            ep_id = str(ep["id"])
            try:
                # Refresh episode revision
                with self.database.connect() as conn:
                    current_ep = conn.execute("SELECT revision FROM episodes WHERE id = ?", (ep_id,)).fetchone()
                    rev = int(current_ep["revision"]) if current_ep else int(ep["revision"])

                key = (
                    f"{idempotency_key}-ready-{ep_id}"
                    if idempotency_key
                    else f"whole-drama-ready-{ep_id}-rev{rev}"
                )
                confirm_res = self.shot_ready_service.confirm(
                    ep_id,
                    expected_episode_revision=rev,
                    idempotency_key=key,
                    actor=actor,
                    auto_heal=True,
                )
                results.append({
                    "episode_id": ep_id,
                    "code": ep["code"],
                    "status": "CONFIRMED",
                    "confirmed_shots": int(confirm_res.get("ready_shot_count") or len(confirm_res.get("ready_shot_ids") or [])),
                })
            except DomainRuleError as err:
                if err.code == "EPISODE_SHOTS_READY_NOTHING_TO_DO":
                    results.append({
                        "episode_id": ep_id,
                        "code": ep["code"],
                        "status": "ALREADY_READY",
                        "detail": "All shots are already confirmed and production-ready.",
                    })
                else:
                    logger.warning("Episode %s prepare failed: %s", ep["code"], err)
                    results.append({
                        "episode_id": ep_id,
                        "code": ep["code"],
                        "status": "BLOCKED",
                        "code_error": err.code,
                        "message": err.message,
                    })

        return {
            "project_id": project_id,
            "prepared_episodes": results,
            "success": all(r["status"] in ("CONFIRMED", "ALREADY_READY") for r in results),
        }

    def run(
        self,
        project_id: str,
        *,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024,
        actor: str = "whole-drama-orchestrator",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch end-to-end automated generation across all episodes."""
        # 1. First ensure all shots across all episodes are healed and confirmed ready
        prep = self.prepare_all_episodes(project_id, actor=actor, idempotency_key=idempotency_key)

        project, episodes = self._get_project_and_episodes(project_id)
        dispatched_runs: list[dict[str, Any]] = []

        for ep in episodes:
            ep_id = str(ep["id"])
            run_key = (
                f"{idempotency_key}-run-{ep_id}"
                if idempotency_key
                else f"whole-drama-run-{ep_id}-{uuid.uuid4().hex[:12]}"
            )
            try:
                run_view = self.run_service.start(
                    ep_id,
                    idempotency_key=run_key,
                    tts_enabled=tts_enabled,
                    production_mode=production_mode,
                    checkpoint_policy=checkpoint_policy,
                    min_free_disk_bytes=min_free_disk_bytes,
                    front_half_only=False,
                    actor=actor,
                )
                dispatched_runs.append({
                    "episode_id": ep_id,
                    "code": ep["code"],
                    "status": "DISPATCHED",
                    "run_id": run_view.get("id"),
                    "run_status": run_view.get("status"),
                })
            except DomainRuleError as err:
                logger.warning("Episode %s dispatch failed: %s", ep["code"], err)
                dispatched_runs.append({
                    "episode_id": ep_id,
                    "code": ep["code"],
                    "status": "BLOCKED",
                    "code_error": err.code,
                    "message": err.message,
                })

        return {
            "project_id": project_id,
            "preparation": prep,
            "dispatched_runs": dispatched_runs,
            "total_episodes": len(episodes),
            "dispatched_count": sum(1 for r in dispatched_runs if r["status"] == "DISPATCHED"),
        }
