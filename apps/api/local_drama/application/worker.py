"""One-job-at-a-time local media worker backed by the persistent queue."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.dialogue import DialogueService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.timeline import TimelineService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class LocalMediaWorker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings)

    def _ffmpeg(self, args: list[str]) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).exists():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            result = subprocess.run([ffmpeg, *args], capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("MEDIA_WORKER_FAILED", "本地媒体 worker 执行失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("MEDIA_WORKER_FAILED", "本地媒体 worker 执行失败", {"stderr_redacted": result.stderr[-500:]})

    def _atomic_file(self, path: Path, writer: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".partial-{path.name}")
        try:
            writer(partial)
            os.replace(partial, path)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise

    def _run_media_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        media_version_id = str(snapshot.get("media_version_id", ""))
        if not media_version_id:
            raise DomainRuleError("JOB_INPUT_INVALID", "媒体 Job 缺少 media_version_id")
        item, source = self.media.content_path(media_version_id)
        if item["media_kind"] != "VIDEO":
            raise DomainRuleError("MEDIA_WORKER_INPUT_UNSUPPORTED", "当前 worker 只处理视频媒体")
        if job["type"] == "MEDIA_THUMBNAIL":
            output = output_root / "thumbnail.webp"
            self._atomic_file(
                output, lambda target: self._ffmpeg(["-i", str(source), "-frames:v", "1", "-vf", "scale=320:-1", "-c:v", "libwebp", "-y", str(target)])
            )
            return "THUMBNAIL", output.relative_to(self.settings.work_root).as_posix()
        if job["type"] == "MEDIA_PROXY":
            output = output_root / "proxy.mp4"
            self._atomic_file(
                output,
                lambda target: self._ffmpeg(
                    [
                        "-i",
                        str(source),
                        "-vf",
                        "scale=480:-2",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-movflags",
                        "+faststart",
                        "-y",
                        str(target),
                    ]
                ),
            )
            probe = self.media._probe(output, "VIDEO")
            if probe.get("probe_status") != "PASS":
                raise DomainRuleError("OUTPUT_INVALID", "proxy 输出无法通过 ffprobe")
            return "PROXY_VIDEO", output.relative_to(self.settings.work_root).as_posix()
        raise DomainRuleError("JOB_TYPE_UNSUPPORTED", "当前 worker 不支持该媒体 Job 类型", {"type": job["type"]})

    def _run_tts_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        text_revision_id = str(snapshot.get("text_revision_id", ""))
        voice_profile_version_id = str(snapshot.get("voice_profile_version_id", ""))
        with self.database.connect() as connection:
            text_revision = connection.execute(
                """SELECT dtr.*,s.project_id FROM dialogue_text_revisions dtr
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id JOIN episodes e ON e.id=dl.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE dtr.id=?""",
                (text_revision_id,),
            ).fetchone()
            voice = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (voice_profile_version_id,)).fetchone()
            profile = connection.execute(
                "SELECT * FROM execution_profile_versions WHERE id=?",
                (job["execution_profile_version_id"],),
            ).fetchone()
        if (
            text_revision is None
            or voice is None
            or profile is None
            or job["subject_type"] != "DIALOGUE_TEXT_REVISION"
            or str(job["subject_id"]) != text_revision_id
            or str(text_revision["project_id"]) != str(job["project_id"])
            or str(voice["project_id"]) != str(job["project_id"])
            or voice["status"] != "ACTIVE"
            or str(voice["provider_profile_version_id"]) != str(profile["id"])
            or profile["status"] != "PUBLISHED"
            or "TTS" not in str(profile["capability"]).upper()
            or str(snapshot.get("provider_kind")) != "WINDOWS_SAPI_LOCAL"
            or snapshot.get("network_allowed") is not False
            or str(snapshot.get("text_hash")) != str(text_revision["text_hash"])
            or str(snapshot.get("text")) != str(text_revision["text"])
            or str(snapshot.get("voice_ref")) != str(voice["voice_ref"])
        ):
            raise DomainRuleError("TTS_JOB_SNAPSHOT_INVALID", "TTS Job 快照与最新持久化文本、音色或 Published Profile 不匹配")
        voice_ref = str(voice["voice_ref"])
        if not voice_ref.startswith("sapi:") or not voice_ref.removeprefix("sapi:").strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "Windows SAPI Job 必须使用 sapi: 音色引用")
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if powershell is None:
            raise DomainRuleError("TTS_RUNTIME_UNAVAILABLE", "本机未找到 PowerShell/System.Speech runtime")
        output = output_root / "speech.wav"
        speech_rate = float(snapshot.get("speech_rate", 1.0))
        sapi_rate = max(-10, min(10, round((speech_rate - 1.0) * 10)))
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "try { $s.SelectVoice($env:LOCAL_DRAMA_TTS_VOICE); "
            "$s.Rate = [int]$env:LOCAL_DRAMA_TTS_RATE; "
            "$s.SetOutputToWaveFile($env:LOCAL_DRAMA_TTS_OUTPUT); "
            "$s.Speak($env:LOCAL_DRAMA_TTS_TEXT) } finally { $s.Dispose() }"
        )

        def synthesize(target: Path) -> None:
            environment = os.environ.copy()
            environment.update(
                {
                    "LOCAL_DRAMA_TTS_VOICE": voice_ref.removeprefix("sapi:").strip(),
                    "LOCAL_DRAMA_TTS_RATE": str(sapi_rate),
                    "LOCAL_DRAMA_TTS_OUTPUT": str(target),
                    "LOCAL_DRAMA_TTS_TEXT": str(text_revision["text"]),
                }
            )
            try:
                result = subprocess.run(
                    [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    env=environment,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 SAPI TTS 执行失败", {"reason": type(error).__name__}) from error
            if result.returncode != 0:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 SAPI TTS 执行失败", {"stderr_redacted": result.stderr[-500:]})

        self._atomic_file(output, synthesize)
        probe = self.media._probe(output, "AUDIO")
        try:
            duration_ms = round(float(probe.get("format", {}).get("duration")) * 1000)
        except (TypeError, ValueError):
            duration_ms = None
        if probe.get("probe_status") != "PASS" or duration_ms is None or duration_ms <= 0:
            raise DomainRuleError("TTS_OUTPUT_INVALID", "SAPI 输出未通过本机 FFprobe")
        return "TTS_AUDIO", output.relative_to(self.settings.work_root).as_posix()

    # ------------------------------------------------------------------
    # Declarative automation task executor (AUTOMATION_WORKFLOW_TASK).
    #
    # The workflow engine persists each batch item as a durable Job; the Job
    # input_snapshot intentionally carries no payload, so the executor reloads
    # the item payload from automation_workflow_run_tasks.item_json by task id.
    # Every action writes one JSON report artifact (kind AUTOMATION_TASK_REPORT)
    # and completes the Job successfully; the machine_check.status in the
    # report then drives the workflow run forward through step_run, which is
    # the only place HITL pauses/limits are decided.  Known business outcomes
    # (missing keyframes, missing timeline, missing delivery target, render
    # errors, ...) become report statuses; unexpected input problems raise
    # DomainRuleError so the Job itself fails like every other worker Job.
    # ------------------------------------------------------------------

    @staticmethod
    def _automation_report(status: str, machine_check: dict[str, Any], produced: dict[str, Any], summary: str) -> dict[str, Any]:
        return {"status": status, "machine_check": machine_check, "produced": produced, "summary": summary}

    @staticmethod
    def _automation_failure(code: str, detail: str) -> dict[str, Any]:
        machine_check = {"status": "FAIL", "ok": False, "code": code, "detail": detail}
        return {"status": "FAIL", "machine_check": machine_check, "produced": {}, "summary": detail}

    def _automation_episode(self, episode_id: str) -> Any:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT e.id, e.code, s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return row

    def _automation_keyframe_check(self, episode_id: str) -> tuple[dict[str, Any], int]:
        with self.database.connect() as connection:
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
        report = self._automation_report(str(machine_check["status"]), machine_check, {"checked_shots": len(shots), "missing_shot_count": len(missing)}, summary)
        return report, 0

    def _automation_tts_batch(self, episode_id: str, run_id: str, task_id: str) -> tuple[dict[str, Any], int]:
        dialogue = DialogueService(self.database, self.settings)
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
        return self._automation_report("PASS", machine_check, produced, summary), 0

    def _automation_render(self, episode_id: str) -> tuple[dict[str, Any], int]:
        with self.database.connect() as connection:
            timeline = connection.execute(
                "SELECT id, revision_no, status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        if timeline is None:
            return self._automation_failure("RENDER_NO_TIMELINE", "该集还没有时间线 revision，无法渲染"), 0
        timeline_service = TimelineService(self.database, self.settings)
        try:
            render = timeline_service.render_episode(str(timeline["id"]), actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
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
        report = self._automation_report(
            "PASS",
            machine_check,
            {"render_version_id": str(render["id"]), "rel_path": str(render["rel_path"]), "byte_size": byte_size},
            "整集渲染完成并登记 episode_render_versions",
        )
        return report, byte_size

    def _automation_delivery(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._automation_episode(episode_id)
        project_id = str(episode["project_id"])
        snapshot = ConfigurationService(self.database).inspect_project_configuration(project_id)
        target_version_id = snapshot.get("selected_delivery_target_version_id")
        if not target_version_id:
            machine_check = {"status": "SKIPPED", "ok": False, "code": "DELIVERY_NO_TARGET", "detail": "项目未选定交付目标版本，交付跳过"}
            return self._automation_report("SKIPPED", machine_check, {}, "交付跳过：项目未选定交付目标版本"), 0
        with self.database.connect() as connection:
            render = connection.execute(
                "SELECT id FROM episode_render_versions WHERE episode_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        if render is None:
            return self._automation_failure("DELIVERY_NO_RENDER", "该集还没有整集渲染版本，无法构建交付包"), 0
        timeline_service = TimelineService(self.database, self.settings)
        try:
            package = timeline_service.build_delivery(str(render["id"]), str(target_version_id), actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
        machine_check = {
            "status": "PASS",
            "ok": True,
            "delivery_package_id": str(package["id"]),
            "target_version_id": str(target_version_id),
            "rel_path": str(package.get("rel_path") or ""),
            "manifest_sha256": str(package.get("manifest_sha256") or ""),
            "package_status": str(package.get("status") or ""),
        }
        report = self._automation_report("PASS", machine_check, {"delivery_package_id": str(package["id"]), "rel_path": str(package.get("rel_path") or "")}, "交付包构建完成（机器预检 PASS，人工/平台审签仍为 PENDING）")
        return report, 0

    def _automation_subtitle(self, episode_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Placeholder-capable SUBTITLE action (not used by the v1 template).

        Cues are derived from the episode dialogue lines in order; timing is an
        estimated speaking-rate projection because the executor never fabricates
        ASR alignment.  The script-authority check in create_subtitle_revision
        still guards the text, so non-verbatim derived text fails closed with
        SUBTITLE_SCRIPT_AUTHORITY_MISMATCH instead of silently writing subtitles.
        """
        dialogue = DialogueService(self.database, self.settings)
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
            return self._automation_failure("SUBTITLE_NO_DIALOGUE", "该集没有可派生字幕的对白"), 0
        authority = {
            "text_authority": "SCRIPT",
            "source_document_version_id": str(payload.get("source_document_version_id") or ""),
        }
        timeline_service = TimelineService(self.database, self.settings)
        try:
            revision = timeline_service.create_subtitle_revision(episode_id, cues, authority=authority, actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
        machine_check = {
            "status": "PASS",
            "ok": True,
            "subtitle_revision_id": str(revision["id"]),
            "cue_count": len(cues),
            "timing_source": "ESTIMATED_SPEAKING_RATE",
        }
        report = self._automation_report("PASS", machine_check, {"subtitle_revision_id": str(revision["id"]), "cue_count": len(cues)}, "字幕 revision 创建完成（文本权威为剧本原文，时间为估算）")
        return report, 0

    def _run_automation_task(self, job: dict[str, Any], output_root: Path, worker_id: str) -> tuple[str, str, dict[str, Any], int]:
        snapshot = job["input_snapshot"]
        run_id = str(snapshot.get("automation_run_id", "") or "")
        task_id = str(snapshot.get("automation_task_id", "") or "")
        if not run_id or not task_id:
            raise DomainRuleError("JOB_INPUT_INVALID", "自动化任务 Job 缺少 automation_run_id/automation_task_id")
        with self.database.connect() as connection:
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
        if action == "KEYFRAME_CHECK":
            report, produced_extra = self._automation_keyframe_check(episode_id)
        elif action == "TTS_BATCH":
            report, produced_extra = self._automation_tts_batch(episode_id, run_id, task_id)
        elif action == "RENDER":
            report, produced_extra = self._automation_render(episode_id)
        elif action == "DELIVERY":
            report, produced_extra = self._automation_delivery(episode_id)
        elif action == "SUBTITLE":
            report, produced_extra = self._automation_subtitle(episode_id, payload)
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
        self._atomic_file(path, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"))
        relative = path.relative_to(self.settings.work_root).as_posix()
        produced_bytes = max(0, int(path.stat().st_size) + produced_extra)
        return "AUTOMATION_TASK_REPORT", relative, report, produced_bytes

    def _advance_automation_run(self, job: dict[str, Any], report: dict[str, Any], produced_bytes: int) -> str | None:
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
        service = AutomationWorkflowService(self.database)
        try:
            service.step_run(
                run_id,
                machine_context={"status": status, "machine_check": machine_check},
                produced_bytes=produced_bytes,
                actor="local-user",
            )
        except DomainRuleError as error:
            if error.code in {"AUTOMATION_RUN_NOT_RUNNING", "AUTOMATION_RUN_NOT_FOUND"}:
                return None  # run was paused/ended externally; nothing to advance
            return error.code
        return None

    def run_once(self, worker_id: str, channels: list[str] | None = None) -> dict[str, Any] | None:
        claim = self.jobs.claim(worker_id, channels or ["CPU"])
        if claim is None:
            return None
        job = claim["job"]
        attempt = claim["attempt"]
        attempt_id = str(attempt["id"])
        token = str(attempt["lease_token"])
        try:
            self.jobs.heartbeat(attempt_id, token, worker_id, progress={"phase": "RUNNING"})
            output_root = self.settings.work_root / "jobs" / str(job["id"])
            report: dict[str, Any] | None = None
            produced_bytes = 0
            if job["type"] == "CPU_TEST":
                output = output_root / "result.txt"
                self._atomic_file(output, lambda target: target.write_text(f"job={job['id']}\nworker={worker_id}\n", encoding="utf-8"))
                kind = "TEXT_RESULT"
                relative = output.relative_to(self.settings.work_root).as_posix()
            elif job["type"] in {"MEDIA_THUMBNAIL", "MEDIA_PROXY"}:
                kind, relative = self._run_media_job(job, output_root)
            elif job["type"] == "TTS_GENERATION":
                kind, relative = self._run_tts_job(job, output_root)
            elif job["type"] == "AUTOMATION_WORKFLOW_TASK":
                kind, relative, report, produced_bytes = self._run_automation_task(job, output_root, worker_id)
            else:
                raise DomainRuleError("JOB_TYPE_UNSUPPORTED", "当前本地 worker 不支持该 Job 类型", {"type": job["type"]})
            artifact = self.jobs.register_artifact(attempt_id, kind, relative)
            result = self.jobs.complete(attempt_id, token, worker_id, success=True)
            advance_error: str | None = None
            if report is not None:
                advance_error = self._advance_automation_run(job, report, produced_bytes)
            payload: dict[str, Any] = {"job": job, "attempt": attempt, "artifact": artifact, "result": result}
            if advance_error is not None:
                payload["advance_error"] = advance_error
            return payload
        except DomainRuleError as error:
            result = self.jobs.complete(attempt_id, token, worker_id, success=False, error_code=error.code, error_detail_redacted=error.message)
            return {"job": job, "attempt": attempt, "result": result, "error": error.code}

    def run_until_idle(self, worker_id: str, max_jobs: int = 100) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for _ in range(max_jobs):
            result = self.run_once(worker_id)
            if result is None:
                break
            results.append(result)
        return results
