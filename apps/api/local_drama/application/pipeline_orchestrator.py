"""Staged story planning pipeline for the one-click creator flow."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from local_drama.application.ports.creative_generation import (
    DocumentImportPort,
    StoryAIGenerationPort,
    StoryPipelineJobPort,
)
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.source_text import source_chapters, source_paragraphs
from local_drama.infrastructure.filesystem.path_policy import controlled_path

logger = logging.getLogger(__name__)

PIPELINE_JOB_TYPE = "STORY_PIPELINE_DRAFT"
PIPELINE_APPLY_JOB_TYPE = "STORY_PIPELINE_APPLY"
PIPELINE_EPISODE_BATCH_LIMIT = 60
PIPELINE_EPISODE_SOURCE_CHARACTER_LIMIT = 24_000
PIPELINE_SECTIONS = {"STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS", "SCRIPT_BREAKDOWN"}
ALLOWED_VISUAL_STYLES = {
    "国风仙侠 电影级写实 (Cinematic Realistic)",
    "现代都市 悬疑写实 (Urban Suspense)",
    "玄幻奇幻 动漫风格 (Anime Fantasy)",
    "复古港风 胶片质感 (Vintage Film)",
    "科幻赛博 霓虹写实 (Cyberpunk Sci-Fi)",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_col(row: Any, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


def _creative_asset_code(kind: str, name: str) -> str:
    """Stable ASCII code for an AI-authored text dossier."""
    return f"AI_{kind}_{_sha(name.casefold())[:12].upper()}"


_LONG_UNIT_SLICE_CHARACTERS = 3_500
_LONG_UNIT_SLICE_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";")


def _bounded_character_slices(text: str, limit: int) -> list[tuple[int, int]]:
    """Split ``text`` into ordered ``(start, end)`` spans of at most ``limit`` characters.

    Every character of ``text`` lands in exactly one span, so the concatenation of
    the spans is the original text. Splitting prefers a separator close to the
    limit so that a natural sentence boundary survives, which keeps single-line
    chapters from being flattened into one unwieldy window.
    """
    spans: list[tuple[int, int]] = []
    position = 0
    total = len(text)
    floor = max(1, int(limit * 0.6))
    while position < total:
        end = min(total, position + limit)
        if end < total:
            window = text[position:end]
            cut = -1
            for separator in _LONG_UNIT_SLICE_SEPARATORS:
                index = window.rfind(separator)
                if index >= 0:
                    cut = max(cut, index + len(separator))
            if cut >= floor:
                end = position + cut
        spans.append((position, end))
        position = end
    return spans


def _paragraph_windows(
    group: list[Any], *, limit: int = PIPELINE_EPISODE_SOURCE_CHARACTER_LIMIT
) -> list[dict[str, Any]]:
    """Return bounded windows over ``group`` preserving the body text exactly.

    The window text never exceeds ``limit`` characters, so the planner no longer
    silently cuts a long chapter at the prompt boundary. Each window carries the
    1-based paragraph span that produced its text, and concatenating the windows in
    order reproduces the original authorised body character for character, which is
    what lets the coverage report prove that nothing was skipped.
    """
    # An explicit blank-line sentinel owns the separator that a paragraph break
    # contributes. Keeping it as its own piece means a window boundary may fall
    # between the sentinel and the paragraph (or between paragraphs) without the
    # separator being dropped or duplicated.
    blank = object()
    pieces: list[tuple[Any, str]] = []
    for paragraph_index, paragraph in enumerate(group):
        if paragraph_index:
            pieces.append((blank, "\n\n"))
        text = str(paragraph.text)
        if len(text) <= limit:
            pieces.append((paragraph, text))
            continue
        for start, end in _bounded_character_slices(text, limit):
            pieces.append((paragraph, text[start:end]))
    windows: list[dict[str, Any]] = []
    current: list[tuple[Any, str]] = []
    current_paragraphs: list[Any] = []
    current_characters = 0

    def flush() -> None:
        nonlocal current, current_paragraphs, current_characters
        if not current or not current_paragraphs:
            current = []
            current_paragraphs = []
            current_characters = 0
            return
        windows.append(
            {
                "text": "".join(text for _, text in current),
                "start_paragraph": int(current_paragraphs[0].number),
                "end_paragraph": int(current_paragraphs[-1].number),
                "character_count": current_characters,
            }
        )
        current = []
        current_paragraphs = []
        current_characters = 0

    for owner, text in pieces:
        # A window must always accept at least one piece; an over-long paragraph was
        # already bounded above, so a single piece can never exceed the limit.
        if current and current_characters + len(text) > limit:
            flush()
        current.append((owner, text))
        current_characters += len(text)
        if owner is not blank:
            current_paragraphs.append(owner)
    flush()
    return windows


def _pipeline_quality_report(draft: dict[str, Any]) -> dict[str, Any]:
    source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
    coverage = draft.get("source_coverage") if isinstance(draft.get("source_coverage"), dict) else {}
    assets = draft.get("assets") if isinstance(draft.get("assets"), dict) else {}
    story_plan = draft.get("story_plan") if isinstance(draft.get("story_plan"), dict) else {}
    generation = draft.get("generation") if isinstance(draft.get("generation"), dict) else {}
    episodes = story_plan.get("episodes") if isinstance(story_plan.get("episodes"), list) else []
    # PR-01: a plan that repeats an episode number cannot be applied, so the draft
    # must be refused HERE rather than failing later on a SQLite unique constraint.
    # Preview and apply share this report, so both refuse it.
    numbers = [int(item.get("number") or 0) for item in episodes if isinstance(item, dict)]
    codes = [str(item.get("code") or "") for item in episodes if isinstance(item, dict)]
    unique_numbers = len(numbers) == len(set(numbers)) and 0 not in numbers
    unique_codes = len(codes) == len(set(codes)) and "" not in codes
    # Every input window that was actually processed must be represented by a planned
    # episode.  A window whose unit is missing means the aggregation dropped its
    # source range.
    planned = {number for number in numbers}
    processed_units = {
        int(item["unit_number"])
        for item in coverage.get("completed_ranges", [])
        if isinstance(item, dict) and item.get("unit_number") is not None
    }
    windows_mapped = processed_units <= planned if processed_units else True
    rules = [
        ("SOURCE_FROZEN", "原稿快照已冻结", "BLOCKER", bool(source.get("sha256"))),
        ("SOURCE_COVERAGE_COMPLETE", "授权原稿范围已完整处理", "WARNING", coverage.get("status") == "FULL"),
        ("AI_GENERATION_CONFIRMED", "分集与核心资产由已配置大模型生成", "BLOCKER", bool(generation.get("model") and generation.get("provider"))),
        ("EPISODES_PRESENT", "已生成分集规划和原文范围", "BLOCKER", bool(episodes)),
        ("EPISODE_NUMBERS_UNIQUE", "分集编号与代码唯一；输入窗口已按单元聚合", "BLOCKER", unique_numbers and unique_codes),
        ("WINDOWS_MAPPED_TO_PLAN", "已完成的分析窗口都能映射到已规划分集", "BLOCKER", windows_mapped),
        ("CORE_CHARACTERS", "已识别可复用核心人物", "BLOCKER", bool(assets.get("characters"))),
        ("CORE_SCENES", "已识别可复用核心场景", "WARNING", bool(assets.get("scenes"))),
        ("EPISODE_DETAILS_DEFERRED", "分场与镜头将在制作每集时按需生成", "INFO", True),
        ("TEXT_ONLY", "未启动图片、视频或媒体生成", "BLOCKER", generation.get("media_generation_started") is False),
        ("NO_PRODUCTION_WRITES", "生成阶段未写入正式镜头或资产", "BLOCKER", True),
    ]
    checks = [
        {
            "code": code,
            "label": label,
            "severity": severity,
            "applicable": True,
            "passed": passed,
        }
        for code, label, severity, passed in rules
    ]
    blockers = [item[1] for item in rules if item[2] == "BLOCKER" and not item[3]]
    warnings = [item[1] for item in rules if item[2] == "WARNING" and not item[3]]
    return {
        "rule_version": "pipeline-quality/v2",
        "status": "BLOCKED" if blockers else "REVIEW_REQUIRED" if warnings else "READY",
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }


class PipelineOrchestratorService:
    """Generate isolated story drafts and materialize them only after review."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        jobs: StoryPipelineJobPort,
        documents: DocumentImportPort,
        ai_generation: StoryAIGenerationPort,
    ) -> None:
        self.database = database
        self.settings = settings
        self.jobs = jobs
        self.documents = documents
        self.ai_generation = ai_generation

    def _row_to_run(self, row: Any) -> dict[str, Any]:
        draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
        assets = _parse_json(row["assets_json"], {"characters": [], "scenes": [], "props": []})
        input_snapshot = _parse_json(_safe_col(row, "input_snapshot_json", "{}"), {})
        authorization = input_snapshot.get("application_authorization")
        if not isinstance(authorization, dict):
            authorization = {"endpoint": "DRAFT_ONLY", "sections": []}
        production_authorization = input_snapshot.get("production_authorization")
        if not isinstance(production_authorization, dict):
            production_authorization = {"endpoint": "STRUCTURE_ONLY"}
        analysis_cursor = input_snapshot.get("analysis_cursor")
        analysis_cursor = analysis_cursor if isinstance(analysis_cursor, dict) else {}
        next_window = int(analysis_cursor.get("next_window_index") or 0)
        total_windows = int(analysis_cursor.get("total_window_count") or 0)
        return {
            "run_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "job_id": str(_safe_col(row, "job_id")) if _safe_col(row, "job_id") else None,
            "state": str(row["state"]),
            "stage": str(row["stage"]),
            "stage_label": str(row["stage_label"]),
            "progress_pct": int(row["progress_pct"]),
            "revision": int(row["revision"]),
            "visual_style": str(row["visual_style"]),
            "target_episode_duration_seconds": int(row["target_episode_duration_seconds"]),
            "voice_preset": str(row["voice_preset"]),
            "source_document_version_id": str(row["source_document_version_id"]) if row["source_document_version_id"] else None,
            "source_label": str(_safe_col(row, "source_label", "") or ""),
            "capability_profile_version_id": str(row["capability_profile_version_id"]) if row["capability_profile_version_id"] else None,
            "episodes_count": int(row["episodes_count"]),
            "characters_count": int(row["characters_count"]),
            "scenes_count": int(row["scenes_count"]),
            "props_count": int(row["props_count"]),
            "shots_count": int(row["shots_count"]),
            "episodes": _parse_json(row["episodes_json"], []),
            "assets": assets,
            "draft": draft,
            "quality_report": _parse_json(_safe_col(row, "quality_report_json", "{}"), {}),
            "apply_state": str(_safe_col(row, "apply_state", "NOT_APPLIED") or "NOT_APPLIED"),
            # PR-05: the applied WATERMARK, so a newer draft revision still has a
            # computable diff instead of being refused as "already applied".
            "applied_revision_hash": str(_safe_col(row, "applied_revision_hash") or "") or None,
            "applied_episode_numbers": _parse_json(
                _safe_col(row, "applied_episode_numbers_json", "[]"), []
            ),
            "applied_sections": _parse_json(_safe_col(row, "applied_sections_json", "[]"), []),
            "applied_at": str(_safe_col(row, "applied_at")) if _safe_col(row, "applied_at") else None,
            "supersedes_run_id": str(_safe_col(row, "supersedes_run_id")) if _safe_col(row, "supersedes_run_id") else None,
            "extraction_mode": str(_safe_col(row, "extraction_mode", "UNKNOWN") or "UNKNOWN"),
            "llm_model": str(_safe_col(row, "llm_model")) if _safe_col(row, "llm_model") else None,
            "llm_provider": str(_safe_col(row, "llm_provider")) if _safe_col(row, "llm_provider") else None,
            "llm_error": str(_safe_col(row, "llm_error")) if _safe_col(row, "llm_error") else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "error_message": str(row["error_message"]) if row["error_message"] else None,
            "auto_run_rendering": False,
            "analysis_cursor": {
                "schema_version": "pipeline.analysis-cursor.v1",
                "completed_window_count": next_window,
                "next_window_index": next_window,
                "total_window_count": total_windows,
                "has_more_windows": bool(total_windows) and next_window < total_windows,
            },
            "application_authorization": authorization,
            "production_authorization": production_authorization,
        }

    def _effective_applied_revision_hash(self, row: Any) -> str:
        """The applied revision hash, backfilled for runs applied before the watermark.

        The watermark migration cannot compute a content hash in SQL, so a run that was
        applied by an earlier build has ``apply_state='APPLIED'`` and no hash.  Treating
        that as "nothing applied" would let a re-apply rewrite episodes the user already
        confirmed, so the current draft is adopted as the applied revision — the old
        boolean semantics, preserved for exactly as long as no new draft revision exists.

        This is read-only: the persisted backfill is a separate call so a caller that
        already owns a write transaction can never nest a second one.
        """

        stored = str(_safe_col(row, "applied_revision_hash") or "")
        if stored:
            return stored
        if str(_safe_col(row, "apply_state", "NOT_APPLIED")) != "APPLIED":
            return ""
        return self._draft_revision_hash(self._row_draft(row))

    @staticmethod
    def _row_draft(row: Any) -> dict[str, Any]:
        draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
        return draft if isinstance(draft, dict) else {}

    def _backfill_applied_watermark(self, row: Any) -> None:
        """Persist the adopted watermark for a pre-migration APPLIED run."""

        if str(_safe_col(row, "applied_revision_hash") or ""):
            return
        if str(_safe_col(row, "apply_state", "NOT_APPLIED")) != "APPLIED":
            return
        draft = self._row_draft(row)
        draft_hash = self._draft_revision_hash(draft)
        if not draft_hash:
            return
        numbers = {
            int(item["number"])
            for item in ((draft.get("story_plan") or {}).get("episodes") or [])
            if isinstance(item, dict) and item.get("number")
        }
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET applied_revision_hash=?,applied_episode_numbers_json=?
                WHERE id=? AND (applied_revision_hash IS NULL OR applied_revision_hash='')""",
                (draft_hash, _json(sorted(numbers)), str(row["id"])),
            )

    def _attach_apply_continuation(self, run: dict[str, Any]) -> dict[str, Any]:
        job_id = str(run["application_authorization"].get("continuation_job_id") or "")
        if not job_id:
            run["apply_continuation"] = {"state": "NOT_AUTHORIZED", "job_id": None, "last_error_code": None}
            return run
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT state,last_error_code,last_error_detail_redacted FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        run["apply_continuation"] = {
            "state": str(row["state"]) if row else "MISSING",
            "job_id": job_id,
            "last_error_code": str(row["last_error_code"]) if row and row["last_error_code"] else None,
            "last_error_detail": str(row["last_error_detail_redacted"]) if row and row["last_error_detail_redacted"] else None,
        }
        return run

    @staticmethod
    def _production_session_idempotency_key(run_id: str) -> str:
        return f"pipeline:{run_id}:production-session"

    def _attach_production_continuation(self, run: dict[str, Any]) -> dict[str, Any]:
        authorization = run.get("production_authorization") or {}
        if authorization.get("endpoint") != "WAITING_REVIEW":
            run["production_continuation"] = {
                "state": "NOT_AUTHORIZED",
                "session_id": None,
                "session_status": None,
            }
            return run
        scope = f"production-session:create:{run['project_id']}"
        key = self._production_session_idempotency_key(str(run["run_id"]))
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
        if row is None:
            run["production_continuation"] = {
                "state": "PENDING" if run.get("apply_state") != "APPLIED" else "NOT_STARTED",
                "session_id": None,
                "session_status": None,
            }
            return run
        response = _parse_json(row["response_json"], {})
        session_id = str((response.get("session") or {}).get("id") or "")
        if not session_id:
            run["production_continuation"] = {
                "state": "INVALID",
                "session_id": None,
                "session_status": None,
            }
            return run
        from local_drama.application.production_sessions import ProductionSessionService

        session = ProductionSessionService(self.database).get(session_id)
        run["production_continuation"] = {
            "state": "STARTED" if session["status"] != "READY" else "READY",
            "session_id": session_id,
            "session_status": session["status"],
            "current_stage": session["current_stage"],
        }
        return run

    def _continue_authorized_production(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_pipeline_by_id(run_id)
        authorization = run.get("production_authorization") or {}
        if authorization.get("endpoint") != "WAITING_REVIEW":
            return None
        if run["apply_state"] != "APPLIED":
            raise DomainRuleError(
                "PIPELINE_PRODUCTION_NOT_READY",
                "故事结构尚未应用，不能创建整部生产会话",
            )
        from local_drama.application.production_session_runner import ProductionSessionRunner
        from local_drama.application.production_sessions import ProductionSessionService

        command = {
            "scope_type": "WHOLE_DRAMA",
            "episode_ids": [],
            "production_mode": str(authorization.get("production_mode") or "BALANCED"),
            "checkpoint_policy": str(authorization.get("checkpoint_policy") or "ON_EXCEPTION"),
            "tts_enabled": bool(authorization.get("tts_enabled", True)),
            "max_parallel_episodes": int(authorization.get("max_parallel_episodes") or 1),
            "min_free_disk_bytes": int(authorization.get("min_free_disk_bytes") or 1),
            "max_duration_seconds": int(authorization.get("max_duration_seconds") or 24 * 60 * 60),
            "max_new_jobs": int(authorization.get("max_new_jobs") or 600),
            "max_attempts_total": int(authorization.get("max_attempts_total") or 1_200),
            "max_output_bytes": int(authorization.get("max_output_bytes") or 100 * 1024 * 1024 * 1024),
            "max_queued_gpu_jobs": int(authorization.get("max_queued_gpu_jobs") or 8),
            "dispatch_shots_per_tick": int(authorization.get("dispatch_shots_per_tick") or 4),
            "actor": "pipeline-authorized-continuation",
        }
        sessions = ProductionSessionService(self.database)
        plan = sessions.plan(str(run["project_id"]), command)
        created = sessions.create(
            str(run["project_id"]),
            {**command, "expected_plan_hash": plan["plan_hash"]},
            idempotency_key=self._production_session_idempotency_key(run_id),
        )
        session = created["session"]
        started = ProductionSessionRunner(self.database, self.settings).start(
            str(session["id"]),
            {
                "expected_revision": int(session["revision"]),
                "actor": "pipeline-authorized-continuation",
            },
            idempotency_key=f"pipeline:{run_id}:production-session:start",
        )
        return started

    def get_pipeline_by_id(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
        return self._attach_production_continuation(
            self._attach_apply_continuation(self._row_to_run(row))
        )

    @staticmethod
    def _row_to_summary(row: Any) -> dict[str, Any]:
        """Return the history-card contract without loading large draft JSON blobs."""
        return {
            "run_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "state": str(row["state"]),
            "stage": str(row["stage"]),
            "stage_label": str(row["stage_label"]),
            "progress_pct": int(row["progress_pct"]),
            "revision": int(row["revision"]),
            "source_label": str(_safe_col(row, "source_label", "") or ""),
            "episodes_count": int(row["episodes_count"]),
            "characters_count": int(row["characters_count"]),
            "scenes_count": int(row["scenes_count"]),
            "props_count": int(row["props_count"]),
            "shots_count": int(row["shots_count"]),
            "apply_state": str(_safe_col(row, "apply_state", "NOT_APPLIED") or "NOT_APPLIED"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "error_message": str(row["error_message"]) if row["error_message"] else None,
        }

    def _project(self, project_id: str) -> Any:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return row

    def _validate_input(
        self,
        source_document_version_id: str | None,
        raw_text: str | None,
        target_episode_duration_seconds: int,
    ) -> tuple[str | None, str]:
        source_id = (source_document_version_id or "").strip() or None
        text = (raw_text or "").strip()
        if not source_id and not text:
            raise DomainRuleError("PIPELINE_SOURCE_REQUIRED", "请选择已有原稿、上传文件或粘贴正文")
        if source_id and text:
            raise DomainRuleError("PIPELINE_SOURCE_CONFLICT", "一次只能使用一个原稿来源")
        if text and len(text) < 20:
            raise DomainRuleError("PIPELINE_SOURCE_TOO_SHORT", "正文至少需要 20 个字符", {"length": len(text)})
        if text and len(text) > 500_000:
            raise DomainRuleError("PIPELINE_SOURCE_TOO_LARGE", "正文超过 500,000 字符，请拆分后导入")
        if not 30 <= target_episode_duration_seconds <= 600:
            raise DomainRuleError("PIPELINE_DURATION_INVALID", "单集目标时长必须在 30—600 秒之间")
        return source_id, text

    def _read_source_version(self, project_id: str, source_version_id: str) -> tuple[str, str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT sdv.extracted_text_rel,sdv.source_name,sdv.text_sha256,p.root_rel
                FROM source_document_versions sdv
                JOIN source_documents sd ON sd.id=sdv.source_document_id
                JOIN projects p ON p.id=sd.project_id
                WHERE sdv.id=? AND sd.project_id=?""",
                (source_version_id, project_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_SOURCE_NOT_FOUND", "所选原稿版本不存在或不属于当前项目")
        project_root = self.settings.resolve_project_root(str(row["root_rel"]))
        path = controlled_path(project_root, str(row["extracted_text_rel"]), must_exist=True, code="EXTRACTED_TEXT_MISSING")
        text = path.read_text(encoding="utf-8")
        if row["text_sha256"] and _sha(text) != str(row["text_sha256"]):
            raise DomainRuleError("PIPELINE_SOURCE_CHANGED", "原稿解析文本已变化，请重新导入")
        return text, str(row["source_name"] or "已有原稿")

    def _persist_pasted_source(self, project_id: str, text: str, actor: str) -> tuple[str, str]:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="story-pipeline-", encoding="utf-8", delete=False
            ) as handle:
                handle.write(text)
                temporary_path = Path(handle.name)
            imported = self.documents.import_document(project_id, temporary_path, actor=actor)
            return str(imported["source_document_version_id"]), "粘贴正文"
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _ensure_source_committed(self, project_id: str, source_version_id: str, actor: str) -> str:
        """Treat the one-click launch as confirmation of the complete source."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT i.id,i.status
                FROM import_sessions i
                WHERE i.project_id=? AND i.source_document_version_id=?
                ORDER BY i.updated_at DESC,i.id DESC LIMIT 1""",
                (project_id, source_version_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_IMPORT_SESSION_MISSING", "原稿缺少可提交的导入记录，请重新上传")
        session_id = str(row["id"])
        if str(row["status"]) == "COMMITTED":
            return session_id
        if str(row["status"]) != "PREVIEW_READY":
            raise DomainRuleError("PIPELINE_IMPORT_NOT_READY", "原稿解析尚未就绪，请稍后重试")
        session = self.documents.get_session(session_id)
        self.documents.commit(session_id, str(session["preview_hash"]), actor=actor)
        return session_id

    def _authorized_source_range(
        self, project_id: str, source_version_id: str, paragraph_count: int
    ) -> dict[str, Any]:
        """Read the creator-confirmed body range; unknown legacy scope is never widened."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT i.id,item.payload_json FROM import_sessions i
                LEFT JOIN import_session_items item ON item.session_id=i.id
                  AND item.item_type='SOURCE_BODY_RANGE' AND item.validation_status='VALID'
                WHERE i.project_id=? AND i.source_document_version_id=?
                  AND EXISTS (SELECT 1 FROM audit_events ae
                    WHERE ae.action='IMPORT_SESSION_COMMITTED'
                    AND ae.subject_type='import_session' AND ae.subject_id=i.id)
                ORDER BY i.updated_at DESC,item.created_at DESC,item.id DESC LIMIT 1""",
                (project_id, source_version_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError(
                "PIPELINE_SOURCE_SCOPE_REQUIRED", "原稿尚无已确认的授权正文范围。"
            )
        payload = _parse_json(row["payload_json"], {})
        start = payload.get("source_paragraph_start") if isinstance(payload, dict) else None
        end = payload.get("source_paragraph_end") if isinstance(payload, dict) else None
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise DomainRuleError(
                "PIPELINE_SOURCE_SCOPE_REQUIRED", "已提交原稿缺少可验证的授权正文范围。"
            )
        if end > paragraph_count:
            raise DomainRuleError("PIPELINE_SOURCE_SCOPE_INVALID", "授权正文范围超出当前原稿。")
        return {
            "import_session_id": str(row["id"]),
            "start_paragraph": start,
            "end_paragraph": end,
            "paragraph_count": end - start + 1,
        }

    def preflight(
        self,
        project_id: str,
        *,
        source_document_version_id: str | None = None,
        raw_text: str | None = None,
        target_episode_duration_seconds: int = 120,
        capability_profile_version_id: str | None = None,
    ) -> dict[str, Any]:
        self._project(project_id)
        source_id, text = self._validate_input(source_document_version_id, raw_text, target_episode_duration_seconds)
        label = "粘贴正文"
        if source_id:
            text, label = self._read_source_version(project_id, source_id)
        paragraphs = source_paragraphs(text)
        chapters = source_chapters(paragraphs)
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT
                (SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?) episodes,
                (SELECT COUNT(*) FROM creative_entries WHERE project_id=? AND kind='SERIES_BIBLE') bibles,
                (SELECT COUNT(*) FROM story_assets WHERE project_id=? AND status='ACTIVE') assets,
                (SELECT COUNT(*) FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?) shots""",
                (project_id, project_id, project_id, project_id),
            ).fetchone()
        warnings: list[str] = []
        if not chapters:
            warnings.append("未识别出明确章节标题，将按正文长度自动分集。")
        if int(existing["episodes"] or 0):
            warnings.append("项目已有分集；应用草案时会保留已进入镜头制作的分集。")
        if int(existing["bibles"] or 0):
            warnings.append("项目已有故事圣经；应用后会新增一个可追溯版本，不覆盖历史版本。")
        ai = self.ai_generation.readiness(capability_profile_version_id)
        if not ai["ready"]:
            warnings.append(str(ai.get("message") or "完整建档需要先配置故事解析大模型。"))
        return {
            "safe_mode": True,
            "ai": ai,
            "source": {
                "label": label,
                "character_count": len(text),
                "paragraph_count": len(paragraphs),
                "chapter_count": len(chapters),
                "sha256": _sha(text),
            },
            "existing": {key: int(existing[key] or 0) for key in ("episodes", "bibles", "assets", "shots")},
            "estimated_episode_count": max(1, min(60, len(chapters) or round(len(text) / 3500) or 1)),
            "warnings": warnings,
            "effects": {
                "generation": "由大模型生成轻量分集规划与核心视觉资产；逐集制作细节稍后按需生成。",
                "apply": "质量检查通过后自动写入分集规划、创作记忆和核心资产。",
            },
        }

    def start_pipeline(
        self,
        project_id: str,
        *,
        source_document_version_id: str | None = None,
        raw_text: str | None = None,
        visual_style: str = "国风仙侠 电影级写实 (Cinematic Realistic)",
        target_episode_duration_seconds: int = 120,
        voice_preset: str = "DEFAULT_VOX_CPM2",
        auto_run_rendering: bool = False,
        application_authorization: dict[str, Any] | None = None,
        production_authorization: dict[str, Any] | None = None,
        capability_profile_version_id: str | None = None,
        llm_config: dict[str, Any] | None = None,
        actor: str = "local-user",
        supersedes_run_id: str | None = None,
    ) -> dict[str, Any]:
        del auto_run_rendering
        authorization = dict(application_authorization or {"endpoint": "DRAFT_ONLY", "sections": []})
        endpoint = str(authorization.get("endpoint") or "DRAFT_ONLY").upper()
        sections = list(dict.fromkeys(str(item).upper() for item in authorization.get("sections") or []))
        if endpoint not in {"DRAFT_ONLY", "APPLY_SELECTED_SECTIONS"}:
            raise DomainRuleError("PIPELINE_AUTHORIZATION_INVALID", "规划应用授权终点无效")
        if endpoint == "DRAFT_ONLY":
            sections = []
        elif not sections or set(sections) - PIPELINE_SECTIONS:
            raise DomainRuleError("PIPELINE_AUTHORIZATION_INVALID", "自动应用必须明确授权有效的文本 sections")
        authorization = {"schema_version": "pipeline-application-authorization/v1", "endpoint": endpoint, "sections": sections}
        production = dict(production_authorization or {"endpoint": "STRUCTURE_ONLY"})
        production_endpoint = str(production.get("endpoint") or "STRUCTURE_ONLY").upper()
        production_mode = str(production.get("production_mode") or "BALANCED").upper()
        checkpoint_policy = str(production.get("checkpoint_policy") or "ON_EXCEPTION").upper()
        if production_endpoint not in {"STRUCTURE_ONLY", "WAITING_REVIEW"}:
            raise DomainRuleError("PIPELINE_PRODUCTION_AUTHORIZATION_INVALID", "整部生产授权终点无效")
        if production_mode not in {"DRAFT", "BALANCED", "QUALITY"}:
            raise DomainRuleError("PIPELINE_PRODUCTION_AUTHORIZATION_INVALID", "整部生产质量档无效")
        if checkpoint_policy not in {
            "AUTO_CONTINUE",
            "AFTER_ASSETS",
            "AFTER_SHOT_PLAN",
            "BEFORE_VIDEO",
            "ON_EXCEPTION",
        }:
            raise DomainRuleError("PIPELINE_PRODUCTION_AUTHORIZATION_INVALID", "整部生产检查点策略无效")
        if production_endpoint == "WAITING_REVIEW" and (
            endpoint != "APPLY_SELECTED_SECTIONS"
            or not {"STORY_PLAN", "ASSET_PROPOSALS"}.issubset(set(sections))
        ):
            raise DomainRuleError(
                "PIPELINE_PRODUCTION_AUTHORIZATION_INVALID",
                "继续整部生产必须先授权应用故事规划和资产建议",
            )
        production = {
            "schema_version": "pipeline-production-authorization/v1",
            "endpoint": production_endpoint,
            "production_mode": production_mode,
            "checkpoint_policy": checkpoint_policy,
            "tts_enabled": bool(production.get("tts_enabled", True)),
            "max_parallel_episodes": int(production.get("max_parallel_episodes") or 1),
            "min_free_disk_bytes": int(production.get("min_free_disk_bytes") or 1),
            "max_duration_seconds": int(production.get("max_duration_seconds") or 24 * 60 * 60),
            "max_new_jobs": int(production.get("max_new_jobs") or 600),
            "max_attempts_total": int(production.get("max_attempts_total") or 1_200),
            "max_output_bytes": int(production.get("max_output_bytes") or 100 * 1024 * 1024 * 1024),
            "max_queued_gpu_jobs": int(production.get("max_queued_gpu_jobs") or 8),
            "dispatch_shots_per_tick": int(production.get("dispatch_shots_per_tick") or 4),
        }
        self._project(project_id)
        source_id, text = self._validate_input(source_document_version_id, raw_text, target_episode_duration_seconds)
        if len(visual_style) > 200:
            visual_style = visual_style[:200]
        if visual_style not in ALLOWED_VISUAL_STYLES:
            logger.info("Using custom pipeline visual style: %s", visual_style)
        with self.database.connect() as connection:
            running = connection.execute(
                "SELECT id FROM pipeline_runs WHERE project_id=? AND state='RUNNING' LIMIT 1", (project_id,)
            ).fetchone()
        if running:
            raise DomainRuleError(
                "PIPELINE_ALREADY_RUNNING", "当前项目已有草案正在生成", {"existing_run_id": str(running["id"])}
            )
        if text:
            source_id, source_label = self._persist_pasted_source(project_id, text, actor)
        else:
            assert source_id is not None
            _, source_label = self._read_source_version(project_id, source_id)
        preflight = self.preflight(
            project_id,
            source_document_version_id=source_id,
            target_episode_duration_seconds=target_episode_duration_seconds,
            capability_profile_version_id=capability_profile_version_id,
        )
        if not preflight["ai"]["ready"]:
            raise DomainRuleError(
                "PIPELINE_LLM_REQUIRED",
                str(preflight["ai"].get("message") or "完整故事建档必须先配置可用大模型"),
            )
        # The launch button is the creator's single confirmation for the
        # complete source.  Downstream per-episode generation can therefore
        # reuse this immutable committed source without another import gate.
        self._ensure_source_committed(project_id, source_id, actor)
        paragraphs = source_paragraphs(self._read_source_version(project_id, source_id)[0])
        authorized_scope = self._authorized_source_range(
            project_id, source_id, len(paragraphs)
        )
        capability_profile_version_id = preflight["ai"].get("profile_version_id") or capability_profile_version_id
        with self.database.transaction() as connection:
            running = connection.execute(
                "SELECT id FROM pipeline_runs WHERE project_id=? AND state='RUNNING' LIMIT 1", (project_id,)
            ).fetchone()
            if running:
                raise DomainRuleError(
                    "PIPELINE_ALREADY_RUNNING", "当前项目已有草案正在生成", {"existing_run_id": str(running["id"])}
                )
            if supersedes_run_id:
                prior = connection.execute(
                    "SELECT id FROM pipeline_runs WHERE id=? AND project_id=?", (supersedes_run_id, project_id)
                ).fetchone()
                if prior is None:
                    raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "要替代的草案运行不存在")
            run_id = f"pipe-{uuid.uuid4().hex[:12]}"
            now = _now()
            input_snapshot = {
                "schema_version": "pipeline.input.v1",
                "source_sha256": preflight["source"]["sha256"],
                "source_document_version_id": source_id,
                "authorized_source_scope": authorized_scope,
                "visual_style": visual_style,
                "target_episode_duration_seconds": target_episode_duration_seconds,
                "voice_preset": voice_preset,
                "capability_profile_version_id": capability_profile_version_id,
                "llm_config": {key: value for key, value in (llm_config or {}).items() if key != "api_key"},
                "application_authorization": authorization,
                "production_authorization": production,
            }
            connection.execute(
                """INSERT INTO pipeline_runs
                (id,project_id,state,stage,stage_label,progress_pct,visual_style,target_episode_duration_seconds,
                 voice_preset,source_document_version_id,capability_profile_version_id,episodes_count,characters_count,
                 scenes_count,props_count,shots_count,episodes_json,assets_json,error_message,extraction_mode,
                 llm_model,llm_provider,llm_error,created_at,updated_at,created_by,revision,schema_version,
                 source_label,input_snapshot_json,draft_json,quality_report_json,apply_state,applied_sections_json,
                 supersedes_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v3',?,?, '{}','{}','NOT_APPLIED','[]',?)""",
                (
                    run_id, project_id, "RUNNING", "QUEUED", "草案已进入本机任务队列", 2, visual_style,
                    target_episode_duration_seconds, voice_preset, source_id, capability_profile_version_id,
                    0, 0, 0, 0, 0, "[]", '{"characters":[],"scenes":[],"props":[]}', None, "UNKNOWN",
                    None, None, None, now, now, actor, source_label, _json(input_snapshot), supersedes_run_id,
                ),
            )
            job = self.jobs.create_job_in_transaction(
                connection,
                project_id,
                PIPELINE_JOB_TYPE,
                "PIPELINE_RUN",
                run_id,
                "CPU",
                {"run_id": run_id, "project_id": project_id},
                f"pipeline-draft:{run_id}",
                actor=actor,
                subject_kind="PIPELINE_RUN",
                scope_kind="PROJECT",
                scope_project_id=project_id,
                stage_code="STORY_PIPELINE",
                max_attempts=1,
            )
            connection.execute("UPDATE pipeline_runs SET job_id=? WHERE id=?", (job["id"], run_id))
            if endpoint == "APPLY_SELECTED_SECTIONS":
                # PR-05: a NEW draft revision published by a continuation needs its OWN
                # dependency Job.  The key used to be ``pipeline-apply:{run_id}``, so the
                # second batch replayed the first batch's already-SUCCEEDED apply job and
                # the new episodes never reached the project.  Keying on the revision is
                # safe because the authorization is expressed in the run snapshot, not in
                # the key: the sections are re-read and re-checked on every continuation.
                continuation = self.jobs.create_job_in_transaction(
                    connection,
                    project_id,
                    PIPELINE_APPLY_JOB_TYPE,
                    "PIPELINE_RUN",
                    run_id,
                    "CPU",
                    {"run_id": run_id, "project_id": project_id, "authorized_sections": sections},
                    f"pipeline-apply:{run_id}:{str(authorization.get('authorized_draft_sha256') or '')}",
                    actor=actor,
                    subject_kind="PIPELINE_RUN",
                    scope_kind="PROJECT",
                    scope_project_id=project_id,
                    stage_code="STORY_PIPELINE",
                    max_attempts=3,
                    depends_on_job_ids=[str(job["id"])],
                )
                authorization["continuation_job_id"] = str(continuation["id"])
                input_snapshot["application_authorization"] = authorization
                connection.execute(
                    "UPDATE pipeline_runs SET input_snapshot_json=? WHERE id=?", (_json(input_snapshot), run_id)
                )
        return self.get_pipeline(project_id, run_id)

    def get_pipeline(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在", {"run_id": run_id})
        run = self._row_to_run(row)
        # PR-04: a run whose Job already reached a terminal state must never keep
        # reporting RUNNING; reading it converges the projection.
        if str(run.get("state") or "") == "RUNNING":
            run = self._reconcile_run_with_job(run)
        return self._attach_production_continuation(
            self._attach_apply_continuation(run)
        )

    def get_latest_pipeline(self, project_id: str) -> dict[str, Any] | None:
        self._project(project_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return (
            self._attach_production_continuation(
                self._attach_apply_continuation(self._row_to_run(row))
            )
            if row
            else None
        )

    def list_pipelines(self, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self._project(project_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,project_id,state,stage,stage_label,progress_pct,revision,source_label,
                episodes_count,characters_count,scenes_count,props_count,shots_count,apply_state,
                created_at,updated_at,error_message
                FROM pipeline_runs WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?""",
                (project_id, max(1, min(100, limit))),
            ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def pause_pipeline(self, project_id: str, run_id: str) -> dict[str, Any]:
        del project_id, run_id
        raise DomainRuleError("PIPELINE_PAUSE_UNSUPPORTED", "草案任务不支持暂停；可以安全取消后重新生成")

    def resume_pipeline(self, project_id: str, run_id: str) -> dict[str, Any]:
        del project_id, run_id
        raise DomainRuleError("PIPELINE_RESUME_UNSUPPORTED", "草案任务不支持恢复；失败任务可直接重试")

    def cancel_pipeline(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_pipeline(project_id, run_id)
        continuation = run.get("apply_continuation") or {}
        if run["state"] == "SUCCEEDED" and continuation.get("state") in {"QUEUED", "CLAIMED", "RUNNING"}:
            self.jobs.cancel(str(continuation["job_id"]))
            with self.database.transaction() as connection:
                row = connection.execute("SELECT input_snapshot_json FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
                snapshot = _parse_json(row["input_snapshot_json"], {})
                authorization = dict(snapshot.get("application_authorization") or {})
                authorization["revoked_at"] = _now()
                authorization["endpoint"] = "DRAFT_ONLY"
                snapshot["application_authorization"] = authorization
                connection.execute(
                    "UPDATE pipeline_runs SET input_snapshot_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (_json(snapshot), _now(), run_id),
                )
            return self.get_pipeline(project_id, run_id)
        if run["state"] != "RUNNING":
            raise DomainRuleError("PIPELINE_STATE_INVALID", "只有生成中的草案可以取消")
        if run["job_id"]:
            self.jobs.cancel(str(run["job_id"]))
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET state='CANCELLED',stage='CANCELLED',stage_label='已取消；未修改正式项目数据',
                error_message='用户取消',updated_at=?,revision=revision+1 WHERE id=? AND project_id=? AND state='RUNNING'""",
                (_now(), run_id, project_id),
            )
        return self.get_pipeline(project_id, run_id)

    def continue_authorized_application(self, run_id: str) -> dict[str, Any]:
        """Replay the original apply command from a durable, explicitly scoped authorization."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
        run = self._row_to_run(row)
        authorization = run["application_authorization"]
        if authorization.get("endpoint") != "APPLY_SELECTED_SECTIONS" or authorization.get("revoked_at"):
            raise DomainRuleError("PIPELINE_APPLICATION_NOT_AUTHORIZED", "本次运行未授权自动应用")
        # PR-05: "already applied" means "THIS draft revision is applied".  The old check
        # was a run-level boolean, so once the first batch was applied no later batch
        # could ever be authorized into the project.
        with self.database.connect() as connection:
            watermark_row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE id=?", (run_id,)
            ).fetchone()
        applied_revision_hash = (
            self._effective_applied_revision_hash(watermark_row) if watermark_row is not None else ""
        )
        current_revision_hash = self._draft_revision_hash(run.get("draft") or {})
        if run["apply_state"] == "APPLIED" and applied_revision_hash and applied_revision_hash == current_revision_hash:
            production = self._continue_authorized_production(run_id)
            return {
                "run": self.get_pipeline_by_id(run_id),
                "production": production,
                "idempotent_replay": True,
            }
        if run["state"] != "SUCCEEDED":
            raise DomainRuleError("PIPELINE_STATE_INVALID", "草案生成尚未成功，不能续接应用")
        authorized_draft_sha256 = str(authorization.get("authorized_draft_sha256") or "")
        if not authorized_draft_sha256 or authorized_draft_sha256 != _sha(_json(run["draft"])):
            if not applied_revision_hash:
                # Nothing was applied yet, so no continuation can be in play: the draft
                # changed after the authorization was granted and must not be applied
                # under it.
                raise DomainRuleError(
                    "PIPELINE_AUTHORIZED_DRAFT_CHANGED",
                    "草案在生成完成后发生变化，原授权不能继续应用",
                )
            # A continuation published a NEW draft revision under the same still-standing
            # authorization.  The grant is re-scoped to that revision, and only to it: the
            # revision applied before the continuation stays applied and is never applied
            # twice.
            self._authorize_current_revision(run_id, run, authorization)
            run = self.get_pipeline(run["project_id"], run_id)
            authorization = run["application_authorization"]
        sections = list(authorization.get("sections") or [])
        preview = self.preview_pipeline_apply(
            run["project_id"], run_id, expected_revision=run["revision"], sections=sections
        )
        if not preview["can_apply"]:
            raise DomainRuleError(
                "PIPELINE_QUALITY_BLOCKED",
                "授权续接已暂停：草案当前未通过应用门禁",
                {"blockers": preview["quality_report"].get("blockers", [])},
            )
        applied = self.apply_pipeline(
            run["project_id"],
            run_id,
            expected_revision=run["revision"],
            sections=sections,
            expected_impact_sha256=preview["impact"]["impact_sha256"],
            actor="pipeline-authorized-continuation",
        )
        production = self._continue_authorized_production(run_id)
        return {
            **applied,
            "run": self.get_pipeline_by_id(run_id),
            "production": production,
        }

    def _authorize_current_revision(
        self, run_id: str, run: dict[str, Any], authorization: dict[str, Any]
    ) -> None:
        """Re-scope an existing auto-apply authorization onto the current draft.

        A continuation publishes a new draft revision; the user's grant was given for
        the run and its selected sections, not for one frozen text.  The new revision is
        adopted, recorded, and applied in the same operation, and the applied watermark
        is untouched, so an already-applied revision can never be applied twice.
        """

        sections = list(authorization.get("sections") or [])
        if not sections:
            raise DomainRuleError("PIPELINE_APPLICATION_NOT_AUTHORIZED", "授权中没有可应用的区块")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT input_snapshot_json,revision FROM pipeline_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
            snapshot = _parse_json(row["input_snapshot_json"], {})
            current = dict(snapshot.get("application_authorization") or {})
            if current.get("endpoint") != "APPLY_SELECTED_SECTIONS" or current.get("revoked_at"):
                raise DomainRuleError("PIPELINE_APPLICATION_NOT_AUTHORIZED", "本次运行未授权自动应用")
            current["authorized_draft_sha256"] = _sha(_json(run["draft"]))
            current["authorized_revision_no"] = int(run["revision"])
            current["reauthorized_for_continuation"] = True
            # The new revision gets its own durable command, so the second batch is
            # observable and independently replayable instead of being hidden inside the
            # first batch's already-SUCCEEDED job.
            staged = self.jobs.create_job_in_transaction(
                connection,
                str(run["project_id"]),
                PIPELINE_APPLY_JOB_TYPE,
                "PIPELINE_RUN",
                run_id,
                "CPU",
                {"run_id": run_id, "project_id": str(run["project_id"]), "authorized_sections": sections},
                # A per-revision key: the same revision replays, a new revision is a new
                # command.
                f"pipeline-apply:{run_id}:{current['authorized_draft_sha256']}",
                actor="pipeline-authorized-continuation",
                subject_kind="PIPELINE_RUN",
                scope_kind="PROJECT",
                scope_project_id=str(run["project_id"]),
                stage_code="STORY_PIPELINE",
                max_attempts=3,
            )
            current["continuation_job_id"] = str(staged["id"])
            snapshot["application_authorization"] = current
            connection.execute(
                "UPDATE pipeline_runs SET input_snapshot_json=?,revision=revision+1 WHERE id=?",
                (_json(snapshot), run_id),
            )

    def retry_pipeline(self, project_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        run = self.get_pipeline(project_id, run_id)
        if run["revision"] != expected_revision:
            raise DomainRuleError("PIPELINE_REVISION_CONFLICT", "草案状态已更新，请刷新后重试")
        if run["state"] != "FAILED" or not run["job_id"]:
            raise DomainRuleError("PIPELINE_STATE_INVALID", "只有失败的草案任务可以重试")
        self.jobs.retry(str(run["job_id"]))
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET state='RUNNING',stage='QUEUED',stage_label='已重新进入任务队列',
                progress_pct=2,error_message=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                (_now(), run_id),
            )
        return self.get_pipeline(project_id, run_id)

    def _analysis_windows(self, run: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Rebuild the current window plan for a run from its frozen source scope."""
        snapshot = self._input_snapshot(str(run["run_id"]))
        authorized_scope = snapshot.get("authorized_source_scope")
        if not isinstance(authorized_scope, dict):
            raise DomainRuleError(
                "PIPELINE_SOURCE_SCOPE_REQUIRED",
                "该任务没有可验证的授权正文范围，请重新发起分析。",
            )
        source_id = str(run.get("source_document_version_id") or "")
        text, _ = self._read_source_version(str(run["project_id"]), source_id)
        if _sha(text) != str(snapshot.get("source_sha256") or ""):
            raise DomainRuleError("PIPELINE_SOURCE_CHANGED", "原稿内容与启动时快照不一致")
        return (
            self._episode_specs(
                text,
                start_paragraph=int(authorized_scope["start_paragraph"]),
                end_paragraph=int(authorized_scope["end_paragraph"]),
            ),
            snapshot,
        )

    def _input_snapshot(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT input_snapshot_json FROM pipeline_runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
        snapshot = _parse_json(_safe_col(row, "input_snapshot_json", "{}"), {})
        return snapshot if isinstance(snapshot, dict) else {}

    def continue_analysis(
        self,
        project_id: str,
        run_id: str,
        *,
        expected_revision: int,
        expected_source_sha256: str,
        expected_next_window_index: int | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Queue the next bounded batch of the same authorised manuscript range.

        The command is resolved against the server-side cursor, not against a
        client-supplied range, so a replayed or stale command cannot skip or repeat
        source text. Only unfinished windows are processed; already-planned units
        are retained verbatim so production that already consumed them is untouched.
        """
        run = self.get_pipeline(project_id, run_id)
        if run["revision"] != expected_revision:
            raise DomainRuleError("PIPELINE_REVISION_CONFLICT", "草案状态已更新，请刷新后继续")
        with self.database.connect() as connection:
            active = connection.execute(
                """SELECT id,state FROM jobs WHERE project_id=? AND subject_type='PIPELINE_RUN'
                AND subject_id=? AND state IN ('QUEUED','CLAIMED','RUNNING','PAUSE_REQUESTED')
                LIMIT 1""",
                (project_id, run_id),
            ).fetchone()
        if active is not None:
            raise DomainRuleError(
                "PIPELINE_ALREADY_RUNNING", "该分析仍在执行，请等待当前批次结束后再继续"
            )
        if run["state"] not in {"SUCCEEDED", "FAILED"}:
            raise DomainRuleError("PIPELINE_STATE_INVALID", "只有已完成或失败的分析可以继续")
        all_specs, snapshot = self._analysis_windows(run)
        source_sha = str(snapshot.get("source_sha256") or "")
        if expected_source_sha256 and expected_source_sha256 != source_sha:
            raise DomainRuleError(
                "PIPELINE_SOURCE_CHANGED", "原稿已变化，旧续接游标不可用，请重新发起分析"
            )
        cursor = run["analysis_cursor"]
        next_window = int(cursor["next_window_index"])
        if expected_next_window_index is not None and next_window != expected_next_window_index:
            raise DomainRuleError(
                "PIPELINE_CURSOR_STALE",
                "续接位置已变化，请刷新后按服务端游标继续",
                {"server_next_window_index": next_window},
            )
        total_windows = len(all_specs)
        if next_window >= total_windows:
            raise DomainRuleError("PIPELINE_COVERAGE_COMPLETE", "授权原稿范围已全部分析完成")
        if not run["job_id"]:
            raise DomainRuleError("PIPELINE_STATE_INVALID", "该分析没有可续接的任务")
        now = _now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT input_snapshot_json,revision FROM pipeline_runs WHERE id=? AND project_id=?",
                (run_id, project_id),
            ).fetchone()
            if current is None:
                raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
            if int(current["revision"]) != expected_revision:
                raise DomainRuleError("PIPELINE_REVISION_CONFLICT", "草案状态已更新，请刷新后继续")
            current_snapshot = _parse_json(current["input_snapshot_json"], {})
            current_cursor = current_snapshot.get("analysis_cursor")
            if isinstance(current_cursor, dict) and int(current_cursor.get("next_window_index") or 0) != next_window:
                raise DomainRuleError(
                    "PIPELINE_CURSOR_STALE",
                    "续接位置已变化，请刷新后按服务端游标继续",
                    {"server_next_window_index": int(current_cursor.get("next_window_index") or 0)},
                )
            job = self.jobs.create_job_in_transaction(
                connection,
                project_id,
                PIPELINE_JOB_TYPE,
                "PIPELINE_RUN",
                run_id,
                "CPU",
                {"run_id": run_id, "project_id": project_id, "continue_from_window_index": next_window},
                f"pipeline-continue:{run_id}:{next_window}",
                actor=actor,
                subject_kind="PIPELINE_RUN",
                scope_kind="PROJECT",
                scope_project_id=project_id,
                stage_code="STORY_PIPELINE",
                max_attempts=1,
            )
            # PR-04: the continuation key is stable for a given cursor, so a second
            # click after a FAILED batch replayed the original command and returned
            # the FAILED Job while this method wrote ``state='RUNNING'`` — a run
            # pinned at RUNNING with a Job nobody could claim, and no way back in
            # because ``retry_pipeline`` requires ``state='FAILED'``.  The live Job
            # state now decides what a continuation means.
            job_state = str(job.get("state") or "QUEUED").upper()
            recovery_action = "SUBMITTED"
            if job.get("idempotent_replay") and job_state in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                # A continuation click is the user's explicit authorisation to retry
                # THIS batch; the retry runs in this same transaction so the Job and
                # the run can never disagree.
                job = self.jobs.retry_in_transaction(connection, str(job["id"]), actor=actor)
                job_state = str(job.get("state") or "QUEUED").upper()
                recovery_action = "RETRIED_FAILED_BATCH"
            elif job.get("idempotent_replay") and job_state == "SUCCEEDED":
                # The batch already finished; the cursor, not a second Job, decides
                # what is left to do.  Nothing is mutated here.
                connection.execute(
                    """UPDATE pipeline_runs SET updated_at=?,revision=revision+1 WHERE id=?""",
                    (now, run_id),
                )
                recovered = self.get_pipeline(project_id, run_id)
                recovered["recovery_action"] = "BATCH_ALREADY_SUCCEEDED"
                recovered["job_state"] = job_state
                return recovered
            if job_state in {"FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"}:
                raise DomainRuleError(
                    "PIPELINE_CONTINUE_JOB_TERMINAL",
                    "该批次的执行任务已进入终态，请先按失败重试或重新发起分析",
                    {"job_id": str(job["id"]), "job_state": job_state},
                )
            connection.execute(
                """UPDATE pipeline_runs SET state='RUNNING',stage='QUEUED',
                stage_label='已按续接位置重新进入任务队列',progress_pct=2,error_message=NULL,
                job_id=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (job["id"], now, run_id),
            )
        result = self.get_pipeline(project_id, run_id)
        result["recovery_action"] = recovery_action
        result["job_state"] = job_state
        return result

    def _reconcile_run_with_job(self, run: dict[str, Any]) -> dict[str, Any]:
        """Converge a stale RUNNING projection onto its Job's real terminal state.

        PR-04: the audit's run was pinned at ``RUNNING`` while its Job was FAILED,
        so ``retry_pipeline`` (which requires ``FAILED``) had no way in and the
        workbench was permanently occupied.  Reading the project is now enough to
        converge the two.
        """

        if str(run.get("state") or "") != "RUNNING" or not run.get("job_id"):
            return run
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT state,last_error_code,last_error_detail_redacted FROM jobs WHERE id=?",
                (str(run["job_id"]),),
            ).fetchone()
        if row is None:
            return run
        job_state = str(row["state"] or "")
        target = {"FAILED": "FAILED", "CANCELLED": "FAILED", "NEEDS_ATTENTION": "FAILED", "ORPHANED": "FAILED"}.get(job_state)
        if target is None:
            return run
        detail = str(row["last_error_detail_redacted"] or row["last_error_code"] or "")
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET state=?,stage='FAILED',stage_label='执行任务已结束，状态已对账',
                error_message=?,updated_at=?,revision=revision+1
                WHERE id=? AND state='RUNNING'""",
                (target, detail or "执行任务未成功完成", _now(), str(run["run_id"])),
            )
        return self.get_pipeline(str(run["project_id"]), str(run["run_id"]))

    def _update_run(self, run_id: str, **values: Any) -> None:
        columns = {
            "state", "stage", "stage_label", "progress_pct", "episodes_count", "characters_count",
            "scenes_count", "props_count", "shots_count", "error_message", "extraction_mode", "llm_model",
            "llm_provider", "llm_error", "draft_json", "quality_report_json", "episodes_json", "assets_json",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, value in values.items():
            if key not in columns:
                continue
            sets.append(f"{key}=?")
            params.append(_json(value) if key.endswith("_json") and not isinstance(value, str) else value)
        if not sets:
            return
        sets.extend(["updated_at=?", "revision=revision+1"])
        params.extend([_now(), run_id])
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE pipeline_runs SET {','.join(sets)} WHERE id=?", params)

    @staticmethod
    def _episode_spec_builder(
        *,
        number: int,
        title: str,
        summary: str,
        window: dict[str, Any],
        window_index: int,
        window_count: int,
    ) -> dict[str, Any]:
        text = str(window["text"])
        return {
            "number": number,
            "code": f"EP{number:02d}",
            "title": title[:160],
            "summary": summary[:320],
            "source_start_paragraph": int(window["start_paragraph"]),
            "source_end_paragraph": int(window["end_paragraph"]),
            "source_text": text,
            # Bounded-input provenance. A long chapter becomes several windows of
            # the same episode unit, so the planner and the coverage report can
            # prove that no character of the authorised range was skipped.
            "unit_number": number,
            "window_index": window_index,
            "window_count": window_count,
            "source_character_count": len(text),
            "source_input_sha256": _sha(text),
        }

    @staticmethod
    def _planned_units(unit_numbers: list[int]) -> list[int]:
        """Distinct unit numbers, in first-appearance order."""

        ordered: list[int] = []
        for number in unit_numbers:
            if number not in ordered:
                ordered.append(number)
        return ordered

    @staticmethod
    def _merge_unit_windows(
        generated: list[dict[str, Any]], window_specs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Collapse the model's per-window outlines into ONE outline per episode unit.

        PR-01 root cause: a long chapter is split into several bounded model *input
        windows*, and the planner treated every window as its own episode.  A short
        chapter plus one 31,500-character chapter therefore produced four windows
        numbered ``[1, 2, 2, 2]``, so the draft proposed three duplicate ``EP02``
        rows and ``apply`` failed on ``UNIQUE constraint failed:
        episodes.season_id, episodes.code``.  Aggregating here — before anything is
        persisted or applied — is what keeps the input-window mechanism from
        becoming a duplicate-episode generator.  De-duplicating on the way into the
        database would be wrong: it would silently drop the later half of the
        chapter.
        """

        by_number: dict[int, list[dict[str, Any]]] = {}
        for item in generated:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item["number"])
            except (KeyError, TypeError, ValueError):
                continue
            by_number.setdefault(number, []).append(item)
        units: list[dict[str, Any]] = []
        for number, items in sorted(by_number.items()):
            base = dict(items[0])
            starts: list[int] = []
            ends: list[int] = []
            bodies: list[str] = []
            windows: list[dict[str, Any]] = []
            for spec in window_specs:
                try:
                    spec_number = int(spec["number"])
                except (KeyError, TypeError, ValueError):
                    continue
                if spec_number != number:
                    continue
                start = int(spec.get("source_start_paragraph") or 0)
                end = int(spec.get("source_end_paragraph") or 0)
                text = str(spec.get("source_text") or "")
                starts.append(start)
                ends.append(end)
                bodies.append(text)
                windows.append(
                    {
                        "window_index": int(spec.get("window_index") or 1),
                        "start_paragraph": start,
                        "end_paragraph": end,
                        "character_count": len(text),
                        "input_sha256": str(spec.get("source_input_sha256") or ""),
                    }
                )
            if not starts:
                starts = [int(item.get("source_start_paragraph") or 0) for item in items]
                ends = [int(item.get("source_end_paragraph") or 0) for item in items]
            if bodies:
                # Every window's text travels with the unit so the episode keeps the
                # whole authorised range instead of only the last window's slice.
                base["source_text"] = "\n".join(bodies)
                base["source_character_count"] = len(base["source_text"])
            base["source_start_paragraph"] = min(starts)
            base["source_end_paragraph"] = max(ends)
            base["unit_number"] = number
            base["window_count"] = len(items)
            base["source_windows"] = windows
            units.append(base)
        return units

    @staticmethod
    def _merge_unit_observations(
        window_episodes: list[dict[str, Any]], unit_episodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Carry every NON-EMPTY field a window produced onto its merged unit.

        A window that yielded new characters would otherwise lose them when its
        outline is folded into the unit's.  Empty values never overwrite the merged
        outline, so a later window cannot erase what an earlier one found.
        """

        by_number: dict[int, list[dict[str, Any]]] = {}
        for item in window_episodes:
            if not isinstance(item, dict):
                continue
            try:
                by_number.setdefault(int(item["number"]), []).append(item)
            except (KeyError, TypeError, ValueError):
                continue
        merged: list[dict[str, Any]] = []
        for unit in unit_episodes:
            number = int(unit["number"])
            result = dict(unit)
            for item in by_number.get(number, []):
                for key, value in item.items():
                    if key in {"number", "code", "source_start_paragraph", "source_end_paragraph"}:
                        continue
                    if key in {"scenes", "entity_observations"}:
                        existing = result.get(key)
                        empty = not existing or (
                            isinstance(existing, dict) and not any(existing.values())
                        ) or (isinstance(existing, list) and not existing)
                        if empty and value:
                            result[key] = value
                        continue
                    if result.get(key) in (None, "", [], {}):
                        result[key] = value
            merged.append(result)
        return merged

    @staticmethod
    def _episode_specs(
        text: str, *, start_paragraph: int = 1, end_paragraph: int | None = None
    ) -> list[dict[str, Any]]:
        paragraphs = source_paragraphs(text)
        last_paragraph = end_paragraph if end_paragraph is not None else len(paragraphs)
        selected_paragraphs = [
            item for item in paragraphs if start_paragraph <= int(item.number) <= last_paragraph
        ]
        chapters = [
            {
                **chapter,
                "start_paragraph": max(start_paragraph, int(chapter["start_paragraph"])),
                "end_paragraph": min(last_paragraph, int(chapter["end_paragraph"])),
            }
            for chapter in source_chapters(paragraphs)
            if int(chapter["end_paragraph"]) >= start_paragraph
            and int(chapter["start_paragraph"]) <= last_paragraph
        ]
        specs: list[dict[str, Any]] = []
        number = 0

        def append_unit(title: str, body_paragraphs: list[Any]) -> None:
            nonlocal number
            if not body_paragraphs:
                return
            number += 1
            windows = _paragraph_windows(body_paragraphs)
            window_count = len(windows)
            body = "\n".join(str(item.text) for item in body_paragraphs)
            for window_index, window in enumerate(windows, start=1):
                specs.append(
                    PipelineOrchestratorService._episode_spec_builder(
                        number=number,
                        title=title or f"第 {number} 集",
                        summary=re.sub(r"\s+", " ", body),
                        window=window,
                        window_index=window_index,
                        window_count=window_count,
                    )
                )

        if chapters:
            # The authorised range may open before the first recognised chapter.
            # That prologue is real manuscript (序章/引子/背景/untitled opening) and
            # must reach the model instead of being dropped between the chapter
            # units; it becomes its own unit unless it carries no text at all.
            first_chapter_start = int(chapters[0]["start_paragraph"])
            append_unit(
                "序幕与开篇",
                [item for item in selected_paragraphs if int(item.number) < first_chapter_start],
            )
            for chapter in chapters:
                start = int(chapter["start_paragraph"])
                end = int(chapter["end_paragraph"])
                # Source ranges are the same 1-based, inclusive paragraph
                # numbers exposed by the import API.
                append_unit(
                    str(chapter.get("title") or f"第 {number + 1} 集"),
                    [item for item in selected_paragraphs if start <= int(item.number) <= end],
                )
        else:
            groups: list[list[Any]] = []
            current: list[Any] = []
            current_chars = 0
            for paragraph in selected_paragraphs:
                # Close the group *before* adding a paragraph that would exceed the
                # target size, so adjacent short paragraphs stay together instead of
                # each becoming its own unit.
                if current and current_chars + len(paragraph.text) > _LONG_UNIT_SLICE_CHARACTERS:
                    groups.append(current)
                    current, current_chars = [], 0
                current.append(paragraph)
                current_chars += len(paragraph.text)
            if current:
                groups.append(current)
            for group in groups:
                append_unit(f"第 {number + 1} 集", group)
        if specs:
            return specs
        fallback = [item for item in selected_paragraphs]
        if not fallback and start_paragraph <= last_paragraph:
            fallback = [
                item
                for item in paragraphs
                if start_paragraph <= int(item.number) <= last_paragraph
            ]
        return [
            {
                "number": 1,
                "code": "EP01",
                "title": "第 1 集",
                "summary": text[:320],
                "source_start_paragraph": start_paragraph,
                "source_end_paragraph": max(start_paragraph, last_paragraph),
                "source_text": "\n".join(item.text for item in fallback),
                "unit_number": 1,
                "window_index": 1,
                "window_count": 1,
                "source_character_count": len("\n".join(item.text for item in fallback)),
                "source_input_sha256": _sha("\n".join(item.text for item in fallback)),
            }
        ]

    @staticmethod
    def _merge_intervals(intervals: list[tuple[int, int]]) -> list[list[int]]:
        merged: list[list[int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged

    @staticmethod
    def _source_coverage(
        *,
        source_sha256: str,
        authorized_scope: dict[str, Any],
        all_specs: list[dict[str, Any]],
        selected_specs: list[dict[str, Any]],
        completed_count: int,
        completed_window_count: int | None = None,
        selected_window_offset: int | None = None,
    ) -> dict[str, Any]:
        completed_specs = selected_specs[: max(0, min(completed_count, len(selected_specs)))]
        # A resumed run reports only the windows it still owes, so the number of
        # already-processed windows is carried in explicitly instead of being
        # inferred from this batch. ``coverage_complete`` is still proved by the
        # interval set difference below, never by these counts.
        prior_window_count = max(0, int(completed_window_count or 0))
        batch_offset = max(0, int(selected_window_offset or 0))
        # Windows before the resumed batch were settled by an earlier batch; they
        # count as covered but must never be reported as still-unprocessed.
        prior_specs = all_specs[:batch_offset]
        completed_ranges: list[dict[str, Any]] = []
        unprocessed_ranges: list[dict[str, Any]] = []
        fully_covered: list[tuple[int, int]] = []
        for index, spec in enumerate(prior_specs + completed_specs):
            settled_by_earlier_batch = index < len(prior_specs)
            # Narrow the local once instead of re-reading the dict, so the checker
            # can prove the value is a usable number.
            declared_characters = spec.get("source_character_count")
            character_count = (
                int(declared_characters)
                if isinstance(declared_characters, int)
                else len(str(spec.get("source_text") or ""))
            )
            submitted_count = min(character_count, PIPELINE_EPISODE_SOURCE_CHARACTER_LIMIT)
            truncated = submitted_count < character_count
            if not settled_by_earlier_batch:
                completed_ranges.append(
                    {
                        "unit_number": int(spec["number"]),
                        "window_index": int(spec.get("window_index") or 1),
                        "start_paragraph": int(spec["source_start_paragraph"]),
                        "end_paragraph": int(spec["source_end_paragraph"]),
                        "authorized_character_count": character_count,
                        "submitted_character_count": submitted_count,
                        "input_sha256": str(spec.get("source_input_sha256") or ""),
                        "status": "PARTIAL" if truncated else "COMPLETED",
                    }
                )
            if not truncated:
                fully_covered.append(
                    (int(spec["source_start_paragraph"]), int(spec["source_end_paragraph"]))
                )
            else:
                unprocessed_ranges.append(
                    {
                        "start_paragraph": int(spec["source_start_paragraph"]),
                        "end_paragraph": int(spec["source_end_paragraph"]),
                        "reason": "EPISODE_INPUT_CHARACTER_LIMIT",
                        "unit_number": int(spec["number"]),
                        "window_index": int(spec.get("window_index") or 1),
                        "resume_character_offset_in_unit": submitted_count,
                        "unprocessed_character_count": character_count - submitted_count,
                    }
                )
        for spec in selected_specs[len(completed_specs):]:
            unprocessed_ranges.append(
                {
                    "start_paragraph": int(spec["source_start_paragraph"]),
                    "end_paragraph": int(spec["source_end_paragraph"]),
                    "reason": "WINDOW_NOT_COMPLETED",
                    "unit_number": int(spec["number"]),
                    "window_index": int(spec.get("window_index") or 1),
                }
            )
        tail = all_specs[batch_offset + len(selected_specs):]
        if tail:
            unprocessed_ranges.append(
                {
                    "start_paragraph": int(tail[0]["source_start_paragraph"]),
                    "end_paragraph": int(tail[-1]["source_end_paragraph"]),
                    "reason": "BATCH_EPISODE_LIMIT",
                    "resume_unit_number": int(tail[0]["number"]),
                    "resume_window_index": int(tail[0].get("window_index") or 1),
                    "unprocessed_window_count": len(tail),
                    "unprocessed_unit_count": len({int(item["number"]) for item in tail}),
                }
            )

        authorized_start = int(authorized_scope["start_paragraph"])
        authorized_end = int(authorized_scope["end_paragraph"])
        merged = PipelineOrchestratorService._merge_intervals(fully_covered)
        # Completeness is a set comparison against the authorised range, never a
        # count comparison. Any authorised paragraph missing from the union of
        # processed intervals is an explicit gap, so a run can no longer report
        # FULL while silently dropping the prologue or a truncated window.
        merged_authorized = PipelineOrchestratorService._merge_intervals(
            [(max(authorized_start, start), min(authorized_end, end)) for start, end in merged
             if start <= authorized_end and end >= authorized_start]
        )
        covered_paragraph_count = sum(end - start + 1 for start, end in merged)
        authorized_count = int(authorized_scope["paragraph_count"])
        coverage_gaps: list[dict[str, Any]] = []
        cursor = authorized_start
        for start, end in merged_authorized:
            if start > cursor:
                coverage_gaps.append(
                    {
                        "start_paragraph": cursor,
                        "end_paragraph": start - 1,
                        "reason": "AUTHORIZED_RANGE_NOT_COVERED",
                    }
                )
            cursor = max(cursor, end + 1)
        if cursor <= authorized_end:
            coverage_gaps.append(
                {
                    "start_paragraph": cursor,
                    "end_paragraph": authorized_end,
                    "reason": "AUTHORIZED_RANGE_NOT_COVERED",
                }
            )
        coverage_complete = (
            not coverage_gaps
            and not unprocessed_ranges
            and prior_window_count + len(completed_specs) >= len(all_specs)
        )
        status = (
            "FULL"
            if coverage_complete
            else "PARTIAL"
            if prior_window_count + completed_count > 0
            else "NOT_STARTED"
        )
        return {
            "schema_version": "pipeline.source-coverage.v2",
            "source_sha256": source_sha256,
            "status": status,
            "coverage_complete": coverage_complete,
            "completed_window_count": prior_window_count + len(completed_specs),
            "total_window_count": len(all_specs),
            "authorized_range": dict(authorized_scope),
            "completed_ranges": completed_ranges,
            "completed_paragraph_intervals": [
                {"start_paragraph": start, "end_paragraph": end} for start, end in merged
            ],
            "covered_paragraph_count": covered_paragraph_count,
            "authorized_paragraph_count": authorized_count,
            "coverage_gaps": coverage_gaps,
            "unprocessed_ranges": unprocessed_ranges,
            "resume": (unprocessed_ranges or coverage_gaps or [None])[0],
        }

    @staticmethod
    def _dialogues(text: str) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for speaker, line in re.findall(r"([\u4e00-\u9fa5A-Za-z]{2,8})[：:][“\"]?([^\n\r”\"]{2,100})", text):
            if speaker not in {"旁白", "画外音", "场景"}:
                found.append({"speaker": speaker.strip(), "text": line.strip()})
        return found[:12]

    def _breakdown(self, episode: dict[str, Any], visual_style: str, target_seconds: int) -> dict[str, Any]:
        text = str(episode.get("source_text") or episode.get("summary") or "")
        sentences = [item.strip() for item in re.split(r"[。！？\n]+", text) if len(item.strip()) >= 4]
        count = max(3, min(8, len(sentences) or 3))
        dialogues = self._dialogues(text)
        duration = max(1.0, round(target_seconds / count, 2))
        shots: list[dict[str, Any]] = []
        for index in range(count):
            action = sentences[index] if index < len(sentences) else "推进核心情节与人物关系"
            dialogue = dialogues[index] if index < len(dialogues) else None
            shots.append({
                "shot_no": index + 1,
                "visual": f"{action[:80]}；{visual_style}",
                "action": action[:160],
                "dialogue": dialogue or "",
                "duration_seconds": duration,
            })
        return {
            "scenes": [{
                "scene_no": 1,
                "title": f"{episode['title']} · 核心场次",
                "summary": episode.get("summary", ""),
                "characters": sorted({item["speaker"] for item in dialogues}),
                "shots": shots,
            }]
        }

    def execute_draft_generation(
        self,
        run_id: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
        report_progress: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        run: dict[str, Any] | None = None
        try:
            with self.database.connect() as connection:
                row = connection.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
            run = self._row_to_run(row)
            if run["state"] == "CANCELLED":
                raise DomainRuleError("JOB_CANCELLED", "草案生成已取消")
            snapshot = _parse_json(_safe_col(row, "input_snapshot_json", "{}"), {})

            def progress(stage: str, label: str, percent: int) -> None:
                if cancel_check and cancel_check():
                    self.cancel_pipeline(run["project_id"], run_id)
                    raise DomainRuleError("JOB_CANCELLED", "草案生成已取消")
                self._update_run(run_id, state="RUNNING", stage=stage, stage_label=label, progress_pct=percent)
                if report_progress:
                    report_progress({"phase": stage, "detail": label, "percent": percent})

            progress("SOURCE_ANALYSIS", "正在理解原稿并定位分集范围", 10)
            source_id = str(run["source_document_version_id"])
            text, _ = self._read_source_version(run["project_id"], source_id)
            if _sha(text) != snapshot.get("source_sha256"):
                raise DomainRuleError("PIPELINE_SOURCE_CHANGED", "原稿内容与启动时快照不一致")
            authorized_scope = snapshot.get("authorized_source_scope")
            if not isinstance(authorized_scope, dict):
                raise DomainRuleError(
                    "PIPELINE_SOURCE_SCOPE_REQUIRED",
                    "该旧任务没有可验证的授权正文范围，请重新发起分析。",
                )
            all_episode_specs = self._episode_specs(
                text,
                start_paragraph=int(authorized_scope["start_paragraph"]),
                end_paragraph=int(authorized_scope["end_paragraph"]),
            )
            # A ``continue-analysis`` job resumes at the durable cursor instead of
            # restarting from the first window, so already-planned units are never
            # re-requested and never overwritten.
            cursor = snapshot.get("analysis_cursor")
            cursor = cursor if isinstance(cursor, dict) else {}
            resume_from_window = max(0, int(cursor.get("next_window_index") or 0))
            if resume_from_window > len(all_episode_specs):
                raise DomainRuleError(
                    "PIPELINE_CURSOR_INVALID", "续接游标已超出当前原稿范围，请重新发起分析"
                )
            remaining_specs = all_episode_specs[resume_from_window:]
            episode_specs = remaining_specs[:PIPELINE_EPISODE_BATCH_LIMIT]
            saved_episodes = _parse_json(_safe_col(row, "episodes_json", "[]"), [])
            saved_episodes = saved_episodes if isinstance(saved_episodes, list) else []
            # PR-03: the whole-drama assets and story memory a previous batch already
            # published travel into this batch's synthesis, so continuing analysis can
            # only accumulate them.
            saved_draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
            saved_draft = saved_draft if isinstance(saved_draft, dict) else {}
            existing_analysis = {
                "episodes": saved_episodes,
                "assets": saved_draft.get("assets") if isinstance(saved_draft.get("assets"), dict) else None,
                "story_bible": saved_draft.get("story_bible") if isinstance(saved_draft.get("story_bible"), dict) else None,
            }
            prior_windows = resume_from_window
            completed = min(len(saved_episodes), len(episode_specs))
            initial_percent = 18 + round(57 * (prior_windows + completed) / max(1, len(all_episode_specs)))
            progress(
                "STORY_PLANNING",
                (
                    f"AI 正在续接原稿；已处理 {prior_windows}/{len(all_episode_specs)} 个输入窗口"
                    if prior_windows
                    else f"AI 正在生成轻量分集提纲；已从检查点恢复 {completed}/{len(episode_specs)} 集"
                    if completed
                    else "AI 正在生成轻量分集提纲"
                ),
                initial_percent,
            )

            def episode_progress(done: int, total: int) -> None:
                percent = 18 + round(57 * (prior_windows + done) / max(1, len(all_episode_specs)))
                progress("STORY_PLANNING", f"AI 已规划 {prior_windows + done}/{len(all_episode_specs)} 个输入窗口", percent)

            def episode_checkpoint(items: list[dict[str, Any]], done: int, total: int) -> None:
                percent = 18 + round(57 * (prior_windows + done) / max(1, len(all_episode_specs)))
                checkpoint_coverage = self._source_coverage(
                    source_sha256=str(snapshot["source_sha256"]),
                    authorized_scope=authorized_scope,
                    all_specs=all_episode_specs,
                    selected_specs=episode_specs,
                    completed_count=done,
                    completed_window_count=prior_windows,
                    selected_window_offset=prior_windows,
                )
                self._update_run(
                    run_id,
                    stage="STORY_PLANNING",
                    stage_label=f"AI 已规划 {prior_windows + done}/{len(all_episode_specs)} 个输入窗口；检查点已保存",
                    progress_pct=percent,
                    episodes_count=len(saved_episodes) + done,
                    episodes_json=[*saved_episodes, *items],
                    draft_json={"source_coverage": checkpoint_coverage},
                )

            generated = self.ai_generation.generate(
                episode_specs=episode_specs,
                visual_style=run["visual_style"],
                target_seconds=run["target_episode_duration_seconds"],
                profile_version_id=run["capability_profile_version_id"],
                cancel_check=cancel_check,
                on_episode=episode_progress,
                # Only the units being continued are handed back as a checkpoint;
                # completed units are retained verbatim so a later batch cannot
                # rewrite names or memory that production already consumed.
                resume_episodes=[] if prior_windows else saved_episodes,
                on_episode_checkpoint=episode_checkpoint,
                # PR-03: a continuation is not a fresh drama.  The whole-drama
                # synthesis must see what earlier batches already established, or each
                # batch overwrites the assets and story memory of the ones before it.
                existing_analysis=existing_analysis,
            )
            progress("ASSET_EXTRACTION", "正在合并核心人物、场景、道具与连续性记忆", 82)
            ai_episodes = generated["episodes"]
            # PR-01: input windows are a model-batch unit, not an episode unit.  Every
            # window outline is aggregated into ONE outline per episode unit, both for
            # this batch and for the checkpoints a previous batch left behind, so a
            # long chapter can never mint duplicate EP numbers.
            planned = self._planned_units([int(spec["number"]) for spec in all_episode_specs])
            retained_units = self._planned_units(
                [int(item["number"]) for item in saved_episodes if isinstance(item, dict) and item.get("number")]
            )
            carried = [
                item for item in self._merge_unit_observations(
                    saved_episodes,
                    self._merge_unit_windows(saved_episodes, all_episode_specs),
                )
                if int(item["number"]) in set(retained_units)
            ]
            fresh_window_episodes = [
                item
                for item in ai_episodes
                if int(item.get("number") or 0) in {int(spec["number"]) for spec in episode_specs}
            ]
            fresh = self._merge_unit_observations(
                fresh_window_episodes,
                self._merge_unit_windows(fresh_window_episodes, all_episode_specs),
            )
            # A unit belongs to exactly one side: the earlier batches that already
            # settled it, or this batch.  That is what makes the merge idempotent and
            # keeps the numbering unique without de-duplicating any source range.
            retained_numbers = {int(item["number"]) for item in carried}
            merged_units = [
                *carried,
                *[row for row in fresh if int(row["number"]) not in retained_numbers],
            ]
            merged_units.sort(key=lambda row: int(row["number"]))
            plan_episodes = [
                {key: value for key, value in item.items() if key != "entity_observations"}
                for item in merged_units
            ]
            numbers = [int(item["number"]) for item in plan_episodes]
            if len(numbers) != len(set(numbers)):
                raise DomainRuleError(
                    "PIPELINE_PLAN_UNIT_DUPLICATED",
                    "分集规划出现重复编号，已拒绝生成会触发唯一约束的草案",
                    {"planned_episode_numbers": numbers},
                )
            if any(number not in planned for number in numbers):
                raise DomainRuleError(
                    "PIPELINE_PLAN_UNIT_UNKNOWN",
                    "分集规划包含不属于当前授权范围的单元编号",
                    {"planned_units": planned, "planned_episode_numbers": numbers},
                )
            assets = generated["assets"]
            bible = {
                **generated["story_bible"],
                "visual_style": run["visual_style"],
                "target_episode_duration_seconds": run["target_episode_duration_seconds"],
            }
            # Detailed scenes and shots are intentionally deferred to the
            # per-episode Agent, which receives only the selected source range.
            breakdowns: list[dict[str, Any]] = []
            shot_count = 0
            source_coverage = self._source_coverage(
                source_sha256=str(snapshot["source_sha256"]),
                authorized_scope=authorized_scope,
                all_specs=all_episode_specs,
                selected_specs=episode_specs,
                completed_count=len(ai_episodes),
                completed_window_count=prior_windows,
                selected_window_offset=prior_windows,
            )
            draft = {
                "schema_version": "pipeline.story-plan.v3",
                "source": {"document_version_id": source_id, "sha256": _sha(text), "character_count": len(text)},
                "source_coverage": source_coverage,
                "settings": {
                    "visual_style": run["visual_style"],
                    "target_episode_duration_seconds": run["target_episode_duration_seconds"],
                    "voice_preset": run["voice_preset"],
                },
                "story_plan": {"episodes": plan_episodes},
                "story_bible": bible,
                "assets": assets,
                "breakdowns": breakdowns,
                "generation": generated["metadata"],
            }
            quality = _pipeline_quality_report(draft)
            self._update_run(
                run_id,
                state="SUCCEEDED",
                stage="REVIEW_READY",
                stage_label=(
                    "本次原稿分析部分完成；请按续接位置继续"
                    if source_coverage["status"] == "PARTIAL"
                    else "授权原稿范围规划完成，正在准备分集制作"
                ),
                progress_pct=100,
                episodes_count=len(plan_episodes),
                characters_count=len(assets["characters"]),
                scenes_count=len(assets["scenes"]),
                props_count=len(assets["props"]),
                shots_count=shot_count,
                episodes_json=plan_episodes,
                assets_json=assets,
                draft_json=draft,
                quality_report_json=quality,
                extraction_mode="LLM",
                llm_model=generated["metadata"]["model"],
                llm_provider=generated["metadata"]["provider"],
                llm_error=None,
                error_message=None,
            )
            # Persist the durable analysis cursor. It is the only thing a later
            # ``continue-analysis`` command trusts about how far this run got, so a
            # replayed or concurrent command can be detected instead of redoing
            # work or skipping a window.
            settled_windows = prior_windows + len(episode_specs)
            with self.database.transaction() as connection:
                current = connection.execute(
                    "SELECT input_snapshot_json FROM pipeline_runs WHERE id=?", (run_id,)
                ).fetchone()
                current_snapshot = _parse_json(current["input_snapshot_json"], {})
                previous_cursor = current_snapshot.get("analysis_cursor")
                previous_cursor = previous_cursor if isinstance(previous_cursor, dict) else {}
                current_snapshot["analysis_cursor"] = {
                    "schema_version": "pipeline.analysis-cursor.v1",
                    "completed_window_count": settled_windows,
                    "next_window_index": settled_windows,
                    "total_window_count": len(all_episode_specs),
                    "source_sha256": str(snapshot["source_sha256"]),
                    "updated_at": _now(),
                    "previous_completed_window_count": int(
                        previous_cursor.get("completed_window_count") or 0
                    ),
                }
                connection.execute(
                    "UPDATE pipeline_runs SET input_snapshot_json=? WHERE id=?",
                    (_json(current_snapshot), run_id),
                )
            # The dependent apply Job cannot be claimed until this draft Job
            # succeeds. Freeze the exact generated draft now, so later edits
            # cannot silently widen the launch-time authorization.
            if snapshot.get("application_authorization", {}).get("endpoint") == "APPLY_SELECTED_SECTIONS":
                with self.database.transaction() as connection:
                    current = connection.execute(
                        "SELECT input_snapshot_json FROM pipeline_runs WHERE id=?", (run_id,)
                    ).fetchone()
                    current_snapshot = _parse_json(current["input_snapshot_json"], {})
                    current_authorization = dict(current_snapshot.get("application_authorization") or {})
                    current_authorization["authorized_draft_sha256"] = _sha(_json(draft))
                    current_snapshot["application_authorization"] = current_authorization
                    connection.execute(
                        "UPDATE pipeline_runs SET input_snapshot_json=? WHERE id=?",
                        (_json(current_snapshot), run_id),
                    )
            if report_progress:
                report_progress(
                    {
                        "phase": "REVIEW_READY",
                        "detail": (
                            "本次原稿分析部分完成"
                            if source_coverage["status"] == "PARTIAL"
                            else "授权原稿范围规划完成"
                        ),
                        "percent": 100,
                    }
                )
            return self.get_pipeline(run["project_id"], run_id)
        except DomainRuleError as error:
            if error.code != "JOB_CANCELLED":
                if (
                    "authorized_scope" in locals()
                    and "all_episode_specs" in locals()
                    and "episode_specs" in locals()
                    and "snapshot" in locals()
                ):
                    with self.database.connect() as connection:
                        checkpoint_row = connection.execute(
                            "SELECT episodes_json FROM pipeline_runs WHERE id=?", (run_id,)
                        ).fetchone()
                    checkpoint_items = (
                        _parse_json(checkpoint_row["episodes_json"], []) if checkpoint_row else []
                    )
                    completed_count = (
                        len(checkpoint_items) if isinstance(checkpoint_items, list) else 0
                    )
                    failed_coverage = self._source_coverage(
                        source_sha256=str(snapshot["source_sha256"]),
                        authorized_scope=authorized_scope,
                        all_specs=all_episode_specs,
                        selected_specs=episode_specs,
                        completed_count=completed_count,
                        completed_window_count=prior_windows,
                    selected_window_offset=prior_windows,
                    )
                    self._update_run(
                        run_id,
                        draft_json={"source_coverage": failed_coverage},
                        quality_report_json={
                            "status": "BLOCKED",
                            "blockers": ["当前分析窗口失败；失败窗口未计入已完成覆盖。"],
                            "warnings": [],
                            "checks": [
                                {
                                    "code": "SOURCE_COVERAGE_COMPLETE",
                                    "label": "授权原稿范围已完整处理",
                                    "passed": False,
                                }
                            ],
                        },
                    )
                self._update_run(
                    run_id, state="FAILED", stage="FAILED", stage_label="草案生成失败，可安全重试",
                    error_message=error.message,
                )
            raise
        except Exception as error:
            logger.exception("Story pipeline draft generation failed")
            self._update_run(
                run_id, state="FAILED", stage="FAILED", stage_label="草案生成失败，可安全重试",
                error_message=str(error),
            )
            raise

    @staticmethod
    def _upsert_creative_dossier(
        connection: Any,
        *,
        project_id: str,
        kind: str,
        name: str,
        item: dict[str, Any],
        run_id: str,
        now: str,
        actor: str,
    ) -> bool:
        code = _creative_asset_code(kind, name)
        entry = connection.execute(
            "SELECT * FROM creative_entries WHERE project_id=? AND kind=? AND code=?",
            (project_id, kind, code),
        ).fetchone()
        content = {**item, "pipeline_run_id": run_id, "media_generation_started": False}
        content_hash = _sha(_json(content))
        if entry is None:
            entry_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
            connection.execute(
                """INSERT INTO creative_entries
                (id,project_id,kind,code,title,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                (entry_id, project_id, kind, code, name, revision_id, now, now, actor),
            )
            connection.execute(
                """INSERT INTO creative_entry_revisions
                (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,
                 change_note,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,1,'v2')""",
                (revision_id, entry_id, _json(content), content_hash, f"应用 AI 完整文字建档 {run_id}", now, now, actor),
            )
            return True
        current = connection.execute(
            "SELECT content_hash FROM creative_entry_revisions WHERE id=?",
            (entry["current_revision_id"],),
        ).fetchone()
        if current is not None and str(current["content_hash"]) == content_hash:
            return False
        revision_id = str(uuid.uuid4())
        revision_no = int(connection.execute(
            "SELECT COALESCE(MAX(revision_no),0)+1 n FROM creative_entry_revisions WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()["n"])
        connection.execute(
            """INSERT INTO creative_entry_revisions
            (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,
             change_note,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,NULL,?,?,?,?,?,?,1,'v2')""",
            (
                revision_id, entry["id"], revision_no, entry["current_revision_id"],
                _json(content), content_hash, f"应用 AI 完整文字建档 {run_id}", now, now, actor,
            ),
        )
        connection.execute(
            "UPDATE creative_entries SET current_revision_id=?,title=?,updated_at=?,revision=revision+1 WHERE id=?",
            (revision_id, name, now, entry["id"]),
        )
        return True

    @staticmethod
    def _pipeline_application_impact(
        connection: Any,
        *,
        project_id: str,
        run_id: str,
        run_revision: int,
        draft: dict[str, Any],
        selected: list[str],
    ) -> dict[str, Any]:
        episode_rows = connection.execute(
            """SELECT e.id,e.code,e.number,e.title,e.revision,
                      (SELECT COUNT(*) FROM shots sh WHERE sh.episode_id=e.id AND sh.archived_at IS NULL) AS shot_count
               FROM episodes e JOIN seasons s ON s.id=e.season_id
               WHERE s.project_id=? ORDER BY e.number,e.id""",
            (project_id,),
        ).fetchall()
        existing_by_number = {int(item["number"]): item for item in episode_rows}
        proposed = draft.get("story_plan", {}).get("episodes", []) if isinstance(draft.get("story_plan"), dict) else []
        proposed_numbers = {int(item["number"]) for item in proposed if isinstance(item, dict) and item.get("number")}
        episodes = {"add": [], "update": [], "preserve": [], "skip": []}
        if "STORY_PLAN" in selected:
            for item in proposed:
                number = int(item["number"])
                existing = existing_by_number.get(number)
                if existing is None:
                    episodes["add"].append({"number": number, "code": str(item.get("code") or ""), "title": str(item.get("title") or "")})
                elif int(existing["shot_count"] or 0) > 0:
                    episodes["preserve"].append(
                        {
                            "number": number,
                            "code": str(existing["code"]),
                            "title": str(existing["title"]),
                            "reason": "已有制作镜头；标题、范围和镜头保持不变",
                        }
                    )
                else:
                    episodes["update"].append(
                        {
                            "number": number,
                            "code": str(existing["code"]),
                            "from_title": str(existing["title"]),
                            "to_title": str(item.get("title") or ""),
                        }
                    )
            episodes["skip"] = [
                {"number": int(item["number"]), "code": str(item["code"]), "title": str(item["title"])}
                for item in episode_rows
                if int(item["number"]) not in proposed_numbers
            ]

        bible = connection.execute(
            """SELECT current_revision_id,revision FROM creative_entries
               WHERE project_id=? AND kind='SERIES_BIBLE' AND code='SERIES_BIBLE_MAIN'""",
            (project_id,),
        ).fetchone()
        asset_reuse: list[str] = []
        asset_add: list[str] = []
        if "ASSET_PROPOSALS" in selected:
            for kind_key, kind in (("characters", "CHARACTER"), ("scenes", "SCENE"), ("props", "PROP")):
                for item in draft.get("assets", {}).get(kind_key, []):
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    existing = connection.execute(
                        """SELECT 1 FROM story_assets WHERE project_id=? AND kind=?
                           AND status='ACTIVE' AND lower(name)=lower(?) LIMIT 1""",
                        (project_id, kind, name),
                    ).fetchone()
                    (asset_reuse if existing else asset_add).append(f"{kind}:{name}")
        produced = [str(item["code"]) for item in episode_rows if int(item["shot_count"] or 0) > 0]
        context_changes = produced if produced and ({"STORY_BIBLE", "ASSET_PROPOSALS"} & set(selected)) else []
        payload = {
            "schema_version": "pipeline-apply-impact/v1",
            "project_id": project_id,
            "run_id": run_id,
            "run_revision": run_revision,
            "sections": selected,
            "episodes": episodes,
            "story_bible": {
                "will_create": "STORY_BIBLE" in selected and bible is None,
                "will_switch_current_revision": "STORY_BIBLE" in selected and bible is not None,
                "previous_revision_id": str(bible["current_revision_id"]) if bible else None,
            },
            "assets": {"reuse": sorted(asset_reuse), "add": sorted(asset_add)},
            "produced_episode_context_changes": context_changes,
            "requires_confirmation": bool(
                episodes["preserve"]
                or ("STORY_BIBLE" in selected and bible is not None)
                or context_changes
            ),
            "writes_performed": False,
        }
        payload["impact_sha256"] = _sha(_json(payload))
        return payload

    @staticmethod
    def _draft_revision_hash(draft: dict[str, Any]) -> str:
        """Stable identity of one draft revision's applicable content (PR-05)."""

        story_plan = draft.get("story_plan") if isinstance(draft.get("story_plan"), dict) else {}
        return _sha(
            _json(
                {
                    "episodes": story_plan.get("episodes") or [],
                    "story_bible": draft.get("story_bible") or {},
                    "assets": draft.get("assets") or {},
                }
            )
        )

    def preview_pipeline_apply(
        self,
        project_id: str,
        run_id: str,
        *,
        expected_revision: int,
        sections: list[str],
    ) -> dict[str, Any]:
        selected = list(dict.fromkeys(section.upper() for section in sections))
        if not selected or set(selected) - PIPELINE_SECTIONS:
            raise DomainRuleError("PIPELINE_APPLY_SECTIONS_INVALID", "请选择至少一个可应用内容")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
            if row is None:
                raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError("PIPELINE_REVISION_CONFLICT", "草案已更新，请刷新后再预览")
            if str(row["state"]) != "SUCCEEDED" or str(_safe_col(row, "apply_state", "NOT_APPLIED")) not in {"NOT_APPLIED", "APPLIED"}:
                raise DomainRuleError("PIPELINE_STATE_INVALID", "当前草案不能预览应用")
            draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
            # PR-05: an already-applied RUN may still preview a NEWER draft revision.
            # Only the identical revision is a no-op, and its diff is empty.
            applied_hash = self._effective_applied_revision_hash(row)
            draft_hash = self._draft_revision_hash(draft if isinstance(draft, dict) else {})
            already_applied_revision = bool(applied_hash) and applied_hash == draft_hash
            self._backfill_applied_watermark(row)
            quality = _pipeline_quality_report(draft)
            stored_quality = _parse_json(_safe_col(row, "quality_report_json", "{}"), {})
            if stored_quality.get("rule_version") != "pipeline-quality/v2":
                quality["checks"].append(
                    {
                        "code": "QUALITY_RULE_CURRENT",
                        "label": "草案使用当前质量规则复核",
                        "severity": "BLOCKER",
                        "applicable": True,
                        "passed": False,
                    }
                )
                quality["blockers"].append("旧版质量报告需要重新生成草案")
                quality["status"] = "BLOCKED"
            source_version_id = str(row["source_document_version_id"] or "")
            source = connection.execute(
                """SELECT v.text_sha256,v.extracted_text_rel,p.root_rel
                   FROM source_document_versions v
                   JOIN source_documents d ON d.id=v.source_document_id
                   JOIN projects p ON p.id=d.project_id
                   WHERE v.id=? AND d.project_id=?""",
                (source_version_id, project_id),
            ).fetchone()
            input_snapshot = _parse_json(row["input_snapshot_json"], {})
            draft_source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
            expected_source_sha = str(input_snapshot.get("source_sha256") or "")
            source_current = bool(
                source is not None
                and str(draft_source.get("document_version_id") or "") == source_version_id
                and str(draft_source.get("sha256") or "") == expected_source_sha
                and str(source["text_sha256"] or "") == expected_source_sha
            )
            if source_current:
                try:
                    source_path = controlled_path(
                        self.settings.projects_root / str(source["root_rel"]),
                        str(source["extracted_text_rel"] or ""),
                        must_exist=True,
                        require_file=True,
                        code="PIPELINE_SOURCE_CHANGED",
                    )
                    source_current = hashlib.sha256(source_path.read_bytes()).hexdigest() == expected_source_sha
                except (DomainRuleError, OSError):
                    source_current = False
            quality["checks"].append(
                {
                    "code": "SOURCE_CURRENT",
                    "label": "当前原稿仍与草案冻结版本一致",
                    "severity": "BLOCKER",
                    "applicable": True,
                    "passed": source_current,
                }
            )
            if not source_current:
                quality["blockers"].append("当前原稿已变化，不能应用旧草案")
                quality["status"] = "BLOCKED"
            impact = self._pipeline_application_impact(
                connection,
                project_id=project_id,
                run_id=run_id,
                run_revision=expected_revision,
                draft=draft,
                selected=selected,
            )
        return {
            "impact": impact,
            "quality_report": quality,
            "can_apply": not quality["blockers"] and not already_applied_revision,
            "apply_watermark": {
                "applied_revision_hash": applied_hash or None,
                "draft_revision_hash": draft_hash,
                "already_applied": already_applied_revision,
                "applied_episode_numbers": _parse_json(
                    _safe_col(row, "applied_episode_numbers_json", "[]"), []
                ),
            },
        }

    def apply_pipeline(
        self,
        project_id: str,
        run_id: str,
        *,
        expected_revision: int,
        sections: list[str],
        expected_impact_sha256: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        selected = list(dict.fromkeys(section.upper() for section in sections))
        invalid = sorted(set(selected) - PIPELINE_SECTIONS)
        if invalid or not selected:
            raise DomainRuleError("PIPELINE_APPLY_SECTIONS_INVALID", "请选择至少一个可应用内容", {"invalid": invalid})
        now = _now()
        created = {
            "episodes": 0,
            "bible_revisions": 0,
            "asset_proposals": 0,
            "creative_dossiers": 0,
            "breakdown_drafts": 0,
        }
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
            if row is None:
                raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在")
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError("PIPELINE_REVISION_CONFLICT", "草案已更新，请刷新后再应用")
            if str(row["state"]) != "SUCCEEDED":
                raise DomainRuleError("PIPELINE_STATE_INVALID", "只有生成完成的草案可以应用")
            draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
            applied_hash = self._effective_applied_revision_hash(row)
            applied_numbers = {
                int(item)
                for item in _parse_json(_safe_col(row, "applied_episode_numbers_json", "[]"), [])
                if isinstance(item, int)
            }
            draft_hash = self._draft_revision_hash(draft if isinstance(draft, dict) else {})
            if str(_safe_col(row, "apply_state", "NOT_APPLIED")) == "APPLIED" and applied_hash == draft_hash:
                # The identical revision applied twice is a genuine no-op, not a
                # second write of the same episodes (PR-05).
                raise DomainRuleError("PIPELINE_ALREADY_APPLIED", "该草案版本已经应用过")
            stored_quality = _parse_json(_safe_col(row, "quality_report_json", "{}"), {})
            if draft.get("schema_version") not in {"pipeline.story-draft.v2", "pipeline.story-plan.v3"}:
                raise DomainRuleError("PIPELINE_DRAFT_VERSION_UNSUPPORTED", "旧版草案不能安全应用，请生成新版本")
            if stored_quality.get("rule_version") != "pipeline-quality/v2":
                raise DomainRuleError("PIPELINE_QUALITY_VERSION_UNSUPPORTED", "旧版质量报告不能用于当前应用，请重新生成草案")
            quality = _pipeline_quality_report(draft)
            if quality.get("blockers"):
                raise DomainRuleError("PIPELINE_QUALITY_BLOCKED", "草案仍有阻断问题，暂不能应用")
            impact = self._pipeline_application_impact(
                connection,
                project_id=project_id,
                run_id=run_id,
                run_revision=expected_revision,
                draft=draft,
                selected=selected,
            )
            if impact["impact_sha256"] != expected_impact_sha256:
                raise DomainRuleError("PIPELINE_APPLY_IMPACT_CONFLICT", "应用影响已变化，请重新预览后确认")

            source_version_id = str(row["source_document_version_id"] or "").strip()
            source = connection.execute(
                """SELECT v.text_sha256,v.extracted_text_rel,p.root_rel FROM source_document_versions v
                JOIN source_documents d ON d.id=v.source_document_id
                JOIN projects p ON p.id=d.project_id
                WHERE v.id=? AND d.project_id=?""",
                (source_version_id, project_id),
            ).fetchone()
            committed_import = connection.execute(
                """SELECT i.id FROM import_sessions i
                WHERE i.project_id=? AND i.source_document_version_id=?
                AND EXISTS (SELECT 1 FROM audit_events ae
                  WHERE ae.action='IMPORT_SESSION_COMMITTED'
                  AND ae.subject_type='import_session' AND ae.subject_id=i.id)
                ORDER BY i.updated_at DESC,i.id DESC LIMIT 1""",
                (project_id, source_version_id),
            ).fetchone()
            if source is None or committed_import is None:
                raise DomainRuleError(
                    "PIPELINE_SOURCE_BINDING_REQUIRED",
                    "规划草案缺少可验证的已提交原稿版本，不能写入分集。",
                )
            input_snapshot = _parse_json(row["input_snapshot_json"], {})
            draft_source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
            expected_source_sha = str(input_snapshot.get("source_sha256") or "")
            if (
                str(draft_source.get("document_version_id") or "") != source_version_id
                or str(draft_source.get("sha256") or "") != expected_source_sha
                or str(source["text_sha256"] or "") != expected_source_sha
            ):
                raise DomainRuleError("PIPELINE_SOURCE_CHANGED", "规划草案的原稿身份已变化，不能应用")
            source_path = controlled_path(
                self.settings.projects_root / str(source["root_rel"]),
                str(source["extracted_text_rel"] or ""),
                must_exist=True,
                require_file=True,
                code="PIPELINE_SOURCE_CHANGED",
            )
            if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_source_sha:
                raise DomainRuleError("PIPELINE_SOURCE_CHANGED", "规划草案的原稿文件已变化，不能应用")
            source_binding = {
                "source_document_version_id": source_version_id,
                "import_session_id": str(committed_import["id"]),
                "text_sha256": str(source["text_sha256"] or ""),
            }

            episode_ids: dict[int, str] = {}
            if "STORY_PLAN" in selected or "SCRIPT_BREAKDOWN" in selected:
                season = connection.execute(
                    "SELECT id FROM seasons WHERE project_id=? ORDER BY display_order,number,id LIMIT 1", (project_id,)
                ).fetchone()
                if season is None:
                    season_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO seasons (id,project_id,number,display_order,code,title,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,1,1,'SEASON_001','第 1 季',?,?,?,1,'v2')""",
                        (season_id, project_id, now, now, actor),
                    )
                else:
                    season_id = str(season["id"])
                episode_rows = connection.execute(
                    "SELECT * FROM episodes WHERE season_id=? ORDER BY display_order,number,id", (season_id,)
                ).fetchall()
                for item in draft["story_plan"]["episodes"]:
                    number = int(item["number"])
                    existing = next((ep for ep in episode_rows if int(ep["number"]) == number), None)
                    if existing is not None:
                        episode_id = str(existing["id"])
                        episode_ids[number] = episode_id
                        # PR-05: an episode whose unit was ALREADY applied by an
                        # earlier revision keeps its committed title and range.  Only
                        # units this revision is newly adding may be written, so a
                        # continuation delta cannot rewrite what a user already
                        # confirmed (or what production already consumed).
                        if number in applied_numbers:
                            continue
                        if "STORY_PLAN" in selected:
                            shot = connection.execute("SELECT 1 FROM shots WHERE episode_id=? LIMIT 1", (episode_id,)).fetchone()
                            if shot is None:
                                connection.execute(
                                    """UPDATE episodes SET title=?,target_duration_ms=?,source_range_json=?,updated_at=?,revision=revision+1
                                    WHERE id=?""",
                                    (
                                        str(item["title"])[:200], int(row["target_episode_duration_seconds"]) * 1000,
                                        _json({"start_paragraph": item.get("source_start_paragraph"), "end_paragraph": item.get("source_end_paragraph"), **source_binding}),
                                        now, episode_id,
                                    ),
                                )
                        continue
                    episode_id = str(uuid.uuid4())
                    episode_ids[number] = episode_id
                    connection.execute(
                        """INSERT INTO episodes
                        (id,season_id,number,display_order,code,title,narrative_status,production_status,target_duration_ms,
                         source_range_json,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,'OUTLINE','NOT_STARTED',?,?,?,?,?,1,'v2')""",
                        (
                            episode_id, season_id, number, number, str(item["code"]), str(item["title"])[:200],
                            int(row["target_episode_duration_seconds"]) * 1000,
                            _json({"start_paragraph": item.get("source_start_paragraph"), "end_paragraph": item.get("source_end_paragraph"), **source_binding}),
                            now, now, actor,
                        ),
                    )
                    created["episodes"] += 1

            if "STORY_BIBLE" in selected:
                entry = connection.execute(
                    "SELECT * FROM creative_entries WHERE project_id=? AND kind='SERIES_BIBLE' AND code='SERIES_BIBLE_MAIN'",
                    (project_id,),
                ).fetchone()
                revision_id = str(uuid.uuid4())
                content = draft["story_bible"]
                content_hash = _sha(_json(content))
                if entry is None:
                    entry_id = str(uuid.uuid4())
                    revision_no = 1
                    parent_revision_id = None
                    connection.execute(
                        """INSERT INTO creative_entries
                        (id,project_id,kind,code,title,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,'SERIES_BIBLE','SERIES_BIBLE_MAIN','整剧故事总纲与世界观',?,?,?,?,1,'v2')""",
                        (entry_id, project_id, revision_id, now, now, actor),
                    )
                else:
                    entry_id = str(entry["id"])
                    parent_revision_id = str(entry["current_revision_id"]) if entry["current_revision_id"] else None
                    revision_no = int(connection.execute(
                        "SELECT COALESCE(MAX(revision_no),0)+1 n FROM creative_entry_revisions WHERE entry_id=?", (entry_id,)
                    ).fetchone()["n"])
                    connection.execute(
                        "UPDATE creative_entries SET current_revision_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (revision_id, now, entry_id),
                    )
                connection.execute(
                    """INSERT INTO creative_entry_revisions
                    (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,change_note,
                     created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,NULL,?,?,?, ?,?,?,1,'v2')""",
                    (revision_id, entry_id, revision_no, parent_revision_id, _json(content), content_hash, f"应用故事规划草案 {run_id}", now, now, actor),
                )
                created["bible_revisions"] += 1

            if "ASSET_PROPOSALS" in selected:
                for kind_key, kind in (("characters", "CHARACTER"), ("scenes", "SCENE"), ("props", "PROP")):
                    for item in draft["assets"].get(kind_key, []):
                        name = str(item.get("name") or "").strip()
                        if not name:
                            continue
                        existing = connection.execute(
                            "SELECT id FROM story_assets WHERE project_id=? AND kind=? AND status='ACTIVE' AND lower(name)=lower(?) LIMIT 1",
                            (project_id, kind, name),
                        ).fetchone()
                        if existing:
                            continue
                        asset_id = str(uuid.uuid4())
                        description = str(item.get("description") or item.get("introduction") or "").strip()
                        connection.execute(
                            """INSERT INTO story_assets
                            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                             created_at,updated_at,created_by,revision,schema_version)
                            VALUES (?,?,?,?,?,?,NULL,?,'ACTIVE',?,?,?,1,'v2')""",
                            (
                                asset_id, project_id, kind, _creative_asset_code(kind, name), name, description,
                                _json({
                                    "source": "story_pipeline",
                                    "pipeline_run_id": run_id,
                                    "importance": item.get("importance"),
                                    "visual_prompt": item.get("visual_prompt"),
                                    "text_dossier": item,
                                    "media_generation_started": False,
                                }),
                                now, now, actor,
                            ),
                        )
                        connection.execute(
                            """INSERT INTO audit_events
                            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                            VALUES (?,'writer','STORY_ASSET_AUTO_CREATED','story_asset',?,'AI 自动建立核心视觉资产',?)""",
                            (actor, asset_id, _json({"pipeline_run_id": run_id, "kind": kind})),
                        )
                        created["creative_dossiers"] += 1

            if "SCRIPT_BREAKDOWN" in selected:
                source_version_id = str(row["source_document_version_id"])
                import_session = connection.execute(
                    "SELECT id FROM import_sessions WHERE source_document_version_id=? ORDER BY created_at DESC LIMIT 1",
                    (source_version_id,),
                ).fetchone()
                if import_session is None:
                    raise DomainRuleError("PIPELINE_IMPORT_SESSION_MISSING", "原稿缺少导入会话，无法创建分场草稿")
                for item in draft["breakdowns"]:
                    episode_number = int(item["episode_number"])
                    # Distinct local name: the surrounding scope already binds
                    # ``episode_id`` to a non-optional id from the STORY_PLAN pass.
                    breakdown_episode_id = episode_ids.get(episode_number)
                    breakdown_payload = {
                        **item["draft"],
                        "episode_id": breakdown_episode_id,
                        "episode_number": episode_number,
                        "pipeline_run_id": run_id,
                    }
                    connection.execute(
                        """INSERT INTO script_breakdown_drafts
                        (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,
                         created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?,?,1,'v2')""",
                        (
                            str(uuid.uuid4()), project_id, source_version_id, str(import_session["id"]),
                            _json(breakdown_payload),
                            _json({
                                "source": "story_pipeline",
                                "run_id": run_id,
                                "generation_mode": "LLM_COMPLETE",
                                "model": draft.get("generation", {}).get("model"),
                                "provider": draft.get("generation", {}).get("provider"),
                                "profile_version_id": draft.get("generation", {}).get("profile_version_id"),
                                "pipeline_duration_contract_status": "PASS",
                                "target_duration_seconds": int(row["target_episode_duration_seconds"]),
                                "total_duration_seconds": sum(
                                    float(shot.get("duration_seconds", 0))
                                    for scene in item["draft"].get("scenes", [])
                                    for shot in scene.get("shots", [])
                                ),
                                "duration_tolerance_ratio": draft.get("generation", {}).get("duration_tolerance_ratio", 0.2),
                                "requires_human_apply": True,
                                "media_generation_started": False,
                            }),
                            now, now, actor,
                        ),
                    )
                    created["breakdown_drafts"] += 1

            updated = connection.execute(
                """UPDATE pipeline_runs SET apply_state='APPLIED',applied_sections_json=?,applied_at=?,
                applied_revision_hash=?,applied_episode_numbers_json=?,updated_at=?,revision=revision+1
                WHERE id=? AND revision=?""",
                (
                    _json(selected),
                    now,
                    draft_hash,
                    _json(sorted(applied_numbers | {
                        int(item["number"])
                        for item in (draft.get("story_plan", {}).get("episodes") or [])
                        if isinstance(item, dict) and item.get("number")
                    })),
                    now,
                    run_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise DomainRuleError("PIPELINE_APPLY_CONFLICT", "草案应用冲突，请刷新后重试")
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_PIPELINE_APPLIED','pipeline_run',?,'质量检查后应用全剧规划',?)""",
                (actor, run_id, _json({"sections": selected, "created": created})),
            )
        return {
            "run": self.get_pipeline(project_id, run_id),
            "created": created,
            "sections": selected,
            "impact": {**impact, "writes_performed": True},
        }
