from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.main import create_app


def test_local_sapi_voice_discovery_is_read_only(workspace, database, monkeypatch) -> None:
    monkeypatch.setattr("local_drama.application.dialogue.shutil.which", lambda _name: "powershell.exe")
    monkeypatch.setattr(
        "local_drama.application.dialogue.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [{"name": "Microsoft Huihui Desktop", "culture": "zh-CN", "gender": "Female", "age": "Adult"}]
            ),
            stderr="",
        ),
    )
    with TestClient(create_app(workspace)) as client:
        with database.connect() as connection:
            before = int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        response = client.get("/api/v1/tts/voices:discover")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "AVAILABLE"
    assert payload["items"] == [
        {
            "name": "Microsoft Huihui Desktop",
            "culture": "zh-CN",
            "gender": "Female",
            "age": "Adult",
            "voice_ref": "sapi:Microsoft Huihui Desktop",
        }
    ]
    assert payload["runtime_contacted"] is True
    assert payload["network_contacted"] is False
    assert payload["mutated"] is False
    with database.connect() as connection:
        assert int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]) == before


def test_publish_local_sapi_profile_requires_real_wav_probe(workspace, database, monkeypatch) -> None:
    def fake_run(*_args, **kwargs):
        if "env" not in kwargs:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"name": "Microsoft Huihui Desktop", "culture": "zh-CN", "gender": "Female", "age": "Adult"}]),
                stderr="",
            )
        output = Path(kwargs["env"]["LD_SAPI_OUTPUT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"RIFF" + b"\x00" * 256)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("local_drama.application.dialogue.shutil.which", lambda _name: "powershell.exe")
    monkeypatch.setattr("local_drama.application.dialogue.subprocess.run", fake_run)
    monkeypatch.setattr(
        "local_drama.application.dialogue.MediaService._probe",
        lambda *_args, **_kwargs: {"probe_status": "PASS", "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}], "format": {"duration": "0.25"}},
    )
    service = DialogueService(database, workspace)
    published = service.publish_local_sapi_profile("sapi:Microsoft Huihui Desktop", "验收短句")
    replay = service.publish_local_sapi_profile("sapi:Microsoft Huihui Desktop", "验收短句")
    assert published["status"] == "PUBLISHED"
    assert published["capability"] == "TTS"
    assert len(published["evidence"]["smoke_sha256"]) == 64
    assert published["evidence"]["network_contacted"] is False
    assert replay["id"] == published["id"]
    with database.connect() as connection:
        row = connection.execute("SELECT capability,status,capability_json FROM execution_profile_versions WHERE id=?", (published["id"],)).fetchone()
    assert row is not None and row["capability"] == "TTS" and row["status"] == "PUBLISHED"
    assert json.loads(row["capability_json"])["smoke_sha256"] == published["evidence"]["smoke_sha256"]


def test_publish_local_sapi_profile_rejects_unscanned_voice(workspace, database, monkeypatch) -> None:
    monkeypatch.setattr(
        DialogueService,
        "discover_local_sapi_voices",
        lambda _self: {"status": "AVAILABLE", "items": [], "message": None, "runtime_contacted": True, "network_contacted": False, "mutated": False},
    )
    service = DialogueService(database, workspace)
    try:
        service.publish_local_sapi_profile("sapi:Not Installed")
    except Exception as error:  # domain code is the contract under test
        assert getattr(error, "code", None) == "SAPI_VOICE_NOT_DISCOVERED"
    else:
        raise AssertionError("undiscovered voice must fail closed")


def _published_sapi_profile(database) -> str:
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES ('sapi-tts','sapi-local-tts','Windows SAPI local TTS')")
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
             status,capability_json,output_contract_json,resource_policy_json)
            VALUES ('sapi-tts-v1','sapi-tts',1,'TTS_SAPI_LOCAL','{}','{}','{}','PUBLISHED',
            '{"provider_kind":"WINDOWS_SAPI_LOCAL","network_allowed":false}',
            '{"media_kind":"AUDIO","container":"wav","codec":"pcm_s16le"}',
            '{"channel":"CPU","max_parallel":1}')"""
        )
    return "sapi-tts-v1"


def test_real_windows_sapi_job_artifact_promotion_and_formal_candidate(workspace, database) -> None:
    discovery = DialogueService(database, workspace).discover_local_sapi_voices()
    expected_voice = "Microsoft Huihui Desktop"
    if discovery["status"] != "AVAILABLE" or expected_voice not in {
        str(item["name"]) for item in discovery["items"]
    }:
        pytest.skip(f"real Windows SAPI voice unavailable: {discovery['status']}")
    project = ProjectService(database, workspace.projects_root).create_project(
        code="real_sapi_tts",
        title="Real SAPI TTS",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    episode = ProjectService(database, workspace.projects_root).list_episodes(
        str(ProjectService(database, workspace.projects_root).list_seasons(project_id)[0]["id"])
    )[0]
    project_root = workspace.projects_root / str(project["root_rel"])
    evidence = project_root / "00_admin" / "voice-license.json"
    evidence.write_text(
        json.dumps({"schema_version": "test.voice-license.v1", "voice": "Microsoft Huihui Desktop", "scope": "isolated local runtime test"}),
        encoding="utf-8",
    )
    profile_id = _published_sapi_profile(database)
    service = DialogueService(database, workspace)
    line = service.create_line(
        str(episode["id"]),
        code="DLG-SAPI-001",
        speaker="测试说话人",
        text="你好，这是本机离线语音任务。",
        pronunciation={},
    )
    text_revision_id = str(line["text_revisions"][0]["id"])
    voice = service.create_voice_profile(
        project_id,
        code="huihui-desktop",
        title="Microsoft Huihui Desktop",
        voice_ref="sapi:Microsoft Huihui Desktop",
        license_status="VERIFIED_LOCAL",
        license_evidence_path_rel="00_admin/voice-license.json",
        provider_profile_version_id=profile_id,
    )
    with TestClient(create_app(workspace)) as client:
        missing_key = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            json={"voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert missing_key.status_code == 422
        submitted = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": "sapi-real-job-1"},
            json={"voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert submitted.status_code == 201, submitted.text
        job = submitted.json()["job"]
        replay_response = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": "sapi-real-job-1"},
            json={"voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert replay_response.status_code == 201
        replay = replay_response.json()["job"]
        assert replay["id"] == job["id"]
        assert replay["idempotent_replay"] is True
        premature = client.post(f"/api/v1/tts-jobs/{job['id']}:finalize")
        assert premature.status_code == 422
        assert premature.json()["error"]["code"] == "TTS_JOB_NOT_FINALIZABLE"
    worker_result = LocalMediaWorker(database, workspace).run_once("sapi-real-worker", ["CPU"])
    assert worker_result is not None
    with database.connect() as connection:
        worker_error = connection.execute(
            "SELECT error_detail_redacted FROM job_attempts WHERE id=?", (worker_result["attempt"]["id"],)
        ).fetchone()
    assert worker_result["result"]["job_state"] == "SUCCEEDED", {
        "code": worker_result.get("error"),
        "detail": worker_error["error_detail_redacted"] if worker_error else None,
    }
    assert worker_result["artifact"]["kind"] == "TTS_AUDIO"
    output = workspace.work_root / str(worker_result["artifact"]["sandbox_rel_path"])
    assert output.is_file() and output.stat().st_size > 44
    with TestClient(create_app(workspace)) as client:
        finalized_response = client.post(f"/api/v1/tts-jobs/{job['id']}:finalize")
        assert finalized_response.status_code == 201, finalized_response.text
        finalized = finalized_response.json()["result"]
        finalized_replay_response = client.post(f"/api/v1/tts-jobs/{job['id']}:finalize")
        assert finalized_replay_response.status_code == 201
        finalized_replay = finalized_replay_response.json()["result"]
    assert finalized["candidate"]["candidate_kind"] == "FORMAL"
    assert finalized["candidate"]["model_ref"] == "WINDOWS_SAPI_LOCAL"
    assert finalized["media"]["media_kind"] == "AUDIO"
    assert int(finalized["media"]["duration_ms"]) > 0
    assert finalized_replay["candidate"]["id"] == finalized["candidate"]["id"]
    assert finalized_replay["idempotent_replay"] is True
    assert finalized["candidate"]["provenance"]["provider_profile_version_id"] == profile_id
    assert finalized["candidate"]["provenance"]["media_sha256"] == finalized["media"]["sha256"]

    invalid_voice = service.create_voice_profile(
        project_id,
        code="invalid-local-ref",
        title="Invalid local ref",
        voice_ref="local:not-sapi",
        license_status="VERIFIED_LOCAL",
        license_evidence_path_rel="00_admin/voice-license.json",
        provider_profile_version_id=profile_id,
    )
    with TestClient(create_app(workspace)) as client:
        invalid_ref = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": "sapi-invalid-ref"},
            json={"voice_profile_version_id": invalid_voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert invalid_ref.status_code == 422
        assert invalid_ref.json()["error"]["code"] == "TTS_PUBLISHED_LOCAL_PROFILE_REQUIRED"
    revised = service.revise_text(
        str(line["id"]),
        expected_revision_no=1,
        text="你好，这是已经更新的本机离线语音任务。",
        pronunciation={},
    )
    assert revised["text_revisions"][-1]["revision_no"] == 2
    with TestClient(create_app(workspace)) as client:
        stale = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision_id}/tts-jobs",
            headers={"Idempotency-Key": "sapi-stale-text"},
            json={"voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert stale.status_code == 422
        assert stale.json()["error"]["code"] == "TTS_JOB_TEXT_STALE"
        tampered_job_response = client.post(
            f"/api/v1/dialogue-text-revisions/{revised['text_revisions'][-1]['id']}/tts-jobs",
            headers={"Idempotency-Key": "sapi-tampered-snapshot"},
            json={"voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0},
        )
        assert tampered_job_response.status_code == 201
        tampered_job = tampered_job_response.json()["job"]
    tampered_snapshot = dict(tampered_job["input_snapshot"])
    tampered_snapshot["text_hash"] = "0" * 64
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET input_snapshot_json=? WHERE id=?",
            (json.dumps(tampered_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), tampered_job["id"]),
        )
    rejected = LocalMediaWorker(database, workspace).run_once("sapi-tamper-worker", ["CPU"])
    assert rejected is not None
    assert rejected["error"] == "TTS_JOB_SNAPSHOT_INVALID"
    assert rejected["result"]["job_state"] == "FAILED"
