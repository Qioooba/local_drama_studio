from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_revisions import BreakdownRevisionService
from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
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
        assert [row["scene_id"] for row in shots] == [scenes[0]["id"], scenes[0]["id"], scenes[1]["id"], scenes[1]["id"]]
        assert [row["shot_type"] for row in shots] == ["CLOSEUP", "MEDIUM", "WIDE", "EXTREME_CLOSEUP"]
        assert all(row["status"] == "DRAFT" for row in shots)
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
        assert first_fields["schema_version"] == "director-intent.v3"
        assert first_fields["shot_type"] == "CLOSEUP"
        assert first_fields["composition"]["preset"] == "LEFT_THIRD"
        assert first_fields["subject_action"] == "开门"
        assert first_fields["suggestion_sources"]["pipeline"]["source_kind"] == "APPLIED_BREAKDOWN_DRAFT"
        lines = connection.execute("SELECT * FROM dialogue_lines WHERE episode_id=? ORDER BY code", (episode["id"],)).fetchall()
        assert [(row["speaker"], row["shot_id"] is not None) for row in lines] == [
            ("母亲", True),
            ("孩子", True),
            ("邻居", True),
            ("待确认说话人", True),
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


def test_human_scene_revision_is_append_only_and_application_freezes_revision(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    revised_scene = {
        "title": "人工校订开场",
        "summary": "母亲在门边迎回孩子",
        "characters": ["母亲", "孩子"],
        "shots": [
            {"shot_no": 1, "visual": "门边近景", "action": "母亲开门", "dialogue": "母亲：你回来了。", "duration_seconds": 5},
            {"shot_no": 2, "visual": "玄关中景", "action": "两人拥抱", "dialogue": {"speaker": "孩子", "text": "嗯，我回来了。"}, "duration_seconds": 4},
        ],
    }
    result = BreakdownRevisionService(database).revise_scene(
        draft_id,
        1,
        revised_scene,
        expected_revision=1,
        change_note="修正场次画面和节奏",
    )
    assert result["effective_draft_revision_no"] == 1
    assert result["human_edited"] is True

    with database.connect() as connection:
        root = connection.execute("SELECT draft_json,revision FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
        stored_revision = connection.execute(
            "SELECT * FROM script_breakdown_draft_revisions WHERE id=?", (result["effective_draft_revision_id"],)
        ).fetchone()
        assert json.loads(root["draft_json"])["scenes"][0]["title"] == "开场"
        assert root["revision"] == 2
        assert json.loads(stored_revision["draft_json"])["scenes"][0]["title"] == "人工校订开场"
        assert stored_revision["change_note"] == "修正场次画面和节奏"

    applied = BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]), scene_nos=[1])
    assert applied["effective_draft_revision_id"] == result["effective_draft_revision_id"]
    with database.connect() as connection:
        scene = connection.execute("SELECT title FROM scenes WHERE project_id=? AND code='SC01'", (project["id"],)).fetchone()
        application = connection.execute(
            "SELECT breakdown_draft_revision_id FROM script_breakdown_scene_applications WHERE breakdown_draft_id=? AND scene_no=1",
            (draft_id,),
        ).fetchone()
        assert scene["title"] == "人工校订开场"
        assert application["breakdown_draft_revision_id"] == result["effective_draft_revision_id"]

    with pytest.raises(DomainRuleError) as applied_error:
        BreakdownRevisionService(database).revise_scene(
            draft_id,
            1,
            revised_scene,
            expected_revision=3,
            change_note="尝试覆盖已应用场次",
        )
    assert applied_error.value.code == "BREAKDOWN_SCENE_ALREADY_APPLIED"


def test_apply_binds_existing_character_scene_and_prop_by_name_or_alias(workspace, database) -> None:
    draft = {
        "scenes": [{
            "scene_no": 1,
            "title": "雨夜仓库",
            "location": "旧仓库",
            "summary": "阿舟找到照骨灯",
            "characters": ["阿舟"],
            "shots": [{
                "shot_no": 1, "visual": "中景", "action": "阿舟举起灯盏", "dialogue": "",
                "duration_seconds": 4, "characters": ["阿舟"], "props": ["灯盏"],
            }],
        }],
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    assets = StoryAssetService(database, workspace)
    character = assets.create_asset(
        str(project["id"]), "CHARACTER", "CHAR_LINZHOU", "林舟",
        extra={"text_dossier": {"aliases": ["阿舟"]}},
    )
    scene = assets.create_asset(str(project["id"]), "SCENE", "SCENE_WAREHOUSE", "旧仓库")
    prop = assets.create_asset(
        str(project["id"]), "PROP", "PROP_LAMP", "照骨灯",
        extra={"text_dossier": {"aliases": ["灯盏"]}},
    )

    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT asset_id,role_in_shot FROM shot_asset_bindings ORDER BY role_in_shot,asset_id"
        ).fetchall()
    assert {(str(row["asset_id"]), str(row["role_in_shot"])) for row in rows} == {
        (str(character["id"]), "main"), (str(scene["id"]), "location"), (str(prop["id"]), "prop"),
    }


def test_human_scene_revision_rejects_stale_root_revision(workspace, database) -> None:
    _project, _episode, draft_id = _persisted_draft(workspace, database)
    with pytest.raises(DomainRuleError) as stale_error:
        BreakdownRevisionService(database).revise_scene(
            draft_id,
            1,
            _rich_draft()["scenes"][0],
            expected_revision=99,
            change_note="过期页面保存",
        )
    assert stale_error.value.code == "BREAKDOWN_DRAFT_REVISION_CONFLICT"


def test_human_scene_revision_enforces_the_same_shot_duration_contract_as_generation(workspace, database) -> None:
    _project, _episode, draft_id = _persisted_draft(workspace, database)
    revised_scene = json.loads(json.dumps(_rich_draft()["scenes"][0]))
    revised_scene["shots"][0]["duration_seconds"] = 16

    with pytest.raises(DomainRuleError) as duration_error:
        BreakdownRevisionService(database).revise_scene(
            draft_id,
            1,
            revised_scene,
            expected_revision=1,
            change_note="验证单镜时长上限",
        )

    assert duration_error.value.code == "BREAKDOWN_SHOT_DURATION_INVALID"


def test_apply_rejects_a_persisted_duration_contract_mismatch_before_any_write(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE script_breakdown_drafts SET confidence_json=? WHERE id=?",
            (
                json.dumps({
                    "target_episode_id": episode["id"],
                    "target_duration_seconds": 60,
                    "total_duration_seconds": 14,
                    "duration_tolerance_ratio": 0.2,
                    "duration_contract_status": "PASS",
                }),
                draft_id,
            ),
        )

    with pytest.raises(DomainRuleError) as rejected:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert rejected.value.code == "BREAKDOWN_DURATION_CONTRACT_MISMATCH"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()[0] == "DRAFT_READY"


def test_apply_and_list_block_a_persisted_grounding_failure_before_any_write(workspace, database) -> None:
    draft = {
        "scenes": [{
            "scene_no": 1,
            "title": "读信",
            "summary": "母亲读信",
            "characters": ["母亲"],
            "shots": [
                {"shot_no": index, "visual": "信件", "action": "读信", "dialogue": "母亲：从未说过的话。" if index == 1 else "", "duration_seconds": 15}
                for index in range(1, 5)
            ],
        }],
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    confidence = {
        "target_episode_id": episode["id"],
        "target_duration_seconds": 60,
        "total_duration_seconds": 60,
        "duration_tolerance_ratio": 0.2,
        "duration_contract_status": "PASS",
        "source_passages": [{"scene_no": 1, "quote": "第一场：母亲读信。", "source_start": 6, "source_end": 15}],
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE script_breakdown_drafts SET confidence_json=? WHERE id=?",
            (json.dumps(confidence), draft_id),
        )

    listed = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))[0]
    assert listed["application_blockers"] == [{
        "code": "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID",
        "message": "模型对白未逐字落在本场已验证原文引用中，禁止保存或应用草稿",
    }]
    with pytest.raises(DomainRuleError) as rejected:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert rejected.value.code == "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()[0] == "DRAFT_READY"


def test_apply_and_list_block_a_persisted_scene_reference_mismatch(workspace, database) -> None:
    draft = {
        "scenes": [{
            "scene_no": 1,
            "title": "地下档案室",
            "summary": "苏晚发现泥脚印和录音机",
            "characters": ["苏晚"],
            "shots": [
                {"shot_no": index, "visual": "泥脚印和录音机", "action": "苏晚检查档案", "dialogue": "", "duration_seconds": 15}
                for index in range(1, 5)
            ],
        }],
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    confidence = {
        "target_episode_id": episode["id"],
        "target_duration_seconds": 60,
        "total_duration_seconds": 60,
        "duration_tolerance_ratio": 0.2,
        "duration_contract_status": "PASS",
        "source_passages": [{"scene_no": 1, "quote": "第一章 雨夜来信", "source_start": 0, "source_end": 8}],
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE script_breakdown_drafts SET confidence_json=? WHERE id=?",
            (json.dumps(confidence), draft_id),
        )

    listed = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))[0]
    assert listed["application_blockers"] == [{
        "code": "LOCAL_LLM_SCENE_GROUNDING_INVALID",
        "message": "场景内容与其声明的原文段落匹配度不足，禁止保存或应用草稿",
    }]
    with pytest.raises(DomainRuleError) as rejected:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert rejected.value.code == "LOCAL_LLM_SCENE_GROUNDING_INVALID"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT status FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()[0] == "DRAFT_READY"


def test_apply_and_list_block_persisted_duplicate_scene_narratives(workspace, database) -> None:
    repeated_summary = "第一场：母亲读信。第一场：母亲读信。"
    draft = {
        "scenes": [
            {
                "scene_no": scene_no,
                "title": f"读信 {scene_no}",
                "summary": repeated_summary,
                "characters": ["母亲"],
                "shots": [{"shot_no": 1, "visual": "母亲读信", "action": "母亲读信", "dialogue": "", "duration_seconds": 30}],
            }
            for scene_no in (1, 2)
        ],
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    confidence = {
        "target_episode_id": episode["id"],
        "target_duration_seconds": 60,
        "total_duration_seconds": 60,
        "duration_tolerance_ratio": 0.2,
        "duration_contract_status": "PASS",
        "source_passages": [
            {"scene_no": 1, "quote": "第一场：母亲读信。", "source_start": 6, "source_end": 15},
            {"scene_no": 2, "quote": "第一场：母亲读信。", "source_start": 6, "source_end": 15},
        ],
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE script_breakdown_drafts SET confidence_json=? WHERE id=?",
            (json.dumps(confidence), draft_id),
        )

    listed = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))[0]
    assert listed["application_blockers"] == [{
        "code": "LOCAL_LLM_SCENE_DUPLICATE",
        "message": "多个场景包含完全重复的剧情摘要，禁止保存或应用草稿",
    }]
    with pytest.raises(DomainRuleError) as rejected:
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    assert rejected.value.code == "LOCAL_LLM_SCENE_DUPLICATE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?", (project["id"],)).fetchone()[0] == 0


def test_apply_draft_flips_list_projection_to_applied(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    items = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    assert items[0]["id"] == draft_id
    assert items[0]["status"] == "APPLIED"
    assert items[0]["application_status"] == "APPLIED"
    assert items[0]["requires_human_action"] is False
    assert items[0]["automatic_apply"] is False


def test_apply_draft_supports_atomic_scene_selection_and_finishes_only_after_all_scenes(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    service = BreakdownApplyService(database, workspace)

    first = service.apply_draft(draft_id, str(episode["id"]), scene_nos=[1])
    assert first["applied"] is False
    assert first["selected_scene_nos"] == [1]
    assert first["applied_scene_nos"] == [1]
    assert first["remaining_scene_nos"] == [2]
    assert first["created"] == {"scenes": 1, "shots": 2, "lines": 2}

    projected = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))[0]
    assert projected["status"] == "DRAFT_READY"
    assert projected["application_status"] == "PARTIALLY_APPLIED"
    assert projected["applied_scene_nos"] == [1]
    assert projected["remaining_scene_nos"] == [2]
    assert projected["requires_human_action"] is True

    with pytest.raises(DomainRuleError) as duplicate:
        service.apply_draft(draft_id, str(episode["id"]), scene_nos=[1])
    assert duplicate.value.code == "BREAKDOWN_SCENE_ALREADY_APPLIED"

    final = service.apply_draft(draft_id, str(episode["id"]), scene_nos=[2])
    assert final["applied"] is True
    assert final["applied_scene_nos"] == [1, 2]
    assert final["remaining_scene_nos"] == []
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM script_breakdown_scene_applications WHERE breakdown_draft_id=?",
            (draft_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM story_asset_proposals WHERE breakdown_draft_id=?",
            (draft_id,),
        ).fetchone()[0] == 3


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


def test_apply_draft_allocates_project_codes_after_existing_scenes(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    ProjectService(database, workspace.projects_root).create_scene(str(project["id"]), "SC01", "已存在场次")
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with database.connect() as connection:
        assert [row[0] for row in connection.execute("SELECT code FROM scenes WHERE project_id=? ORDER BY code", (project["id"],))] == ["SC01", "SC02", "SC03"]
        assert [row[0] for row in connection.execute("SELECT ordinal FROM episode_scene_ranges WHERE episode_id=? ORDER BY ordinal", (episode["id"],))] == [1, 2]
        assert connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 4


def test_two_episodes_can_apply_drafts_with_the_same_local_scene_numbers(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="two_episode_scenes", title="两集场次", episode_count=2,
        aspect_ratio="9:16", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    for episode in episodes:
        _, _, draft_id = _persisted_draft(workspace, database, project=project)
        BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with database.connect() as connection:
        assert [row[0] for row in connection.execute("SELECT code FROM scenes WHERE project_id=? ORDER BY code", (project["id"],))] == ["SC01", "SC02", "SC03", "SC04"]
        for episode in episodes:
            assert [row[0] for row in connection.execute("SELECT ordinal FROM episode_scene_ranges WHERE episode_id=? ORDER BY ordinal", (episode["id"],))] == [1, 2]
            assert connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 4


def test_apply_draft_api_and_http_errors(workspace, database) -> None:
    _, episode, draft_id = _persisted_draft(workspace, database)
    _, _, second_draft_id = _persisted_draft(workspace, database)
    _, _, revision_draft_id = _persisted_draft(workspace, database)
    with TestClient(create_app(workspace)) as client:
        revised_scene = _rich_draft()["scenes"][0]
        revised_scene["title"] = "API 人工校订"
        revision_response = client.put(
            f"/api/v1/breakdown-drafts/{revision_draft_id}/scenes/1:revise",
            json={"expected_revision": 1, "change_note": "通过审核界面校订", **revised_scene},
        )
        assert revision_response.status_code == 200, revision_response.text
        assert revision_response.json()["revision"]["effective_draft_revision_no"] == 1

        response = client.post(f"/api/v1/breakdown-drafts/{draft_id}:apply", json={"episode_id": episode["id"]})
        assert response.status_code == 200, response.text
        apply = response.json()["apply"]
        assert apply["created"] == {"scenes": 2, "shots": 4, "lines": 4}
        assert apply["applied"] is True

        _, partial_episode, partial_draft_id = _persisted_draft(workspace, database)
        partial = client.post(
            f"/api/v1/breakdown-drafts/{partial_draft_id}:apply",
            json={"episode_id": partial_episode["id"], "scene_nos": [2]},
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["apply"]["selected_scene_nos"] == [2]
        assert partial.json()["apply"]["remaining_scene_nos"] == [1]

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
