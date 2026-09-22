"""NP03: revising one field of a breakdown scene must not erase the others.

The legacy ``revise_scene`` endpoint rebuilt the scene from a handful of form
fields, so changing only the title discarded the scene's director context and
every shot's camera/composition/continuity information, and the lossy version
could still be applied to formal shots. These tests assert the fixed PATCH
semantics end to end: service revision -> real HTTP revision -> formal materialization.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_revisions import BreakdownRevisionService
from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app

DIRECTOR_SCENE = {
    "scenes": [
        {
            "scene_no": 1,
            "title": "古桥夜谈",
            "summary": "林默在古桥寻灯",
            "characters": ["林默", "苏晚"],
            "location": "古桥东岸",
            "time": "深夜",
            "atmosphere": "紧张",
            "lighting": "蓝色月光",
            "props": ["青灯"],
            "purpose": "交代青灯的来历",
            "continuity": "青灯始终在右手",
            "shots": [
                {
                    "shot_no": 1,
                    "visual": "近景，古桥石栏",
                    "action": "林默握紧青灯",
                    "dialogue": "林默：我得找到它。",
                    "duration_seconds": 4,
                    "shot_type": "CLOSEUP",
                    "camera": "推进",
                    "composition": {"preset": "RIGHT_THIRD", "headroom": "TIGHT"},
                    "lighting": "蓝色月光",
                    "sound": "水声与虫鸣",
                    "emotion": "警觉",
                    "emotion_intensity": 0.7,
                    "continuity": "青灯在右手，衣角被雨淋湿",
                    "creative_intent": "用近景强调青灯的光",
                    "camera_direction": "FORWARD",
                    "camera_intensity": 0.4,
                    "camera_curve": "LINEAR",
                    "facial_action": "眉头紧锁",
                    "eye_line": "看向桥下",
                    "blocking_summary": "主角在画面右侧三分之一处",
                    "transition_plan": {"kind": "DISSOLVE", "duration_ms": 400},
                },
                {
                    "shot_no": 2,
                    "visual": "中景，两人相对",
                    "action": "苏晚走近",
                    "dialogue": "苏晚：我知道路。",
                    "duration_seconds": 5,
                    "shot_type": "MEDIUM",
                    "camera": "跟随",
                    "composition": {"preset": "CENTER"},
                    "lighting": "侧逆光",
                    "sound": "脚步声",
                    "emotion": "坚定",
                    "continuity": "两人保持一臂距离",
                },
            ],
        }
    ]
}


def _persisted_director_draft(workspace, database) -> tuple[dict, dict, str]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=f"np03_{uuid.uuid4().hex[:6]}",
        title="NP03 修订保护",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    source = workspace.work_root / f"np03-{uuid.uuid4().hex[:6]}.md"
    source.write_text("# 剧本\n\n第一场：古桥夜谈。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    draft_id, now = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?, 'local-llm',1,'v2')""",
            (
                draft_id,
                project["id"],
                imported["source_document_version_id"],
                imported["import_session_id"],
                json.dumps(DIRECTOR_SCENE),
                json.dumps({"source": "model_output", "confidence": {"overall": 0.8}, "questions": [], "source_passages": []}),
                "DRAFT_READY",
                now,
                now,
            ),
        )
    return project, episode, draft_id


def test_title_only_revision_preserves_every_unsubmitted_director_field(workspace, database) -> None:
    _project, _episode, draft_id = _persisted_director_draft(workspace, database)
    original_scene = DIRECTOR_SCENE["scenes"][0]

    result = BreakdownRevisionService(database).revise_scene(
        draft_id,
        1,
        {
            "title": "河岸寻灯",
            "shots": [
                {
                    "shot_no": 1,
                    "visual": original_scene["shots"][0]["visual"],
                    "action": original_scene["shots"][0]["action"],
                    "dialogue": original_scene["shots"][0]["dialogue"],
                    "duration_seconds": original_scene["shots"][0]["duration_seconds"],
                },
                {
                    "shot_no": 2,
                    "visual": original_scene["shots"][1]["visual"],
                    "action": original_scene["shots"][1]["action"],
                    "dialogue": original_scene["shots"][1]["dialogue"],
                    "duration_seconds": original_scene["shots"][1]["duration_seconds"],
                },
            ],
        },
        expected_revision=1,
        change_note="只改标题",
    )
    revised = result["draft"]["scenes"][0]
    assert revised["title"] == "河岸寻灯"
    # Every unsubmitted scene-level director field survives byte for byte.
    for field in ("summary", "characters", "location", "time", "atmosphere", "lighting", "props", "purpose", "continuity"):
        assert revised[field] == original_scene[field], field
    # ...and so does every unsubmitted shot-level director field.
    for position, original_shot in enumerate(original_scene["shots"]):
        for field, expected in original_shot.items():
            assert revised["shots"][position][field] == expected, (position, field)


def test_only_the_submitted_shot_field_changes(workspace, database) -> None:
    _project, _episode, draft_id = _persisted_director_draft(workspace, database)
    original = DIRECTOR_SCENE["scenes"][0]
    shots = [
        {
            "shot_no": 1,
            "visual": "近景，桥下青灯微光",
            "action": original["shots"][0]["action"],
            "dialogue": original["shots"][0]["dialogue"],
            "duration_seconds": original["shots"][0]["duration_seconds"],
        },
        {
            "shot_no": 2,
            "visual": original["shots"][1]["visual"],
            "action": original["shots"][1]["action"],
            "dialogue": original["shots"][1]["dialogue"],
            "duration_seconds": 6,
        },
    ]
    revised = BreakdownRevisionService(database).revise_scene(
        draft_id, 1, {"title": original["title"], "shots": shots},
        expected_revision=1, change_note="只改第一个镜头画面与第二个镜头时长",
    )["draft"]["scenes"][0]

    assert revised["shots"][0]["visual"] == "近景，桥下青灯微光"
    assert revised["shots"][0]["camera"] == original["shots"][0]["camera"]
    assert revised["shots"][0]["composition"] == original["shots"][0]["composition"]
    assert revised["shots"][0]["continuity"] == original["shots"][0]["continuity"]
    assert revised["shots"][0]["sound"] == original["shots"][0]["sound"]
    assert revised["shots"][1]["duration_seconds"] == 6
    assert revised["shots"][1]["camera"] == original["shots"][1]["camera"]
    assert revised["shots"][1]["visual"] == original["shots"][1]["visual"]
    # An explicitly submitted director field is still honoured.
    explicit = BreakdownRevisionService(database).revise_scene(
        draft_id,
        1,
        {
            "title": original["title"],
            "shots": [
                {**shots[0], "camera": "拉远", "composition": {"preset": "LEFT_THIRD"}},
                shots[1],
            ],
        },
        expected_revision=2,
        change_note="显式覆盖运镜与构图",
    )["draft"]["scenes"][0]
    assert explicit["shots"][0]["camera"] == "拉远"
    assert explicit["shots"][0]["composition"] == {"preset": "LEFT_THIRD"}
    assert explicit["shots"][0]["continuity"] == original["shots"][0]["continuity"]


def test_http_revision_and_apply_keep_director_intent(workspace, database) -> None:
    project, episode, draft_id = _persisted_director_draft(workspace, database)
    original = DIRECTOR_SCENE["scenes"][0]
    with TestClient(create_app(workspace)) as client:
        response = client.put(
            f"/api/v1/breakdown-drafts/{draft_id}/scenes/1:revise",
            json={
                "expected_revision": 1,
                "change_note": "只改标题",
                "title": "河岸寻灯",
                "shots": [
                    {
                        "shot_no": position + 1,
                        "visual": shot["visual"],
                        "action": shot["action"],
                        "dialogue": shot["dialogue"],
                        "duration_seconds": shot["duration_seconds"],
                    }
                    for position, shot in enumerate(original["shots"])
                ],
            },
        )
        assert response.status_code == 200, response.text
        scene = response.json()["revision"]["draft"]["scenes"][0]
        assert scene["location"] == "古桥东岸"
        assert scene["shots"][0]["camera"] == "推进"
        assert scene["shots"][0]["composition"] == {"preset": "RIGHT_THIRD", "headroom": "TIGHT"}
        assert scene["shots"][0]["sound"] == "水声与虫鸣"

    applied = BreakdownApplyService(database, workspace).apply_draft(
        draft_id, str(episode["id"]), scene_nos=[1]
    )
    assert applied["applied_scene_nos"] == [1]
    with database.connect() as connection:
        shot = connection.execute(
            """SELECT sr.fields_json FROM shot_revisions sr
            JOIN shots sh ON sh.id=sr.shot_id
            JOIN scenes sc ON sc.id=sh.scene_id
            WHERE sc.project_id=? AND sc.code='SC01' AND sh.code LIKE '%-01-01'
            ORDER BY sr.revision_no DESC LIMIT 1""",
            (project["id"],),
        ).fetchone()
    assert shot is not None, "the revised scene must materialize a shot revision"
    fields = json.loads(shot["fields_json"])
    # The formal DirectorIntent keeps the camera plan and composition instead of
    # degrading to STATIC/CENTER with an empty sound plan.
    assert fields["camera_plan"]["movement"] == "PUSH_IN"
    assert fields["composition"]["preset"] == "RIGHT_THIRD"
    assert fields["sound_plan"]["description"] == "水声与虫鸣"
    assert fields["lighting"] == "蓝色月光"
    assert fields["continuity"] == "青灯在右手，衣角被雨淋湿"
