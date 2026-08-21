from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient

from local_drama.application.director_desk import DirectorDeskReadModelService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.shot_groups import ShotGroupService
from local_drama.main import create_app
from tests.test_generation_variants import _project
from tests.test_qc_auto_reroll_policy import _child
from tests.test_qc_auto_reroll_policy import _context as _generation_context


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
    projects.create_shot_revision(str(active["id"]), {
        "scene_id": "legacy-scene-must-not-win", "group_id": "legacy-group-must-not-win", "source_text": "原文证据",
    })
    active = projects.get_shot(str(active["id"]))
    groups = ShotGroupService(database)
    groups.assign_scene(
        shot_id=str(active["id"]), scene_id=str(real_scene["id"]), expected_revision=int(active["revision"]),
    )
    group = groups.create_group(
        episode_id=episode_id, kind="BEAT", code="BEAT_001", title="真实节拍", scene_id=str(real_scene["id"]),
        metadata={}, order_key=None,
    )
    groups.replace_members(group_id=str(group["id"]), shot_ids=[str(active["id"])], expected_revision=int(group["revision"]))
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shots SET archived_at='2026-08-20T00:00:00Z',source_shot_id=? WHERE id=?",
            (active["id"], archived["id"]),
        )

    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/projects/{project_id}/episodes/{episode_id}/director-desk",
            params={"shot_id": str(active["id"])},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["episode"]["shot_count"] == 1
    assert body["shot_nav"]["total"] == 1
    assert [item["id"] for item in body["shot_nav"]["items"]] == [active["id"]]
    nav = body["shot_nav"]["items"][0]
    assert (nav["scene_id"], nav["scene_code"], nav["scene_title"]) == (
        real_scene["id"], "SCENE_REAL", "真实场景",
    )
    assert (nav["group_id"], nav["group_code"], nav["group_title"]) == (
        group["id"], "BEAT_001", "真实节拍",
    )
    assert body["current_shot"]["shot"]["scene_id"] == real_scene["id"]
    assert body["current_shot"]["source_context"]["scene_id"] == real_scene["id"]
    assert body["current_shot"]["source_context"]["source_range"]["source_start"] == 10


def test_director_desk_requires_explicit_shot_selection(workspace, database) -> None:
    project, episode, projects = _project_episode(workspace, database, "director_explicit_shot")
    projects.create_shot(str(episode["id"]), "SHOT_001", 2400, "MEDIUM")

    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/projects/{project['id']}/episodes/{episode['id']}/director-desk"
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DIRECTOR_SHOT_REQUIRED"


def test_director_desk_contract_handles_empty_revision_and_stale_candidate(workspace, database) -> None:
    context = _generation_context(workspace, database, "director_candidate_contract")
    source = workspace.work_root / "director-candidate.bin"
    source.write_bytes(b"local candidate fixture")
    imported = MediaService(database, workspace).import_file(
        context["project_id"], source, purpose="CANDIDATE", owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"], media_kind="IMAGE", stage="KEYFRAME",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE generation_variants SET is_stale=1,stale_reason='asset_reference_changed' WHERE id=?",
            (context["variant_id"],),
        )
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/projects/{context['project_id']}/episodes/{context['episode_id']}/director-desk",
            params={"shot_id": context["shot_id"]},
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
    older_source = workspace.work_root / "director-older-candidate.bin"
    newer_source = workspace.work_root / "director-newer-candidate.bin"
    older_source.write_bytes(b"older local candidate")
    newer_source.write_bytes(b"newer local candidate")
    older = MediaService(database, workspace).import_file(
        context["project_id"], older_source, purpose="CANDIDATE", owner_type="GENERATION_VARIANT",
        owner_id=context["variant_id"], media_kind="IMAGE", stage="KEYFRAME",
    )
    newer_variant_id = _child(database, context, context["variant_id"], 2)
    newer = MediaService(database, workspace).import_file(
        context["project_id"], newer_source, purpose="CANDIDATE", owner_type="GENERATION_VARIANT",
        owner_id=newer_variant_id, media_kind="IMAGE", stage="KEYFRAME",
    )

    desk_url = f"/api/v1/projects/{context['project_id']}/episodes/{context['episode_id']}/director-desk"
    with TestClient(create_app(workspace)) as client:
        for media_version_id in (newer["media_version_id"], older["media_version_id"]):
            selected = client.post(
                f"/api/v1/media-versions/{media_version_id}:select", json={"selection_type": "KEYFRAME"},
            )
            assert selected.status_code == 200, selected.text
        restored_old = client.get(desk_url, params={"shot_id": context["shot_id"]}).json()["current_shot"]
        assert restored_old["current_media"]["media_version_id"] == older["media_version_id"]
        assert restored_old["selected_variant"]["id"] == context["variant_id"]
        assert [item["media_version_id"] for item in restored_old["candidates"] if item["selected"]] == [older["media_version_id"]]

        undo = client.post(
            f"/api/v1/media-versions/{newer['media_version_id']}:select", json={"selection_type": "KEYFRAME"},
        )
        assert undo.status_code == 200, undo.text
        restored_new = client.get(desk_url, params={"shot_id": context["shot_id"]}).json()["current_shot"]
        assert restored_new["current_media"]["media_version_id"] == newer["media_version_id"]
        assert restored_new["selected_variant"]["id"] == newer_variant_id
        assert [item["media_version_id"] for item in restored_new["candidates"] if item["selected"]] == [newer["media_version_id"]]

    with database.connect() as connection:
        history_count = connection.execute(
            """SELECT COUNT(*) FROM selections se JOIN media_versions mv ON mv.id=se.media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            JOIN generation_variants gv ON gv.id=ma.owner_id JOIN generation_intents gi ON gi.id=gv.intent_id
            WHERE gi.owner_type='SHOT' AND gi.owner_id=? AND se.selection_type='KEYFRAME'""",
            (context["shot_id"],),
        ).fetchone()[0]
    assert history_count == 3


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
    result = DirectorDeskReadModelService(database).get(
        str(project["id"]), episode_id, shot_ids[50], nav_radius=12,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result["shot_nav"]["total"] == 100
    assert len(result["shot_nav"]["items"]) == 25
    assert result["shot_nav"]["selected_index"] == 50
    assert len(statements) <= 40, statements
    assert elapsed_ms < 700, f"Director aggregate took {elapsed_ms:.1f}ms"
