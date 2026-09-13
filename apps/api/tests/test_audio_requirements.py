from __future__ import annotations

import json
import subprocess
import uuid

import pytest

from local_drama.application.audio_requirements import (
    canonical_audio_requirements,
    canonical_tts_requirements,
)
from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from tests.test_character_voice_batch import (
    _bind_tts_profile,
    _insert_asset,
)


def _episode_with_project(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    return projects, projects.list_episodes(str(season["id"]))[0]


def _replace_current_revision(database, shot_id: str, fields: dict[str, object]) -> str:
    revision_id = str(uuid.uuid4())
    with database.transaction() as connection:
        next_no = int(
            connection.execute(
                "SELECT COALESCE(MAX(revision_no),0)+1 FROM shot_revisions WHERE shot_id=?",
                (shot_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """INSERT INTO shot_revisions
            (id,shot_id,revision_no,fields_json,is_frozen,created_by,revision,schema_version)
            VALUES (?,?,?,?,0,'test',1,'v2')""",
            (revision_id, shot_id, next_no, json.dumps(fields)),
        )
        connection.execute(
            "UPDATE shots SET current_revision_id=? WHERE id=?",
            (revision_id, shot_id),
        )
    return revision_id


def test_audio_requirements_only_read_current_revisions_of_active_shots(workspace, database) -> None:
    projects, episode = _episode_with_project(workspace, database, "audio_current_cues")
    episode_id = str(episode["id"])

    revised = projects.create_shot(episode_id, "S001", 3_000)
    with database.transaction() as connection:
        old_revision_id = str(revised["current_revision_id"])
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps({"music_cue": "historical BGM"}), old_revision_id),
        )
    _replace_current_revision(
        database,
        str(revised["id"]),
        {"music_cue": None, "sfx_cues": [], "caption_cues": {}, "sfx_cue": "  "},
    )

    archived = projects.create_shot(episode_id, "S002", 3_000)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps({"sfx_cues": ["archived SFX"]}), str(archived["current_revision_id"])),
        )
        connection.execute("UPDATE shots SET archived_at='2026-09-12T00:00:00Z' WHERE id=?", (str(archived["id"]),))

    active = projects.create_shot(episode_id, "S003", 3_000)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps({"music_cues": ["current BGM"]}), str(active["current_revision_id"])),
        )
        before_revision_count = int(connection.execute("SELECT COUNT(*) FROM shot_revisions").fetchone()[0])
        requirements = canonical_audio_requirements(connection, episode_id)
        after_revision_count = int(connection.execute("SELECT COUNT(*) FROM shot_revisions").fetchone()[0])

    assert requirements["required_tracks"] == ["BGM"]
    assert requirements["counts"]["music_cues"] == 1
    assert requirements["counts"]["sfx_cues"] == 0
    assert requirements["counts"]["caption_cues"] == 0
    assert after_revision_count == before_revision_count


def test_audio_requirements_report_missing_current_revision(workspace, database) -> None:
    projects, episode = _episode_with_project(workspace, database, "audio_missing_revision")
    shot = projects.create_shot(str(episode["id"]), "S001", 3_000)
    with database.transaction() as connection:
        connection.execute("UPDATE shots SET current_revision_id=NULL WHERE id=?", (str(shot["id"]),))
        with pytest.raises(DomainRuleError) as caught:
            canonical_audio_requirements(connection, str(episode["id"]))

    assert caught.value.code == "SHOT_CURRENT_REVISION_MISSING"


def test_tts_requirements_only_check_speakers_that_need_generation(
    workspace, database,
) -> None:
    _projects, episode = _episode_with_project(workspace, database, "tts_actual_speakers")
    with database.connect() as connection:
        project = connection.execute(
            """SELECT p.id,p.root_rel FROM projects p JOIN seasons se ON se.project_id=p.id
            JOIN episodes e ON e.season_id=se.id WHERE e.id=?""",
            (str(episode["id"]),),
        ).fetchone()
    assert project is not None
    project_id = str(project["id"])
    # Three cast members exist, but only the current dialogue facts can create
    # a TTS requirement.
    alice = _insert_asset(database, project_id, "ALICE", "Alice")
    _insert_asset(database, project_id, "BOB", "Bob")
    _insert_asset(database, project_id, "CAROL", "Carol")
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / "tts-k14-license.json"
    evidence.write_text('{"owner":"test"}\n', encoding="utf-8")
    dialogue = DialogueService(database, workspace)
    voice = dialogue.create_voice_profile(
        project_id,
        code="K14-ALICE",
        title="Alice",
        voice_ref="sapi:Alice",
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/tts-k14-license.json",
    )
    _bind_tts_profile(database, str(voice["id"]), "tts-k14-alice")
    dialogue.bind_character_voice(project_id, alice, str(voice["id"]))
    dialogue.create_line(
        str(episode["id"]), code="DLG-A", speaker="Alice", text="Only Alice speaks.", pronunciation={}
    )

    with database.connect() as connection:
        initial = canonical_tts_requirements(connection, str(episode["id"]))
    assert initial["status"] == "PASS"
    assert initial["generation_required_count"] == 1
    assert initial["pending"][0]["speaker"] == "Alice"

    dialogue.create_line(
        str(episode["id"]), code="DLG-B", speaker="Bob", text="Bob now speaks.", pronunciation={}
    )
    dialogue.create_line(
        str(episode["id"]), code="DLG-N", speaker="旁白", text="Narration is separate.", pronunciation={}
    )
    with database.connect() as connection:
        expanded = canonical_tts_requirements(connection, str(episode["id"]))
    reasons = {item["speaker"]: item["blocked_reason"] for item in expanded["blockers"]}
    assert reasons == {"Bob": "VOICE_UNRESOLVED", "旁白": "NARRATOR_VOICE_REQUIRED"}


def test_tts_requirements_reuse_current_verified_audio_without_voice_generation(
    workspace, database,
) -> None:
    _projects, episode = _episode_with_project(workspace, database, "tts_reuse_current_audio")
    with database.connect() as connection:
        project = connection.execute(
            """SELECT p.id,p.root_rel FROM projects p JOIN seasons se ON se.project_id=p.id
            JOIN episodes e ON e.season_id=se.id WHERE e.id=?""",
            (str(episode["id"]),),
        ).fetchone()
    assert project is not None
    project_id = str(project["id"])
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / "tts-import-license.json"
    evidence.write_text('{"owner":"test"}\n', encoding="utf-8")
    media_service = MediaService(database, workspace)
    dialogue = DialogueService(database, workspace, media=media_service)
    voice = dialogue.create_voice_profile(
        project_id,
        code="IMPORTED-NARRATOR",
        title="Imported narrator source",
        voice_ref="local:imported",
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/tts-import-license.json",
    )
    line = dialogue.create_line(
        str(episode["id"]), code="N001", speaker="旁白", text="Already recorded.", pronunciation={}
    )
    wav = workspace.work_root / "tts-reusable-current.wav"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            "3",
            "-y",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    media = media_service.import_file(project_id, wav, media_kind="AUDIO")
    candidate = dialogue.register_candidate(
        str(line["text_revisions"][-1]["id"]),
        voice_profile_version_id=str(voice["id"]),
        media_version_id=str(media["media_version_id"]),
        emotion="NEUTRAL",
        speech_rate=1.0,
        seed=None,
        model_ref="IMPORTED_LOCAL_AUDIO",
        candidate_kind="PREVIEW",
    )
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO dialogue_candidate_selections
            (id,dialogue_line_id,tts_candidate_id,source_text_revision_id,created_at,updated_at,
             created_by,revision,schema_version)
            VALUES (?,?,?,?, 'now','now','test',1,'v2')""",
            (
                str(uuid.uuid4()),
                str(line["id"]),
                str(candidate["id"]),
                str(line["text_revisions"][-1]["id"]),
            ),
        )
        requirements = canonical_tts_requirements(connection, str(episode["id"]))

    assert requirements["status"] == "PASS"
    assert requirements["generation_required_count"] == 0
    assert requirements["reusable_count"] == 1
    assert requirements["items"][0]["reusable_media_version_id"] == media["media_version_id"]
