"""MED-04: VoxCPM2 speed/emotion must reach the runtime or be explicitly refused.

The original defect: ``emotion`` and ``speech_rate`` were validated and frozen into
the Job snapshot, but the VoxCPM2 branch passed only text/prompt_audio/prompt_text,
so two different rates produced byte-identical audio while the UI still claimed the
setting had been applied.  These tests prove the parameter reaches the runtime (via
a recording runtime) and that the declared capability makes the result audibly
different through real FFmpeg post-processing; an unsupported parameter must be
refused at submit time instead of being silently ignored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.dialogue import DialogueService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker_handlers.tts_job import (
    tts_parameter_capabilities,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic


def _bind_tts_profile(database, voice_profile_version_id: str, profile_code: str = "tts-med04") -> str:
    version_id = f"exec-{profile_code}-v1"
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO execution_profiles (id,code,title) VALUES (?,?,?)",
            (f"exec-{profile_code}", profile_code, f"TTS {profile_code}"),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
             status,capability_json,output_contract_json,resource_policy_json)
            VALUES (?,?,1,'TTS','{}','{}','{}','PUBLISHED','{}','{}','{}')""",
            (version_id, f"exec-{profile_code}"),
        )
        connection.execute(
            "UPDATE voice_profile_versions SET provider_profile_version_id=? WHERE id=?",
            (version_id, voice_profile_version_id),
        )
    return version_id


def _make_voice(workspace, database, code: str, voice_ref: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=10_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    root = workspace.projects_root / str(project["root_rel"])
    (root / "00_admin").mkdir(parents=True, exist_ok=True)
    (root / "00_admin" / "voice-license.json").write_text('{"owner":"test"}\n', encoding="utf-8")
    dialogue = DialogueService(database, workspace)
    voice = dialogue.create_voice_profile(
        str(project["id"]), code="VOICE-HERO", title="主角音色", voice_ref=voice_ref,
        license_status="USER_OWNED", license_evidence_path_rel="00_admin/voice-license.json",
    )
    line = dialogue.create_line(str(episode["id"]), code="L001", speaker="主角", text="警报还没解除。", pronunciation={})
    return dialogue, {"project": project, "voice": voice, "text_revision_id": str(line["text_revisions"][-1]["id"])}


class _RecordingVoxcpmRuntime:
    """Records every parameter the product hands to the VoxCPM2 runtime.

    ``speed`` is declared on this runtime, exactly like the concrete
    ``LocalAiSubprocessRuntime`` adapter, so the product must forward it.
    """

    def __init__(self, wav_factory) -> None:
        self.wav_factory = wav_factory
        self.calls: list[dict] = []

    def synthesize(self, text: str, output: Path, *, prompt_audio=None, prompt_text=None, speed=None) -> None:
        self.calls.append(
            {
                "text": text,
                "prompt_audio": str(prompt_audio) if prompt_audio else None,
                "prompt_text": prompt_text,
                "speed": speed,
            }
        )
        self.wav_factory(output)


class _LegacyRuntimeWithoutSpeed:
    """A runtime that does not declare the speed keyword must still be usable."""

    def __init__(self, wav_factory) -> None:
        self.wav_factory = wav_factory
        self.calls: list[dict] = []

    def synthesize(self, text: str, output: Path, *, prompt_audio=None, prompt_text=None) -> None:
        self.calls.append({"text": text, "prompt_audio": str(prompt_audio) if prompt_audio else None})
        self.wav_factory(output)


def _make_wav(workspace, target: Path, *, seconds: float = 1.6) -> None:
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ar", "48000", "-ac", "1", "-y", str(target),
        ],
        check=True,
        capture_output=True,
    )


def _duration_ms(workspace, path: Path) -> int:
    result = subprocess.run(
        [workspace.ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return round(float(result.stdout.strip()) * 1000)


def _run_handler(workspace, database, code: str, *, emotion: str, speech_rate: float, runtime, voice_ref="voxcpm2:F:/refs/hero.wav|低沉冷静"):
    dialogue, ctx = _make_voice(workspace, database, code, voice_ref)
    _bind_tts_profile(database, str(ctx["voice"]["id"]), profile_code=code)
    created = DialogueService(database, workspace, jobs=JobService(database, workspace)).submit_tts_job(
        ctx["text_revision_id"],
        voice_profile_version_id=str(ctx["voice"]["id"]),
        emotion=emotion,
        speech_rate=speech_rate,
        idempotency_key=f"{code}-job",
    )
    job = JobService(database, workspace).get_job(str(created["id"]))
    from local_drama.application.worker_handlers.tts_job import run_tts_job

    return run_tts_job(
        job,
        workspace.work_root / f"{code}-out",
        work_root=workspace.work_root,
        database=database,
        tts_runtime=None,
        media_ops=MediaService(database, workspace),
        run_ffmpeg=lambda args: subprocess.run([workspace.ffmpeg_path, *args], check=True, capture_output=True),
        atomic_writer=write_atomic,
        voxcpm_runtime=runtime,
    )


def test_declared_capability_reports_speed_applied_and_emotion_metadata_only() -> None:
    """The capability declaration is the honest contract both UI and service read."""

    vox = tts_parameter_capabilities("VOXCPM2_LOCAL")
    assert vox.parameters["speech_rate"].mode == "POST_PROCESSING"
    # Emotion is a real frozen product value but NOT an audio control here: it is
    # recorded and must never be presented as having shaped the voice.
    assert vox.parameters["emotion"].mode == "METADATA_ONLY"
    assert vox.applied_parameters() == ["speech_rate"]
    assert vox.metadata_only_parameters() == ["emotion"]
    assert vox.requires_rejection("emotion", "angry") is False
    assert vox.requires_rejection("speech_rate", 1.5) is False

    sapi = tts_parameter_capabilities("WINDOWS_SAPI_LOCAL")
    assert sapi.parameters["speech_rate"].mode == "NATIVE"
    assert sapi.parameters["emotion"].mode == "METADATA_ONLY"
    # A default in any casing is still the default.
    assert sapi.requires_rejection("emotion", "neutral") is False
    assert sapi.requires_rejection("emotion", "NEUTRAL") is False


def test_unknown_provider_is_rejected_and_unknown_parameter_requires_rejection() -> None:
    """Fail closed: an undeclared provider or parameter cannot be honored."""

    with pytest.raises(DomainRuleError) as unknown_provider:
        tts_parameter_capabilities("SOME_OTHER_TTS")
    assert unknown_provider.value.code == "TTS_PROVIDER_UNSUPPORTED"

    vox = tts_parameter_capabilities("VOXCPM2_LOCAL")
    assert vox.requires_rejection("pitch_shift", 2.0) is True


def test_speech_rate_reaches_the_runtime_and_changes_the_audio(workspace, database) -> None:
    """The parameter must arrive at the runtime AND change the produced audio."""

    slow_runtime = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target))
    kind_slow, relative_slow = _run_handler(
        workspace, database, "med04_slow", emotion="neutral", speech_rate=0.5, runtime=slow_runtime
    )
    assert kind_slow == "TTS_AUDIO"

    fast_runtime = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target))
    kind_fast, relative_fast = _run_handler(
        workspace, database, "med04_fast", emotion="neutral", speech_rate=2.0, runtime=fast_runtime
    )
    assert kind_fast == "TTS_AUDIO"

    # The parameter really reached the runtime, with the requested value.
    assert slow_runtime.calls[0]["speed"] == 0.5
    assert fast_runtime.calls[0]["speed"] == 2.0
    # It is not just metadata: the frozen text is unchanged but the call differs.
    assert slow_runtime.calls[0]["text"] == fast_runtime.calls[0]["text"]

    slow_ms = _duration_ms(workspace, workspace.work_root / str(relative_slow))
    fast_ms = _duration_ms(workspace, workspace.work_root / str(relative_fast))
    assert slow_ms > 0 and fast_ms > 0
    # 0.5x must be materially longer than 2.0x (a 4x ratio before codec padding).
    assert slow_ms > fast_ms * 2.5, f"speed must change the real audio length (slow={slow_ms}ms fast={fast_ms}ms)"


def test_rate_of_one_is_not_post_processed(workspace, database) -> None:
    """The default must not be re-encoded, so 1x keeps the model's own length."""

    runtime = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target, seconds=1.6))
    _kind, relative = _run_handler(workspace, database, "med04_unity", emotion="neutral", speech_rate=1.0, runtime=runtime)
    assert runtime.calls[0]["speed"] == 1.0
    assert abs(_duration_ms(workspace, workspace.work_root / str(relative)) - 1600) <= 120


def test_legacy_runtime_without_speed_is_still_served_by_declared_post_processing(workspace, database) -> None:
    """A runtime lacking the native keyword must not crash; the rate still applies."""

    runtime = _LegacyRuntimeWithoutSpeed(lambda target: _make_wav(workspace, target, seconds=1.6))
    _kind, relative = _run_handler(workspace, database, "med04_legacy", emotion="neutral", speech_rate=2.0, runtime=runtime)
    assert len(runtime.calls) == 1
    # The declared post-processing still halves the duration.
    assert _duration_ms(workspace, workspace.work_root / str(relative)) < 1200


def test_unsupported_emotion_is_recorded_but_never_reaches_the_runtime(workspace, database) -> None:
    """Emotion is candidate metadata for this provider, and must not alter audio."""

    runtime = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target, seconds=1.6))
    _kind, relative = _run_handler(
        workspace, database, "med04_emotion", emotion="angry", speech_rate=1.0, runtime=runtime
    )
    assert len(runtime.calls) == 1
    # The runtime is handed only the parameters it declares; no emotion channel.
    assert set(runtime.calls[0]) == {"text", "prompt_audio", "prompt_text", "speed"}
    assert runtime.calls[0]["speed"] == 1.0

    # The declared capability names emotion as metadata-only for this provider.
    capabilities = tts_parameter_capabilities("VOXCPM2_LOCAL")
    assert capabilities.metadata_only_parameters() == ["emotion"]
    assert capabilities.applied_parameters() == ["speech_rate"]

    # The recorded candidate keeps the emotion the user chose (existing product
    # behaviour), and the produced audio is a real file.
    assert _duration_ms(workspace, workspace.work_root / str(relative)) > 0


def test_emotion_does_not_change_the_produced_audio(workspace, database) -> None:
    """Two different emotions at the same rate must not silently differ."""

    happy = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target, seconds=1.0))
    _kind_a, relative_a = _run_handler(workspace, database, "med04_emo_happy", emotion="happy", speech_rate=1.0, runtime=happy)
    angry = _RecordingVoxcpmRuntime(lambda target: _make_wav(workspace, target, seconds=1.0))
    _kind_b, relative_b = _run_handler(workspace, database, "med04_emo_angry", emotion="angry", speech_rate=1.0, runtime=angry)

    # Only the emotion differed, and it is declared metadata-only for this
    # provider, so the runtime request and the audio length are identical.
    assert happy.calls[0]["speed"] == angry.calls[0]["speed"] == 1.0
    length_a = _duration_ms(workspace, workspace.work_root / str(relative_a))
    length_b = _duration_ms(workspace, workspace.work_root / str(relative_b))
    assert abs(length_a - length_b) <= 60, f"emotion has no audio channel (a={length_a}ms b={length_b}ms)"


def test_finalize_candidate_names_the_real_provider(workspace, database) -> None:
    """A VoxCPM2 job must not be recorded as a WINDOWS_SAPI_LOCAL candidate."""

    dialogue, ctx = _make_voice(workspace, database, "med04_provider", "voxcpm2:F:/refs/hero.wav")
    _bind_tts_profile(database, str(ctx["voice"]["id"]), profile_code="med04_provider")
    created = DialogueService(database, workspace, jobs=JobService(database, workspace)).submit_tts_job(
        ctx["text_revision_id"],
        voice_profile_version_id=str(ctx["voice"]["id"]),
        emotion="neutral",
        speech_rate=1.0,
        idempotency_key="med04-provider",
    )
    job = JobService(database, workspace).get_job(str(created["id"]))
    assert job["input_snapshot"]["provider_kind"] == "VOXCPM2_LOCAL"
