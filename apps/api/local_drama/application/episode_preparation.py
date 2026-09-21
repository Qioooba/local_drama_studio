from __future__ import annotations

import json
from typing import Any

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_execution import resolve_breakdown_execution
from local_drama.application.episode_source_binding import (
    resolve_episode_source_binding,
    validate_episode_source_binding,
)
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


class EpisodePreparationService:
    """Prepare one episode from its approved source scope.

    This command is intentionally narrow: it reuses the existing durable
    breakdown job and apply authority.  It does not expose model/profile/source
    controls on the episode screen and it never expands the scope beyond the
    source range already stored on the episode outline.
    """

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _source_scope(raw: object) -> tuple[int, int]:
        try:
            payload = json.loads(str(raw or "{}"))
        except (TypeError, ValueError):
            payload = {}
        start = payload.get("start_paragraph") or payload.get("source_paragraph_start")
        end = payload.get("end_paragraph") or payload.get("source_paragraph_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise DomainRuleError(
                "EPISODE_SOURCE_RANGE_REQUIRED",
                "本集还没有已确认的原文范围，请先完成分集大纲。",
            )
        return start, end

    def _context(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = connection.execute(
                """SELECT e.id,e.source_range_json,e.target_duration_ms,s.project_id,
                (SELECT COUNT(*) FROM shots sh WHERE sh.episode_id=e.id AND sh.archived_at IS NULL) AS shot_count
                FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            source = resolve_episode_source_binding(connection, episode_id)
            validate_episode_source_binding(self.settings, source)
            try:
                execution = resolve_breakdown_execution(
                    connection, str(episode["project_id"]), episode_id
                )
            except DomainRuleError as error:
                if error.code != "EPISODE_BREAKDOWN_MODEL_REQUIRED":
                    raise
                execution = {
                    "profile_version_id": None,
                    "error_code": error.code,
                    "error_message": error.message,
                    "error_details": error.details,
                }
            draft = connection.execute(
                """SELECT id FROM script_breakdown_drafts
                WHERE project_id=? AND status='DRAFT_READY'
                AND json_extract(confidence_json,'$.target_episode_id')=?
                AND source_document_version_id=? AND import_session_id=?
                AND json_extract(confidence_json,'$.source_paragraph_start')=?
                AND json_extract(confidence_json,'$.source_paragraph_end')=?
                AND json_extract(confidence_json,'$.target_duration_seconds')=?
                AND json_extract(confidence_json,'$.profile_version_id')=?
                AND json_extract(confidence_json,'$.request_identity_sha256') IS NOT NULL
                ORDER BY updated_at DESC,id DESC LIMIT 1""",
                (
                    episode["project_id"],
                    episode_id,
                    source["source_document_version_id"],
                    source["import_session_id"],
                    source["start_paragraph"],
                    source["end_paragraph"],
                    int(episode["target_duration_ms"]) / 1000,
                    execution["profile_version_id"],
                ),
            ).fetchone()
            applied_plan_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM script_breakdown_scene_applications WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()[0]
            )
        return {
            **dict(episode),
            "source_binding": source,
            "import_session_id": source["import_session_id"],
            "ready_draft_id": str(draft["id"]) if draft else None,
            "execution": execution,
            "applied_plan_count": applied_plan_count,
        }

    def prepare(
        self,
        episode_id: str,
        *,
        idempotency_key: str,
        actor: str = "production-session-worker",
        application_authority: str = "MACHINE_TEMPORARY",
    ) -> dict[str, Any]:
        context = self._context(episode_id)
        # A single hand-authored shot is not evidence that the episode plan is
        # complete. Only a previously applied breakdown gives the preparation
        # command authority to treat the current shot set as an episode plan.
        if int(context["shot_count"]) > 0 and int(context["applied_plan_count"]) > 0:
            return {"status": "READY", "episode_id": episode_id, "shot_count": int(context["shot_count"])}

        if context["ready_draft_id"]:
            applied = BreakdownApplyService(self.database, self.settings).apply_draft(
                str(context["ready_draft_id"]),
                episode_id,
                actor=actor,
                application_authority=application_authority,
            )
            return {
                "status": "APPLIED",
                "episode_id": episode_id,
                "draft_id": str(context["ready_draft_id"]),
                "shot_count": int(applied["created"]["shots"]),
            }

        start, end = self._source_scope(context["source_range_json"])
        if not context["import_session_id"]:
            raise DomainRuleError("EPISODE_SOURCE_COMMIT_REQUIRED", "没有已确认提交的原文，无法生成本集方案。")
        execution = context["execution"]
        if not execution.get("profile_version_id"):
            raise DomainRuleError(
                str(execution.get("error_code") or "EPISODE_BREAKDOWN_MODEL_REQUIRED"),
                str(execution.get("error_message") or "当前项目没有可用的故事拆解模型。"),
                dict(execution.get("error_details") or {}),
            )
        job = LocalLLMService(self.database, self.settings).enqueue_breakdown(
            str(context["import_session_id"]),
            execution["profile_version_id"],
            idempotency_key,
            target_episode_id=episode_id,
            source_paragraph_start=start,
            source_paragraph_end=end,
            automatic_apply=True,
            actor=actor,
        )
        return {
            "status": "QUEUED",
            "episode_id": episode_id,
            "job_id": str(job["id"]),
            "job_ownership": "REUSED" if bool(job.get("idempotent_replay")) else "OWNED",
            "shot_count": 0,
        }
