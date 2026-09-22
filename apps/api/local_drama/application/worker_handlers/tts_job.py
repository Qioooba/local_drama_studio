"""TTS_GENERATION job handler: local speech synthesis with an explicit parameter contract."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import TtsRuntimeError

FfmpegRunner = Callable[[list[str]], None]
AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


def _parameter_token(value: Any) -> Any:
    """Normalize a TTS parameter for default comparison.

    Strings compare case-insensitively (``NEUTRAL`` == ``neutral``); every other
    type keeps identity/equality semantics so ``1.0`` is not coerced to ``"1.0"``.
    """
    if isinstance(value, str):
        return value.strip().casefold()
    return value


@dataclass(frozen=True)
class TtsParameterCapability:
    """What one local TTS provider can actually do with a user-set parameter.

    ``mode`` is the honest answer the UI and the submit path both need:

    * ``NATIVE`` — the value is compiled into the runtime request.
    * ``POST_PROCESSING`` — the runtime has no native control, so this product
      applies a clearly labelled FFmpeg post-process (for example ``atempo``)
      and records that its output length is not the model's own length.
    * ``METADATA_ONLY`` — the value is a real, frozen product parameter that is
      deliberately NOT an audio control for this provider (it is persisted on
      the candidate and in the Job snapshot for provenance, and never sent to
      the runtime).  This is reported as such so no surface can imply the
      provider acted on it.
    * ``UNSUPPORTED`` — the parameter cannot be honored at all.  The UI must
      disable it with ``reason`` and the service must reject non-default values
      instead of accepting and silently ignoring them.
    """

    mode: str
    reason: str
    default: Any = None


@dataclass(frozen=True)
class TtsProviderParameterCapabilities:
    provider_kind: str
    parameters: Mapping[str, TtsParameterCapability] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_kind": self.provider_kind,
            "parameters": {
                name: {"mode": item.mode, "reason": item.reason, "default": item.default}
                for name, item in sorted(self.parameters.items())
            },
        }

    def applied_parameters(self) -> list[str]:
        """Parameters this provider genuinely turns into runtime/audio behaviour."""
        return sorted(name for name, item in self.parameters.items() if item.mode in {"NATIVE", "POST_PROCESSING"})

    def metadata_only_parameters(self) -> list[str]:
        """Frozen product parameters that are recorded but never sent to the runtime."""
        return sorted(name for name, item in self.parameters.items() if item.mode == "METADATA_ONLY")

    def requires_rejection(self, parameter: str, value: Any) -> bool:
        """True when ``value`` is a non-default setting the provider cannot honor.

        String values are compared case-insensitively: the product's canonical
        emotion is the uppercase ``NEUTRAL`` (see the dialogue API schema and the
        batch submit default), while a provider may declare its default in any
        casing. Treating ``NEUTRAL`` as a non-default request would reject the
        product's own default through the batch endpoint.
        """
        capability = self.parameters.get(parameter)
        if capability is None:
            return True
        if capability.mode != "UNSUPPORTED":
            return False
        return bool(_parameter_token(value) != _parameter_token(capability.default))


SAPI_PARAMETER_CAPABILITIES = TtsProviderParameterCapabilities(
    provider_kind="WINDOWS_SAPI_LOCAL",
    parameters={
        # System.Speech consumes rate as an integer -10..10; the product maps a
        # 0.5-2.0 multiplier onto that scale (see the handler below).
        "speech_rate": TtsParameterCapability("NATIVE", "Windows SAPI 通过 SSML/System.Speech Rate 原生支持语速", 1.0),
        # Emotion has never been a SAPI audio control, but it IS part of the
        # frozen candidate provenance the product records.  Reporting it as
        # METADATA_ONLY keeps that behaviour while stating plainly that SAPI does
        # not act on it, instead of implying the emotion changed the voice.
        "emotion": TtsParameterCapability("METADATA_ONLY", "Windows SAPI 没有情绪通道；情绪只作为候选元数据记录，不影响合成语音", "NEUTRAL"),
    },
)

VOXCPM2_PARAMETER_CAPABILITIES = TtsProviderParameterCapabilities(
    provider_kind="VOXCPM2_LOCAL",
    parameters={
        # VoxCPM2's ``generate`` has no speed/rate argument, so this product
        # applies a declared FFmpeg atempo post-process.  The applied value is
        # recorded in the attempt's parameter evidence.
        "speech_rate": TtsParameterCapability("POST_PROCESSING", "VoxCPM2 无原生语速参数；本机 FFmpeg 以 atempo 后处理并记录 applied_parameters", 1.0),
        # Same reasoning as SAPI: the emotion is a real frozen product value used
        # for candidate provenance, but VoxCPM2 has no emotion input, so it must
        # never be presented as having shaped the audio.
        "emotion": TtsParameterCapability("METADATA_ONLY", "VoxCPM2 运行时没有情绪输入通道；情绪只作为候选元数据记录，不影响合成语音", "NEUTRAL"),
    },
)


def tts_parameter_capabilities(provider_kind: str) -> TtsProviderParameterCapabilities:
    """Resolve the declared parameter capability for one local TTS provider."""
    if provider_kind == "VOXCPM2_LOCAL":
        return VOXCPM2_PARAMETER_CAPABILITIES
    if provider_kind == "WINDOWS_SAPI_LOCAL":
        return SAPI_PARAMETER_CAPABILITIES
    raise DomainRuleError("TTS_PROVIDER_UNSUPPORTED", f"未知的本机 TTS provider：{provider_kind}", {"provider_kind": provider_kind})


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
    """Offline VoxCPM2 subprocess port for zero-shot cloned speech.

    ``speed`` is the declared product speech-rate parameter.  A concrete
    adapter that has no native control simply does not declare the keyword; the
    handler then relies on the declared FFmpeg ``atempo`` post-process.
    """

    def synthesize(
        self,
        text: str,
        output: Path,
        *,
        prompt_audio: Path | None = None,
        prompt_text: str | None = None,
        speed: float | None = None,
    ) -> Any:  # pragma: no cover - protocol boundary
        ...


def _runtime_parameter_names(runtime: object) -> frozenset[str]:
    """Names the concrete runtime really accepts, so a real control is used.

    A stub or an older adapter that predates the speed/emotion channel simply
    does not advertise the name, and the handler then relies on the declared
    post-processing mode instead of inventing a parameter the runtime would
    reject.
    """
    try:
        signature = inspect.signature(runtime.synthesize)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return frozenset()
    names = {name for name in signature.parameters if name != "self"}
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        names.add("**kwargs")
    return frozenset(names)


def _atempo_chain(speed: float) -> str:
    """FFmpeg atempo respects a 0.5-2.0 factor per stage; chain longer factors."""
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


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
        or snapshot.get("synthesis_scope", "LOCAL_TEST_ONLY") != "LOCAL_TEST_ONLY"
        or snapshot.get("commercial_authorization", False) is not False
        or str(snapshot.get("text_hash")) != str(text_revision["text_hash"])
        or str(snapshot.get("text")) != str(text_revision["text"])
        or str(snapshot.get("voice_ref")) != str(voice["voice_ref"])
    ):
        raise DomainRuleError("TTS_JOB_SNAPSHOT_INVALID", "TTS Job 快照与最新持久化文本、音色或 Published Profile 不匹配")
    voice_ref = str(voice["voice_ref"])
    text = str(text_revision["text"])
    # The declared capability is the single authority for both branches: a
    # non-default value the provider cannot honor is rejected here, so a Job can
    # never be reported successful while a user-visible parameter was ignored.
    capabilities = tts_parameter_capabilities(provider_kind)
    requested_speech_rate = float(snapshot.get("speech_rate", 1.0))
    requested_emotion = str(snapshot.get("emotion", "neutral"))
    if capabilities.requires_rejection("emotion", requested_emotion):
        raise DomainRuleError(
            "TTS_PARAMETER_UNSUPPORTED",
            capabilities.parameters["emotion"].reason,
            {"provider_kind": provider_kind, "parameter": "emotion", "value": requested_emotion},
        )
    applied_parameters: dict[str, Any] = {
        "requested": {"speech_rate": requested_speech_rate, "emotion": requested_emotion},
        "provider_kind": provider_kind,
        "modes": {name: item.mode for name, item in capabilities.parameters.items()},
        # These parameters are recorded but never sent to the runtime.  Naming
        # them here is what stops any surface from implying the provider acted
        # on a value it has no channel for.
        "metadata_only": capabilities.metadata_only_parameters(),
    }
    raw_parameter_filter: str | None = None
    if provider_kind == "WINDOWS_SAPI_LOCAL":
        if not voice_ref.startswith("sapi:") or not voice_ref.removeprefix("sapi:").strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "Windows SAPI Job 必须使用 sapi: 音色引用")
        raw_output = output_root / "speech.sapi.wav"
        sapi_rate = max(-10, min(10, round((requested_speech_rate - 1.0) * 10)))
        applied_parameters["applied"] = {"speech_rate": requested_speech_rate, "sapi_rate": sapi_rate, "emotion": None}
        applied_parameters["emotion_channel"] = "NONE"

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
        # The runtime only receives a name it really declares; the concrete
        # LocalAiSubprocessRuntime adapter accepts ``speed`` and forwards it as
        # ``--speed``.  Either way the audible rate is enforced by the declared
        # atempo post-process below, and ``applied`` records both facts.
        runtime_parameters = _runtime_parameter_names(voxcpm_runtime)
        runtime_accepts_speed = "speed" in runtime_parameters or "**kwargs" in runtime_parameters
        applied_parameters["applied"] = {
            "speech_rate": requested_speech_rate,
            "runtime_speed_argument": runtime_accepts_speed,
            "post_processing": None,
        }
        applied_parameters["emotion_channel"] = "NONE"
        if requested_speech_rate != 1.0:
            raw_parameter_filter = _atempo_chain(requested_speech_rate)
            applied_parameters["applied"]["post_processing"] = "atempo"
            applied_parameters["applied"]["atempo_filters"] = raw_parameter_filter

        def synthesize(target: Path) -> None:
            kwargs: dict[str, Any] = {
                "prompt_audio": prompt_audio,
                "prompt_text": prompt_text.strip() or None,
            }
            if runtime_accepts_speed:
                kwargs["speed"] = requested_speech_rate
            try:
                voxcpm_runtime.synthesize(text, target, **kwargs)
            except TtsRuntimeError as error:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 VoxCPM2 执行失败", {"reason": type(error).__name__}) from error

    output = output_root / "speech.wav"
    atomic_writer(raw_output, synthesize)
    try:
        # Local speech runtimes can peak just above the project's -1 dBFS
        # safety ceiling.  Freeze deterministic local headroom into the actual
        # Job artifact instead of asking a reviewer to approve a technically
        # failing WAV.
        final_filter = "volume=-1.5dB" + (f",{raw_parameter_filter}" if raw_parameter_filter else "")
        atomic_writer(
            output,
            lambda target: run_ffmpeg(
                [
                    "-i",
                    str(raw_output),
                    "-filter:a",
                    final_filter,
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
