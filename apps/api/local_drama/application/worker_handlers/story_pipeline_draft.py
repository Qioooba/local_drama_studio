"""Persistent worker entry point for isolated story-pipeline draft generation."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.domain.errors import DomainRuleError


def run_story_pipeline_draft_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    pipeline: PipelineOrchestratorService,
    atomic_writer: Callable[[Path, Any], None],
    cancel_check: Callable[[], bool],
    report_progress: Callable[..., bool],
) -> tuple[str, str]:
    snapshot = job.get("input_snapshot") or {}
    run_id = str(snapshot.get("run_id") or "").strip()
    project_id = str(snapshot.get("project_id") or "").strip()
    if not run_id or not project_id:
        raise DomainRuleError("PIPELINE_JOB_INPUT_INVALID", "故事规划草案任务缺少运行标识")
    if str(job.get("project_id") or "") != project_id or str(job.get("subject_id") or "") != run_id:
        raise DomainRuleError("PIPELINE_JOB_SCOPE_MISMATCH", "故事规划草案任务作用域不一致")

    heartbeat_stop = threading.Event()

    def keep_lease_alive() -> None:
        while not heartbeat_stop.wait(20):
            try:
                if report_progress({"detail": "故事规划草案仍在生成"}, force=True):
                    return
            except Exception:
                return

    heartbeat = threading.Thread(target=keep_lease_alive, name=f"story-pipeline-{run_id[:8]}", daemon=True)
    heartbeat.start()
    try:
        result = pipeline.execute_draft_generation(
            run_id,
            cancel_check=cancel_check,
            report_progress=lambda progress: report_progress(progress, force=True),
        )
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
    output_path = output_root / "story-pipeline-draft.json"
    atomic_writer(
        output_path,
        lambda target: target.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "draft_schema_version": result.get("draft", {}).get("schema_version"),
                    "quality_status": result.get("quality_report", {}).get("status"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        ),
    )
    return "STORY_PIPELINE_DRAFT", output_path.relative_to(work_root).as_posix()
