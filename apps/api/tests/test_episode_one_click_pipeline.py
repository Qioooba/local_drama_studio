"""Phase-2 acceptance: a zero-touch AUTO_CONTINUE episode production run.

Every front-half human authority (committed script source, applied breakdown,
approved identity pack, production-ready shot revisions, approved keyframes)
is seeded once; the run itself then needs no human interaction: videos are
adopted automatically, TTS is synthesized/finalized/selected, the timeline is
assembled with a dialogue track, and the episode renders through local ffmpeg.
Only the ComfyUI loopback probe is stubbed (no live ComfyUI in tests); video
candidates are pre-seeded so no Comfy jobs are dispatched.
"""
from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.dialogue import DialogueService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.main import create_app


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _make_wav(workspace, target: Path) -> None:
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
         "-ar", "48000", "-ac", "1", "-y", str(target)],
        check=True, capture_output=True,
    )


class _FakeTtsRuntime:
    """SAPI stand-in that writes a real wav so the artifact probe passes."""

    def __init__(self, workspace) -> None:
        self.workspace = workspace

    def synthesize(self, *, voice: str, text: str, output: Path, rate: int, timeout: int) -> None:
        _make_wav(self.workspace, output)


def _seed_published_profile(database, code: str, capability: str, *, parameter_schema_json: str = "{}") -> str:
    version_id = f"exec-{code}-v1"
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO execution_profiles (id,code,title) VALUES (?,?,?)",
            (f"exec-{code}", code, f"{capability} {code}"),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
             status,capability_json,output_contract_json,resource_policy_json)
            VALUES (?,?,1,?,'{}','{}',?,'PUBLISHED','{}','{}','{}')""",
            (version_id, f"exec-{code}", capability, parameter_schema_json),
        )
    return version_id


def _seed_imported_source(workspace, database, project_id: str) -> None:
    source = workspace.work_root / "one-click-script.txt"
    source.write_text("第一场。主角在废墟中醒来。\n警报还没解除，我们不能停。", encoding="utf-8")
    with TestClient(create_app(workspace)) as client:
        imported = client.post(f"/api/v1/projects/{project_id}/imports", json={"source_path": str(source)})
        assert imported.status_code == 201, imported.text
        result = imported.json()["import"]
        committed = client.post(
            f"/api/v1/import-sessions/{result['import_session_id']}:commit",
            json={"expected_preview_hash": result["preview_hash"]},
        )
        assert committed.status_code == 200, committed.text


def _seed_applied_breakdown(database, project_id: str, episode_id: str) -> None:
    draft_id = str(uuid.uuid4())
    now = _now()
    with database.transaction() as connection:
        session_row = connection.execute(
            "SELECT id, source_document_version_id FROM import_sessions WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        source_version_id = str(session_row["source_document_version_id"])
        import_session_id = str(session_row["id"])
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?, '{}', 'APPLIED',?,?,'test',1,'v1')""",
            (draft_id, project_id, source_version_id, import_session_id, '{"episodes": []}', now, now),
        )
        scene_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO scenes (id,project_id,code,title,location,time_of_day,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'SC-001','废墟实验室','废墟','夜',?,?, 'test',1,'v1')""",
            (scene_id, project_id, now, now),
        )
        connection.execute(
            """INSERT INTO script_breakdown_scene_applications
            (id,breakdown_draft_id,episode_id,scene_no,created_scene_id,created_at,created_by)
            VALUES (?,?,?,1,?,?, 'test')""",
            (str(uuid.uuid4()), draft_id, episode_id, scene_id, now),
        )
        connection.execute(
            """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('test','producer','SCRIPT_BREAKDOWN_APPLIED','script_breakdown_draft',?,'验收种子：拆解已应用',?)""",
            (draft_id, '{"episode_id": "%s"}' % episode_id),
        )


def _seed_keyframe(workspace, database, project_id: str, shot_id: str) -> None:
    frame = workspace.work_root / "one-click-keyframe.png"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=1", "-frames:v", "1", "-y", str(frame)],
        check=True, capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        project_id, frame,
        purpose="KEYFRAME", owner_type="SHOT", owner_id=shot_id, media_kind="IMAGE", stage="KEYFRAME",
    )
    reviews = ReviewService(database, workspace)
    version_id = str(media["media_version_id"])
    reviews.machine_check(version_id, actor="acceptance")
    context = reviews.review_context(version_id)
    template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    decision = reviews.submit_review(
        version_id,
        str(template["id"]),
        "APPROVED",
        expected_subject_revision=int(context["subject_revision"]),
        checks=[{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]],
        actor="acceptance",
    )
    assert decision["decision"] == "APPROVED"


def test_one_click_auto_continue_run_produces_rough_cut(workspace, database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_drama.application.episode_production_runs._probe_loopback",
        lambda *args, **kwargs: ("PASS", {}),
    )

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="one_click_pipeline", title="一键成片验收", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=4_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    episode_id = str(episode["id"])
    shot = projects.create_shot(episode_id, "S001", 4_000)
    shot_id = str(shot["id"])

    # Front-half human authorities, seeded once per project/episode.
    _seed_imported_source(workspace, database, project_id)
    _seed_applied_breakdown(database, project_id, episode_id)

    # Character asset + approved three-view identity pack bound to the shot.
    assets = StoryAssetService(database, workspace)
    character = assets.create_asset(project_id, "CHARACTER", "HERO_01", "主角", "验收主角")
    state_id = str(uuid.uuid4())
    now = _now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_asset_states
            (id, project_id, story_asset_id, code, label, state_kind, description, state_json, status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, 'OUTFIT_BASE', '常服', 'OUTFIT', '验收常服', '{}', 'ACTIVE', ?, ?, 'test', 1, 'v1')""",
            (state_id, project_id, str(character["id"]), now, now),
        )
    media_service = MediaService(database, workspace)
    pack_service = CharacterIdentityPackService(database)
    slot_media: dict[str, str] = {}
    for slot, color in (("FRONT", "red"), ("LEFT", "green"), ("RIGHT", "blue")):
        view = workspace.work_root / f"one-click-{slot.lower()}.png"
        subprocess.run(
            [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=1", "-frames:v", "1", "-y", str(view)],
            check=True, capture_output=True,
        )
        imported = media_service.import_file(project_id, view, media_kind="IMAGE")
        WorkspaceAssetService(database, workspace).authorize_media_version(project_id, str(imported["media_version_id"]))
        slot_media[slot] = str(imported["media_version_id"])
    pack = pack_service.create_pack(
        project_id=project_id, story_asset_id=str(character["id"]),
        code="PACK_ONE_CLICK", name="验收身份包",
    )
    version = pack["versions"][0]
    for slot in ("FRONT", "LEFT", "RIGHT"):
        pack_service.set_version_slot(version["id"], slot, slot_media[slot])
    approved = pack_service.approve_pack_version(version["id"], comment="验收人工批准")
    pack_service.bind_shot_identity_pack(shot_id, str(character["id"]), str(approved["id"]))
    # The production preflight also requires a canonical reference and an
    # ACTIVE effective asset state on the shot-character binding.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE story_assets SET canonical_media_version_id=? WHERE id=?",
            (slot_media["FRONT"], str(character["id"])),
        )
        connection.execute(
            """UPDATE shot_asset_bindings SET asset_state_id=?
            WHERE shot_id=? AND asset_id=?""",
            (state_id, shot_id, str(character["id"])),
        )

    # Published profiles (camera + video) before the shot revision references them.
    camera_profile_version = _seed_published_profile(
        database, "camera-acceptance", "CAMERA",
        parameter_schema_json='{"capabilities": {"camera": {"support": "NATIVE"}}}',
    )
    video_profile_version = _seed_published_profile(database, "video-acceptance", "VIDEO_I2V")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO project_profile_bindings
            (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'VIDEO_I2V',?,'ACTIVE',?,?, 'test',1,'v1')""",
            (str(uuid.uuid4()), project_id, video_profile_version, _now(), _now()),
        )
        # The run resolves the per-shot VIDEO_I2V profile through the shot ->
        # episode -> project generation preference chain, not through the
        # coarse project binding above.
        preference_set_id = str(uuid.uuid4())
        preference_version_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO generation_preference_sets
            (id,project_id,owner_type,owner_id,capability,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'PROJECT',?,'VIDEO_I2V',NULL,'ACTIVE',?,?, 'test',1,'v1')""",
            (preference_set_id, project_id, project_id, _now(), _now()),
        )
        connection.execute(
            """INSERT INTO generation_preference_versions
            (id,preference_set_id,version_no,execution_profile_version_id,resolution_mode,settings_json,reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,1,?,'EXPLICIT','{}','验收种子',1,?, 'test','v1')""",
            (preference_version_id, preference_set_id, video_profile_version, _now()),
        )
        connection.execute(
            "UPDATE generation_preference_sets SET current_version_id=? WHERE id=?",
            (preference_version_id, preference_set_id),
        )

    # Production-ready shot revision with a complete director intent.
    shot_studio = shot_studio_command_service(database)
    shot_studio.save_draft_revision(shot_id, {
        "shot_type": "WIDE",
        "composition": {"preset": "RULE_OF_THIRDS", "framing": "FULL", "subject_position": "CENTER", "depth_plan": "MID"},
        "subject_action": "主角从废墟中站起",
        "camera_plan": {"mode": "NATIVE", "shot_type": "WIDE", "movement": "PUSH_IN", "direction": "FORWARD", "intensity": 0.5, "curve": "LINEAR", "profile_version_id": camera_profile_version},
        "target_duration_ms": 4_000,
        "dialogue": "",
        "environment": "废墟实验室",
        "continuity": "开场镜头，与片头字幕相接",
        "creative_intent": "建立空间与主角的脆弱感",
    }, freeze=True)
    shot_studio.mark_ready_shot(shot_id)

    # Approved keyframe (human review decision on a verified image).
    _seed_keyframe(workspace, database, project_id, shot_id)

    # Pre-seeded video candidate so no Comfy job is dispatched.
    video_source = workspace.work_root / "one-click-shot.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(video_source)],
        check=True, capture_output=True,
    )
    video_media = media_service.import_file(
        project_id, video_source,
        purpose="SHOT_VIDEO", owner_type="SHOT", owner_id=shot_id, media_kind="VIDEO", stage="PROXY",
    )
    ReviewService(database, workspace).select_version(str(video_media["media_version_id"]), "PROXY_WINNER")

    # Usable local model file for the preflight model check.
    model_file = workspace.work_root / "one-click-model.bin"
    model_file.write_bytes(b"local model placeholder")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO model_artifacts
            (id,code,kind,machine_path_ref,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'VIDEO_DIFFUSION',?,'ACTIVE',?,?, 'test',1,'v1')""",
            (str(uuid.uuid4()), "video-model-acceptance", str(model_file), _now(), _now()),
        )

    # Voice: sapi profile + character binding + dialogue line for the speaker.
    dialogue = DialogueService(database, workspace)
    root = workspace.projects_root / str(project["root_rel"])
    (root / "00_admin").mkdir(parents=True, exist_ok=True)
    (root / "00_admin" / "voice-license.json").write_text('{"owner":"test"}\n', encoding="utf-8")
    voice = dialogue.create_voice_profile(
        project_id, code="VOICE-HERO", title="主角音色", voice_ref="sapi:Huihui",
        license_status="USER_OWNED", license_evidence_path_rel="00_admin/voice-license.json",
    )
    tts_profile_version = _seed_published_profile(database, "tts-acceptance", "TTS")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE voice_profile_versions SET provider_profile_version_id=? WHERE id=?",
            (tts_profile_version, str(voice["id"])),
        )
    dialogue.bind_character_voice(project_id, str(character["id"]), str(voice["id"]))
    dialogue.create_line(episode_id, code="L001", speaker="主角", text="警报还没解除，我们不能停。", pronunciation={}, shot_id=shot_id)

    # One click: AUTO_CONTINUE from start to delivery.
    service = EpisodeProductionRunService(database, workspace)
    preflight = service.preflight(
        episode_id, tts_enabled=True, production_mode="DRAFT",
        checkpoint_policy="AUTO_CONTINUE", include_front_half=True,
    )
    assert preflight["status"] == "PASS", [check for check in preflight["checks"] if check["blocking"]]

    run = service.start(
        episode_id, idempotency_key="one-click-acceptance-1",
        tts_enabled=True, production_mode="DRAFT",
        checkpoint_policy="AUTO_CONTINUE",
    )
    worker = LocalMediaWorker(database, workspace, tts_runtime=_FakeTtsRuntime(workspace))
    worker.run_until_idle("acceptance-worker", max_jobs=300)

    final_run = service.get(str(run["id"]), include_jobs=True)
    raw_run = service.automation.get_run(str(run["id"]))
    assert final_run["status"] == "SUCCEEDED", {
        "status": final_run.get("status"),
        "tasks": [
            {"key": task.get("item_key"), "status": task.get("status"), "machine": task.get("machine_context")}
            for task in raw_run.get("tasks", [])
        ],
    }
    assert all(task["status"] == "SUCCEEDED" for task in raw_run.get("tasks", []))

    # Assembled timeline: v3 with video + dialogue tracks.
    with database.connect() as connection:
        timeline = connection.execute(
            "SELECT id,input_snapshot_json FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        assert timeline is not None
        snapshot = connection.execute(
            "SELECT track_type, COUNT(*) AS total FROM timeline_items WHERE timeline_revision_id=? GROUP BY track_type",
            (str(timeline["id"]),),
        ).fetchall()
        tracks = {str(row["track_type"]): int(row["total"]) for row in snapshot}
        assert tracks.get("VIDEO") == 1
        assert tracks.get("DIALOGUE") == 1
        assert '"schema_version": "localdrama.timeline-editor.v3"' in str(timeline["input_snapshot_json"]) or "localdrama.timeline-editor.v3" in str(timeline["input_snapshot_json"])
        render_row = connection.execute(
            "SELECT id FROM episode_render_versions WHERE episode_id=? ORDER BY created_at DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        assert render_row is not None, "整集渲染版本未登记"
        selection_rows = connection.execute(
            "SELECT COUNT(*) FROM dialogue_candidate_selections",
        ).fetchone()[0]
        assert int(selection_rows) == 1
