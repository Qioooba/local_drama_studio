from __future__ import annotations

import subprocess
import uuid

import pytest

from local_drama.api.schemas.production_sessions_v2 import ProductionSessionEpisodeConfirmResponse
from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.application.production_choices import (
    ProductionChoiceService,
    session_keyframe_for_shot,
)
from local_drama.application.production_session_review import ProductionSessionReviewService
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)


def test_machine_tts_choice_is_session_scoped_and_drives_session_timeline_input(
    workspace, database,
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="session_tts_choice",
        title="会话临时配音",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(
        str(projects.list_seasons(str(project["id"]))[0]["id"])
    )[0]
    episode_id = str(episode["id"])
    shot = projects.create_shot(episode_id, "S001", 4_000)
    dialogue = DialogueService(database, workspace)
    line = dialogue.create_line(
        episode_id,
        code="L001",
        speaker="主角",
        text="机器继续工作。",
        pronunciation={},
        shot_id=str(shot["id"]),
    )
    text_revision_id = str(line["text_revisions"][-1]["id"])
    license_path = workspace.projects_root / str(project["root_rel"]) / "00_admin" / "voice-license.json"
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text('{"owner":"test"}\n', encoding="utf-8")
    voice = dialogue.create_voice_profile(
        str(project["id"]),
        code="VOICE-HERO",
        title="主角音色",
        voice_ref="sapi:Huihui",
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/voice-license.json",
    )
    audio_source = workspace.work_root / "session-tts-choice.wav"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-y",
            str(audio_source),
        ],
        check=True,
        capture_output=True,
    )
    audio_version_id = str(
        MediaService(database, workspace).import_file(
            str(project["id"]),
            audio_source,
            purpose="DIALOGUE_TTS",
            owner_type="DIALOGUE_TEXT_REVISION",
            owner_id=text_revision_id,
            media_kind="AUDIO",
            stage="FORMAL",
        )["media_version_id"]
    )
    candidate_id = str(uuid.uuid4())
    now = "2026-09-21T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tts_candidates
               (id,dialogue_text_revision_id,voice_profile_version_id,media_version_id,
                emotion,speech_rate,seed,model_ref,candidate_kind,provenance_json,status,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES (?,?,?,?, 'neutral',1.0,NULL,'WINDOWS_SAPI_LOCAL','FORMAL','{}','READY',?,?,?,1,'v2')""",
            (candidate_id, text_revision_id, str(voice["id"]), audio_version_id, now, now, "test"),
        )
    sessions = ProductionSessionService(database)
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [episode_id],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": True,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    plan = sessions.plan(str(project["id"]), request)
    session = sessions.create(
        str(project["id"]),
        {**request, "expected_plan_hash": plan["plan_hash"]},
        idempotency_key="session-tts-choice-create",
    )["session"]

    choice = ProductionChoiceService(database, workspace).record_tts_choice(
        str(session["id"]),
        episode_id,
        str(line["id"]),
        candidate_id,
        actor="test-machine",
    )

    assert choice["selection_authority"] == "MACHINE_TEMPORARY"
    assert choice["media_version_id"] == audio_version_id
    with database.connect() as connection:
        global_selection_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM dialogue_candidate_selections WHERE dialogue_line_id=?",
                (line["id"],),
            ).fetchone()[0]
        )
        stored = connection.execute(
            """SELECT target_kind,slot_role,candidate_id,choice_type,selection_state,
                      human_review_decision_id,reason_json
               FROM production_choices WHERE id=?""",
            (choice["id"],),
        ).fetchone()
    assert global_selection_count == 0
    assert stored["target_kind"] == "DIALOGUE_LINE"
    assert stored["slot_role"] == "TTS_AUDIO"
    assert stored["candidate_id"] == audio_version_id
    assert stored["choice_type"] == "MACHINE"
    assert stored["selection_state"] == "TEMPORARY"
    assert stored["human_review_decision_id"] is None

    timeline = TimelineService(database, workspace)
    ordinary = timeline._dialogue_selection_rows(episode_id)
    scoped = timeline._dialogue_selection_rows(episode_id, str(session["id"]))
    assert ordinary[0]["selection_id"] is None
    assert scoped[0]["selection_id"] == choice["id"]
    assert scoped[0]["tts_candidate_id"] == candidate_id
    assert scoped[0]["media_version_id"] == audio_version_id
    replay_batch = dialogue.submit_episode_tts_batch(
        episode_id,
        idempotency_key_prefix="session-tts-reuse",
        production_session_id=str(session["id"]),
    )
    assert replay_batch["counts"] == {"submitted": 0, "skipped": 1, "failed": 0}
    assert replay_batch["skipped"][0]["reason"] == "CURRENT_AUDIO_REUSABLE"
    with database.connect() as connection:
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE type='TTS_GENERATION' AND scope_episode_id=?",
                (episode_id,),
            ).fetchone()[0]
        ) == 0
    draft = timeline.plan_tts_subtitle_draft(
        episode_id,
        production_session_id=str(session["id"]),
    )
    assert draft["cues"][0]["text"] == "机器继续工作。"
    assert draft["evidence"][0]["selection_id"] == choice["id"]


def test_machine_keyframe_choice_is_session_scoped_and_never_writes_human_approval(
    workspace, database,
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="session_choice",
        title="机器临时选择",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(
        str(projects.list_seasons(str(project["id"]))[0]["id"])
    )[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    profile = next(
        item
        for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
        if item["capability"] == "VIDEO_T2V"
    )
    image = workspace.work_root / "session-choice.png"
    image.write_bytes(PNG)
    imported = MediaService(database, workspace).import_file(
        str(project["id"]), image, media_kind="IMAGE"
    )
    media_version_id = str(imported["media_version_id"])

    sessions = ProductionSessionService(database)
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [str(episode["id"])],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": True,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    plan = sessions.plan(str(project["id"]), request)
    session = sessions.create(
        str(project["id"]),
        {**request, "expected_plan_hash": plan["plan_hash"]},
        idempotency_key="session-choice-create",
    )["session"]

    with database.transaction() as connection:
        connection.execute(
            "UPDATE media_versions SET stage='KEYFRAME',integrity_status='VERIFIED' WHERE id=?",
            (media_version_id,),
        )
        connection.execute(
            """INSERT INTO shot_keyframe_generation_batches
               (id,project_id,episode_id,frame_strategy,candidate_count,status,plan_hash,
                idempotency_key,selected_shot_count,queued_count,created_at,updated_at,created_by)
               VALUES ('choice-batch',?,?,'FIRST_ONLY',2,'RUNNING',?,'choice-batch',1,2,?,?, 'test')""",
            (project["id"], episode["id"], "a" * 64, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            """INSERT INTO shot_keyframe_generation_batch_items
               (id,batch_id,shot_id,shot_revision,frame_role,candidate_index,profile_version_id,
                status,prompt_snapshot,input_snapshot_json,media_version_id,created_at,updated_at)
               VALUES ('choice-item','choice-batch',?,?,'FIRST_FRAME',1,?,'SUCCEEDED','prompt','{}',?,?,?)""",
            (
                shot["id"],
                shot["revision"],
                profile["version_id"],
                media_version_id,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )

    report, produced_bytes = ProductionChoiceService(database).ensure_keyframes(
        str(session["id"]),
        str(episode["id"]),
        actor="test-machine",
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert report["machine_check"]["human_approval_written"] is False
    assert report["produced"]["items"][0]["selection_authority"] == "MACHINE_TEMPORARY"
    with database.connect() as connection:
        choice = connection.execute(
            "SELECT * FROM production_choices WHERE session_id=?",
            (session["id"],),
        ).fetchone()
        resolved = session_keyframe_for_shot(connection, str(session["id"]), str(shot["id"]))
        review_count = connection.execute(
            "SELECT COUNT(*) FROM review_decisions WHERE subject_id=?",
            (media_version_id,),
        ).fetchone()[0]
        asset = connection.execute(
            "SELECT approved_version_id FROM media_assets WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)",
            (media_version_id,),
        ).fetchone()
    assert choice["choice_type"] == "MACHINE"
    assert choice["selection_state"] == "TEMPORARY"
    assert choice["human_review_decision_id"] is None
    assert resolved is not None
    assert resolved["media_version_id"] == media_version_id
    assert resolved["human_approved"] is False
    assert review_count == 0
    assert asset["approved_version_id"] is None

    video_source = workspace.work_root / "session-video-choice.png"
    video_source.write_bytes(PNG)
    imported_video = MediaService(database, workspace).import_file(
        str(project["id"]), video_source, media_kind="IMAGE"
    )
    video_version_id = str(imported_video["media_version_id"])
    with database.transaction() as connection:
        connection.execute(
            """UPDATE media_assets SET owner_type='SHOT',owner_id=?,purpose='SHOT_VIDEO_CANDIDATE',
               media_kind='VIDEO' WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)""",
            (shot["id"], video_version_id),
        )
        connection.execute(
            "UPDATE media_versions SET stage='FORMAL',mime_type='video/mp4',integrity_status='VERIFIED' WHERE id=?",
            (video_version_id,),
        )
        connection.execute(
            """INSERT INTO machine_check_runs
               (id,subject_type,subject_id,policy_version,status,created_by)
               VALUES ('choice-video-qc','MEDIA_VERSION',?,'test-policy','PASS','test')""",
            (video_version_id,),
        )

    alternate_source = workspace.work_root / "session-video-choice-alternate.png"
    alternate_source.write_bytes(PNG + b"\x00")
    imported_alternate = MediaService(database, workspace).import_file(
        str(project["id"]), alternate_source, media_kind="IMAGE"
    )
    alternate_video_id = str(imported_alternate["media_version_id"])
    with database.transaction() as connection:
        connection.execute(
            """UPDATE media_assets SET owner_type='SHOT',owner_id=?,purpose='SHOT_VIDEO_CANDIDATE',
               media_kind='VIDEO' WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)""",
            (shot["id"], alternate_video_id),
        )
        connection.execute(
            "UPDATE media_versions SET stage='FORMAL',mime_type='video/mp4',integrity_status='VERIFIED' WHERE id=?",
            (alternate_video_id,),
        )
        alternate_asset = connection.execute(
            """SELECT ma.id,ma.revision FROM media_assets ma
               JOIN media_versions mv ON mv.media_asset_id=ma.id WHERE mv.id=?""",
            (alternate_video_id,),
        ).fetchone()
        connection.execute(
            """INSERT INTO selections
               (id,media_asset_id,media_version_id,selection_type,source_revision,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES ('preexisting-human-selection',?,?,'FORMAL_SELECTION',?,
                       '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','human-reviewer',1,'v2')""",
            (alternate_asset["id"], alternate_video_id, int(alternate_asset["revision"])),
        )
        connection.execute(
            """UPDATE media_assets SET selected_version_id=?,version_counter=version_counter+1,
               revision=revision+1 WHERE id=?""",
            (alternate_video_id, alternate_asset["id"]),
        )
        connection.execute(
            """INSERT INTO machine_check_runs
               (id,subject_type,subject_id,policy_version,status,created_by)
               VALUES ('choice-video-qc-alternate','MEDIA_VERSION',?,'test-policy','PASS','test')""",
            (alternate_video_id,),
        )

    video_choice = ProductionChoiceService(database).record_video_choice(
        str(session["id"]),
        str(episode["id"]),
        str(shot["id"]),
        video_version_id,
        "choice-video-qc",
        actor="test-machine",
    )

    assert video_choice["selection_authority"] == "MACHINE_TEMPORARY"
    assert video_choice["human_approved"] is False
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT slot_role,choice_type,selection_state,human_review_decision_id FROM production_choices WHERE session_id=? ORDER BY slot_role",
            (session["id"],),
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "slot_role": "FIRST_FRAME",
            "choice_type": "MACHINE",
            "selection_state": "TEMPORARY",
            "human_review_decision_id": None,
        },
        {
            "slot_role": "VIDEO",
            "choice_type": "MACHINE",
            "selection_state": "TEMPORARY",
            "human_review_decision_id": None,
        },
    ]

    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='WAITING_USER',current_stage='ASSETS' WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            "UPDATE production_session_items SET state='WAITING',current_stage='WAITING_REVIEW' WHERE session_id=?",
            (session["id"],),
        )
        connection.execute(
            """INSERT INTO timeline_revisions
               (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status)
               VALUES ('human-timeline',?,1,'[]','{}',?,'FROZEN')""",
            (episode["id"], "a" * 64),
        )
        connection.execute(
            """INSERT INTO timeline_revisions
               (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status)
               VALUES ('choice-timeline',?,2,'[]',?,?,'FROZEN')""",
            (
                episode["id"],
                f'{{"production_session_id":"{session["id"]}"}}',
                "b" * 64,
            ),
        )
        connection.execute(
            """INSERT INTO timeline_items
               (id,timeline_revision_id,track_type,media_version_id,start_us,end_us,parameters_json)
               VALUES ('choice-timeline-item','choice-timeline','VIDEO',?,0,4000000,?)""",
            (video_version_id, f'{{"shot_id":"{shot["id"]}"}}'),
        )

    review = ProductionSessionReviewService(database).inspect(
        str(session["id"]), cursor=0, limit=10
    )
    assert review["human_approval_written"] is False
    assert review["summary"]["temporary_choice_count"] == 2
    assert review["items"][0]["timeline_choice_consistency"]["status"] == "MATCH"
    assert review["items"][0]["review_status"] == "BLOCKED"
    assert {item["code"] for item in review["items"][0]["blockers"]} == {
        "EPISODE_PREVIEW_RENDER_MISSING"
    }
    assert review["items"][0]["repair_plan"]["recommended_strategy"] == "RECOMPOSE_ONLY"
    assert review["items"][0]["repair_plan"]["can_retry_now"] is True
    assert review["items"][0]["allowed_actions"] == ["REQUEST_LOCAL_RETRY"]

    with database.connect() as connection:
        video_choice_row = connection.execute(
            "SELECT id,revision FROM production_choices WHERE session_id=? AND slot_role='VIDEO'",
            (session["id"],),
        ).fetchone()
        global_selection_before = [
            dict(row)
            for row in connection.execute(
                """SELECT id,media_asset_id,media_version_id,selection_type,source_revision,
                          created_at,updated_at,created_by,revision,schema_version
                   FROM selections ORDER BY id"""
            ).fetchall()
        ]
        selected_assets_before = [
            dict(row)
            for row in connection.execute(
                """SELECT id,selected_version_id,version_counter,revision,updated_at
                   FROM media_assets WHERE project_id=? ORDER BY id""",
                (project["id"],),
            ).fetchall()
        ]
    rerolled = ProductionChoiceService(database).reroll(
        str(session["id"]),
        str(video_choice_row["id"]),
        {
            "expected_session_revision": 1,
            "expected_choice_revision": int(video_choice_row["revision"]),
            "actor": "reviewer",
        },
        idempotency_key="reroll-video-choice",
    )
    rerolled_video_id = str(rerolled["choice"]["candidate_id"])
    assert rerolled_video_id != video_version_id
    assert rerolled["outcome"] == "PREVIEW_REBUILD_QUEUED"
    assert rerolled["session"]["status"] == "RUNNING"
    with database.connect() as connection:
        global_selection_after = [
            dict(row)
            for row in connection.execute(
                """SELECT id,media_asset_id,media_version_id,selection_type,source_revision,
                          created_at,updated_at,created_by,revision,schema_version
                   FROM selections ORDER BY id"""
            ).fetchall()
        ]
        selected_assets_after = [
            dict(row)
            for row in connection.execute(
                """SELECT id,selected_version_id,version_counter,revision,updated_at
                   FROM media_assets WHERE project_id=? ORDER BY id""",
                (project["id"],),
            ).fetchall()
        ]
        timeline_status_after = connection.execute(
            "SELECT status FROM timeline_revisions WHERE id='choice-timeline'"
        ).fetchone()[0]
        human_timeline_status_after = connection.execute(
            "SELECT status FROM timeline_revisions WHERE id='human-timeline'"
        ).fetchone()[0]
    assert global_selection_after == global_selection_before
    assert selected_assets_after == selected_assets_before
    assert timeline_status_after == "STALE"
    assert human_timeline_status_after == "FROZEN"

    with database.transaction() as connection:
        connection.execute(
            """UPDATE timeline_items SET media_version_id=? WHERE id='choice-timeline-item'""",
            (rerolled_video_id,),
        )
        connection.execute(
            "UPDATE timeline_revisions SET status='FROZEN' WHERE id='choice-timeline'"
        )
        connection.execute(
            """UPDATE production_session_items SET state='WAITING',current_stage='WAITING_REVIEW'
               WHERE session_id=?""",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_sessions SET status='WAITING_REVIEW',current_stage='WAITING_REVIEW'
               WHERE id=?""",
            (session["id"],),
        )

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO episode_render_versions
               (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,
                duration_ms,mime_type,created_by)
               VALUES ('choice-preview',?,'choice-timeline','renders/choice.mp4',?,'{}','VERIFIED',
                       4000,'video/mp4','test')""",
            (episode["id"], "c" * 64),
        )
        connection.execute(
            """INSERT INTO review_decisions
               (id,subject_type,subject_id,review_template_version_id,decision,comment,
                subject_revision,is_stale,created_by)
               VALUES ('choice-preview-approval','EPISODE_RENDER_VERSION','choice-preview',
                       'test-template','APPROVED','preview approved',1,0,'reviewer')"""
        )
        choice_rows = connection.execute(
            "SELECT id,candidate_id FROM production_choices WHERE session_id=? ORDER BY id",
            (session["id"],),
        ).fetchall()
        approvals = []
        for index, choice_row in enumerate(choice_rows, start=1):
            decision_id = f"choice-approval-{index}"
            connection.execute(
                """INSERT INTO review_decisions
                   (id,subject_type,subject_id,review_template_version_id,decision,comment,
                    subject_revision,is_stale,created_by)
                   VALUES (?,'MEDIA_VERSION',?,'test-template','APPROVED','test approval',1,0,'reviewer')""",
                (decision_id, choice_row["candidate_id"]),
            )
            approvals.append(
                {
                    "production_choice_id": str(choice_row["id"]),
                    "review_decision_id": decision_id,
                }
            )

    ready_review = ProductionSessionReviewService(database).inspect(
        str(session["id"]), cursor=0, limit=10
    )
    assert ready_review["items"][0]["preview_render"]["human_approval_current"] is True
    assert ready_review["items"][0]["preview_render"]["available_human_approval_id"] == "choice-preview-approval"

    with database.transaction() as connection:
        connection.execute("DELETE FROM review_decisions WHERE id='choice-preview-approval'")
    with pytest.raises(DomainRuleError) as missing_render_approval:
        ProductionSessionReviewService(database).confirm_episode(
            str(session["id"]),
            str(episode["id"]),
            {"expected_revision": 2, "approvals": approvals, "actor": "reviewer"},
            idempotency_key="confirm-choice-without-render-approval",
        )
    assert missing_render_approval.value.code == "EPISODE_RENDER_APPROVAL_REQUIRED"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO review_decisions
               (id,subject_type,subject_id,review_template_version_id,decision,comment,
                subject_revision,is_stale,created_by)
               VALUES ('choice-preview-approval','EPISODE_RENDER_VERSION','choice-preview',
                       'test-template','APPROVED','preview approved',1,0,'reviewer')"""
        )

    confirmed = ProductionSessionReviewService(database).confirm_episode(
        str(session["id"]),
        str(episode["id"]),
        {"expected_revision": 2, "approvals": approvals, "actor": "reviewer"},
        idempotency_key="confirm-choice-episode",
    )
    replay = ProductionSessionReviewService(database).confirm_episode(
        str(session["id"]),
        str(episode["id"]),
        {"expected_revision": 2, "approvals": approvals, "actor": "reviewer"},
        idempotency_key="confirm-choice-episode",
    )
    assert confirmed["outcome"] == "SESSION_COMPLETED"
    assert confirmed["session"]["status"] == "COMPLETED"
    assert confirmed["confirmed_choice_count"] == 2
    assert confirmed["preview_render_review_decision_id"] == "choice-preview-approval"
    assert ProductionSessionEpisodeConfirmResponse.model_validate(
        confirmed
    ).preview_render_review_decision_id == "choice-preview-approval"
    assert replay["idempotent_replay"] is True
    with database.connect() as connection:
        confirmed_rows = connection.execute(
            "SELECT choice_type,selection_state,human_review_decision_id FROM production_choices WHERE session_id=?",
            (session["id"],),
        ).fetchall()
    assert all(row["choice_type"] == "HUMAN" for row in confirmed_rows)
    assert all(row["selection_state"] == "CONFIRMED" for row in confirmed_rows)
    assert all(row["human_review_decision_id"] for row in confirmed_rows)
