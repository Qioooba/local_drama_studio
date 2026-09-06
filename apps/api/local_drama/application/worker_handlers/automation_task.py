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

from ..keyframe_references import approved_keyframes_for_shots

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]
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

    def keyframe_generation(
        self, episode_id: str, run_id: str, task_id: str, *, candidate_count: int = 1,
    ) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...

    def video_generation(
        self,
        episode_id: str,
        run_id: str,
        task_id: str,
        *,
        target_take_count: int = 1,
        target_shot_ids: tuple[str, ...] | None = None,
        force_new_take: bool = False,
    ) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...

    def qc(
        self, episode_id: str, run_id: str, task_id: str, *, auto_select: bool = False
    ) -> tuple[dict[str, Any], int]:  # pragma: no cover - protocol boundary
        ...


class AutomationDialoguePort(Protocol):
    """Dialogue capabilities required by TTS batch / subtitle actions."""

    def submit_episode_tts_batch(
        self, episode_id: str, *, idempotency_key_prefix: str, actor: str = "local-user"
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def finalize_episode_tts_jobs(
        self, episode_id: str, *, auto_select: bool, actor: str = "episode-run-auto"
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

    def assemble_episode_timeline(
        self, episode_id: str, *, actor: str = "local-user"
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def plan_tts_subtitle_draft(
        self, episode_id: str, *, source_document_version_id: str | None = None, align_words: bool = False
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

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
            """SELECT s.id,s.code,se.project_id FROM shots s
            JOIN episodes e ON e.id=s.episode_id
            JOIN seasons se ON se.id=e.season_id
            WHERE s.episode_id=? AND s.archived_at IS NULL
            ORDER BY CAST(s.order_key AS REAL),s.code""",
            (episode_id,),
        ).fetchall()
        project_id = str(shots[0]["project_id"]) if shots else None
        approved = approved_keyframes_for_shots(
            connection,
            (str(shot["id"]) for shot in shots),
            project_id=project_id,
        )
        missing = [
            {"shot_id": str(shot["id"]), "shot_code": str(shot["code"])}
            for shot in shots if str(shot["id"]) not in approved
        ]
    if missing:
        summary = f"{len(missing)} 个镜头缺少已批准关键帧，等待人工确认"
        machine_check: dict[str, Any] = {
            "status": "NEEDS_HITL",
            "ok": False,
            "code": "APPROVED_KEYFRAME_REQUIRED",
            "detail": "镜头缺少未过期的人工批准关键帧",
            "checked_shots": len(shots),
            "missing_shots": missing,
        }
    else:
        machine_check = {
            "status": "PASS",
            "ok": True,
            "code": "APPROVED_KEYFRAMES_VERIFIED",
            "detail": "全部镜头关键帧均有未过期的人工批准证据",
            "checked_shots": len(shots),
            "missing_shots": [],
        }
        summary = "全部镜头关键帧已有人工批准"
    report = _automation_report(str(machine_check["status"]), machine_check, {"checked_shots": len(shots), "missing_shot_count": len(missing)}, summary)
    return report, 0


def _automation_tts_batch(dialogue: AutomationDialoguePort, episode_id: str, run_id: str, task_id: str) -> tuple[dict[str, Any], int]:
    prefix = f"automation:{run_id}:{task_id}"
    result = dialogue.submit_episode_tts_batch(episode_id, idempotency_key_prefix=prefix, actor="local-user")
    counts = {key: int(result["counts"].get(key, 0)) for key in ("submitted", "skipped", "failed")}
    machine_check = {"status": "PASS", "ok": True, "counts": counts, "job_count": counts["submitted"]}
    submitted = [
        {"line_id": str(item["line_id"]), "code": str(item["code"]), "job_id": str(item["job_id"])}
        for item in result["submitted"]
    ]
    produced = {
        "counts": counts,
        # "items" is the dependency-attach contract consumed by
        # advance_automation_run: the next workflow task must wait for the
        # actual TTS Jobs, not just for this submission report.
        "items": [{"submissions": submitted}],
        "submitted": submitted,
    }
    summary = f"整集 TTS 批量提交完成：提交 {counts['submitted']}、跳过 {counts['skipped']}、失败 {counts['failed']}"
    return _automation_report("PASS", machine_check, produced, summary), 0


def _automation_timeline_assembly(
    timeline_factory: Callable[[], AutomationTimelinePort],
    episode_id: str,
) -> tuple[dict[str, Any], int]:
    """Assemble (or reuse) the frozen episode timeline from adopted facts.

    BLOCKED means the current selections cannot build a timeline yet (missing
    adopted videos, integrity failures, ...); reporting FAIL lets step_run
    pause the run per its checkpoint policy instead of rendering a broken cut.
    """
    try:
        result = timeline_factory().assemble_episode_timeline(episode_id, actor="local-user")
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    status = str(result.get("status"))
    if status == "SKIPPED":
        machine_check = {"status": "SKIPPED", "ok": False, "code": "TIMELINE_ALREADY_CURRENT", "detail": "最新时间线仍与当前采用事实一致，跳过重组"}
        return _automation_report("SKIPPED", machine_check, {"timeline_revision_id": str(result.get("timeline_revision_id") or "")}, "时间线已是最新，跳过自动组装"), 0
    if status == "BLOCKED":
        blockers = result.get("blockers", [])
        machine_check = {"status": "FAIL", "ok": False, "code": "TIMELINE_ASSEMBLY_BLOCKED", "detail": "当前采用事实不足以组装时间线", "blockers": blockers}
        return _automation_report("FAIL", machine_check, {"blockers": blockers}, f"时间线自动组装被阻塞（{len(blockers)} 项）"), 0
    revision = result.get("timeline") or {}
    machine_check = {
        "status": "PASS",
        "ok": True,
        "timeline_revision_id": str(revision.get("id", "")),
        "revision_no": revision.get("revision_no"),
    }
    return _automation_report("PASS", machine_check, {"timeline_revision_id": str(revision.get("id", ""))}, "时间线已按当前采用事实自动组装并冻结"), 0


def _automation_tts_finalize(
    dialogue: AutomationDialoguePort,
    episode_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Register succeeded TTS Jobs and fill empty voice selections.

    TTS_BATCH only submits Jobs; without this finalize step the synthesized
    audio never becomes a registered candidate and the episode would render
    without dialogue.  Failures stay per-job: a single bad line parks the run
    via the report instead of losing the whole batch.
    """
    mode_policy = payload.get("mode_policy", {})
    auto_select = bool(mode_policy.get("auto_select_videos", False)) if isinstance(mode_policy, dict) else False
    result = dialogue.finalize_episode_tts_jobs(episode_id, auto_select=auto_select, actor="local-user")
    finalized = result.get("finalized", [])
    failures = result.get("failures", [])
    selected = result.get("auto_selected", [])
    produced = {"finalized": finalized, "auto_selected": selected, "failures": failures}
    if failures:
        machine_check = {"status": "FAIL", "ok": False, "code": "TTS_FINALIZE_FAILED", "failures": failures, "finalized_count": len(finalized)}
        return _automation_report("FAIL", machine_check, produced, f"TTS 收尾存在 {len(failures)} 个失败任务"), 0
    machine_check = {
        "status": "PASS",
        "ok": True,
        "succeeded_jobs": int(result.get("succeeded_jobs", 0)),
        "finalized_count": len(finalized),
        "auto_selected_count": len(selected),
    }
    summary = f"TTS 收尾完成：登记 {len(finalized)} 条候选"
    if auto_select and selected:
        summary += f"，自动采用 {len(selected)} 条"
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
    timeline_factory: Callable[[], AutomationTimelinePort],
    episode_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Create the episode subtitle revision from adopted TTS facts.

    The draft planner already derives line-level timing from selected TTS
    media durations and fails closed against script text drift
    (SUBTITLE_SCRIPT_AUTHORITY_MISMATCH); the executor only commits the draft.
    """
    requested_source = str(payload.get("source_document_version_id") or "").strip() or None
    try:
        draft = timeline_factory().plan_tts_subtitle_draft(episode_id, source_document_version_id=requested_source, align_words=True)
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    if draft["status"] == "BLOCKED" or not draft["cues"]:
        source_blockers = {"SUBTITLE_SOURCE_REQUIRED", "SUBTITLE_SOURCE_AMBIGUOUS"}
        blocker_codes = {str(blocker.get("code")) for blocker in draft["blockers"]}
        if draft["status"] == "BLOCKED" and blocker_codes and blocker_codes <= source_blockers:
            # A rough cut is a valid outcome without subtitles: missing script
            # authority is a configuration gap, not a production failure.
            machine_check = {"status": "SKIPPED", "ok": False, "code": "SUBTITLE_SOURCE_REQUIRED", "detail": "本集没有已应用的剧本权威，字幕跳过（粗剪不依赖字幕）"}
            return _automation_report("SKIPPED", machine_check, {"blockers": draft["blockers"]}, "字幕跳过：本集缺少剧本权威"), 0
        machine_check = {"status": "FAIL", "ok": False, "code": "SUBTITLE_DRAFT_BLOCKED", "detail": "字幕草稿无法构建", "blockers": draft["blockers"]}
        return _automation_report("FAIL", machine_check, {"blockers": draft["blockers"]}, "字幕草稿被阻塞，无法创建 revision"), 0
    try:
        revision = timeline_factory().create_subtitle_revision(episode_id, draft["cues"], authority=draft["authority"], actor="local-user")
    except DomainRuleError as error:
        return _automation_failure(error.code, error.message), 0
    machine_check = {
        "status": "PASS",
        "ok": True,
        "subtitle_revision_id": str(revision["id"]),
        "cue_count": len(draft["cues"]),
        "missing_count": len(draft["missing"]),
        "timing_authority": str(draft["timing_authority"]),
        "aligned_lines": int((draft.get("summary") or {}).get("aligned_lines") or 0),
        "source_document_version_id": draft.get("source_document_version_id"),
    }
    report = _automation_report(
        "PASS",
        machine_check,
        {"subtitle_revision_id": str(revision["id"]), "cue_count": len(draft["cues"]), "missing": draft["missing"]},
        "字幕 revision 创建完成（时间权威为已采用 TTS 媒体，文本权威为剧本原文）",
    )
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
    elif action == "KEYFRAME_GENERATION":
        mode_policy = payload.get("mode_policy", {})
        count = int(mode_policy.get("target_take_count", 1)) if isinstance(mode_policy, dict) else 1
        report, produced_extra = episode_worker_actions_factory().keyframe_generation(
            episode_id, run_id, task_id, candidate_count=max(1, min(4, count)),
        )
    elif action == "VIDEO_GENERATION":
        mode_policy = payload.get("mode_policy", {})
        target_take_count = int(mode_policy.get("target_take_count", 2)) if isinstance(mode_policy, dict) else 2
        report, produced_extra = episode_worker_actions_factory().video_generation(
            episode_id,
            run_id,
            task_id,
            target_take_count=target_take_count,
            target_shot_ids=tuple(str(item) for item in payload.get("target_shot_ids", []) if str(item).strip()),
            force_new_take=bool(payload.get("force_new_take", False)),
        )
    elif action == "QC":
        mode_policy = payload.get("mode_policy", {})
        auto_select = bool(mode_policy.get("auto_select_videos", False)) if isinstance(mode_policy, dict) else False
        report, produced_extra = episode_worker_actions_factory().qc(episode_id, run_id, task_id, auto_select=auto_select)
    elif action == "TTS_BATCH":
        report, produced_extra = _automation_tts_batch(dialogue_factory(), episode_id, run_id, task_id)
    elif action == "TTS_FINALIZE":
        report, produced_extra = _automation_tts_finalize(dialogue_factory(), episode_id, payload)
    elif action == "TIMELINE_ASSEMBLY":
        report, produced_extra = _automation_timeline_assembly(timeline_factory, episode_id)
    elif action == "RENDER":
        report, produced_extra = _automation_render(database, timeline_factory, episode_id)
    elif action == "DELIVERY":
        report, produced_extra = _automation_delivery(database, configuration_factory(), timeline_factory, episode_id)
    elif action == "SUBTITLE":
        report, produced_extra = _automation_subtitle(timeline_factory, episode_id, payload)
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
