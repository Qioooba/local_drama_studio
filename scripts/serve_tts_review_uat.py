"""Serve an isolated snapshot seeded with a real TTS governance chain.

Creates a fresh SQLite database and project tree, discovers installed Windows
SAPI voices, synthesizes a real PCM WAV through System.Speech, registers a
published TTS profile, voice authorization evidence, dialogue text revision
and a FORMAL TTS candidate, then serves the real FastAPI app on a loopback
port for a read-only three-viewport browser UAT.  Production data untouched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.dialogue import (
    DialogueService,  # type: ignore[import-not-found]
)
from local_drama.application.media import MediaService  # type: ignore[import-not-found]
from local_drama.application.projects import (
    ProjectService,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.infrastructure.database.sqlite import (
    Database,  # type: ignore[import-not-found]
)
from local_drama.main import create_app  # type: ignore[import-not-found]

from scripts.migrate import migrate


def build_settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=root / "work" / "comfy-production" / "output",
        comfy_input_root=root / "work" / "comfy-production" / "input",
        port=port,
    )


def _synthesize_wav(powershell: str, text: str, target: Path) -> None:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{target.as_posix()}'); "
        f"$s.Speak('{text}'); $s.Dispose()"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"TTS_SYNTHESIS_FAILED: {result.stderr[:500]}")


def _published_sapi_profile(database: Database) -> str:
    profile_id = f"sapi-tts-{uuid.uuid4()}"
    version_id = f"{profile_id}-v1"
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES (?,?,?)", (profile_id, "sapi-local-tts", "Windows SAPI local TTS (isolated UAT)"))
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,capability_json,output_contract_json,resource_policy_json)
            VALUES (?,?,1,'TTS_SAPI_LOCAL','{}','{}','{}','PUBLISHED',?,?,?)""",
            (
                version_id,
                profile_id,
                json.dumps({"provider_kind": "WINDOWS_SAPI_LOCAL", "network_allowed": False}),
                json.dumps({"media_kind": "AUDIO", "container": "wav", "codec": "pcm_s16le"}),
                json.dumps({"channel": "CPU", "max_parallel": 1}),
            ),
        )
    return version_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=3224)
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root, args.port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    dialogue = DialogueService(database, settings)

    voices = dialogue.discover_local_sapi_voices()
    if voices.get("status") != "AVAILABLE" or not voices.get("items"):
        raise RuntimeError(f"TTS_SAPI_VOICE_UNAVAILABLE: {voices}")
    voice = dict(voices["items"][0])

    project = ProjectService(database, settings.projects_root).create_project(
        code="tts_review_uat",
        title="TTS review three-viewport UAT",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    project_service = ProjectService(database, settings.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot_id = str(project_service.create_shot(str(episode["id"]), "SHOT_001", 5_000)["id"])

    project_root = settings.projects_root / str(project["root_rel"])
    evidence_path = project_root / "00_admin" / "voice-license.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps({"schema_version": "g10.voice-license.v1", "voice": voice["name"], "scope": "isolated local Windows SAPI runtime UAT", "user_owned": True}, ensure_ascii=False),
        encoding="utf-8",
    )

    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    wav_path = root / "work" / "tts-sample.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    _synthesize_wav(str(powershell), "这是本地短剧测试音色，用于验证对白候选审核界面。", wav_path)
    imported = MediaService(database, settings).import_file(project_id, wav_path, purpose="TTS_AUDITION", owner_type="SHOT", owner_id=shot_id, media_kind="AUDIO", stage="FORMAL")

    profile_version_id = _published_sapi_profile(database)
    voice_profile = dialogue.create_voice_profile(
        project_id,
        code="sapi-test-voice",
        title=f"SAPI {voice['name']}",
        voice_ref=str(voice["voice_ref"]),
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/voice-license.json",
        provider_profile_version_id=profile_version_id,
    )
    line = dialogue.create_line(str(episode["id"]), code="DIA_001", speaker="角色甲", text="这是本地短剧测试音色，用于验证对白候选审核界面。", pronunciation={}, shot_id=shot_id)
    dialogue.revise_text(str(line["id"]), expected_revision_no=1, text="这是本地短剧测试音色，用于验证对白候选审核界面。", pronunciation={})
    line = dialogue.get_line(str(line["id"]))
    revision_id = str(line["text_revisions"][-1]["id"])
    candidate = dialogue.register_candidate(
        revision_id,
        voice_profile_version_id=str(voice_profile["id"]),
        media_version_id=str(imported["media_version_id"]),
        emotion="平静",
        speech_rate=1.0,
        seed=20260817,
        model_ref=f"sapi:{voice['name']}",
        candidate_kind="FORMAL",
    )

    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    print(
        f"tts review UAT snapshot ready project={project_id} episode={episode['id']} voice={voice['name']} "
        f"candidate={candidate['id']} wav_bytes={wav_path.stat().st_size} integrity={integrity} port={args.port}",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
