"""One-job-at-a-time local media worker backed by the persistent queue."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
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
            if job["type"] == "CPU_TEST":
                output = output_root / "result.txt"
                self._atomic_file(output, lambda target: target.write_text(f"job={job['id']}\nworker={worker_id}\n", encoding="utf-8"))
                kind = "TEXT_RESULT"
                relative = output.relative_to(self.settings.work_root).as_posix()
            elif job["type"] in {"MEDIA_THUMBNAIL", "MEDIA_PROXY"}:
                kind, relative = self._run_media_job(job, output_root)
            elif job["type"] == "TTS_GENERATION":
                kind, relative = self._run_tts_job(job, output_root)
            else:
                raise DomainRuleError("JOB_TYPE_UNSUPPORTED", "当前本地 worker 不支持该 Job 类型", {"type": job["type"]})
            artifact = self.jobs.register_artifact(attempt_id, kind, relative)
            result = self.jobs.complete(attempt_id, token, worker_id, success=True)
            return {"job": job, "attempt": attempt, "artifact": artifact, "result": result}
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
