from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.main import create_app
from tests.test_character_voice_batch import _bind_shot_character, _bind_tts_profile, _create_voice, _insert_asset
from tests.test_shot_studio_commands_v2 import _workspace_shot


def test_shot_dialogue_draft_is_versioned_idempotent_and_projected(workspace, database) -> None:
    project, episode, shot = _workspace_shot(workspace, database, "shot_dialogue_v2_draft")
    payload = {
        "code": "DLG-001",
        "speaker": "阿宁",
        "text": "快走。",
        "pronunciation": {"快走": "kuai4 zou3"},
        "expected_shot_revision": 1,
        "idempotency_key": "dialogue-create-1",
    }
    with TestClient(create_app(workspace)) as client:
        created = client.put(f"/api/v2/shots/{shot['id']}/dialogue-draft", json=payload)
        assert created.status_code == 200, created.text
        fact = created.json()["dialogue"]
        assert fact["text_revision"]["revision_no"] == 1
        assert fact["idempotent_replay"] is False

        replay = client.put(f"/api/v2/shots/{shot['id']}/dialogue-draft", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["dialogue"]["line_id"] == fact["line_id"]
        assert replay.json()["dialogue"]["idempotent_replay"] is True

        mismatch = client.put(f"/api/v2/shots/{shot['id']}/dialogue-draft", json={**payload, "text": "不同文本"})
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "DIALOGUE_IDEMPOTENCY_MISMATCH"

        revised = client.put(
            f"/api/v2/shots/{shot['id']}/dialogue-draft",
            json={**payload, "line_id": fact["line_id"], "text": "现在快走。", "expected_text_revision_no": 1, "idempotency_key": "dialogue-revise-1"},
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["dialogue"]["text_revision"]["revision_no"] == 2

        conflict = client.put(
            f"/api/v2/shots/{shot['id']}/dialogue-draft",
            json={**payload, "line_id": fact["line_id"], "text": "过期覆盖", "expected_text_revision_no": 1, "idempotency_key": "dialogue-revise-stale"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "DIALOGUE_TEXT_REVISION_CONFLICT"

        studio = client.get(f"/api/v2/episodes/{episode['id']}/shots/{shot['id']}/studio")
        assert studio.status_code == 200, studio.text
        line = studio.json()["current_shot"]["dialogue"]["lines"][0]
        assert line["id"] == fact["line_id"]
        assert line["current_text"]["revision_no"] == 2
        assert line["current_text"]["text"] == "现在快走。"

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialogue_text_revisions WHERE dialogue_line_id=?", (fact["line_id"],)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (fact["line_id"],)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type='DialogueVersionChanged' AND subject_id=?", (fact["line_id"],)).fetchone()[0] == 2


def test_shot_tts_job_has_canonical_scope_and_working_adoption_is_not_approval(workspace, database) -> None:
    project, episode, shot = _workspace_shot(workspace, database, "shot_dialogue_v2_tts")
    project_id = str(project["id"])
    root = workspace.projects_root / str(project["root_rel"])
    (root / "00_admin" / "voice-license.json").write_text('{"owner":"test"}\n', encoding="utf-8", newline="")
    character_id = _insert_asset(database, project_id, "ANING", "阿宁")
    _bind_shot_character(database, str(shot["id"]), character_id)

    with TestClient(create_app(workspace)) as client:
        voice = _create_voice(client, project_id, "voice-aning", "阿宁青年声线", "sapi:TestVoice")
        _bind_tts_profile(database, str(voice["id"]), "shot-dialogue")
        binding = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": character_id, "voice_profile_version_id": voice["id"]},
        )
        assert binding.status_code == 201, binding.text
        created = client.put(
            f"/api/v2/shots/{shot['id']}/dialogue-draft",
            json={"code": "DLG-001", "speaker": "阿宁", "text": "快走。", "expected_shot_revision": 1, "idempotency_key": "dialogue-tts-line"},
        )
        assert created.status_code == 200, created.text
        line = created.json()["dialogue"]

        submitted = client.post(
            f"/api/v2/dialogue-lines/{line['line_id']}/tts-generations",
            json={"expected_text_revision_no": 1, "voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0, "idempotency_key": "shot-tts-job-1"},
        )
        assert submitted.status_code == 200, submitted.text
        job = submitted.json()["job"]
        assert job == {
            "id": job["id"], "state": "QUEUED", "subject_kind": "DIALOGUE_TEXT_REVISION",
            "scope_project_id": project_id, "scope_episode_id": episode["id"], "scope_shot_id": shot["id"],
            "stage_code": "AUDIO_SUBTITLE", "idempotent_replay": False,
        }
        replay = client.post(
            f"/api/v2/dialogue-lines/{line['line_id']}/tts-generations",
            json={"expected_text_revision_no": 1, "voice_profile_version_id": voice["id"], "emotion": "neutral", "speech_rate": 1.0, "idempotency_key": "shot-tts-job-1"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["job"]["id"] == job["id"]
        assert replay.json()["job"]["idempotent_replay"] is True

        audio_path = workspace.work_root / "shot-dialogue-candidate.wav"
        subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=330:duration=4", "-y", str(audio_path)], check=True, capture_output=True)
        media = MediaService(database, workspace).import_file(project_id, audio_path, purpose="DIALOGUE_TTS", media_kind="AUDIO")
        candidate = DialogueService(database, workspace, media=MediaService(database, workspace)).register_candidate(
            str(line["text_revision"]["id"]), voice_profile_version_id=str(voice["id"]), media_version_id=str(media["media_version_id"]),
            emotion="neutral", speech_rate=1.0, seed=None, model_ref="IMPORTED_LOCAL_AUDIO", candidate_kind="PREVIEW",
        )
        adopted = client.post(
            f"/api/v2/audio-versions/{media['media_version_id']}:adopt-working",
            json={"expected_text_revision_no": 1, "idempotency_key": "adopt-audio-1"},
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["adoption"]["tts_candidate_id"] == candidate["id"]
        assert adopted.json()["adoption"]["status"] == "ADOPTED"
        replay_adoption = client.post(
            f"/api/v2/audio-versions/{media['media_version_id']}:adopt-working",
            json={"expected_text_revision_no": 1, "idempotency_key": "adopt-audio-1"},
        )
        assert replay_adoption.status_code == 200
        assert replay_adoption.json()["adoption"]["idempotent_replay"] is True

        studio = client.get(f"/api/v2/episodes/{episode['id']}/shots/{shot['id']}/studio")
        projected = studio.json()["current_shot"]["dialogue"]["lines"][0]
        assert projected["voice_binding"]["voice_profile_version_id"] == voice["id"]
        assert projected["working_selection"]["tts_candidate_id"] == candidate["id"]
        assert projected["candidates"][0]["selected"] is True

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialogue_candidate_selections WHERE dialogue_line_id=?", (line["line_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM review_decisions WHERE subject_id=?", (media["media_version_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type='AudioWorkingCandidateChanged' AND subject_id=?", (line["line_id"],)).fetchone()[0] == 1
