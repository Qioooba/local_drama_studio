"""SCRIPT_BREAKDOWN_LOCAL_LLM job handler.

The runner wraps this flow with the lease heartbeat thread; this module owns
only the business conversion of one import session into a draft + report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError


class LocalLLMBreakdownPort(Protocol):
    """Gateway capability required by SCRIPT_BREAKDOWN handlers."""

    def breakdown(
        self,
        session_id: str,
        profile_version_id: str,
        *,
        job_id: str,
        input_snapshot: dict[str, Any],
        on_progress: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def apply_generated_breakdown(self, draft_id: str, episode_id: str) -> dict[str, Any]: ...


AtomicWriter = Callable[[Path, Callable[[Path], object]], None]
ProgressCallback = Callable[[dict[str, Any]], None]


def run_script_breakdown_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    local_llm: LocalLLMBreakdownPort,
    atomic_writer: AtomicWriter,
    on_progress: ProgressCallback,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    session_id = str(snapshot.get("import_session_id") or "")
    profile_version_id = str(snapshot.get("profile_version_id") or "")
    automatic_apply = snapshot.get("automatic_apply") is True
    requires_human_action = snapshot.get("requires_human_action") is True
    if (
        job["subject_type"] != "IMPORT_SESSION"
        or str(job["subject_id"]) != session_id
        or str(job.get("execution_profile_version_id") or "") != profile_version_id
        or automatic_apply == requires_human_action
        or (automatic_apply and not snapshot.get("target_episode_id"))
    ):
        raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_INVALID", "AI 拆解 Job 缺少不可变源/Profile/应用策略快照")
    result = local_llm.breakdown(
        session_id,
        profile_version_id,
        job_id=str(job["id"]),
        input_snapshot=snapshot,
        on_progress=on_progress,
    )
    scenes = result.get("draft", {}).get("scenes", [])
    scene_count = len(scenes) if isinstance(scenes, list) else 0
    shot_count = sum(
        len(scene.get("shots", []))
        for scene in scenes
        if isinstance(scene, dict) and isinstance(scene.get("shots", []), list)
    )
    apply_result = None
    if automatic_apply:
        on_progress({"phase": "APPLYING_TO_EPISODE", "percent": 92, "draft_id": str(result["id"])})
        apply_result = local_llm.apply_generated_breakdown(
            str(result["id"]), str(snapshot["target_episode_id"])
        )
    report = {
        "schema_version": "localdrama.script-breakdown-job-report.v1",
        "job_id": str(job["id"]),
        "draft_id": str(result["id"]),
        "draft_status": str(result["status"]),
        "scene_count": scene_count,
        "shot_count": shot_count,
        "idempotent_replay": bool(result.get("idempotent_replay")),
        "automatic_apply": automatic_apply,
        "requires_human_action": requires_human_action,
        "application": apply_result,
        "local_only": True,
        "remote_provider_contacted": False,
    }
    output = output_root / "script-breakdown-report.json"
    atomic_writer(
        output,
        lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"),
    )
    on_progress({
        "phase": "EPISODE_PLAN_APPLIED" if automatic_apply else "DRAFT_READY",
        "percent": 100,
        "draft_id": str(result["id"]),
        **({"episode_id": str(snapshot["target_episode_id"])} if automatic_apply else {}),
    })
    return "SCRIPT_BREAKDOWN_REPORT", output.relative_to(work_root).as_posix()
