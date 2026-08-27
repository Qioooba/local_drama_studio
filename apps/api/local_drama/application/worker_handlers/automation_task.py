"""AUTOMATION_WORKFLOW_TASK job handler plus its run-advance step.

The workflow engine persists each batch item as a durable Job; the Job
input_snapshot intentionally carries no payload, so the executor reloads the
item payload from automation_workflow_run_tasks.item_json by task id.  Every
action writes one JSON report artifact (kind AUTOMATION_TASK_REPORT) and
completes the Job successfully; the machine_check.status in the report then
drives the workflow run forward through step_run, which is the only place HITL
pauses/limits are decided.  Known business outcomes (missing keyframes,
missing timeline, missing delivery target, render errors, ...) become report
statuses; unexpected input problems raise DomainRuleError so the Job itself
fails like every other worker Job.  All application services are injected via
ports; this module constructs none of them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError

AtomicWriter = Callable[[Path, Callable[[Path], None]], None]
ProgressCallback = Callable[[dict[str, Any]], None]


class AutomationPersistencePort(Protocol):
    """Minimum persistence abstraction used by automation handlers."""

    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...


class FrontHalfActionPort(Protocol):
    """Front-half authority probes that never auto-apply creative facts."""

    ACTIONS: frozenset[str]

    def run(self, action: str, episode_id: str) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...


class EpisodeWorkerActionsPort(Protocol):
    """Episode production actions exposed to worker executions."""

    def video_generation(
        self, episode_id: str, run_id: str, task_id: str, *, target_take_count: int = 1
    ) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...

    def qc(self, episode_id: str, run_id: str, task_id: str) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...


class AutomationDialoguePort(Protocol):
    """Dialogue capabilities required by TTS batch / subtitle actions."""

    def submit_episode_tts_batch(
        self, episode_id: str, *, idempotency_key_prefix: str, actor: str = "local-user"
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def list_lines(self, episode_id: str) -> list[dict[str, Any]]:  # pragma: no cover - protocol boundary
        ...


class AutomationConfigurationPort(Protocol):
    """Project configuration inspection for delivery targets."""

    def inspect_project_configuration(self, project_id: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class AutomationTimelinePort(Protocol):
    """Timeline render/delivery/subtitle capabilities for automation actions."""

    def render_episode(
        self, timeline_revision_id: str, *, force_rerender: bool = False, actor: str = "local-user"
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def build_delivery(
        self,
        episode_render_version_id: str,
        target_version_id: str,
        brand_kit_id: str | None = None,
        watermark_profile_id: str | None = None,
        compliance_policy_id: str | None = None,
        *,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def create_subtitle_revision(
        self,
        episode_id: str,
        cues: list[dict[str, Any]],
        *,
        format: str = "SRT",
        authority: dict[str, Any],
        style: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class AutomationRunStepperPort(Protocol):
    """Declarative workflow run state machine step."""

    def step_run(
        self,
        run_id: str,
        *,
        machine_context: dict[str, Any] | None = None,
        produced_bytes: int = 0,
        expected_completed_job_id: str | None = None,
        additional_dependency_job_ids: list[str] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


def _automation_report(status: str, machine_check: dict[str, Any], produced: dict[str, Any], summary: str) -> dict[str, Any]:
    return {"status": status, "machine_check": machine_check, "produced": produced, "summary": summary}


def _automation_failure(code: str, detail: str) -> dict[str, Any]:
    machine_check = {"status": "FAIL", "ok": False, "code": code, "detail": detail}
    return {"status": "FAIL", "machine_check": machine_check, "produced": {}, "summary": detail}


def _automation_episode(database: AutomationPersistencePort, episode_id: str) -> Any:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT e.id, e.code, s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
            (episode_id,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
    return row


def _automation_keyframe_check(database: AutomationPersistencePort, episode_id: str) -> tuple[dict[str, Any], int]:
    with database.connect() as connection:
        shots = connection.execute(
            "SELECT id, code FROM shots WHERE episode_id=? ORDER BY CAST(order_key AS REAL), code",
            (episode_id,),
        ).fetchall()
        missing: list[dict[str, str]] = []
        for shot in shots:
            approved = connection.execute(
                """SELECT 1 FROM media_assets ma JOIN media_versions mv ON mv.id=ma.approved_version_id
                WHERE ma.owner_type='SHOT' AND ma.owner_id=? AND ma.purpose='KEYFRAME'
                AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                LIMIT 1""",
                (str(shot["id"]),),
            ).fetchone()
            if approved is None:
                missing.append({"shot_id": str(shot["id"]), "shot_code": str(shot["code"])})
    if missing:
        machine_check: dict[str, Any] = {"status": "NEEDS_HITL", "ok": False, "checked_shots": len(shots), "missing_shots": missing}
        summary = f"{len(missing)} 个镜头缺少已批准关键帧，等待人工确认"
    else:
        machine_check = {"status": "PASS", "ok": True, "checked_shots": len(shots), "missing_shots": []}
        summary = "全部镜头关键帧已有人工批准"
    report = _automation_report(str(machine_check["status"]), machine_check, {"checked_shots": len(shots), "missing_shot_count": len(missing)}, summary)
    return report, 0


def _automation_tts_batch(dialogue: AutomationDialoguePort, episode_id: str, run_id: str, task_id: str) -> tuple[dict[str, Any], int]:
    prefix = f"automation:{run_id}:{task_id}"
    result = dialogue.submit_episode_tts_batch(episode_id, idempotency_key_prefix=prefix, actor="local-user")
    counts = {key: int(result["counts"].get(key, 0)) for key in ("submitted", "skipped", "failed")}
    machine_check = {"status": "PASS", "ok": True, "counts": counts, "job_count": counts["submitted"]}
    produced = {
        "counts": counts,
        "submitted": [
            {"line_id": str(item["line_id"]), "code": str(item["code"]), "job_id": str(item["job_id"])}
            for item in result["submitted"]
        ],
    }
    summary = f"整集 TTS 批量提交完成：提交 {counts['submitted']}、跳过 {counts['skipped']}、失败 {counts['failed']}"
    return _automation_report("PASS", machine_check, produced, summary), 0


def _automation_render(
    database: AutomationPersistencePort,
    timeline_factory: Callable[[], AutomationTimelinePort],
    episode_id: str,
) -> tuple[dict[str, Any], int]:
    with database.connect() as connection:
        timeline = connection.execute(
            "SELECT id, revision_no, status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
    if timeline is None:
        return _automation_failure("RENDER_NO_TIMELINE", "该集还没有时间线 revision，无法渲染"), 0
    try:
        render = timeline_factory().render_episode(str(timeline["id"]), actor="local-user")
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    byte_size = int(render.get("byte_size") or 0)
    probe = render.get("probe") or {}
    machine_check = {
        "status": "PASS",
        "ok": True,
        "render_version_id": str(render["id"]),
        "timeline_revision_id": str(render["timeline_revision_id"]),
        "sha256": str(render["sha256"]),
        "byte_size": byte_size,
        "duration_ms": probe.get("duration_ms"),
    }
    report = _automation_report(
        "PASS",
        machine_check,
        {"render_version_id": str(render["id"]), "rel_path": str(render["rel_path"]), "byte_size": byte_size},
        "整集渲染完成并登记 episode_render_versions",
    )
    return report, byte_size


def _automation_delivery(
    database: AutomationPersistencePort,
    configuration: AutomationConfigurationPort,
    timeline_factory: Callable[[], AutomationTimelinePort],
    episode_id: str,
) -> tuple[dict[str, Any], int]:
    episode = _automation_episode(database, episode_id)
    project_id = str(episode["project_id"])
    snapshot = configuration.inspect_project_configuration(project_id)
    target_version_id = snapshot.get("selected_delivery_target_version_id")
    if not target_version_id:
        machine_check = {"status": "SKIPPED", "ok": False, "code": "DELIVERY_NO_TARGET", "detail": "项目未选定交付目标版本，交付跳过"}
        return _automation_report("SKIPPED", machine_check, {}, "交付跳过：项目未选定交付目标版本"), 0
    with database.connect() as connection:
        render = connection.execute(
            "SELECT id FROM episode_render_versions WHERE episode_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
    if render is None:
        return _automation_failure("DELIVERY_NO_RENDER", "该集还没有整集渲染版本，无法构建交付包"), 0
    try:
        package = timeline_factory().build_delivery(str(render["id"]), str(target_version_id), actor="local-user")
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    machine_check = {
        "status": "PASS",
        "ok": True,
        "delivery_package_id": str(package["id"]),
        "target_version_id": str(target_version_id),
        "rel_path": str(package.get("rel_path") or ""),
        "manifest_sha256": str(package.get("manifest_sha256") or ""),
        "package_status": str(package.get("status") or ""),
    }
    report = _automation_report("PASS", machine_check, {"delivery_package_id": str(package["id"]), "rel_path": str(package.get("rel_path") or "")}, "交付包构建完成（机器预检 PASS，人工/平台审签仍为 PENDING）")
    return report, 0


def _automation_subtitle(
    database: AutomationPersistencePort,
    dialogue: AutomationDialoguePort,
    timeline_factory: Callable[[], AutomationTimelinePort],
    episode_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Placeholder-capable SUBTITLE action (not used by the v1 template).

    Cues are derived from the episode dialogue lines in order; timing is an
    estimated speaking-rate projection because the executor never fabricates
    ASR alignment.  The script-authority check in create_subtitle_revision
    still guards the text, so non-verbatim derived text fails closed with
    SUBTITLE_SCRIPT_AUTHORITY_MISMATCH instead of silently writing subtitles.
    """
    lines = dialogue.list_lines(episode_id)
    cues: list[dict[str, Any]] = []
    cursor_us = 0
    for line in lines:
        revisions = line.get("text_revisions") or []
        if not revisions:
            continue
        text = str(revisions[-1]["text"]).strip()
        if not text:
            continue
        duration_us = max(1_000_000, len(text) * 200_000)
        cues.append({"start_us": cursor_us, "end_us": cursor_us + duration_us, "text": text})
        cursor_us += duration_us
    if not cues:
        return _automation_failure("SUBTITLE_NO_DIALOGUE", "该集没有可派生字幕的对白"), 0
    authority = {
        "text_authority": "SCRIPT",
        "source_document_version_id": str(payload.get("source_document_version_id") or ""),
    }
    try:
        revision = timeline_factory().create_subtitle_revision(episode_id, cues, authority=authority, actor="local-user")
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    machine_check = {
        "status": "PASS",
        "ok": True,
        "subtitle_revision_id": str(revision["id"]),
        "cue_count": len(cues),
        "timing_source": "ESTIMATED_SPEAKING_RATE",
    }
    report = _automation_report("PASS", machine_check, {"subtitle_revision_id": str(revision["id"]), "cue_count": len(cues)}, "字幕 revision 创建完成（文本权威为剧本原文，时间为估算）")
    return report, 0


def run_automation_task(
    job: dict[str, Any],
    output_root: Path,
    *,
    worker_id: str,
    work_root: Path,
    database: AutomationPersistencePort,
    front_half_actions_factory: Callable[[], FrontHalfActionPort],
    episode_worker_actions_factory: Callable[[], EpisodeWorkerActionsPort],
    dialogue_factory: Callable[[], AutomationDialoguePort],
    configuration_factory: Callable[[], AutomationConfigurationPort],
    timeline_factory: Callable[[], AutomationTimelinePort],
    atomic_writer: AtomicWriter,
) -> tuple[str, str, dict[str, Any], int]:
    snapshot = job["input_snapshot"]
    run_id = str(snapshot.get("automation_run_id", "") or "")
    task_id = str(snapshot.get("automation_task_id", "") or "")
    if not run_id or not task_id:
        raise DomainRuleError("JOB_INPUT_INVALID", "自动化任务 Job 缺少 automation_run_id/automation_task_id")
    with database.connect() as connection:
        task = connection.execute(
            "SELECT item_json FROM automation_workflow_run_tasks WHERE id=? AND run_id=?", (task_id, run_id)
        ).fetchone()
    if task is None:
        raise DomainRuleError("AUTOMATION_TASK_NOT_FOUND", "workflow 任务不存在", {"task_id": task_id})
    try:
        item = json.loads(str(task["item_json"] or "{}"))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "workflow 任务 item_json 不是合法 JSON") from error
    payload = item.get("payload", {}) if isinstance(item, dict) else {}
    if not isinstance(payload, dict):
        raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "workflow 任务 payload 必须是对象")
    action = str(payload.get("action", "") or "").strip()
    episode_id = str(payload.get("episode_id", "") or "").strip()
    if not action:
        raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "自动化任务 payload 缺少 action")
    if not episode_id:
        raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "自动化任务 payload 缺少 episode_id")
    managed_front_half = action != "KEYFRAME_CHECK" or bool(payload.get("front_half_managed"))
    front_half = front_half_actions_factory()
    if action in front_half.ACTIONS and managed_front_half:
        # Front-half Jobs never auto-apply/auto-approve creative facts.
        # The action service emits PASS/SKIPPED for existing authorities
        # or NEEDS_HITL with bounded evidence for the workflow gate.
        report, produced_extra = front_half.run(action, episode_id)
    elif action == "KEYFRAME_CHECK":
        # Frozen WHOLE_DRAMA v1 workflows retain their historical
        # approved_version authority.  New Episode Production snapshots
        # set front_half_managed and require an explicit ReviewDecision.
        report, produced_extra = _automation_keyframe_check(database, episode_id)
    elif action == "VIDEO_GENERATION":
        mode_policy = payload.get("mode_policy", {})
        target_take_count = int(mode_policy.get("target_take_count", 2)) if isinstance(mode_policy, dict) else 2
        report, produced_extra = episode_worker_actions_factory().video_generation(
            episode_id, run_id, task_id, target_take_count=target_take_count,
        )
    elif action == "QC":
        report, produced_extra = episode_worker_actions_factory().qc(episode_id, run_id, task_id)
    elif action == "TTS_BATCH":
        report, produced_extra = _automation_tts_batch(dialogue_factory(), episode_id, run_id, task_id)
    elif action == "RENDER":
        report, produced_extra = _automation_render(database, timeline_factory, episode_id)
    elif action == "DELIVERY":
        report, produced_extra = _automation_delivery(database, configuration_factory(), timeline_factory, episode_id)
    elif action == "SUBTITLE":
        report, produced_extra = _automation_subtitle(database, dialogue_factory(), timeline_factory, episode_id, payload)
    else:
        raise DomainRuleError("AUTOMATION_ACTION_UNSUPPORTED", "不支持的自动化任务 action", {"action": action})
    report = {
        "schema_version": "localdrama.automation-task-report.v1",
        "job_id": str(job["id"]),
        "automation_run_id": run_id,
        "automation_task_id": task_id,
        "ordinal": int(snapshot.get("ordinal", 0) or 0),
        "item_key": str(snapshot.get("item_key", "") or ""),
        "action": action,
        "episode_id": episode_id,
        "worker_id": worker_id,
        "created_at": datetime.now(UTC).isoformat(),
        **report,
    }
    path = output_root / "report.json"
    atomic_writer(path, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"))
    relative = path.relative_to(work_root).as_posix()
    produced_bytes = max(0, int(path.stat().st_size) + produced_extra)
    return "AUTOMATION_TASK_REPORT", relative, report, produced_bytes


def advance_automation_run(
    job: dict[str, Any],
    report: dict[str, Any],
    produced_bytes: int,
    *,
    workflow_steps: AutomationRunStepperPort,
) -> str | None:
    """Push the workflow run to its next task after this task Job succeeded.

    The machine_check.status from the report is the only input to the run
    state machine: PASS/SKIPPED continue, NEEDS_HITL/FAIL/FAILED/BLOCKED
    pause the run on the declarative conditions.  Returns a non-benign
    error code (attached to the result) instead of raising, because the Job
    is already SUCCEEDED and must not be rolled back by a run-level issue.
    """
    run_id = str(job["input_snapshot"].get("automation_run_id", "") or "")
    if not run_id:
        return None
    machine_check = report.get("machine_check", {})
    status = str(machine_check.get("status", "PASS"))
    produced = report.get("produced", {})
    dependency_job_ids: list[str] = []
    if isinstance(produced, dict):
        for item in produced.get("items", []):
            if not isinstance(item, dict):
                continue
            candidates = [item, *(item.get("submissions", []) if isinstance(item.get("submissions"), list) else [])]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("job_id"):
                    job_id = str(candidate["job_id"])
                    if job_id not in dependency_job_ids:
                        dependency_job_ids.append(job_id)
            for job_id_value in item.get("dependency_job_ids", []) if isinstance(item.get("dependency_job_ids"), list) else []:
                job_id = str(job_id_value)
                if job_id and job_id not in dependency_job_ids:
                    dependency_job_ids.append(job_id)
    try:
        workflow_steps.step_run(
            run_id,
            machine_context={"status": status, "machine_check": machine_check},
            produced_bytes=produced_bytes,
            expected_completed_job_id=str(job["id"]),
            additional_dependency_job_ids=dependency_job_ids,
            actor="local-user",
        )
    except DomainRuleError as error:
        if error.code in {"AUTOMATION_RUN_NOT_RUNNING", "AUTOMATION_RUN_NOT_FOUND"}:
            return None  # run was paused/ended externally; nothing to advance
        return error.code
    return None
