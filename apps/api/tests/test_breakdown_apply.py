from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _rich_draft() -> dict:
    return {
        "scenes": [
            {
                "scene_no": 1,
                "title": "开场",
                "summary": "母亲迎回孩子",
                "characters": ["母亲", "孩子"],
                "shots": [
                    {"shot_no": 1, "visual": "近景", "action": "开门", "dialogue": "母亲：你回来了。", "duration_seconds": 4},
                    {"shot_no": 2, "visual": "中景", "action": "拥抱", "dialogue": {"speaker": "孩子", "text": "嗯，我回来了。"}, "duration_seconds": 3},
                ],
            },
            {
                "scene_no": 2,
                "title": "邻居来访",
                "summary": "邻居闲聊",
                "characters": ["邻居", "母亲"],
                "shots": [
                    {"shot_no": 1, "visual": "全景", "action": "进门", "dialogue": "邻居:今天天气不错。", "duration_seconds": 5},
                    {"shot_no": 2, "visual": "特写", "action": "倒茶", "dialogue": "喝杯茶吧。", "duration_seconds": 2},
                ],
            },
        ]
    }


def _persisted_draft(workspace, database, *, project=None, status="DRAFT_READY", draft=None):
    """Insert a script_breakdown_drafts row directly (no real LLM involved)."""
    projects = ProjectService(database, workspace.projects_root)
    if project is None:
        project = projects.create_project(
            code=f"apply_{uuid.uuid4().hex[:6]}",
            title="Apply draft",
            episode_count=1,
            aspect_ratio="16:9",
            fps_num=24,
            fps_den=1,
            target_duration_ms=60_000,
            allow_unconfigured_capabilities=True,
        )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    source = workspace.work_root / f"apply-{uuid.uuid4().hex[:6]}.md"
    source.write_text("# 剧本\n\n第一场：母亲读信。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    draft_id, now = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    payload = draft if draft is not None else _rich_draft()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?, 'local-llm',1,'v2')""",
            (
                draft_id,
                project["id"],
                imported["source_document_version_id"],
                imported["import_session_id"],
                json.dumps(payload),
                json.dumps({"source": "model_output", "confidence": {"overall": 0.8}, "questions": [], "source_passages": []}),
                status,
                now,
                now,
            ),
        )
    return project, episode, draft_id


def test_apply_draft_materializes_scenes_shots_and_dialogue(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    result = BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))

    assert result["draft_id"] == draft_id
    assert result["episode_id"] == str(episode["id"])
    assert result["applied"] is True
    assert result["created"] == {"scenes": 2, "shots": 4, "lines": 4}
    assert result["extracted_characters"] == [
        {"name": "母亲", "scene_count": 2},
        {"name": "孩子", "scene_count": 1},
        {"name": "邻居", "scene_count": 1},
    ]

    with database.connect() as connection:
        scenes = connection.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY code", (project["id"],)).fetchall()
        assert [dict(row)["code"] for row in scenes] == ["SC01", "SC02"]
        assert [dict(row)["title"] for row in scenes] == ["开场", "邻居来访"]
        ranges = connection.execute(
            "SELECT * FROM episode_scene_ranges WHERE episode_id=? ORDER BY ordinal", (episode["id"],)
        ).fetchall()
        assert [(row["ordinal"], row["source_start"], row["source_end"], row["source_label"]) for row in ranges] == [
            (1, 0, 1, "第1场"),
            (2, 0, 1, "第2场"),
        ]
        shots = connection.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY CAST(order_key AS REAL)", (episode["id"],)).fetchall()
        assert [row["code"] for row in shots] == ["EPISODE_001-01-01", "EPISODE_001-01-02", "EPISODE_001-02-01", "EPISODE_001-02-02"]
        assert [row["target_duration_ms"] for row in shots] == [4000, 3000, 5000, 2000]
        assert all(row["shot_type"] == "STANDARD" and row["status"] == "DRAFT" for row in shots)
        revisions = connection.execute(
            """SELECT sr.* FROM shot_revisions sr JOIN shots s ON s.id=sr.shot_id
            WHERE s.episode_id=? ORDER BY CAST(s.order_key AS REAL)""",
            (episode["id"],),
        ).fetchall()
        assert len(revisions) == 4
        assert all(row["is_frozen"] == 0 for row in revisions)
        first_fields = json.loads(revisions[0]["fields_json"])
        assert first_fields["visual"] == "近景" and first_fields["action"] == "开门"
        assert first_fields["dialogue"] == "母亲：你回来了。" and first_fields["summary"] == "母亲迎回孩子"
        lines = connection.execute("SELECT * FROM dialogue_lines WHERE episode_id=? ORDER BY code", (episode["id"],)).fetchall()
        assert [(row["speaker"], row["shot_id"] is not None) for row in lines] == [
            ("母亲", True),
            ("孩子", True),
            ("邻居", True),
            ("邻居", True),
        ]
        assert [row["code"] for row in lines] == ["AI-DL-0001", "AI-DL-0002", "AI-DL-0003", "AI-DL-0004"]
        text_revisions = connection.execute(
            """SELECT dtr.* FROM dialogue_text_revisions dtr JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id
            WHERE dl.episode_id=? ORDER BY dl.code""",
            (episode["id"],),
        ).fetchall()
        assert len(text_revisions) == 4
        assert all(len(row["text_hash"]) == 64 for row in text_revisions)
        assert [row["text"] for row in text_revisions] == ["你回来了。", "嗯，我回来了。", "今天天气不错。", "喝杯茶吧。"]
        status = connection.execute("SELECT status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
        assert status["status"] == "APPLIED"
        audit = connection.execute(
            "SELECT * FROM audit_events WHERE action='SCRIPT_BREAKDOWN_APPLIED' AND subject_type='script_breakdown_draft' AND subject_id=?",
            (draft_id,),
        ).fetchone()
        assert audit is not None and audit["actor"] == "local-user"
        metadata = json.loads(audit["metadata_redacted_json"])
        assert metadata["episode_id"] == str(episode["id"])
        assert metadata["created"] == {"scenes": 2, "shots": 4, "lines": 4}
        assert metadata["extracted_characters"][0]["name"] == "母亲"
        assert metadata["extracted_characters"][0]["scene_count"] == 2


def test_apply_draft_flips_list_projection_to_applied(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    items = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    assert items[0]["id"] == draft_id
    assert items[0]["status"] == "APPLIED"
    assert items[0]["application_status"] == "APPLIED"
    assert items[0]["requires_human_action"] is False
    assert items[0]["automatic_apply"] is False


def test_apply_draft_twice_is_rejected(workspace, database) -> None:
    _, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert caught.value.code == "BREAKDOWN_DRAFT_ALREADY_APPLIED"


def test_apply_draft_cross_project_episode_is_rejected(workspace, database) -> None:
    project, _, draft_id = _persisted_draft(workspace, database)
    other = ProjectService(database, workspace.projects_root).create_project(
        code=f"other_{uuid.uuid4().hex[:6]}",
        title="Other project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    other_season = ProjectService(database, workspace.projects_root).list_seasons(str(other["id"]))[0]
    other_episode = ProjectService(database, workspace.projects_root).list_episodes(str(other_season["id"]))[0]
    assert str(project["id"]) != str(other["id"])
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(other_episode["id"]))
    assert caught.value.code == "EPISODE_PROJECT_MISMATCH"


def test_apply_draft_missing_episode_is_rejected(workspace, database) -> None:
    _, _, draft_id = _persisted_draft(workspace, database)
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(uuid.uuid4()))
    assert caught.value.code == "EPISODE_NOT_FOUND"


def test_apply_draft_missing_or_not_ready_is_rejected(workspace, database) -> None:
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(str(uuid.uuid4()), str(uuid.uuid4()))
    assert caught.value.code == "BREAKDOWN_DRAFT_NOT_READY"

    _, episode, draft_id = _persisted_draft(workspace, database, status="REJECTED")
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert caught.value.code == "BREAKDOWN_DRAFT_NOT_READY"


def test_apply_draft_rolls_back_on_scene_code_conflict(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    ProjectService(database, workspace.projects_root).create_scene(str(project["id"]), "SC01", "已存在场次")
    with pytest.raises(DomainRuleError) as caught:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert caught.value.code == "SCENE_CODE_CONFLICT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM episode_scene_ranges WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()["status"] == "DRAFT_READY"


def test_apply_draft_api_and_http_errors(workspace, database) -> None:
    _, episode, draft_id = _persisted_draft(workspace, database)
    _, _, second_draft_id = _persisted_draft(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/breakdown-drafts/{draft_id}:apply", json={"episode_id": episode["id"]})
        assert response.status_code == 200, response.text
        apply = response.json()["apply"]
        assert apply["created"] == {"scenes": 2, "shots": 4, "lines": 4}
        assert apply["applied"] is True

        second = client.post(f"/api/v1/breakdown-drafts/{draft_id}:apply", json={"episode_id": episode["id"]})
        assert second.status_code == 422
        assert second.json()["error"]["code"] == "BREAKDOWN_DRAFT_ALREADY_APPLIED"

        invalid = client.post(f"/api/v1/breakdown-drafts/{second_draft_id}:apply", json={"episode_id": "missing"})
        assert invalid.status_code == 404
        assert invalid.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_apply_draft_source_ranges_from_confidence_passages(workspace, database) -> None:
    draft = _rich_draft()
    confidence = {
        "source": "model_output",
        "confidence": {"overall": 0.8},
        "questions": [],
        "source_passages": [
            {"scene_no": 1, "quote": "你回来了。", "source_start": 10, "source_end": 16},
            {"scene_no": 2, "quote": "天气不错。", "source_start": 20, "source_end": 26},
        ],
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    with database.transaction() as connection:
        connection.execute("UPDATE script_breakdown_drafts SET confidence_json=? WHERE id=?", (json.dumps(confidence), draft_id))
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with database.connect() as connection:
        ranges = connection.execute(
            "SELECT ordinal, source_start, source_end FROM episode_scene_ranges WHERE episode_id=? ORDER BY ordinal", (episode["id"],)
        ).fetchall()
        assert [(row["ordinal"], row["source_start"], row["source_end"]) for row in ranges] == [(1, 10, 16), (2, 20, 26)]
