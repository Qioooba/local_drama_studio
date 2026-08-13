"""One-job-at-a-time local media worker backed by the persistent queue."""

from __future__ import annotations

import os
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
