"""Whole-drama automated orchestration engine.

Coordinates end-to-end multi-episode drama production:
1. Asset readiness and canonical reference verification.
2. Cross-episode shot plan auto-healing & production-readiness confirmation.
3. Automated multi-episode run dispatch (keyframes -> video -> TTS -> timeline assembly -> render -> delivery).
4. Whole-drama status tracking, pause/resume, and idempotency control.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.episode_shot_ready import EpisodeShotReadyService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError

logger = logging.getLogger(__name__)
_RUN_LOCKS: dict[str, threading.Lock] = {}
_RUN_LOCKS_GUARD = threading.Lock()


def _run_lock(scope: str) -> threading.Lock:
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(scope, threading.Lock())


class WholeDramaOrchestratorService:
    def __init__(self, database: DatabaseUnitOfWork, settings: Any) -> None:
        self.database = database
        self.settings = settings
        self.run_service = EpisodeProductionRunService(database, settings)
        self.shot_ready_service = EpisodeShotReadyService(database)
        self.automation = AutomationWorkflowService(database)

    @staticmethod
    def _summarize_run_states(statuses: list[str]) -> tuple[str, dict[str, int]]:
        counts: dict[str, int] = {}
        for status in statuses:
            counts[status] = counts.get(status, 0) + 1
        total = len(statuses)
        if total == 0:
            return "EMPTY", counts
        if counts.get("NOT_STARTED", 0) == total:
            return "NOT_STARTED", counts
        if counts.get("SUCCEEDED", 0) == total:
            return "COMPLETED", counts
        if counts.get("CANCELLED", 0) == total:
            return "CANCELLED", counts
        if any(counts.get(status, 0) for status in ("RUNNING", "WAITING_ON_TASK")):
            return "RUNNING", counts
        if counts.get("PAUSED_HITL", 0):
            return "PAUSED_HITL", counts
        if any(counts.get(status, 0) for status in ("FAILED", "LIMIT_REACHED", "STOPPED")):
            return "FAILED", counts
        return "PARTIAL", counts

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

    @staticmethod
    def _select_episodes(
        episodes: list[dict[str, Any]], episode_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        if episode_ids is None:
            return episodes
        normalized = [episode_id.strip() for episode_id in episode_ids]
        if not normalized or any(not episode_id for episode_id in normalized):
            raise DomainRuleError("EPISODE_SCOPE_REQUIRED", "所选分集范围不能为空")
        if len(normalized) > 2:
            raise DomainRuleError("EPISODE_SCOPE_TOO_LARGE", "小样运行一次最多选择 2 集")
        if len(set(normalized)) != len(normalized):
            raise DomainRuleError("EPISODE_SCOPE_DUPLICATED", "所选分集不能重复")
        selected = [episode for episode in episodes if str(episode["id"]) in normalized]
        found = {str(episode["id"]) for episode in selected}
        missing = [episode_id for episode_id in normalized if episode_id not in found]
        if missing:
            raise DomainRuleError(
                "EPISODE_SCOPE_INVALID",
                "所选分集不属于当前项目",
                {"episode_ids": missing},
            )
        return selected

    def inspect(self, project_id: str) -> dict[str, Any]:
        """Inspect the current production state across all episodes of a drama."""
        project, episodes = self._get_project_and_episodes(project_id)

        episode_reports: list[dict[str, Any]] = []
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
                    AND (
                      EXISTS (
                        SELECT 1 FROM automation_workflow_run_tasks t
                        WHERE t.run_id=r.id
                        AND json_extract(t.item_json, '$.payload.episode_id')=?
                      )
                      OR EXISTS (
                        SELECT 1 FROM json_each(w.definition_json, '$.nodes') node
                        WHERE json_extract(node.value, '$.metadata.episode_id')=?
                      )
                    )
                    ORDER BY r.updated_at DESC, r.id DESC LIMIT 1""",
                    (project_id, ep_id, ep_id),
                ).fetchone()

                total_shots = int(shot_stats["total_shots"] or 0) if shot_stats else 0
                ready_shots = int(shot_stats["ready_shots"] or 0) if shot_stats else 0
                draft_shots = int(shot_stats["draft_shots"] or 0) if shot_stats else 0
                keyframes_count = int(working_slots["keyframes_count"] or 0) if working_slots else 0
                videos_count = int(working_slots["videos_count"] or 0) if working_slots else 0
                run_status = str(workflow_run["status"]) if workflow_run else "NOT_STARTED"

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

        summary_status, state_counts = self._summarize_run_states(
            [str(item["workflow_run_status"]) for item in episode_reports]
        )

        return {
            "project_id": project_id,
            "project_code": project["code"],
            "project_title": project["title"],
            "overall_status": summary_status,
            "state_counts": state_counts,
            "total_episodes": len(episodes),
            "episodes": episode_reports,
        }

    def prepare_all_episodes(
        self,
        project_id: str,
        *,
        episode_ids: list[str] | None = None,
        actor: str = "whole-drama-orchestrator",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Auto-heal and confirm production readiness across all episodes."""
        project, all_episodes = self._get_project_and_episodes(project_id)
        episodes = self._select_episodes(all_episodes, episode_ids)
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
        episode_ids: list[str] | None = None,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024,
        actor: str = "whole-drama-orchestrator",
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Dispatch one durable parent command across all episodes."""
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "整剧启动必须提供有效 Idempotency-Key")
        scope = f"whole-drama-run:{project_id}"
        payload = {
            "project_id": project_id,
            "episode_ids": episode_ids,
            "tts_enabled": tts_enabled,
            "production_mode": production_mode,
            "checkpoint_policy": checkpoint_policy,
            "min_free_disk_bytes": min_free_disk_bytes,
        }
        payload_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with _run_lock(scope):
            with self.database.connect() as connection:
                prior = connection.execute(
                    "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                    (scope, key),
                ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError(
                        "IDEMPOTENCY_PAYLOAD_MISMATCH",
                        "相同 Idempotency-Key 不能启动不同的整剧生产请求",
                    )
                replay = json.loads(str(prior["response_json"]))
                replay["idempotent_replay"] = True
                return replay
            result = self._run_once(
                project_id,
                episode_ids=episode_ids,
                tts_enabled=tts_enabled,
                production_mode=production_mode,
                checkpoint_policy=checkpoint_policy,
                min_free_disk_bytes=min_free_disk_bytes,
                actor=actor,
                idempotency_key=key,
            )
            stored = {**result, "idempotent_replay": False}
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                    (scope, key, payload_hash, json.dumps(stored, ensure_ascii=False, separators=(",", ":"))),
                )
            return stored

    def _run_once(
        self,
        project_id: str,
        *,
        episode_ids: list[str] | None,
        tts_enabled: bool,
        production_mode: str,
        checkpoint_policy: str,
        min_free_disk_bytes: int,
        actor: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # 1. First ensure all shots across all episodes are healed and confirmed ready
        prep = self.prepare_all_episodes(
            project_id,
            episode_ids=episode_ids,
            actor=actor,
            idempotency_key=idempotency_key,
        )

        project, all_episodes = self._get_project_and_episodes(project_id)
        episodes = self._select_episodes(all_episodes, episode_ids)
        dispatched_runs: list[dict[str, Any]] = []

        for ep in episodes:
            ep_id = str(ep["id"])
            run_key = f"{idempotency_key}:episode:{ep_id}"
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

        dispatched_count = sum(1 for result in dispatched_runs if result["status"] == "DISPATCHED")
        total_episodes = len(episodes)
        if total_episodes == 0 or dispatched_count == 0:
            dispatch_status = "NOT_STARTED"
            dispatch_reason = "NO_EPISODES" if total_episodes == 0 else "ALL_EPISODES_BLOCKED"
        elif dispatched_count == total_episodes:
            dispatch_status = "DISPATCHED"
            dispatch_reason = None
        else:
            dispatch_status = "PARTIALLY_DISPATCHED"
            dispatch_reason = "SOME_EPISODES_BLOCKED"
        return {
            "project_id": project_id,
            "preparation": prep,
            "dispatched_runs": dispatched_runs,
            "total_episodes": total_episodes,
            "dispatched_count": dispatched_count,
            "blocked_count": total_episodes - dispatched_count,
            "dispatch_status": dispatch_status,
            "dispatch_reason": dispatch_reason,
        }
