from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_traceable_imported_tts_candidate_selection_and_stale_text_gate(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="tts_candidate_governance",
        title="TTS candidate governance",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = ProjectService(database, workspace.projects_root).list_seasons(project_id)[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / "voice-license.json"
    evidence.write_text('{"owner":"test-operator","scope":"local test voice"}\n', encoding="utf-8", newline="")
    audio_path = workspace.work_root / "tts-candidate.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=330:duration=4", "-y", str(audio_path)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(project_id, audio_path, purpose="DIALOGUE_TTS", media_kind="AUDIO")
    short_audio_path = workspace.work_root / "tts-candidate-too-short.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-y", str(short_audio_path)],
        check=True,
        capture_output=True,
    )
    short_media = MediaService(database, workspace).import_file(project_id, short_audio_path, purpose="DIALOGUE_TTS", media_kind="AUDIO")

    with TestClient(create_app(workspace)) as client:
        line_response = client.post(
            f"/api/v1/episodes/{episode['id']}/dialogue-lines",
            json={"code": "DLG-001", "speaker": "周桂兰", "text": "谁在里面？", "pronunciation": {"里面": "li3 mian4"}},
        )
        assert line_response.status_code == 201, line_response.text
        line = line_response.json()["dialogue"]
        text_revision = line["text_revisions"][0]
        assert text_revision["revision_no"] == 1

        invalid_evidence = client.post(
            f"/api/v1/projects/{project_id}/voice-profile-versions",
            json={"code": "voice-a", "title": "Voice A", "voice_ref": "local:test", "license_status": "USER_OWNED", "license_evidence_path_rel": "../outside.json"},
        )
        assert invalid_evidence.status_code == 422
        assert invalid_evidence.json()["error"]["code"] == "VOICE_LICENSE_EVIDENCE_INVALID"
        voice_response = client.post(
            f"/api/v1/projects/{project_id}/voice-profile-versions",
            json={
                "code": "voice-a",
                "title": "Voice A",
                "voice_ref": "local:test",
                "license_status": "USER_OWNED",
                "license_evidence_path_rel": "00_admin/voice-license.json",
            },
        )
        assert voice_response.status_code == 201, voice_response.text
        voice = voice_response.json()["voice_profile"]
        assert len(voice["license_evidence"]["sha256"]) == 64
        short_preview = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision['id']}/tts-candidates",
            json={
                "voice_profile_version_id": voice["id"],
                "media_version_id": short_media["media_version_id"],
                "emotion": "neutral",
                "speech_rate": 1.0,
                "model_ref": "IMPORTED_LOCAL_AUDIO",
                "candidate_kind": "PREVIEW",
            },
        )
        assert short_preview.status_code == 422
        assert short_preview.json()["error"]["code"] == "TTS_PREVIEW_DURATION_INVALID"

        candidate_response = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision['id']}/tts-candidates",
            json={
                "voice_profile_version_id": voice["id"],
                "media_version_id": media["media_version_id"],
                "emotion": "警觉",
                "speech_rate": 0.95,
                "seed": 42,
                "model_ref": "IMPORTED_LOCAL_AUDIO",
                "candidate_kind": "PREVIEW",
            },
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()["candidate"]
        assert candidate["provenance"]["text_hash"] == text_revision["text_hash"]
        assert candidate["provenance"]["voice_license_status"] == "USER_OWNED"
        assert candidate["provenance"]["emotion"] == "警觉"
        assert candidate["provenance"]["speech_rate"] == 0.95
        assert candidate["provenance"]["seed"] == 42
        assert candidate["provenance"]["media_duration_ms"] == 4000
        formal_without_profile = client.post(
            f"/api/v1/dialogue-text-revisions/{text_revision['id']}/tts-candidates",
            json={
                "voice_profile_version_id": voice["id"],
                "media_version_id": media["media_version_id"],
                "emotion": "警觉",
                "speech_rate": 1.0,
                "model_ref": "IMPORTED_LOCAL_AUDIO",
                "candidate_kind": "FORMAL",
            },
        )
        assert formal_without_profile.status_code == 422
        assert formal_without_profile.json()["error"]["code"] == "TTS_FORMAL_PROFILE_REQUIRED"
        selected = client.post(f"/api/v1/tts-candidates/{candidate['id']}:select")
        assert selected.status_code == 201, selected.text
        with database.transaction() as connection:
            connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES ('tts-profile','tts-test','TTS test')")
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
                 status,capability_json,output_contract_json,resource_policy_json)
                VALUES ('tts-profile-v1','tts-profile',1,'TTS','{}','{}','{}','PUBLISHED','{}','{}','{}')"""
            )
            connection.execute("UPDATE voice_profile_versions SET provider_profile_version_id='tts-profile-v1' WHERE id=?", (voice["id"],))
            connection.execute("UPDATE tts_candidates SET candidate_kind='FORMAL' WHERE id=?", (candidate["id"],))
        unapproved_formal = client.post(f"/api/v1/tts-candidates/{candidate['id']}:select")
        assert unapproved_formal.status_code == 422
        assert unapproved_formal.json()["error"]["code"] == "TTS_FORMAL_APPROVAL_REQUIRED"

        revised = client.post(
            f"/api/v1/dialogue-lines/{line['id']}/text-revisions",
            json={"expected_revision_no": 1, "text": "是谁在里面？", "pronunciation": {}},
        )
        assert revised.status_code == 201, revised.text
        assert len(revised.json()["dialogue"]["text_revisions"]) == 2
        stale = client.post(f"/api/v1/tts-candidates/{candidate['id']}:select")
        assert stale.status_code == 422
        assert stale.json()["error"]["code"] == "TTS_CANDIDATE_TEXT_STALE"


def test_tts_candidate_rejects_cross_project_audio(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    first = service.create_project(code="tts_first", title="First", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    second = service.create_project(code="tts_second", title="Second", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(first["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    evidence = workspace.projects_root / str(first["root_rel"]) / "00_admin" / "voice-license.txt"
    evidence.write_text("owned test voice", encoding="utf-8", newline="")
    audio_path = workspace.work_root / "cross-project.wav"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=220:duration=1", "-y", str(audio_path)], check=True, capture_output=True)
    foreign_media = MediaService(database, workspace).import_file(str(second["id"]), audio_path, purpose="DIALOGUE_TTS", media_kind="AUDIO")
    with TestClient(create_app(workspace)) as client:
        line = client.post(f"/api/v1/episodes/{episode['id']}/dialogue-lines", json={"code": "DLG", "speaker": "A", "text": "local"}).json()["dialogue"]
        voice = client.post(
            f"/api/v1/projects/{first['id']}/voice-profile-versions",
            json={"code": "voice", "title": "Voice", "voice_ref": "local", "license_status": "VERIFIED_LOCAL", "license_evidence_path_rel": "00_admin/voice-license.txt"},
        ).json()["voice_profile"]
        response = client.post(
            f"/api/v1/dialogue-text-revisions/{line['text_revisions'][0]['id']}/tts-candidates",
            json={"voice_profile_version_id": voice["id"], "media_version_id": foreign_media["media_version_id"], "emotion": "neutral", "speech_rate": 1.0, "model_ref": "IMPORTED_LOCAL_AUDIO", "candidate_kind": "PREVIEW"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "TTS_CANDIDATE_MEDIA_INVALID"
