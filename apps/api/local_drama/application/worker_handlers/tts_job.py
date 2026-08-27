"""TTS_GENERATION job handler: SAPI speech synthesis with deterministic headroom."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import TtsRuntimeError

FfmpegRunner = Callable[[list[str]], None]
AtomicWriter = Callable[[Path, Callable[[Path], None]], None]


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


class WorkerTtsMediaOpsPort(Protocol):
    """Output verification capability required by TTS handlers."""

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
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    text_revision_id = str(snapshot.get("text_revision_id", ""))
    voice_profile_version_id = str(snapshot.get("voice_profile_version_id", ""))
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
    output = output_root / "speech.wav"
    sapi_output = output_root / "speech.sapi.wav"
    speech_rate = float(snapshot.get("speech_rate", 1.0))
    sapi_rate = max(-10, min(10, round((speech_rate - 1.0) * 10)))

    def synthesize(target: Path) -> None:
        try:
            tts_runtime.synthesize(
                voice=voice_ref.removeprefix("sapi:").strip(),
                text=str(text_revision["text"]),
                output=target,
                rate=sapi_rate,
                timeout=120,
            )
        except TtsRuntimeError as error:
            raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 SAPI TTS 执行失败", {"reason": type(error).__name__}) from error

    atomic_writer(sapi_output, synthesize)
    try:
        # SAPI can peak just above the project's -1 dBFS safety ceiling.
        # Freeze deterministic local headroom into the actual Job artifact
        # instead of asking a reviewer to approve a technically failing WAV.
        atomic_writer(
            output,
            lambda target: run_ffmpeg(
                [
                    "-i",
                    str(sapi_output),
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
        sapi_output.unlink(missing_ok=True)
    probe = media_ops.probe_output(output, "AUDIO")
    try:
        duration_ms = round(float(probe.get("format", {}).get("duration")) * 1000)
    except (TypeError, ValueError):
        duration_ms = None
    if probe.get("probe_status") != "PASS" or duration_ms is None or duration_ms <= 0:
        raise DomainRuleError("TTS_OUTPUT_INVALID", "SAPI 输出未通过本机 FFprobe")
    return "TTS_AUDIO", output.relative_to(work_root).as_posix()
