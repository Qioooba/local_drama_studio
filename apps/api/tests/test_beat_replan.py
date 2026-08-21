from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app
from tests.test_generation_variants import _project


def _context(workspace, database):
    project = _project(workspace, database, "beat_replan")
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    scene = projects.create_scene(str(project["id"]), "SCENE_REPLAN", "重排场景")
    shots = [
        projects.create_shot(str(episode["id"]), "SHOT_KEEP", 3000, "STANDARD"),
        projects.create_shot(str(episode["id"]), "SHOT_MODIFY", 1000, "CLOSE"),
        projects.create_shot(str(episode["id"]), "SHOT_FROZEN", 1000, "CLOSE"),
    ]
    source = workspace.work_root / "beat-replan.md"
    source.write_text("# 重排\n\n角色冲进门，冻结镜头必须保留。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    draft_id, now = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    draft = {"scenes": [{"scene_no": 1, "summary": "重排场景", "shots": [
        {"shot_no": 1, "visual": "固定开场", "action": "", "dialogue": "", "duration_seconds": 3},
        {"shot_no": 2, "visual": "冲进门", "action": "快速推进", "dialogue": "", "duration_seconds": 2},
        {"shot_no": 3, "visual": "AI 想覆盖冻结镜头", "action": "", "dialogue": "", "duration_seconds": 2},
        {"shot_no": 4, "visual": "新增反应", "action": "回头", "dialogue": "", "duration_seconds": 2},
    ]}]}
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?,'test',1,'v2')""",
            (draft_id, project["id"], imported["source_document_version_id"], imported["import_session_id"],
             json.dumps(draft), "{}", now, now),
        )
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps({"visual": "固定开场", "action": "", "dialogue": "", "summary": "重排场景"}),
             shots[0]["current_revision_id"]),
        )
        connection.execute(
            "UPDATE shot_revisions SET is_frozen=1 WHERE id=?", (shots[2]["current_revision_id"],),
        )
    return project, episode, scene, shots, draft_id


def test_selected_beat_replan_plan_apply_is_safe_idempotent_and_audited(workspace, database) -> None:
    _, episode, scene, shots, draft_id = _context(workspace, database)
    episode_id = str(episode["id"])
    with TestClient(create_app(workspace)) as client:
        group = client.post(f"/api/v1/episodes/{episode_id}/shot-groups", json={
            "kind": "BEAT", "code": "BEAT_REPLAN", "title": "选定节拍", "scene_id": scene["id"],
        }).json()["group"]
        group = client.put(f"/api/v1/shot-groups/{group['id']}/members", json={
            "shot_ids": [shot["id"] for shot in shots], "expected_revision": group["revision"],
        }).json()["group"]
        request = {"draft_id": draft_id, "proposal_scene_no": 1, "expected_group_revision": group["revision"]}
        preview = client.post(
            f"/api/v1/episodes/{episode_id}/shot-groups/{group['id']}/replan:plan", json=request,
        )
        assert preview.status_code == 200
        plan = preview.json()["plan"]
        assert plan["valid"] is True
        assert plan["summary"] == {"KEEP": 1, "ADD": 1, "MODIFY": 1, "DELETE": 0, "PROTECTED": 1}
        assert plan["scope"] == {"selected_group_only": True, "outside_group_shots_touched": 0}

        payload = {**request, "expected_plan_hash": plan["plan_hash"], "idempotency_key": "beat-replan-1"}
        applied = client.post(
            f"/api/v1/episodes/{episode_id}/shot-groups/{group['id']}/replan:apply", json=payload,
        )
        assert applied.status_code == 200, applied.text
        result = applied.json()["apply"]
        assert result["modified_shot_ids"] == [shots[1]["id"]]
        assert result["protected_shot_ids"] == [shots[2]["id"]]
        assert len(result["created_shot_ids"]) == 1
        assert result["historical_variants_deleted"] == 0
        repeated = client.post(
            f"/api/v1/episodes/{episode_id}/shot-groups/{group['id']}/replan:apply", json=payload,
        )
        assert repeated.status_code == 200
        assert repeated.json()["apply"] == result

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shot_revisions WHERE shot_id=?", (shots[1]["id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT current_revision_id FROM shots WHERE id=?", (shots[2]["id"],),
        ).fetchone()[0] == shots[2]["current_revision_id"]
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='BEAT_REPLAN_APPLIED' AND subject_id=?", (group["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE type='episode.shot_plan.changed' AND subject_id=?", (group["id"],),
        ).fetchone()[0] == 1


def test_selected_beat_replan_stale_hash_cannot_apply(workspace, database) -> None:
    _, episode, scene, shots, draft_id = _context(workspace, database)
    with TestClient(create_app(workspace)) as client:
        group = client.post(f"/api/v1/episodes/{episode['id']}/shot-groups", json={
            "kind": "BEAT", "code": "BEAT_STALE", "title": "并发保护", "scene_id": scene["id"],
        }).json()["group"]
        group = client.put(f"/api/v1/shot-groups/{group['id']}/members", json={
            "shot_ids": [shots[0]["id"]], "expected_revision": group["revision"],
        }).json()["group"]
        response = client.post(f"/api/v1/episodes/{episode['id']}/shot-groups/{group['id']}/replan:apply", json={
            "draft_id": draft_id, "proposal_scene_no": 1, "expected_group_revision": group["revision"],
            "expected_plan_hash": "0" * 64, "idempotency_key": str(uuid.uuid4()),
        })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BEAT_REPLAN_PLAN_STALE"
