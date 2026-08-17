from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_asset(
    database,
    project_id: str,
    code: str,
    name: str,
    *,
    kind: str = "CHARACTER",
    status: str = "ACTIVE",
) -> str:
    asset_id = str(uuid.uuid4())
    now = _now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (asset_id, project_id, kind, code, name, "", None, "{}", status, now, now, "test"),
        )
    return asset_id


def _bind_shot_character(database, shot_id: str, asset_id: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO shot_asset_bindings
            (id,shot_id,asset_id,role_in_shot,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,1,'v2')""",
            (str(uuid.uuid4()), shot_id, asset_id, "main", _now(), "test"),
        )


def _bind_tts_profile(database, voice_profile_version_id: str, profile_code: str = "tts-test") -> None:
    execution_profile_id = f"exec-{profile_code}"
    version_id = f"{execution_profile_id}-v1"
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO execution_profiles (id,code,title) VALUES (?,?,?)",
            (execution_profile_id, profile_code, f"TTS {profile_code}"),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
             status,capability_json,output_contract_json,resource_policy_json)
            VALUES (?,?,1,'TTS','{}','{}','{}','PUBLISHED','{}','{}','{}')""",
            (version_id, execution_profile_id),
        )
        connection.execute(
            "UPDATE voice_profile_versions SET provider_profile_version_id=? WHERE id=?",
            (version_id, voice_profile_version_id),
        )


def _make_project(workspace, database, code: str, title: str) -> dict:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=title,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / "voice-license.json"
    evidence.write_text('{"owner":"test","scope":"character voice batch"}\n', encoding="utf-8", newline="")
    return {"project": project, "episode": episode, "root": root}


def _create_voice(client, project_id: str, code: str, title: str, voice_ref: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/voice-profile-versions",
        json={
            "code": code,
            "title": title,
            "voice_ref": voice_ref,
            "license_status": "USER_OWNED",
            "license_evidence_path_rel": "00_admin/voice-license.json",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["voice_profile"]


def test_character_voice_binding_lifecycle_and_audit(workspace, database) -> None:
    data = _make_project(workspace, database, "cvb_lifecycle", "CVB lifecycle")
    project = data["project"]
    project_id = str(project["id"])
    character_id = _insert_asset(database, project_id, "CHAR-001", "周桂兰")
    with TestClient(create_app(workspace)) as client:
        voice = _create_voice(client, project_id, "voice-a", "Voice A", "sapi:TestVoice")
        response = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": character_id, "voice_profile_version_id": voice["id"]},
        )
        assert response.status_code == 201, response.text
        binding = response.json()["binding"]
        assert binding["character_asset_id"] == character_id
        assert binding["voice_profile_version_id"] == voice["id"]
        assert binding["character"] == {
            "id": character_id,
            "code": "CHAR-001",
            "name": "周桂兰",
            "kind": "CHARACTER",
            "status": "ACTIVE",
        }
        assert binding["voice"]["title"] == "Voice A"
        assert binding["voice"]["voice_ref"] == "sapi:TestVoice"

        duplicate = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": character_id, "voice_profile_version_id": voice["id"]},
        )
        assert duplicate.status_code == 422
        assert duplicate.json()["error"]["code"] == "CHARACTER_VOICE_ALREADY_BOUND"

        listed = client.get(f"/api/v1/projects/{project_id}/character-voice-bindings")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == binding["id"]
        assert items[0]["character"]["name"] == "周桂兰"

        with database.connect() as connection:
            bound_events = connection.execute(
                "SELECT action,metadata_redacted_json FROM audit_events WHERE action='CHARACTER_VOICE_BOUND'"
            ).fetchall()
            assert len(bound_events) == 1
            assert json.loads(bound_events[0]["metadata_redacted_json"])["character_asset_id"] == character_id

        unbound = client.delete(f"/api/v1/character-voice-bindings/{binding['id']}")
        assert unbound.status_code == 200, unbound.text
        assert unbound.json()["result"]["status"] == "UNBOUND"

        missing = client.delete(f"/api/v1/character-voice-bindings/{binding['id']}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "CHARACTER_VOICE_BINDING_NOT_FOUND"

        assert client.get(f"/api/v1/projects/{project_id}/character-voice-bindings").json()["items"] == []
        with database.connect() as connection:
            unbound_events = connection.execute(
                "SELECT action FROM audit_events WHERE action='CHARACTER_VOICE_UNBOUND'"
            ).fetchall()
            assert len(unbound_events) == 1


def test_character_voice_binding_rejects_invalid_targets(workspace, database) -> None:
    first = _make_project(workspace, database, "cvb_first", "CVB first")
    second = _make_project(workspace, database, "cvb_second", "CVB second")
    first_id, second_id = str(first["project"]["id"]), str(second["project"]["id"])
    character_id = _insert_asset(database, first_id, "CHAR-001", "周桂兰")
    scene_id = _insert_asset(database, first_id, "SCN-001", "客厅", kind="SCENE")
    foreign_character_id = _insert_asset(database, second_id, "CHAR-002", "王大力")
    archived_character_id = _insert_asset(database, first_id, "CHAR-003", "旧角色", status="ARCHIVED")
    with TestClient(create_app(workspace)) as client:
        voice = _create_voice(client, first_id, "voice-a", "Voice A", "sapi:TestVoice")
        foreign_voice = _create_voice(client, second_id, "voice-b", "Voice B", "sapi:VoiceB")
        inactive_voice = _create_voice(client, first_id, "voice-c", "Voice C", "sapi:VoiceC")
        with database.transaction() as connection:
            connection.execute("UPDATE voice_profile_versions SET status='RETIRED' WHERE id=?", (inactive_voice["id"],))

        def bind(project_id: str, character_asset_id: str, voice_profile_version_id: str):
            return client.post(
                f"/api/v1/projects/{project_id}/character-voice-bindings",
                json={"character_asset_id": character_asset_id, "voice_profile_version_id": voice_profile_version_id},
            )

        cases = [
            ("STORY_ASSET_NOT_FOUND", (first_id, str(uuid.uuid4()), voice["id"])),
            ("STORY_ASSET_KIND_INVALID", (first_id, scene_id, voice["id"])),
            ("STORY_ASSET_PROJECT_MISMATCH", (first_id, foreign_character_id, voice["id"])),
            ("STORY_ASSET_NOT_ACTIVE", (first_id, archived_character_id, voice["id"])),
            ("VOICE_PROFILE_NOT_ACTIVE", (first_id, character_id, str(uuid.uuid4()))),
            ("VOICE_PROFILE_PROJECT_MISMATCH", (first_id, character_id, foreign_voice["id"])),
            ("VOICE_PROFILE_NOT_ACTIVE", (first_id, character_id, inactive_voice["id"])),
            ("PROJECT_NOT_FOUND", (str(uuid.uuid4()), character_id, voice["id"])),
        ]
        for code, (project_id, character_asset_id, voice_profile_version_id) in cases:
            response = bind(project_id, character_asset_id, voice_profile_version_id)
            assert response.status_code == 404 if code == "PROJECT_NOT_FOUND" else 422
            assert response.json()["error"]["code"] == code, response.text


def test_episode_tts_batch_resolves_shot_and_speaker_and_skips(workspace, database) -> None:
    data = _make_project(workspace, database, "cvb_batch", "CVB batch")
    project, episode = data["project"], data["episode"]
    project_id, episode_id = str(project["id"]), str(episode["id"])
    projects = ProjectService(database, workspace.projects_root)
    shot = projects.create_shot(episode_id, "SH-001", 4_000)
    guilan_id = _insert_asset(database, project_id, "CHAR-001", "周桂兰")
    dali_id = _insert_asset(database, project_id, "CHAR-002", "王大力")
    passerby_id = _insert_asset(database, project_id, "CHAR-003", "路人甲")
    _bind_shot_character(database, str(shot["id"]), guilan_id)
    with TestClient(create_app(workspace)) as client:
        guilan_voice = _create_voice(client, project_id, "voice-a", "Voice A", "sapi:TestVoice")
        dali_voice = _create_voice(client, project_id, "voice-b", "Voice B", "sapi:VoiceB")
        local_voice = _create_voice(client, project_id, "voice-c", "Voice C", "local:test")
        _bind_tts_profile(database, guilan_voice["id"])
        _bind_tts_profile(database, dali_voice["id"], profile_code="tts-test-2")
        # 路人甲 voice is bound but not job-eligible (no Published TTS profile).
        bindings = [
            (guilan_id, guilan_voice["id"]),
            (dali_id, dali_voice["id"]),
            (passerby_id, local_voice["id"]),
        ]
        for character_asset_id, voice_profile_version_id in bindings:
            response = client.post(
                f"/api/v1/projects/{project_id}/character-voice-bindings",
                json={"character_asset_id": character_asset_id, "voice_profile_version_id": voice_profile_version_id},
            )
            assert response.status_code == 201, response.text

        lines = [
            ("DLG-001", "周桂兰", "谁在里面？", str(shot["id"])),
            ("DLG-002", "王大力", "是我。", None),
            ("DLG-003", "CHAR-001", "桂兰也到了。", None),
            ("DLG-004", "神秘人", "别出声。", None),
            ("DLG-005", "路人甲", "借过一下。", None),
        ]
        for code, speaker, text, shot_id in lines:
            payload = {"code": code, "speaker": speaker, "text": text}
            if shot_id:
                payload["shot_id"] = shot_id
            response = client.post(f"/api/v1/episodes/{episode_id}/dialogue-lines", json=payload)
            assert response.status_code == 201, response.text

        batch = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "ep-batch-1", "emotion": "NEUTRAL", "speech_rate": 1.0},
        )
        assert batch.status_code == 201, batch.text
        result = batch.json()["batch"]
        assert result["episode_id"] == episode_id
        assert result["counts"] == {"submitted": 3, "skipped": 2, "failed": 0}
        submitted_by_code = {item["code"]: item for item in result["submitted"]}
        assert set(submitted_by_code) == {"DLG-001", "DLG-002", "DLG-003"}
        # DLG-001 resolves through the single CHARACTER shot binding (role irrelevant).
        assert submitted_by_code["DLG-001"]["character_asset_id"] == guilan_id
        # DLG-002 resolves through speaker name; DLG-003 through speaker == asset code.
        assert submitted_by_code["DLG-002"]["character_asset_id"] == dali_id
        assert submitted_by_code["DLG-003"]["character_asset_id"] == guilan_id
        assert submitted_by_code["DLG-002"]["voice_profile_version_id"] == dali_voice["id"]
        skipped_by_code = {item["code"]: item for item in result["skipped"]}
        assert skipped_by_code["DLG-004"]["reason"] == "VOICE_UNRESOLVED"
        assert skipped_by_code["DLG-005"]["reason"] == "VOICE_NOT_JOB_ELIGIBLE"

        with database.connect() as connection:
            jobs = connection.execute(
                "SELECT id,state,idempotency_key FROM jobs WHERE type='TTS_GENERATION' AND project_id=? ORDER BY idempotency_key",
                (project_id,),
            ).fetchall()
            assert len(jobs) == 3
            assert all(job["state"] == "QUEUED" for job in jobs)
            keys = {job["idempotency_key"] for job in jobs}
            assert keys == {f"ep-batch-1:{item['line_id']}" for item in result["submitted"]}
            assert {job["id"] for job in jobs} == {item["job_id"] for item in result["submitted"]}
            events = connection.execute(
                "SELECT metadata_redacted_json FROM audit_events WHERE action='EPISODE_TTS_BATCH_SUBMITTED'"
            ).fetchall()
            assert len(events) == 1
            metadata = json.loads(events[0]["metadata_redacted_json"])
            assert metadata["counts"] == {"submitted": 3, "skipped": 2, "failed": 0}
            assert len(metadata["job_ids"]) == 3


def test_episode_tts_batch_shot_ambiguity_falls_back_to_speaker(workspace, database) -> None:
    data = _make_project(workspace, database, "cvb_ambig", "CVB ambiguity")
    project, episode = data["project"], data["episode"]
    project_id, episode_id = str(project["id"]), str(episode["id"])
    projects = ProjectService(database, workspace.projects_root)
    shot = projects.create_shot(episode_id, "SH-001", 4_000)
    guilan_id = _insert_asset(database, project_id, "CHAR-001", "周桂兰")
    dali_id = _insert_asset(database, project_id, "CHAR-002", "王大力")
    oldman_id = _insert_asset(database, project_id, "CHAR-003", "老汉")
    # The shot binds two characters -> ambiguous, batch must fall back to speaker match.
    _bind_shot_character(database, str(shot["id"]), guilan_id)
    _bind_shot_character(database, str(shot["id"]), dali_id)
    with TestClient(create_app(workspace)) as client:
        oldman_voice = _create_voice(client, project_id, "voice-a", "Voice A", "sapi:TestVoice")
        _bind_tts_profile(database, oldman_voice["id"])
        response = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": oldman_id, "voice_profile_version_id": oldman_voice["id"]},
        )
        assert response.status_code == 201, response.text
        for code, speaker, shot_id in [("DLG-001", "老汉", str(shot["id"])), ("DLG-002", "神秘人", str(shot["id"]))]:
            payload = {"code": code, "speaker": speaker, "text": "test", "shot_id": shot_id}
            response = client.post(f"/api/v1/episodes/{episode_id}/dialogue-lines", json=payload)
            assert response.status_code == 201, response.text
        batch = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "ep-ambig"},
        )
        assert batch.status_code == 201, batch.text
        result = batch.json()["batch"]
        assert result["counts"]["submitted"] == 1
        assert result["submitted"][0]["code"] == "DLG-001"
        assert result["submitted"][0]["character_asset_id"] == oldman_id
        assert result["skipped"][0]["code"] == "DLG-002"
        assert result["skipped"][0]["reason"] == "VOICE_UNRESOLVED"


def test_episode_tts_batch_validation_and_idempotency(workspace, database) -> None:
    data = _make_project(workspace, database, "cvb_idem", "CVB idempotency")
    project, episode = data["project"], data["episode"]
    project_id, episode_id = str(project["id"]), str(episode["id"])
    character_id = _insert_asset(database, project_id, "CHAR-001", "周桂兰")
    with TestClient(create_app(workspace)) as client:
        voice_a = _create_voice(client, project_id, "voice-a", "Voice A", "sapi:TestVoice")
        _bind_tts_profile(database, voice_a["id"])
        bind_response = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": character_id, "voice_profile_version_id": voice_a["id"]},
        )
        assert bind_response.status_code == 201, bind_response.text
        binding_id = bind_response.json()["binding"]["id"]
        line = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-lines",
            json={"code": "DLG-001", "speaker": "周桂兰", "text": "谁在里面？"},
        ).json()["dialogue"]

        # Schema blocks out-of-range speech_rate; the domain rule is still enforced at service level.
        with pytest.raises(DomainRuleError) as caught:
            DialogueService(database, workspace).submit_episode_tts_batch(
                episode_id, idempotency_key_prefix="bad", speech_rate=3.0
            )
        assert caught.value.code == "TTS_SPEECH_RATE_INVALID"
        with pytest.raises(DomainRuleError) as caught:
            DialogueService(database, workspace).submit_episode_tts_batch(
                episode_id, idempotency_key_prefix="bad", speech_rate=0.2
            )
        assert caught.value.code == "TTS_SPEECH_RATE_INVALID"

        empty_emotion = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "bad", "emotion": "   "},
        )
        assert empty_emotion.status_code == 422
        assert empty_emotion.json()["error"]["code"] == "TTS_JOB_PARAMETERS_INVALID"

        missing_episode = client.post(
            "/api/v1/episodes/00000000-0000-0000-0000-000000000000/dialogue-tts:batch",
            json={"idempotency_key_prefix": "bad"},
        )
        assert missing_episode.status_code == 404
        assert missing_episode.json()["error"]["code"] == "EPISODE_NOT_FOUND"

        first = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "ep-run", "emotion": "警觉", "speech_rate": 0.95},
        )
        assert first.status_code == 201, first.text
        first_result = first.json()["batch"]
        assert first_result["counts"] == {"submitted": 1, "skipped": 0, "failed": 0}
        job_id = first_result["submitted"][0]["job_id"]
        with database.connect() as connection:
            jobs = connection.execute("SELECT COUNT(*) FROM jobs WHERE type='TTS_GENERATION' AND project_id=?", (project_id,)).fetchone()[0]
            assert jobs == 1

        # Same prefix replays idempotently: same job, no duplicates.
        replay = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "ep-run", "emotion": "警觉", "speech_rate": 0.95},
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["batch"]["submitted"][0]["job_id"] == job_id
        with database.connect() as connection:
            jobs = connection.execute("SELECT COUNT(*) FROM jobs WHERE type='TTS_GENERATION' AND project_id=?", (project_id,)).fetchone()[0]
            assert jobs == 1

        # Rebinding the character changes the payload: same key now conflicts on rerun.
        voice_b = _create_voice(client, project_id, "voice-b", "Voice B", "sapi:VoiceB")
        _bind_tts_profile(database, voice_b["id"], profile_code="tts-test-2")
        response = client.delete(f"/api/v1/character-voice-bindings/{binding_id}")
        assert response.status_code == 200, response.text
        rebound = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": character_id, "voice_profile_version_id": voice_b["id"]},
        )
        assert rebound.status_code == 201, rebound.text
        rerun = client.post(
            f"/api/v1/episodes/{episode_id}/dialogue-tts:batch",
            json={"idempotency_key_prefix": "ep-run", "emotion": "警觉", "speech_rate": 0.95},
        )
        assert rerun.status_code == 201, rerun.text
        rerun_result = rerun.json()["batch"]
        assert rerun_result["counts"] == {"submitted": 0, "skipped": 0, "failed": 1}
        assert rerun_result["failed"][0]["code"] == line["code"]
        assert rerun_result["failed"][0]["reason"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
