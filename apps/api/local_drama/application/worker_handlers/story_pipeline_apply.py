"""Durable continuation for an explicitly authorized story-pipeline apply command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.domain.errors import DomainRuleError


def run_story_pipeline_apply_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    pipeline: PipelineOrchestratorService,
    atomic_writer: Callable[[Path, Any], None],
) -> tuple[str, str]:
    snapshot = job.get("input_snapshot") or {}
    run_id = str(snapshot.get("run_id") or "").strip()
    project_id = str(snapshot.get("project_id") or "").strip()
    if not run_id or not project_id:
        raise DomainRuleError("PIPELINE_JOB_INPUT_INVALID", "规划应用续接任务缺少运行标识")
    if str(job.get("project_id") or "") != project_id or str(job.get("subject_id") or "") != run_id:
        raise DomainRuleError("PIPELINE_JOB_SCOPE_MISMATCH", "规划应用续接任务作用域不一致")

    result = pipeline.continue_authorized_application(run_id)
    output_path = output_root / "story-pipeline-apply.json"
    atomic_writer(
        output_path,
        lambda target: target.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "apply_state": result["run"]["apply_state"],
                    "sections": result.get("sections") or result["run"].get("applied_sections", []),
                    "production_session_id": (
                        ((result.get("production") or {}).get("session") or {}).get("id")
                    ),
                    "idempotent_replay": bool(result.get("idempotent_replay")),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        ),
    )
    return "STORY_PIPELINE_APPLY", output_path.relative_to(work_root).as_posix()
