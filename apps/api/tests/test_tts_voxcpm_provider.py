"""VoxCPM2 provider branch for production TTS: submit resolution + handler."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.dialogue import DialogueService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker_handlers.tts_job import run_tts_job
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic


def _bind_tts_profile(database, voice_profile_version_id: str, profile_code: str = "tts-vox") -> str:
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


def _make_voice(workspace, database, code: str, voice_ref: str) -> tuple[DialogueService, dict]:
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
    line = dialogue.create_line(
        str(episode["id"]), code="L001", speaker="主角", text="警报还没解除。", pronunciation={},
    )
    text_revision_id = str(line["text_revisions"][-1]["id"])
    return dialogue, {"project": project, "voice": voice, "text_revision_id": text_revision_id}


def test_submit_tts_job_resolves_provider_kind_from_voice_ref(workspace, database) -> None:
    dialogue, ctx = _make_voice(workspace, database, "vox_submit", "voxcpm2:F:/refs/hero.wav|低沉冷静")
    _bind_tts_profile(database, str(ctx["voice"]["id"]))
    dialogue_with_jobs = DialogueService(database, workspace, jobs=JobService(database, workspace))

    job = dialogue_with_jobs.submit_tts_job(
        ctx["text_revision_id"], voice_profile_version_id=str(ctx["voice"]["id"]),
        emotion="neutral", speech_rate=1.0, idempotency_key="vox-submit-1",
    )
    assert job["input_snapshot"]["provider_kind"] == "VOXCPM2_LOCAL"
    assert job["input_snapshot"]["voice_ref"] == "voxcpm2:F:/refs/hero.wav|低沉冷静"

    _, sapi_ctx = _make_voice(workspace, database, "vox_submit_sapi", "sapi:Huihui")
    _bind_tts_profile(database, str(sapi_ctx["voice"]["id"]), profile_code="tts-vox-sapi")
    sapi_job = dialogue_with_jobs.submit_tts_job(
        sapi_ctx["text_revision_id"], voice_profile_version_id=str(sapi_ctx["voice"]["id"]),
        emotion="neutral", speech_rate=1.0, idempotency_key="sapi-submit-1",
    )
    assert sapi_job["input_snapshot"]["provider_kind"] == "WINDOWS_SAPI_LOCAL"


class _FakeVoxcpmRuntime:
    def __init__(self, wav_factory) -> None:
        self.wav_factory = wav_factory
        self.calls: list[dict] = []

    def synthesize(self, text: str, output: Path, *, prompt_audio=None, prompt_text=None) -> None:
        self.calls.append({"text": text, "prompt_audio": str(prompt_audio) if prompt_audio else None, "prompt_text": prompt_text})
        self.wav_factory(output)


def _make_wav(workspace, target: Path) -> None:
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-ar", "48000", "-ac", "1", "-y", str(target)],
        check=True, capture_output=True,
    )


def test_run_tts_job_voxcpm_branch_synthesizes_and_parses_reference(workspace, database) -> None:
    dialogue, ctx = _make_voice(workspace, database, "vox_handler", "voxcpm2:F:/refs/hero.wav|低沉冷静")
    _bind_tts_profile(database, str(ctx["voice"]["id"]))
    created = DialogueService(database, workspace, jobs=JobService(database, workspace)).submit_tts_job(
        ctx["text_revision_id"], voice_profile_version_id=str(ctx["voice"]["id"]),
        emotion="neutral", speech_rate=1.0, idempotency_key="vox-handler-1",
    )
    job = JobService(database, workspace).get_job(str(created["id"]))

    runtime = _FakeVoxcpmRuntime(lambda target: _make_wav(workspace, target))
    kind, relative = run_tts_job(
        job,
        workspace.work_root / "vox-job-out",
        work_root=workspace.work_root,
        database=database,
        tts_runtime=None,
        media_ops=MediaService(database, workspace),
        run_ffmpeg=lambda args: subprocess.run([workspace.ffmpeg_path, *args], check=True, capture_output=True),
        atomic_writer=write_atomic,
        voxcpm_runtime=runtime,
    )
    assert kind == "TTS_AUDIO"
    output = workspace.work_root / relative
    assert output.is_file() and output.stat().st_size > 0
    assert runtime.calls == [{
        "text": "警报还没解除。",
        "prompt_audio": str(Path("F:/refs/hero.wav")),
        "prompt_text": "低沉冷静",
    }]


def test_run_tts_job_voxcpm_requires_runtime(workspace, database) -> None:
    dialogue, ctx = _make_voice(workspace, database, "vox_nort", "voxcpm2:F:/refs/hero.wav")
    _bind_tts_profile(database, str(ctx["voice"]["id"]))
    created = DialogueService(database, workspace, jobs=JobService(database, workspace)).submit_tts_job(
        ctx["text_revision_id"], voice_profile_version_id=str(ctx["voice"]["id"]),
        emotion="neutral", speech_rate=1.0, idempotency_key="vox-nort-1",
    )
    job = JobService(database, workspace).get_job(str(created["id"]))
    with pytest.raises(DomainRuleError, match="VoxCPM2"):
        run_tts_job(
            job,
            workspace.work_root / "vox-nort-out",
            work_root=workspace.work_root,
            database=database,
            tts_runtime=None,
            media_ops=None,
            run_ffmpeg=lambda args: None,
            atomic_writer=write_atomic,
            voxcpm_runtime=None,
        )
