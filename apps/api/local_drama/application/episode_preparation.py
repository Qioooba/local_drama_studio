from __future__ import annotations

import json
from typing import Any

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_execution import resolve_breakdown_execution
from local_drama.application.local_llm import LocalLLMService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class EpisodePreparationService:
    """Prepare one episode from its approved source scope.

    This command is intentionally narrow: it reuses the existing durable
    breakdown job and apply authority.  It does not expose model/profile/source
    controls on the episode screen and it never expands the scope beyond the
    source range already stored on the episode outline.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
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
                """SELECT e.id,e.source_range_json,s.project_id,
                (SELECT COUNT(*) FROM shots sh WHERE sh.episode_id=e.id AND sh.archived_at IS NULL) AS shot_count
                FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            source = connection.execute(
                """SELECT i.id AS import_session_id
                FROM import_sessions i
                WHERE i.project_id=? AND i.status IN ('COMMITTED','BREAKDOWN_READY')
                AND EXISTS (SELECT 1 FROM audit_events ae
                  WHERE ae.action='IMPORT_SESSION_COMMITTED'
                  AND ae.subject_type='import_session' AND ae.subject_id=i.id)
                ORDER BY i.updated_at DESC,i.id DESC LIMIT 1""",
                (episode["project_id"],),
            ).fetchone()
            draft = connection.execute(
                """SELECT id FROM script_breakdown_drafts
                WHERE project_id=? AND status='DRAFT_READY'
                AND json_extract(confidence_json,'$.target_episode_id')=?
                ORDER BY updated_at DESC,id DESC LIMIT 1""",
                (episode["project_id"], episode_id),
            ).fetchone()
        return {
            **dict(episode),
            "import_session_id": str(source["import_session_id"]) if source else None,
            "ready_draft_id": str(draft["id"]) if draft else None,
        }

    def prepare(self, episode_id: str, *, idempotency_key: str) -> dict[str, Any]:
        context = self._context(episode_id)
        if int(context["shot_count"]) > 0:
            return {"status": "READY", "episode_id": episode_id, "shot_count": int(context["shot_count"])}

        if context["ready_draft_id"]:
            applied = BreakdownApplyService(self.database, self.settings).apply_draft(
                str(context["ready_draft_id"]), episode_id
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
        with self.database.connect() as connection:
            execution = resolve_breakdown_execution(connection, str(context["project_id"]), episode_id)
        job = LocalLLMService(self.database, self.settings).enqueue_breakdown(
            str(context["import_session_id"]),
            execution["profile_version_id"],
            idempotency_key,
            target_episode_id=episode_id,
            source_paragraph_start=start,
            source_paragraph_end=end,
            automatic_apply=True,
        )
        return {
            "status": "QUEUED",
            "episode_id": episode_id,
            "job_id": str(job["id"]),
            "shot_count": 0,
        }
