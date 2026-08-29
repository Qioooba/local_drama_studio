"""TTS_GENERATION job handler: SAPI speech synthesis with deterministic headroom."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import TtsRuntimeError

FfmpegRunner = Callable[[list[str]], None]
AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


class WorkerPersistencePort(Protocol):
    """Minimum persistence abstraction used by TTS snapshot validation."""

    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...


class WorkerTtsRuntimePort(Protocol):
    """Local speech runtime contract consumed by TTS handlers."""

    def synthesize(
        self,
        *,
        voice: str,
        text: str,
        output: Path,
        rate: int,
        timeout: int,
    ) -> Any:  # pragma: no cover - protocol boundary
        ...


class WorkerVoxcpmRuntimePort(Protocol):
    """Offline VoxCPM2 subprocess port for zero-shot cloned speech."""

    def synthesize(
        self,
        text: str,
        output: Path,
        *,
        prompt_audio: Path | None = None,
        prompt_text: str | None = None,
    ) -> Any:  # pragma: no cover - protocol boundary
        ...


class WorkerTtsMediaOpsPort(Protocol):
    """Verified content access + output verification for TTS handlers."""

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:  # pragma: no cover - protocol boundary
        ...

    def probe_output(self, path: Path, kind: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


def run_tts_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    database: WorkerPersistencePort,
    tts_runtime: WorkerTtsRuntimePort,
    media_ops: WorkerTtsMediaOpsPort,
    run_ffmpeg: FfmpegRunner,
    atomic_writer: AtomicWriter,
    voxcpm_runtime: WorkerVoxcpmRuntimePort | None = None,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    text_revision_id = str(snapshot.get("text_revision_id", ""))
    voice_profile_version_id = str(snapshot.get("voice_profile_version_id", ""))
    provider_kind = str(snapshot.get("provider_kind", ""))
    with database.connect() as connection:
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
        or provider_kind not in {"WINDOWS_SAPI_LOCAL", "VOXCPM2_LOCAL"}
        or snapshot.get("network_allowed") is not False
        or str(snapshot.get("text_hash")) != str(text_revision["text_hash"])
        or str(snapshot.get("text")) != str(text_revision["text"])
        or str(snapshot.get("voice_ref")) != str(voice["voice_ref"])
    ):
        raise DomainRuleError("TTS_JOB_SNAPSHOT_INVALID", "TTS Job 快照与最新持久化文本、音色或 Published Profile 不匹配")
    voice_ref = str(voice["voice_ref"])
    text = str(text_revision["text"])
    if provider_kind == "WINDOWS_SAPI_LOCAL":
        if not voice_ref.startswith("sapi:") or not voice_ref.removeprefix("sapi:").strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "Windows SAPI Job 必须使用 sapi: 音色引用")
        raw_output = output_root / "speech.sapi.wav"
        speech_rate = float(snapshot.get("speech_rate", 1.0))
        sapi_rate = max(-10, min(10, round((speech_rate - 1.0) * 10)))

        def synthesize(target: Path) -> None:
            try:
                tts_runtime.synthesize(
                    voice=voice_ref.removeprefix("sapi:").strip(),
                    text=text,
                    output=target,
                    rate=sapi_rate,
                    timeout=120,
                )
            except TtsRuntimeError as error:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 SAPI TTS 执行失败", {"reason": type(error).__name__}) from error

    else:
        if voxcpm_runtime is None:
            raise DomainRuleError("TTS_VOXCPM_RUNTIME_UNAVAILABLE", "本机未配置 VoxCPM2 子进程运行时")
        if not voice_ref.startswith("voxcpm2:") or not voice_ref.removeprefix("voxcpm2:").strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "VoxCPM2 Job 必须使用 voxcpm2:<参考音频>[|<参考文本>] 音色引用")
        reference = voice_ref.removeprefix("voxcpm2:").strip()
        prompt_audio_ref, _, prompt_text = reference.partition("|")
        if not prompt_audio_ref.strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "VoxCPM2 音色引用缺少参考音频路径")
        if prompt_audio_ref.startswith("media:"):
            # Cloned voices pin the reference as a project media version; the
            # worker resolves it through integrity-checked content access.
            _meta, prompt_audio_path = media_ops.content_path(prompt_audio_ref.removeprefix("media:"))
            prompt_audio: Path | None = prompt_audio_path
        else:
            prompt_audio = Path(prompt_audio_ref.strip())
        raw_output = output_root / "speech.voxcpm2.wav"

        def synthesize(target: Path) -> None:
            try:
                voxcpm_runtime.synthesize(
                    text,
                    target,
                    prompt_audio=prompt_audio,
                    prompt_text=prompt_text.strip() or None,
                )
            except TtsRuntimeError as error:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 VoxCPM2 执行失败", {"reason": type(error).__name__}) from error

    output = output_root / "speech.wav"
    atomic_writer(raw_output, synthesize)
    try:
        # Local speech runtimes can peak just above the project's -1 dBFS
        # safety ceiling.  Freeze deterministic local headroom into the actual
        # Job artifact instead of asking a reviewer to approve a technically
        # failing WAV.
        atomic_writer(
            output,
            lambda target: run_ffmpeg(
                [
                    "-i",
                    str(raw_output),
                    "-filter:a",
                    "volume=-1.5dB",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(target),
                ]
            ),
        )
    finally:
        raw_output.unlink(missing_ok=True)
    probe = media_ops.probe_output(output, "AUDIO")
    try:
        duration_ms = round(float(probe.get("format", {}).get("duration")) * 1000)
    except (TypeError, ValueError):
        duration_ms = None
    if probe.get("probe_status") != "PASS" or duration_ms is None or duration_ms <= 0:
        raise DomainRuleError("TTS_OUTPUT_INVALID", "TTS 输出未通过本机 FFprobe")
    return "TTS_AUDIO", output.relative_to(work_root).as_posix()
