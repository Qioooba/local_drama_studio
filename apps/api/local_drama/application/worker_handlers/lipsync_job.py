"""LIPSYNC_GENERATION job handler: LatentSync lip sync over verified media.

The snapshot pins the exact video and audio MediaVersions; both must still be
VERIFIED in the same project when the Job runs.  LatentSync is a local GPU
subprocess (settings.latentsync_python/latentsync_root); the Job claims the
PYTORCH GPU runtime through the standard type mapping, so it never runs
concurrently with Comfy or local TTS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


class LipsyncPersistencePort(Protocol):
    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...


class LipsyncRuntimePort(Protocol):
    def run_lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        inference_steps: int = 20,
        timeout: float = 3600,
    ) -> Any:  # pragma: no cover - protocol boundary
        ...


class LipsyncMediaPort(Protocol):
    """Verified content access + output probe for lipsync inputs/outputs."""

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:  # pragma: no cover - protocol boundary
        ...

    def probe_output(self, path: Path, kind: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


def run_lipsync_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    database: LipsyncPersistencePort,
    lipsync_runtime: LipsyncRuntimePort | None,
    media_ops: LipsyncMediaPort,
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    video_media_version_id = str(snapshot.get("video_media_version_id", ""))
    audio_media_version_id = str(snapshot.get("audio_media_version_id", ""))
    if not video_media_version_id or not audio_media_version_id:
        raise DomainRuleError("LIPSYNC_JOB_SNAPSHOT_INVALID", "唇形同步 Job 快照缺少视频或音频 MediaVersion")
    if lipsync_runtime is None:
        raise DomainRuleError("LIPSYNC_RUNTIME_UNAVAILABLE", "本机未配置 LatentSync 子进程运行时")
    video_meta, video_path = media_ops.content_path(video_media_version_id)
    audio_meta, audio_path = media_ops.content_path(audio_media_version_id)
    if (
        str(video_meta["project_id"]) != str(job["project_id"])
        or str(video_meta["media_kind"]).upper() != "VIDEO"
        or str(video_meta["integrity_status"]).upper() != "VERIFIED"
        or str(audio_meta["project_id"]) != str(job["project_id"])
        or str(audio_meta["media_kind"]).upper() != "AUDIO"
        or str(audio_meta["integrity_status"]).upper() != "VERIFIED"
        or str(video_meta["sha256"]) != str(snapshot.get("video_sha256"))
        or str(audio_meta["sha256"]) != str(snapshot.get("audio_sha256"))
    ):
        raise DomainRuleError("LIPSYNC_JOB_SNAPSHOT_INVALID", "唇形同步 Job 快照与当前媒体事实不一致")
    output = output_root / "lipsync.mp4"
    try:
        lipsync_runtime.run_lipsync(video_path=video_path, audio_path=audio_path, output_path=output)
    except RuntimeError as error:
        raise DomainRuleError("LIPSYNC_RUNTIME_FAILED", "本机 LatentSync 执行失败", {"reason": type(error).__name__}) from error
    if not output.is_file() or output.stat().st_size == 0:
        raise DomainRuleError("LIPSYNC_OUTPUT_MISSING", "LatentSync 未生成输出视频")
    probe = media_ops.probe_output(output, "VIDEO")
    try:
        duration_ms = round(float(probe.get("format", {}).get("duration")) * 1000)
    except (TypeError, ValueError):
        duration_ms = None
    if probe.get("probe_status") != "PASS" or duration_ms is None or duration_ms <= 0:
        raise DomainRuleError("LIPSYNC_OUTPUT_INVALID", "唇形同步输出未通过本机 FFprobe")
    return "LIPSYNC_VIDEO", output.relative_to(work_root).as_posix()
