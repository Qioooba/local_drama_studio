from __future__ import annotations

import base64
import subprocess
import time
import uuid

from fastapi.testclient import TestClient

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.shot_groups import ShotGroupService
from local_drama.application.shot_studio import ShotStudioQueryService
from local_drama.application.timeline import TimelineService
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.infrastructure.database.shot_studio_repository import SqliteShotStudioReadRepository
from local_drama.main import create_app
from tests.test_breakdown_apply import _persisted_draft
from tests.test_generation_variants import _project
from tests.test_qc_auto_reroll_policy import _child
from tests.test_qc_auto_reroll_policy import _context as _generation_context

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def _studio(database) -> ShotStudioQueryService:
    return ShotStudioQueryService(SqliteShotStudioReadRepository(database))


def _project_episode(workspace, database, code: str):
    project = _project(workspace, database, code)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    return project, episode, projects


def test_director_desk_uses_authoritative_scene_group_and_excludes_archived(workspace, database) -> None:
    project, episode, projects = _project_episode(workspace, database, "director_authority")
    project_id, episode_id = str(project["id"]), str(episode["id"])
    real_scene = projects.create_scene(project_id, "SCENE_REAL", "真实场景")
    projects.bind_episode_scene_range(episode_id, str(real_scene["id"]), 1, 10, 40, "第一场")
    active = projects.create_shot(episode_id, "SHOT_001", 2400, "MEDIUM")
    archived = projects.create_shot(episode_id, "SHOT_SPLIT_PARENT", 3000, "WIDE")
    shot_studio_command_service(database).save_draft_revision(
        str(active["id"]),
        {
            "scene_id": "legacy-scene-must-not-win",
            "group_id": "legacy-group-must-not-win",
            "source_text": "原文证据",
        },
    )
    active = projects.get_shot(str(active["id"]))
    groups = ShotGroupService(database)
    groups.assign_scene(
        shot_id=str(active["id"]),
        scene_id=str(real_scene["id"]),
        expected_revision=int(active["revision"]),
    )
    group = groups.create_group(
        episode_id=episode_id,
        kind="BEAT",
        code="BEAT_001",
        title="真实节拍",
        scene_id=str(real_scene["id"]),
        metadata={},
        order_key=None,
    )
    groups.replace_members(group_id=str(group["id"]), shot_ids=[str(active["id"])], expected_revision=int(group["revision"]))
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shots SET archived_at='2026-08-20T00:00:00Z',source_shot_id=? WHERE id=?",
            (active["id"], archived["id"]),
        )

    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v2/episodes/{episode_id}/shots/{active['id']}/studio",
        )
    assert response.status_code == 200
    body = response.json()
    assert body["request_shape"] == "bounded_shot_studio_v2"
    assert body["allowed_actions"]["write_review_decision"] is False
    assert body["review_handoff"]["write_owner"] == "REVIEW_WORKSPACE"
    assert "permissions" not in body
    assert body["episode"]["shot_count"] == 1
    assert body["shot_nav"]["total"] == 1
    assert [item["id"] for item in body["shot_nav"]["items"]] == [active["id"]]
    nav = body["shot_nav"]["items"][0]
    assert (nav["scene_id"], nav["scene_code"], nav["scene_title"]) == (
        real_scene["id"],
        "SCENE_REAL",
        "真实场景",
    )
    assert (nav["group_id"], nav["group_code"], nav["group_title"]) == (
        group["id"],
        "BEAT_001",
        "真实节拍",
    )
    assert body["current_shot"]["shot"]["scene_id"] == real_scene["id"]
    assert body["current_shot"]["source_context"]["scene_id"] == real_scene["id"]
    assert body["current_shot"]["source_context"]["source_range"]["source_start"] == 10


def test_legacy_director_desk_read_route_is_retired(workspace, database) -> None:
    project, episode, projects = _project_episode(workspace, database, "director_explicit_shot")
    projects.create_shot(str(episode["id"]), "SHOT_001", 2400, "MEDIUM")

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/episodes/{episode['id']}/director-desk")

    assert response.status_code == 404


def test_openapi_exposes_only_the_typed_shot_studio_read_contract(workspace) -> None:
    schema = create_app(workspace).openapi()
    assert "/api/v2/episodes/{episode_id}/shots/{shot_id}/studio" in schema["paths"]
    assert "/api/v1/projects/{project_id}/episodes/{episode_id}/director-desk" not in schema["paths"]
    operation = schema["paths"]["/api/v2/episodes/{episode_id}/shots/{shot_id}/studio"]["get"]
    assert operation["operationId"] == "getShotStudioV2"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ShotStudioResponse")


def test_director_desk_suggests_grounded_scene_and_previous_shot_context(workspace, database) -> None:
    project, episode, projects = _project_episode(workspace, database, "director_intent_suggestions")
    scene = projects.create_scene(
        str(project["id"]),
        "LIGHTHOUSE_NIGHT",
        "海边旧灯塔",
        location="灯塔内",
        time_of_day="夜景",
    )
    previous = projects.create_shot(str(episode["id"]), "SHOT_001", 2400, "MEDIUM")
    current = projects.create_shot(str(episode["id"]), "SHOT_002", 2400, "CLOSEUP")
    previous_revision = shot_studio_command_service(database).save_draft_revision(
        str(previous["id"]),
        {
            "environment": "冷色调，雾气弥漫",
            "continuity": "男主身着风衣，左肩受伤",
            "performance": {"blocking_summary": "男主站在窗边"},
        },
    )
    with database.transaction() as connection:
        connection.execute("UPDATE shots SET scene_id=? WHERE id IN (?,?)", (scene["id"], previous["id"], current["id"]))
    shot_studio_command_service(database).save_draft_revision(
        str(current["id"]),
        {
            "suggestion_sources": {"continuity": {"source_revision": previous_revision["id"]}},
        },
    )

    result = _studio(database).studio(
        str(episode["id"]),
        str(current["id"]),
        nav_radius=2,
    )
    suggestions = result["current_shot"]["intent_suggestions"]
    assert suggestions["environment"]["value"] == "灯塔内；夜景"
    assert suggestions["environment"]["source_label"] == "LIGHTHOUSE_NIGHT · 海边旧灯塔"
    assert suggestions["environment"]["source_kind"] == "SCENE"
    assert suggestions["environment"]["source_revision"] == "1"
    assert suggestions["environment"]["stale"] is False
    assert suggestions["continuity"]["eligible"] is True
    assert suggestions["continuity"]["source_label"] == "SHOT_001"
    assert suggestions["continuity"]["value"] == "男主身着风衣，左肩受伤；环境：冷色调，雾气弥漫；站位：男主站在窗边"
    assert suggestions["continuity"]["stale"] is False

    shot_studio_command_service(database).save_draft_revision(str(previous["id"]), {"continuity": "男主已放下风衣"})
    refreshed = _studio(database).studio(
        str(episode["id"]),
        str(current["id"]),
        nav_radius=2,
    )
    assert refreshed["current_shot"]["intent_suggestions"]["continuity"]["stale"] is True
    assert "上一镜版本已更新" in refreshed["current_shot"]["intent_suggestions"]["continuity"]["stale_reason"]


def test_director_desk_maps_applied_breakdown_shot_to_grounded_script_suggestion(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]), scene_nos=[1])
    with database.connect() as connection:
        shot = connection.execute(
            "SELECT id FROM shots WHERE episode_id=? AND code='EPISODE_001-01-01'",
            (episode["id"],),
        ).fetchone()
    result = _studio(database).studio(
        str(episode["id"]),
        str(shot["id"]),
        nav_radius=2,
    )
    suggestion = result["current_shot"]["intent_suggestions"]["script"]
    assert suggestion["subject_action"] == "开门"
    assert suggestion["creative_intent"] == "近景；母亲迎回孩子"
    assert suggestion["dialogue"] == "母亲：你回来了。"
    assert suggestion["source_label"].endswith("场 1 镜 1")
    assert len(suggestion["source_fingerprint"]) == 64
    assert suggestion["stale"] is False


def test_director_desk_contract_handles_empty_revision_and_stale_candidate(workspace, database) -> None:
    context = _generation_context(workspace, database, "director_candidate_contract")
    source = workspace.work_root / "director-candidate.png"
    source.write_bytes(PNG)
    imported = MediaService(database, workspace).import_file(
        context["project_id"],
        source,
        purpose="CANDIDATE",
        owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"],
        media_kind="IMAGE",
        stage="KEYFRAME",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE generation_variants SET is_stale=1,stale_reason='asset_reference_changed' WHERE id=?",
            (context["variant_id"],),
        )
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v2/episodes/{context['episode_id']}/shots/{context['shot_id']}/studio",
        )
    assert response.status_code == 200
    body = response.json()
    assert body["current_shot"]["current_revision"]["fields"] == {}
    assert body["current_shot"]["source_context"]["scene_id"] is None
    candidate = next(item for item in body["current_shot"]["candidates"] if item["id"] == context["variant_id"])
    assert candidate["media_version_id"] == imported["media_version_id"]
    assert candidate["is_stale"] is True
    assert candidate["stale_reason"] == "asset_reference_changed"


def test_director_desk_latest_selection_supersedes_history_and_undo_reloads(workspace, database) -> None:
    context = _generation_context(workspace, database, "director_selection_supersedes")
    older_source = workspace.work_root / "director-older-candidate.png"
    newer_source = workspace.work_root / "director-newer-candidate.png"
    older_source.write_bytes(PNG + b"older")
    newer_source.write_bytes(PNG + b"newer")
    older = MediaService(database, workspace).import_file(
        context["project_id"],
        older_source,
        purpose="CANDIDATE",
        owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"],
        media_kind="IMAGE",
        stage="KEYFRAME",
    )
    newer_variant_id = _child(database, context, context["variant_id"], 2)
    newer = MediaService(database, workspace).import_file(
        context["project_id"],
        newer_source,
        purpose="CANDIDATE",
        owner_type="GENERATION_VARIANT",
        owner_id=newer_variant_id,
        media_kind="IMAGE",
        stage="KEYFRAME",
    )

    desk_url = f"/api/v2/episodes/{context['episode_id']}/shots/{context['shot_id']}/studio"
    with TestClient(create_app(workspace)) as client:
        for media_version_id in (newer["media_version_id"], older["media_version_id"]):
            selected = client.post(f"/api/v2/media-versions/{media_version_id}:adopt")
            assert selected.status_code == 200, selected.text
        restored_old = client.get(desk_url).json()["current_shot"]
        assert restored_old["current_media"]["media_version_id"] == older["media_version_id"]
        assert restored_old["selected_variant"]["id"] == context["variant_id"]
        assert [item["media_version_id"] for item in restored_old["candidates"] if item["selected"]] == [older["media_version_id"]]

        undo = client.post(f"/api/v2/media-versions/{newer['media_version_id']}:adopt")
        assert undo.status_code == 200, undo.text
        restored_new = client.get(desk_url).json()["current_shot"]
        assert restored_new["current_media"]["media_version_id"] == newer["media_version_id"]
        assert restored_new["selected_variant"]["id"] == newer_variant_id
        assert [item["media_version_id"] for item in restored_new["candidates"] if item["selected"]] == [newer["media_version_id"]]

    with database.connect() as connection:
        slot = connection.execute(
            """SELECT id,slot_type,media_version_id,revision FROM shot_working_media_slots
            WHERE shot_id=? AND slot_type='KEYFRAME'""",
            (context["shot_id"],),
        ).fetchone()
        audit_count = connection.execute(
            """SELECT COUNT(*) FROM audit_events
            WHERE action='SHOT_WORKING_VERSION_ADOPTED' AND subject_id=?""",
            (slot["id"],),
        ).fetchone()[0]
    assert slot["slot_type"] == "KEYFRAME"
    assert slot["media_version_id"] == newer["media_version_id"]
    assert slot["revision"] == 3
    assert audit_count == 3


def test_director_desk_navigator_exposes_only_selected_video_for_timeline(workspace, database) -> None:
    context = _generation_context(workspace, database, "director_nav_video")
    image_source = workspace.work_root / "director-nav-image.png"
    image_source.write_bytes(PNG)
    image = MediaService(database, workspace).import_file(
        context["project_id"],
        image_source,
        purpose="KEYFRAME",
        owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"],
        media_kind="IMAGE",
        stage="KEYFRAME",
    )
    video_source = workspace.work_root / "director-nav-video.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(video_source)],
        check=True,
        capture_output=True,
    )
    video = MediaService(database, workspace).import_file(
        context["project_id"],
        video_source,
        purpose="CANDIDATE",
        owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"],
        media_kind="VIDEO",
        stage="PROXY",
    )

    desk_url = f"/api/v2/episodes/{context['episode_id']}/shots/{context['shot_id']}/studio"
    with TestClient(create_app(workspace)) as client:
        assert client.post(f"/api/v2/media-versions/{image['media_version_id']}:adopt").status_code == 200
        image_only_nav = client.get(desk_url).json()["shot_nav"]["items"][0]
        assert image_only_nav["current_video_media_version_id"] is None

        assert client.post(f"/api/v2/media-versions/{video['media_version_id']}:adopt").status_code == 200
        video_nav = client.get(desk_url).json()["shot_nav"]["items"][0]
        assert video_nav["current_video_media_version_id"] == video["media_version_id"]


def test_director_desk_100_shots_has_bounded_queries_and_latency(workspace, database, monkeypatch) -> None:
    project, episode, _ = _project_episode(workspace, database, "director_perf_100")
    episode_id = str(episode["id"])
    now = "2026-08-20T00:00:00Z"
    shot_ids: list[str] = []
    with database.transaction() as connection:
        for index in range(100):
            shot_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
            shot_ids.append(shot_id)
            connection.execute(
                """INSERT INTO shots
                (id,episode_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,
                created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,2000,'MEDIUM','DIRECTED',?,?,?,'perf',1,'v2')""",
                (shot_id, episode_id, f"SHOT_{index + 1:03d}", str(index + 1), revision_id, now, now),
            )
            connection.execute(
                """INSERT INTO shot_revisions
                (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,'{}',0,?,?,'perf',1,'v2')""",
                (revision_id, shot_id, now, now),
            )

    statements: list[str] = []
    original_connect = database.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
        return connection

    monkeypatch.setattr(database, "connect", traced_connect)
    started = time.perf_counter()
    result = _studio(database).studio(
        episode_id,
        shot_ids[50],
        nav_radius=12,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result["shot_nav"]["total"] == 100
    assert len(result["shot_nav"]["items"]) == 25
    assert result["shot_nav"]["selected_index"] == 50
    assert len(statements) <= 40, statements
    assert elapsed_ms < 700, f"Director aggregate took {elapsed_ms:.1f}ms"

    statements.clear()
    timeline = TimelineService(database, workspace).timeline_selections(
        str(project["id"]),
        episode_id,
        limit=500,
    )
    assert timeline["total"] == 100
    assert len(timeline["items"]) == 100
    assert timeline["has_more"] is False
    assert timeline["request_shape"] == "bounded_timeline_selection_read_model"
    assert len(statements) <= 3, statements
