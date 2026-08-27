"""MEDIA_DERIVATIVE / MEDIA_THUMBNAIL / MEDIA_PROXY job handlers.

Pure derivative/thumbnail/proxy production against the media operations port;
the runner owns FFmpeg execution, progress and cancellation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError

FfmpegRunner = Callable[[list[str]], None]
AtomicWriter = Callable[[Path, Callable[[Path], None]], None]
DurationSetter = Callable[[int | None], None]


class WorkerMediaOpsPort(Protocol):
    """Media capabilities required by MEDIA_* worker handlers."""

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:  # pragma: no cover - protocol boundary
        ...

    def thumbnail(self, media_version_id: str, size: str = "small", frame: str = "poster") -> tuple[Path, str]:  # pragma: no cover - protocol boundary
        ...

    def filmstrip(self, media_version_id: str) -> tuple[Path, str]:  # pragma: no cover - protocol boundary
        ...

    def waveform(self, media_version_id: str) -> tuple[Path, str]:  # pragma: no cover - protocol boundary
        ...

    def proxy(self, media_version_id: str) -> tuple[Path, str]:  # pragma: no cover - protocol boundary
        ...

    def probe_output(self, path: Path, kind: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


def run_media_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    cache_root: Path,
    media_ops: WorkerMediaOpsPort,
    run_ffmpeg: FfmpegRunner,
    set_expected_duration: DurationSetter,
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    media_version_id = str(snapshot.get("media_version_id", ""))
    if not media_version_id:
        raise DomainRuleError("JOB_INPUT_INVALID", "媒体 Job 缺少 media_version_id")
    item, source = media_ops.content_path(media_version_id)
    set_expected_duration(int(item.get("duration_ms") or 0) or None)
    if job["type"] == "MEDIA_DERIVATIVE":
        derivative_kind = str(snapshot.get("kind") or "").upper()
        if str(snapshot.get("source_sha256") or "") != str(item["sha256"]):
            raise DomainRuleError("MEDIA_DERIVATIVE_SOURCE_STALE", "媒体派生任务的源版本指纹已变化")
        if derivative_kind == "THUMBNAIL":
            path, mime = media_ops.thumbnail(
                media_version_id,
                str(snapshot.get("size") or "small"),
                str(snapshot.get("frame") or "poster"),
            )
        elif derivative_kind == "FILMSTRIP":
            path, mime = media_ops.filmstrip(media_version_id)
        elif derivative_kind == "WAVEFORM":
            path, mime = media_ops.waveform(media_version_id)
        elif derivative_kind == "PROXY":
            path, mime = media_ops.proxy(media_version_id)
        else:
            raise DomainRuleError("MEDIA_DERIVATIVE_KIND_INVALID", "媒体派生任务类型无效", {"kind": derivative_kind})
        cache_root_resolved = cache_root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(cache_root_resolved) or not resolved.is_file() or resolved.is_symlink():
            raise DomainRuleError("MEDIA_DERIVATIVE_OUTPUT_INVALID", "媒体派生输出未安全落入缓存目录")
        report = {
            "schema_version": "localdrama.media-derivative-report.v1",
            "job_id": str(job["id"]),
            "media_version_id": media_version_id,
            "source_sha256": str(item["sha256"]),
            "kind": derivative_kind,
            "cache_rel_path": resolved.relative_to(cache_root_resolved).as_posix(),
            "mime_type": mime,
            "byte_size": resolved.stat().st_size,
            "local_only": True,
            "network_contacted": False,
        }
        output = output_root / "media-derivative-report.json"
        atomic_writer(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "MEDIA_DERIVATIVE_REPORT", output.relative_to(work_root).as_posix()
    if item["media_kind"] != "VIDEO":
        raise DomainRuleError("MEDIA_WORKER_INPUT_UNSUPPORTED", "当前 worker 只处理视频媒体")
    if job["type"] == "MEDIA_THUMBNAIL":
        output = output_root / "thumbnail.webp"
        atomic_writer(
            output, lambda target: run_ffmpeg(["-i", str(source), "-frames:v", "1", "-vf", "scale=320:-1", "-c:v", "libwebp", "-y", str(target)])
        )
        return "THUMBNAIL", output.relative_to(work_root).as_posix()
    if job["type"] == "MEDIA_PROXY":
        output = output_root / "proxy.mp4"
        atomic_writer(
            output,
            lambda target: run_ffmpeg(
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
        probe = media_ops.probe_output(output, "VIDEO")
        if probe.get("probe_status") != "PASS":
            raise DomainRuleError("OUTPUT_INVALID", "proxy 输出无法通过 ffprobe")
        return "PROXY_VIDEO", output.relative_to(work_root).as_posix()
    raise DomainRuleError("JOB_TYPE_UNSUPPORTED", "当前 worker 不支持该媒体 Job 类型", {"type": job["type"]})
