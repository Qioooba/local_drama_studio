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
        }

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
        capability_profile_version_id: str | None = None,
        llm_config: dict[str, Any] | None = None,
        actor: str = "local-user",
        supersedes_run_id: str | None = None,
    ) -> dict[str, Any]:
        del auto_run_rendering
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
                "visual_style": visual_style,
                "target_episode_duration_seconds": target_episode_duration_seconds,
                "voice_preset": voice_preset,
                "capability_profile_version_id": capability_profile_version_id,
                "llm_config": {key: value for key, value in (llm_config or {}).items() if key != "api_key"},
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
        return self.get_pipeline(project_id, run_id)

    def get_pipeline(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
        if row is None:
            raise DomainRuleError("PIPELINE_RUN_NOT_FOUND", "草案运行记录不存在", {"run_id": run_id})
        return self._row_to_run(row)

    def get_latest_pipeline(self, project_id: str) -> dict[str, Any] | None:
        self._project(project_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

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
    def _episode_specs(text: str) -> list[dict[str, Any]]:
        paragraphs = source_paragraphs(text)
        chapters = source_chapters(paragraphs)
        specs: list[dict[str, Any]] = []
        if chapters:
            for index, chapter in enumerate(chapters[:60], start=1):
                start = int(chapter["start_paragraph"])
                end = int(chapter["end_paragraph"])
                # Source ranges are the same 1-based, inclusive paragraph
                # numbers exposed by the import API.
                body = "\n".join(item.text for item in paragraphs[start - 1:end])
                specs.append({
                    "number": index,
                    "code": f"EP{index:02d}",
                    "title": str(chapter.get("title") or f"第 {index} 集")[:160],
                    "summary": re.sub(r"\s+", " ", body)[:320],
                    "source_start_paragraph": start,
                    "source_end_paragraph": end,
                    "source_text": body,
                })
        else:
            paragraph_groups: list[tuple[int, int, list[Any]]] = []
            current: list[Any] = []
            current_chars = 0
            group_start = 1
            for paragraph in paragraphs:
                if not current:
                    group_start = int(paragraph.number)
                current.append(paragraph)
                current_chars += len(paragraph.text)
                if current_chars >= 3500:
                    paragraph_groups.append((group_start, int(paragraph.number), current))
                    current, current_chars = [], 0
            if current:
                paragraph_groups.append((group_start, int(current[-1].number), current))
            fallback_groups = paragraph_groups or [(1, max(1, len(paragraphs)), paragraphs)]
            for index, (start, end, group) in enumerate(fallback_groups[:60], start=1):
                body = "\n".join(item.text for item in group)
                specs.append({
                    "number": index, "code": f"EP{index:02d}", "title": f"第 {index} 集",
                    "summary": re.sub(r"\s+", " ", body)[:320],
                    "source_start_paragraph": start, "source_end_paragraph": end,
                    "source_text": body,
                })
        return specs or [{
            "number": 1, "code": "EP01", "title": "第 1 集", "summary": text[:320],
            "source_start_paragraph": 1, "source_end_paragraph": max(1, len(paragraphs)), "source_text": text,
        }]

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
            episode_specs = self._episode_specs(text)
            saved_episodes = _parse_json(_safe_col(row, "episodes_json", "[]"), [])
            saved_episodes = saved_episodes if isinstance(saved_episodes, list) else []
            completed = min(len(saved_episodes), len(episode_specs))
            initial_percent = 18 + round(57 * completed / max(1, len(episode_specs)))
            progress(
                "STORY_PLANNING",
                f"AI 正在生成轻量分集提纲；已从检查点恢复 {completed}/{len(episode_specs)} 集" if completed else "AI 正在生成轻量分集提纲",
                initial_percent,
            )

            def episode_progress(done: int, total: int) -> None:
                percent = 18 + round(57 * done / max(1, total))
                progress("STORY_PLANNING", f"AI 已规划 {done}/{total} 集", percent)

            def episode_checkpoint(items: list[dict[str, Any]], done: int, total: int) -> None:
                percent = 18 + round(57 * done / max(1, total))
                self._update_run(
                    run_id,
                    stage="STORY_PLANNING",
                    stage_label=f"AI 已规划 {done}/{total} 集；检查点已保存",
                    progress_pct=percent,
                    episodes_count=done,
                    episodes_json=items,
                )

            generated = self.ai_generation.generate(
                episode_specs=episode_specs,
                visual_style=run["visual_style"],
                target_seconds=run["target_episode_duration_seconds"],
                profile_version_id=run["capability_profile_version_id"],
                cancel_check=cancel_check,
                on_episode=episode_progress,
                resume_episodes=saved_episodes,
                on_episode_checkpoint=episode_checkpoint,
            )
            progress("ASSET_EXTRACTION", "正在合并核心人物、场景、道具与连续性记忆", 82)
            ai_episodes = generated["episodes"]
            plan_episodes = [
                {key: value for key, value in item.items() if key not in {"scenes", "entity_observations"}}
                for item in ai_episodes
            ]
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
            blockers: list[str] = []
            warnings: list[str] = []
            if len(episode_specs) >= 60:
                warnings.append("分集数量达到单次上限 60 集，请重点核对原稿章节边界。")
            quality = {
                "status": "BLOCKED" if blockers else "REVIEW_REQUIRED" if warnings else "READY",
                "blockers": blockers,
                "warnings": warnings,
                "checks": [
                    {"code": "SOURCE_FROZEN", "label": "原稿快照已冻结", "passed": True},
                    {"code": "AI_GENERATION_CONFIRMED", "label": "分集与核心资产由已配置大模型生成", "passed": True},
                    {"code": "EPISODES_PRESENT", "label": "已生成分集规划和原文范围", "passed": bool(plan_episodes)},
                    {"code": "CORE_CHARACTERS", "label": "已识别可复用核心人物", "passed": bool(assets["characters"])},
                    {"code": "CORE_SCENES", "label": "已识别可复用核心场景", "passed": isinstance(assets["scenes"], list)},
                    {"code": "EPISODE_DETAILS_DEFERRED", "label": "分场与镜头将在制作每集时按需生成", "passed": True},
                    {"code": "TEXT_ONLY", "label": "未启动图片、视频或媒体生成", "passed": True},
                    {"code": "NO_PRODUCTION_WRITES", "label": "生成阶段未写入正式镜头或资产", "passed": True},
                ],
            }
            draft = {
                "schema_version": "pipeline.story-plan.v3",
                "source": {"document_version_id": source_id, "sha256": _sha(text), "character_count": len(text)},
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
            self._update_run(
                run_id,
                state="SUCCEEDED",
                stage="REVIEW_READY",
                stage_label="全剧规划完成，正在准备分集制作",
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
            if report_progress:
                report_progress({"phase": "REVIEW_READY", "detail": "全剧规划完成", "percent": 100})
            return self.get_pipeline(run["project_id"], run_id)
        except DomainRuleError as error:
            if error.code != "JOB_CANCELLED":
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

    def apply_pipeline(
        self,
        project_id: str,
        run_id: str,
        *,
        expected_revision: int,
        sections: list[str],
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
            if str(_safe_col(row, "apply_state", "NOT_APPLIED")) == "APPLIED":
                raise DomainRuleError("PIPELINE_ALREADY_APPLIED", "该草案已经应用过")
            draft = _parse_json(_safe_col(row, "draft_json", "{}"), {})
            quality = _parse_json(_safe_col(row, "quality_report_json", "{}"), {})
            if draft.get("schema_version") not in {"pipeline.story-draft.v2", "pipeline.story-plan.v3"}:
                raise DomainRuleError("PIPELINE_DRAFT_VERSION_UNSUPPORTED", "旧版草案不能安全应用，请生成新版本")
            if quality.get("blockers"):
                raise DomainRuleError("PIPELINE_QUALITY_BLOCKED", "草案仍有阻断问题，暂不能应用")

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
                        if "STORY_PLAN" in selected:
                            shot = connection.execute("SELECT 1 FROM shots WHERE episode_id=? LIMIT 1", (episode_id,)).fetchone()
                            if shot is None:
                                connection.execute(
                                    """UPDATE episodes SET title=?,target_duration_ms=?,source_range_json=?,updated_at=?,revision=revision+1
                                    WHERE id=?""",
                                    (
                                        str(item["title"])[:200], int(row["target_episode_duration_seconds"]) * 1000,
                                        _json({"start_paragraph": item.get("source_start_paragraph"), "end_paragraph": item.get("source_end_paragraph")}),
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
                            _json({"start_paragraph": item.get("source_start_paragraph"), "end_paragraph": item.get("source_end_paragraph")}),
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
                    episode_id = episode_ids.get(episode_number)
                    breakdown_payload = {
                        **item["draft"],
                        "episode_id": episode_id,
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
                """UPDATE pipeline_runs SET apply_state='APPLIED',applied_sections_json=?,applied_at=?,updated_at=?,revision=revision+1
                WHERE id=? AND revision=? AND apply_state='NOT_APPLIED'""",
                (_json(selected), now, now, run_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise DomainRuleError("PIPELINE_APPLY_CONFLICT", "草案应用冲突，请刷新后重试")
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_PIPELINE_APPLIED','pipeline_run',?,'质量检查后应用全剧规划',?)""",
                (actor, run_id, _json({"sections": selected, "created": created})),
            )
        return {"run": self.get_pipeline(project_id, run_id), "created": created, "sections": selected}
