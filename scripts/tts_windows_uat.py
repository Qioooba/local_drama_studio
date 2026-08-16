"""Run a real, isolated Windows SAPI TTS governance UAT.

The script deliberately creates a fresh LOCAL_ONLY database and project.  It
never uses the production database, never downloads a voice/model, and leaves
the generated WAV under the caller-provided temporary root for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.dialogue import DialogueService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _published_sapi_profile(database: Database) -> str:
    profile_id = f"sapi-tts-{uuid.uuid4()}"
    version_id = f"{profile_id}-v1"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO execution_profiles (id,code,title) VALUES (?,?,?)",
            (profile_id, "sapi-local-tts", "Windows SAPI local TTS (isolated UAT)"),
        )
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


def _check(code: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), **details}


def run(root: Path) -> dict[str, Any]:
    settings = _settings(root)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    dialogue = DialogueService(database, settings)
    voices = dialogue.discover_local_sapi_voices()
    if voices.get("status") != "AVAILABLE" or not voices.get("items"):
        return {
            "schema_version": "g10.dialogue_tts_windows_uat.v1",
            "status": "BLOCKED",
            "blocked_reason": "WINDOWS_SAPI_VOICE_UNAVAILABLE",
            "scope": "isolated migrated database; no production database or network",
            "voice_discovery": voices,
            "runtime_contacted": bool(voices.get("runtime_contacted")),
            "network_contacted": False,
            "production_database_contacted": False,
            "observed_at": datetime.now(UTC).isoformat(),
        }

    selected = dict(voices["items"][0])
    project = ProjectService(database, settings.projects_root).create_project(
        code=f"g10_sapi_{uuid.uuid4().hex[:8]}",
        title="G10 isolated Windows SAPI UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    project_service = ProjectService(database, settings.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    project_root = settings.projects_root / str(project["root_rel"])
    evidence_path = project_root / "00_admin" / "voice-license.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "g10.voice-license.v1",
                "voice": selected["name"],
                "scope": "isolated local Windows SAPI runtime UAT",
                "user_owned": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    profile_version_id = _published_sapi_profile(database)
    line = dialogue.create_line(
        str(episode["id"]),
        code="DLG-SAPI-UAT-001",
        speaker="local-sapi-uat",
        text="This is an offline Windows SAPI voice governance test.",
        pronunciation={},
    )
    text_revision_id = str(line["text_revisions"][0]["id"])
    voice_profile = dialogue.create_voice_profile(
        project_id,
        code="sapi-uat-voice",
        title=selected["name"],
        voice_ref=str(selected["voice_ref"]),
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/voice-license.json",
        provider_profile_version_id=profile_version_id,
    )
    payload = {"voice_profile_version_id": voice_profile["id"], "emotion": "neutral", "speech_rate": 1.0}
    key = f"g10-sapi-{uuid.uuid4()}"
    with TestClient(create_app(settings)) as client:
        missing_key = client.post(f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs", json=payload)
        submitted = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        job = submitted.json().get("job", {})
        replay = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        premature = client.post(f"/api/v1/tts-jobs/{job.get('id', '')}:finalize")

    worker_result = LocalMediaWorker(database, settings).run_once(f"g10-sapi-worker-{uuid.uuid4()}", ["CPU"])
    artifact_path: Path | None = None
    artifact_sha = None
    if worker_result and worker_result.get("artifact", {}).get("sandbox_rel_path"):
        artifact_path = settings.work_root / str(worker_result["artifact"]["sandbox_rel_path"])
        if artifact_path.is_file():
            artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    with TestClient(create_app(settings)) as client:
        finalized = client.post(f"/api/v1/tts-jobs/{job.get('id', '')}:finalize") if job.get("id") else None
        finalized_payload = finalized.json().get("result", {}) if finalized is not None else {}
        finalize_replay = client.post(f"/api/v1/tts-jobs/{job.get('id', '')}:finalize") if job.get("id") else None
        replay_payload = finalize_replay.json().get("result", {}) if finalize_replay is not None else {}

    media = finalized_payload.get("media", {})
    candidate = finalized_payload.get("candidate", {})
    provenance = candidate.get("provenance", {})
    checks = [
        _check("SAPI_VOICE_DISCOVERED", bool(selected.get("voice_ref", "").startswith("sapi:")), voice=selected),
        _check("NO_NETWORK_DISCOVERY", voices.get("network_contacted") is False),
        _check("MISSING_IDEMPOTENCY_KEY_REJECTED", missing_key.status_code == 422),
        _check("TTS_JOB_SUBMITTED", submitted.status_code == 201 and bool(job.get("id"))),
        _check("TTS_JOB_IDEMPOTENT_REPLAY", replay.status_code == 201 and replay.json().get("job", {}).get("id") == job.get("id") and replay.json().get("job", {}).get("idempotent_replay") is True),
        _check("PREMATURE_FINALIZE_REJECTED", premature.status_code == 422),
        _check("REAL_SAPI_WORKER_SUCCEEDED", bool(worker_result and worker_result.get("result", {}).get("job_state") == "SUCCEEDED")),
        _check("WAV_ARTIFACT_VERIFIED", bool(artifact_path and artifact_path.is_file() and artifact_path.stat().st_size > 44 and artifact_sha)),
        _check("FORMAL_CANDIDATE_PROMOTED", finalized is not None and finalized.status_code == 201 and candidate.get("candidate_kind") == "FORMAL" and media.get("media_kind") == "AUDIO"),
        _check("PROVENANCE_HASH_BOUND", bool(provenance.get("media_sha256") and provenance.get("media_sha256") == media.get("sha256") == artifact_sha)),
        _check("FINALIZE_IDEMPOTENT_REPLAY", finalize_replay is not None and finalize_replay.status_code == 201 and replay_payload.get("candidate", {}).get("id") == candidate.get("id") and replay_payload.get("idempotent_replay") is True),
        _check("DATABASE_INTEGRITY", database.integrity_check() == "ok"),
    ]
    return {
        "schema_version": "g10.dialogue_tts_windows_uat.v1",
        "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
        "scope": "isolated migrated database and real in-process FastAPI + Windows System.Speech runtime",
        "selected_voice": selected,
        "profile_version_id": profile_version_id,
        "job_id": job.get("id"),
        "artifact": {"path_rel": artifact_path.relative_to(root).as_posix() if artifact_path else None, "sha256": artifact_sha},
        "checks": checks,
        "runtime_contacted": True,
        "network_contacted": False,
        "production_database_contacted": False,
        "production_profile_mutated": False,
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"g10-sapi-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "dialogue-tts-windows-uat-2026-08-16.json")
    args = parser.parse_args()
    result = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    if result["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
