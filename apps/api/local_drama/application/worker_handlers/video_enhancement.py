"""VIDEO_ENHANCEMENT job handler: plan-hash-guarded deterministic media enhancement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from local_drama.application.worker_handlers._timeline_ports import VideoEnhancementJobPort
from local_drama.domain.errors import DomainRuleError

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


def run_video_enhancement_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    timeline_factory: Callable[[], VideoEnhancementJobPort],
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    media_version_id = str(snapshot.get("input_media_version_id") or "")
    recipe_id = str(snapshot.get("recipe_id") or "")
    plan_hash = str(snapshot.get("plan_hash") or "")
    parameters = snapshot.get("parameters")
    if (
        job["subject_type"] != "MEDIA_VERSION"
        or str(job["subject_id"]) != media_version_id
        or not recipe_id
        or not plan_hash
        or not isinstance(parameters, dict)
    ):
        raise DomainRuleError("ENHANCEMENT_JOB_SNAPSHOT_INVALID", "增强 Job 缺少不可变媒体、配方或计划输入")
    timeline = timeline_factory()
    current = timeline.plan_enhancement(media_version_id, recipe_id, parameters)
    if str(current["plan_hash"]) != plan_hash:
        raise DomainRuleError("ENHANCEMENT_PLAN_STALE", "增强入队后输入或配方已变化，请重新预检并提交")
    enhancement = timeline.run_enhancement(
        media_version_id,
        recipe_id,
        plan_hash,
        parameters,
        actor="enhancement-worker",
    )
    report = {
        "schema_version": "localdrama.enhancement-job-report.v1",
        "job_id": str(job["id"]),
        "enhancement_run_id": str(enhancement["id"]),
        "output_media_version_id": str(enhancement["output_media_version_id"]),
        "output_sha256": str(enhancement["output_sha256"]),
        "qc_passed": bool(enhancement.get("qc", {}).get("passed")),
        "local_only": True,
        "network_contacted": False,
    }
    output = output_root / "enhancement-report.json"
    atomic_writer(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
    return "VIDEO_ENHANCEMENT_REPORT", output.relative_to(work_root).as_posix()
