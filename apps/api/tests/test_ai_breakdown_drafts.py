from __future__ import annotations

import errno
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import (
    LocalLLMService,
    _normalize_breakdown_durations,
    _normalize_scene_source_passages,
    _numbered_source_paragraphs,
    _validate_breakdown_output,
    validate_scene_distinctness,
    validate_scene_source_grounding,
)
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.main import create_app


def _persisted_draft(workspace, database):
    project = ProjectService(database, workspace.projects_root).create_project(
        code="ai_draft",
        title="AI draft",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "ai-draft.md"
    source.write_text("# 第一场\n\n母亲打开信件。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    draft_id, now = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    draft = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "开场",
                "summary": "母亲读信",
                "characters": ["母亲"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "近景",
                        "action": "打开信件",
                        "dialogue": "",
                        "duration_seconds": 4,
                    }
                ],
            }
        ]
    }
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?, 'local-llm',1,'v2')""",
            (
                draft_id,
                project["id"],
                imported["source_document_version_id"],
                imported["import_session_id"],
                json.dumps(draft),
                json.dumps({"source": "model_output"}),
                now,
                now,
            ),
        )
    return project, draft_id


def test_breakdown_draft_projection_never_applies_or_overwrites_authority(workspace, database) -> None:
    project, draft_id = _persisted_draft(workspace, database)
    with database.connect() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("scenes", "shots", "creative_entries")
        }
    items = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    with database.connect() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("scenes", "shots", "creative_entries")
        }
    assert before == after
    assert items[0]["id"] == draft_id
    assert items[0]["status"] == "DRAFT_READY"
    assert items[0]["application_status"] == "NOT_APPLIED"
    assert items[0]["automatic_apply"] is False
    assert items[0]["requires_human_action"] is True
    assert items[0]["profile_version_id"] is None
    assert items[0]["evidence_status"] == "LEGACY_INCOMPLETE"


def test_breakdown_draft_api_is_read_only_and_explicit(workspace, database) -> None:
    project, _ = _persisted_draft(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/script-breakdown-drafts")
    assert response.status_code == 200
    assert response.json()["automatic_apply"] is False
    assert response.json()["requires_human_action"] is True
    assert len(response.json()["items"]) == 1


def test_structured_breakdown_evidence_requires_exact_source_quotes() -> None:
    source = "第一场。母亲打开信件。\n第二场。孩子走进房间。"
    output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "读信",
                "summary": "母亲读信",
                "characters": ["母亲"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "近景",
                        "action": "打开信件",
                        "dialogue": "",
                        "duration_seconds": 4,
                    }
                ],
            },
            {
                "scene_no": 2,
                "title": "进门",
                "summary": "孩子进门",
                "characters": ["孩子"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "全景",
                        "action": "走进房间",
                        "dialogue": "",
                        "duration_seconds": 3,
                    }
                ],
            },
        ],
        "confidence": {"overall": 0.82, "notes": ["第二场人物关系待确认"]},
        "questions": ["孩子与母亲是什么关系？"],
        "source_passages": [
            {"scene_no": 1, "quote": "母亲打开信件。"},
            {"scene_no": 2, "quote": "孩子走进房间。"},
        ],
    }
    draft, evidence = _validate_breakdown_output(output, source)
    assert len(draft["scenes"]) == 2
    assert evidence["confidence"]["overall"] == 0.82
    assert evidence["source_passages"][0]["source_start"] == source.index("母亲打开信件。")
    assert evidence["source_passages"][1]["source_end"] == len(source)

    output["source_passages"][1]["quote"] = "原文中不存在的动作。"
    with pytest.raises(DomainRuleError) as caught:
        _validate_breakdown_output(output, source)
    assert caught.value.code == "LOCAL_LLM_SOURCE_QUOTE_INVALID"


def test_structured_breakdown_evidence_conservatively_realigns_local_model_quotes() -> None:
    source = (
        "修表师林默收到一封没有邮票的信，信封上只有一句话。\n\n"
        "“信还没送到十年前。”林默说。\n\n"
        "苏晚笑了：“你已经送到了。”"
    )
    output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "来信",
                "summary": "林默收到信。",
                "characters": ["林默"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "信封",
                        "action": "读信",
                        "dialogue": "",
                        "duration_seconds": 2,
                    }
                ],
            },
            {
                "scene_no": 2,
                "title": "送信",
                "summary": "二人谈论来信。",
                "characters": ["林默", "苏晚"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "二人",
                        "action": "交谈",
                        "dialogue": "信还没送到十年前。",
                        "duration_seconds": 3,
                    }
                ],
            },
        ],
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [
            {"scene_no": 1, "quote": "修表师林默收到一封没有邮票的信，信封里只有一句话。"},
            {"scene_no": 2, "quote": "'信还没送到十年前。'\n'你已经送到了。'"},
        ],
    }

    _, evidence = _validate_breakdown_output(output, source)

    first, second = evidence["source_passages"]
    assert first["alignment_method"] == "STRICT_LOW_EDIT_DISTANCE"
    assert first["quote"] == "修表师林默收到一封没有邮票的信，信封上只有一句话。"
    assert second["alignment_method"] == "ORDERED_EXACT_FRAGMENTS"
    assert second["quote"] == "“信还没送到十年前。”林默说。\n\n苏晚笑了：“你已经送到了。”"
    assert source[first["source_start"] : first["source_end"]] == first["quote"]
    assert source[second["source_start"] : second["source_end"]] == second["quote"]


def test_structured_breakdown_paragraph_ids_resolve_to_exact_immutable_source() -> None:
    source = "第一段：雾港的钟停在十一点。\n\n第二段：林默把怀表放在灯下。\n\n第三段：苏晚指向旧灯塔。"
    output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "怀表",
                "summary": "林默检查怀表",
                "characters": ["林默"],
                "shots": [{"shot_no": 1, "visual": "灯下怀表", "action": "检查", "dialogue": "", "duration_seconds": 3}],
            },
            {
                "scene_no": 2,
                "title": "灯塔",
                "summary": "苏晚指出方向",
                "characters": ["苏晚"],
                "shots": [{"shot_no": 1, "visual": "旧灯塔", "action": "指向", "dialogue": "", "duration_seconds": 2}],
            },
        ],
        "confidence": {"overall": 0.9, "notes": []},
        "questions": [],
        "source_passages": [
            {"scene_no": 1, "paragraph_no": 2},
            {"scene_no": 2, "paragraph_no": 3},
        ],
    }

    _, evidence = _validate_breakdown_output(output, source)

    assert [item["quote"] for item in evidence["source_passages"]] == [
        "第二段：林默把怀表放在灯下。",
        "第三段：苏晚指向旧灯塔。",
    ]
    assert all(item["alignment_method"] == "PARAGRAPH_ID_EXACT" for item in evidence["source_passages"])
    for item in evidence["source_passages"]:
        assert source[item["source_start"] : item["source_end"]] == item["quote"]


def test_runtime_numbering_skips_chapter_headings_but_preserves_original_offsets() -> None:
    source = "第一章 雨夜来信\n\n林默在剧院发现信件。\n\n## 第二章\n\n苏晚进入后台。"
    numbered, offsets = _numbered_source_paragraphs(source, skip_headings=True)
    assert numbered.splitlines() == ["[P001] 林默在剧院发现信件。", "[P002] 苏晚进入后台。"]
    assert source[slice(*offsets[1])] == "林默在剧院发现信件。"
    assert source[slice(*offsets[2])] == "苏晚进入后台。"


def test_runtime_numbering_matches_import_preview_when_paragraph_contains_single_newlines() -> None:
    source = "第一章 雨夜来信\n\n林默进入剧院。\n他打开手电。\n\n苏晚在门外等待。"
    numbered, offsets = _numbered_source_paragraphs(
        source,
        skip_headings=True,
        paragraph_start=2,
        paragraph_end=2,
    )

    assert numbered == "[P001] 林默进入剧院。\n他打开手电。"
    assert source[slice(*offsets[1])] == "林默进入剧院。\n他打开手电。"


def test_runtime_rejects_wrong_scene_reference_and_incomplete_selected_coverage() -> None:
    source = "第一章 雨夜来信\n\n林默在剧院发现信件。\n\n苏晚进入后台并提到黑车。"
    _, offsets = _numbered_source_paragraphs(source, skip_headings=True)
    output = {
        "scenes": [{
            "scene_no": 1,
            "title": "苏晚到来",
            "summary": "苏晚进入后台并提到黑车",
            "characters": ["苏晚"],
            "shots": [{"shot_no": 1, "visual": "后台", "action": "苏晚进入并提到黑车", "dialogue": "", "duration_seconds": 3}],
        }],
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}],
    }
    with pytest.raises(DomainRuleError) as wrong_reference:
        _validate_breakdown_output(
            output,
            source,
            paragraph_offsets=offsets,
            minimum_scene_source_similarity=0.15,
        )
    assert wrong_reference.value.code == "LOCAL_LLM_SCENE_GROUNDING_INVALID"

    output["source_passages"] = [{"scene_no": 1, "paragraph_no": 2}]
    with pytest.raises(DomainRuleError) as incomplete:
        _validate_breakdown_output(
            output,
            source,
            paragraph_offsets=offsets,
            required_source_paragraph_nos=set(offsets),
            minimum_scene_source_similarity=0.15,
        )
    assert incomplete.value.code == "LOCAL_LLM_SOURCE_COVERAGE_INCOMPLETE"


def test_runtime_can_replace_ungrounded_scene_with_auditable_extractive_fallback() -> None:
    source = "林默在雨夜剧院发现一封旧信。信封盖着停业电影院的火漆章。"
    _, offsets = _numbered_source_paragraphs(source, skip_headings=True)
    output = {
        "scenes": [{
            "scene_no": 1,
            "title": "晴天码头追逐",
            "summary": "苏晚驾驶快艇追击逃犯",
            "characters": ["苏晚", "逃犯"],
            "shots": [
                {"shot_no": 1, "visual": "快艇冲浪", "action": "苏晚追击", "dialogue": "抓住他", "duration_seconds": 3},
                {"shot_no": 2, "visual": "码头爆炸", "action": "逃犯跳海", "dialogue": "", "duration_seconds": 2},
            ],
        }],
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}],
    }

    draft, evidence = _validate_breakdown_output(
        output,
        source,
        paragraph_offsets=offsets,
        required_source_paragraph_nos={1},
        minimum_scene_source_similarity=0.15,
        sanitize_ungrounded_scenes=True,
        sanitize_ungrounded_dialogue=True,
    )

    scene = draft["scenes"][0]
    assert [shot["duration_seconds"] for shot in scene["shots"]] == [3, 2]
    assert len(scene["shots"]) == 2
    assert all(shot["dialogue"] == "" for shot in scene["shots"])
    assert all(shot["visual"] in source and shot["action"] in source for shot in scene["shots"])
    assert evidence["scene_grounding_status"] == "PASS"
    assert evidence["scene_adjustment_status"] == "EXTRACTIVE_FALLBACK"
    assert evidence["extractive_fallback_scene_count"] == 1
    assert len(evidence["scene_adjustments"][0]["model_scene_sha256"]) == 64
    assert evidence["scene_adjustments"][0]["model_source_similarity"] < 0.15
    assert "快艇" not in json.dumps(evidence, ensure_ascii=False)
    validate_scene_source_grounding([scene], evidence["source_passages"], minimum_similarity=0.15)


def test_extractive_fallback_partitions_one_source_across_multiple_scenes() -> None:
    source = "林默听见脚步声。苏晚躲到铁架后。黑衣人推门进入。工作灯突然亮起。男人撞门逃走。桌上留下铜钥匙。"
    _, offsets = _numbered_source_paragraphs(source, skip_headings=True)
    scenes = []
    for scene_no in (1, 2):
        scenes.append({
            "scene_no": scene_no,
            "title": f"无关标题{scene_no}",
            "summary": f"码头快艇追逐{scene_no}",
            "characters": ["陌生人"],
            "shots": [
                {"shot_no": shot_no, "visual": "海上追逐", "action": "驾驶快艇", "dialogue": "", "duration_seconds": 2}
                for shot_no in range(1, 4)
            ],
        })
    output = {
        "scenes": scenes,
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}, {"scene_no": 2, "paragraph_no": 1}],
    }

    draft, evidence = _validate_breakdown_output(
        output,
        source,
        paragraph_offsets=offsets,
        required_source_paragraph_nos={1},
        minimum_scene_source_similarity=0.15,
        sanitize_ungrounded_scenes=True,
    )

    first, second = draft["scenes"]
    assert first["summary"] != second["summary"]
    assert first["summary"] in source and second["summary"] in source
    assert source.index(first["summary"]) < source.index(second["summary"])
    assert evidence["extractive_fallback_scene_count"] == 2
    validate_scene_distinctness(draft["scenes"])


def test_scene_distinctness_rejects_duplicate_summary_without_leaking_text() -> None:
    duplicate = "林默在地下室发现录音机并听见火灾真相。"
    with pytest.raises(DomainRuleError) as caught:
        validate_scene_distinctness([
            {"scene_no": 1, "summary": duplicate},
            {"scene_no": 2, "summary": duplicate},
        ])
    assert caught.value.code == "LOCAL_LLM_SCENE_DUPLICATE"
    assert duplicate not in json.dumps(caught.value.details, ensure_ascii=False)


def test_breakdown_dialogue_must_be_verbatim_in_the_same_scene_evidence() -> None:
    source = "林默说：门外有人。\n\n苏晚回答：我去看看。"
    output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "门外",
                "summary": "林默示警",
                "characters": ["林默"],
                "shots": [{"shot_no": 1, "visual": "门", "action": "回头", "dialogue": "林默：门外有人。", "duration_seconds": 2}],
            },
            {
                "scene_no": 2,
                "title": "查看",
                "summary": "苏晚起身",
                "characters": ["苏晚"],
                "shots": [{"shot_no": 1, "visual": "苏晚", "action": "起身", "dialogue": "苏晚：我去看看。", "duration_seconds": 2}],
            },
        ],
        "confidence": {"overall": 0.9, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}, {"scene_no": 2, "paragraph_no": 2}],
    }

    _, evidence = _validate_breakdown_output(output, source)
    assert evidence["dialogue_grounding_status"] == "PASS"

    output["scenes"][0]["shots"][0]["dialogue"] = "林默：我去看看。"
    with pytest.raises(DomainRuleError) as caught:
        _validate_breakdown_output(output, source)
    assert caught.value.code == "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID"


def test_breakdown_rejects_invented_multi_speaker_dialogue_without_leaking_text() -> None:
    source = "林默翻开记录本。周启明站在门边。"
    output = {
        "scenes": [{
            "scene_no": 1,
            "title": "记录",
            "summary": "二人查证",
            "characters": ["林默", "周启明"],
            "shots": [{
                "shot_no": 1,
                "visual": "记录本",
                "action": "翻页",
                "dialogue": "林默：证据确凿。周启明：我会配合。",
                "duration_seconds": 3,
            }],
        }],
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}],
    }

    with pytest.raises(DomainRuleError) as caught:
        _validate_breakdown_output(output, source)
    assert caught.value.code == "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID"
    assert "证据确凿" not in str(caught.value.details)


def test_runtime_can_strip_ungrounded_dialogue_with_auditable_hashes() -> None:
    source = "林默翻开记录本。周启明站在门边。"
    output = {
        "scenes": [{
            "scene_no": 1,
            "title": "记录",
            "summary": "二人查证",
            "characters": ["林默", "周启明"],
            "shots": [
                {"shot_no": 1, "visual": "记录本", "action": "翻页", "dialogue": "林默：原文没有这句话。", "duration_seconds": 3},
                {"shot_no": 2, "visual": "门边", "action": "站立", "dialogue": "", "duration_seconds": 2},
            ],
        }],
        "confidence": {"overall": 0.8, "notes": []},
        "questions": [],
        "source_passages": [{"scene_no": 1, "paragraph_no": 1}],
    }

    draft, evidence = _validate_breakdown_output(output, source, sanitize_ungrounded_dialogue=True)
    assert draft["scenes"][0]["shots"][0]["dialogue"] == ""
    assert evidence["dialogue_grounding_status"] == "PASS"
    assert evidence["dialogue_adjustment_status"] == "STRIPPED_UNGROUNDED"
    assert evidence["stripped_ungrounded_dialogue_count"] == 1
    assert len(evidence["dialogue_adjustments"][0]["model_dialogue_sha256"]) == 64
    assert "原文没有" not in json.dumps(evidence, ensure_ascii=False)


def test_local_llm_json_parser_prefers_complete_payload_over_reasoning_example() -> None:
    payload = _valid_breakdown_output()
    content = (
        '先确认格式示例：{"overall": 0.8, "notes": []}\n'
        "```json\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n```"
    )

    assert LocalLLMClient._parse_json_content(content) == payload


def test_local_llm_json_parser_unwraps_common_result_envelope() -> None:
    payload = _valid_breakdown_output()

    assert LocalLLMClient._parse_json_content(
        json.dumps({"script_breakdown": payload}, ensure_ascii=False)
    ) == payload


def test_local_llm_json_parser_preserves_complete_episode_over_nested_scene_key() -> None:
    payload = {
        "title": "第一集",
        "summary": "主角踏入山门。",
        "core_conflict": "主角必须通过考验。",
        "ending_hook": "古灯突然亮起。",
        "source_evidence": ["踏入山门"],
        "entity_observations": {
            "characters": [{"name": "林照", "observation": "本集主角"}],
            "scenes": [{"name": "山门", "observation": "入门考验地点"}],
            "props": [{"name": "照骨灯", "observation": "结尾亮起"}],
        },
    }

    assert LocalLLMClient._parse_json_content(json.dumps(payload, ensure_ascii=False)) == payload


def test_request_script_breakdown_flow(workspace, database, monkeypatch) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="bk_flow",
        title="Breakdown Flow",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "flow.md"
    source.write_text("# 第一幕\n\n侦探走进书房。拿起桌上的钥匙。", encoding="utf-8")
    import_svc = DocumentImportService(database, workspace)
    imported = import_svc.import_document(str(project["id"]), source)
    import_svc.commit(imported["import_session_id"], imported["preview_hash"])

    llm_svc = LocalLLMService(database, workspace)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {"status": "PASS", "model": "qwen2.5:7b", "runtime": "ollama"},
    )
    synced = llm_svc.sync_candidate("qwen2.5:7b")
    published = llm_svc.publish(synced["profile_version_id"])

    expected_output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "书房调查",
                "summary": "侦探拿钥匙",
                "characters": ["侦探"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "中景",
                        "action": "走进书房",
                        "dialogue": "",
                        "duration_seconds": 3,
                    },
                    {
                        "shot_no": 2,
                        "visual": "特写",
                        "action": "拿起桌上的钥匙",
                        "dialogue": "",
                        "duration_seconds": 2,
                    },
                ],
            }
        ],
        "confidence": {"overall": 0.95, "notes": []},
        "questions": ["钥匙的样式是否需指定？"],
        "source_passages": [{"scene_no": 1, "quote": "侦探走进书房。拿起桌上的钥匙。"}],
    }
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: expected_output,
    )

    with TestClient(create_app(workspace)) as client:
        resp = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "durable-breakdown-flow-1"},
            json={"profile_version_id": published["profile_version_id"]},
        )
        assert resp.status_code == 202
        submission = resp.json()
        assert submission["automatic_apply"] is False
        assert submission["requires_human_action"] is True
        job = submission["job"]
        assert job["type"] == "SCRIPT_BREAKDOWN_LOCAL_LLM"
        assert job["state"] == "QUEUED"
        assert job["subject_type"] == "IMPORT_SESSION"
        assert job["subject_id"] == imported["import_session_id"]

        replay = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "durable-breakdown-flow-1"},
            json={"profile_version_id": published["profile_version_id"]},
        )
        assert replay.status_code == 202
        assert replay.json()["job"]["id"] == job["id"]
        assert replay.json()["job"]["idempotent_replay"] is True

        list_resp = client.get(f"/api/v1/projects/{project['id']}/script-breakdown-drafts")
        assert list_resp.status_code == 200
        assert list_resp.json()["items"] == []

    outcome = LocalMediaWorker(database, workspace).run_once("script-breakdown-worker", ["CPU"])
    assert outcome is not None
    assert outcome["job"]["id"] == job["id"]
    assert outcome["result"]["job_state"] == "SUCCEEDED"
    assert outcome["artifact"]["kind"] == "SCRIPT_BREAKDOWN_REPORT"

    persisted = JobService(database, workspace).get_job(str(job["id"]))
    assert persisted["state"] == "SUCCEEDED"
    assert persisted["progress"]["phase"] == "DRAFT_READY"
    assert persisted["progress"]["percent"] == 100
    assert len(persisted["attempts"]) == 1
    with database.connect() as connection:
        # A completed model Job creates only a reviewable draft, never
        # production scenes/shots or an implicit apply audit event.
        assert connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='SCRIPT_BREAKDOWN_APPLIED'").fetchone()[0] == 0
        completed_audit = connection.execute(
            "SELECT job_id FROM audit_events WHERE action='SCRIPT_BREAKDOWN_COMPLETED' ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        assert completed_audit is not None and completed_audit["job_id"] == job["id"]

    # A new API process recovers the same SQLite-backed status after refresh.
    with TestClient(create_app(workspace)) as refreshed_client:
        job_resp = refreshed_client.get(f"/api/v1/jobs/{job['id']}")
        assert job_resp.status_code == 200
        assert job_resp.json()["job"]["state"] == "SUCCEEDED"
        list_resp = refreshed_client.get(f"/api/v1/projects/{project['id']}/script-breakdown-drafts")
        items = list_resp.json()["items"]
        assert len(items) == 1
        assert items[0]["evidence_status"] == "COMPLETE"
        assert items[0]["confidence"]["confidence"]["overall"] == 0.95
        assert items[0]["automatic_apply"] is False
        assert items[0]["requires_human_action"] is True


def _durable_breakdown_setup(workspace, database, monkeypatch, code: str, source_text: str | None = None):
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / f"{code}.md"
    source.write_text(source_text or "# 第一场\n\n侦探走进书房。拿起桌上的钥匙。", encoding="utf-8")
    documents = DocumentImportService(database, workspace)
    imported = documents.import_document(str(project["id"]), source)
    documents.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {"status": "PASS", "model": "qwen2.5:7b", "runtime": "ollama"},
    )
    llm = LocalLLMService(database, workspace)
    profile = llm.sync_candidate("qwen2.5:7b")
    llm.publish(str(profile["profile_version_id"]))
    return project, imported, str(profile["profile_version_id"])


def _valid_breakdown_output() -> dict[str, object]:
    return {
        "scenes": [{
            "scene_no": 1,
            "title": "书房调查",
            "summary": "侦探拿钥匙",
            "characters": ["侦探"],
            "shots": [{
                "shot_no": 1,
                "visual": "中景",
                "action": "走进书房",
                "dialogue": "",
                "duration_seconds": 3,
            }],
        }],
        "confidence": {"overall": 0.9, "notes": []},
        "questions": ["钥匙样式是否需要指定？"],
        "source_passages": [{"scene_no": 1, "quote": "侦探走进书房。拿起桌上的钥匙。"}],
    }


def _duration_matched_breakdown_output() -> dict[str, object]:
    output = _valid_breakdown_output()
    scenes = output["scenes"]
    assert isinstance(scenes, list) and isinstance(scenes[0], dict)
    scenes[0]["shots"] = [
        {
            "shot_no": shot_no,
            "visual": f"书房镜头 {shot_no}",
            "action": "侦探检查钥匙",
            "dialogue": "",
            "duration_seconds": 15,
        }
        for shot_no in range(1, 5)
    ]
    return output


def test_durable_breakdown_freezes_and_enforces_episode_duration_contract(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_duration_contract",
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    captured_call: dict[str, object] = {}

    def matched_output(self, prompt, text, **_kwargs):
        captured_call["prompt"] = prompt
        captured_call["schema"] = _kwargs["json_schema"]
        return _duration_matched_breakdown_output()

    monkeypatch.setattr("local_drama.infrastructure.local_llm.LocalLLMClient.chat_json", matched_output)
    with TestClient(create_app(workspace)) as client:
        queued = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "duration-contract-pass"},
            json={"profile_version_id": profile_version_id, "episode_id": episode["id"]},
        )
        assert queued.status_code == 202
        job_id = str(queued.json()["job"]["id"])
        invalid_range = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "duration-contract-invalid-range"},
            json={
                "profile_version_id": profile_version_id,
                "episode_id": episode["id"],
                "source_paragraph_start": 2,
                "source_paragraph_end": 1,
            },
        )
        assert invalid_range.status_code == 422
        assert invalid_range.json()["error"]["code"] == "BREAKDOWN_SOURCE_RANGE_INVALID"

    persisted = JobService(database, workspace).get_job(job_id)
    assert persisted["input_snapshot"]["schema_version"] == "localdrama.script-breakdown-job.v4"
    assert persisted["input_snapshot"]["target_episode_id"] == episode["id"]
    assert persisted["input_snapshot"]["target_duration_seconds"] == 60
    assert persisted["input_snapshot"]["source_paragraph_start"] == 1
    assert persisted["input_snapshot"]["source_paragraph_end"] == 2
    assert persisted["input_snapshot"]["source_paragraph_count"] == 2
    outcome = LocalMediaWorker(database, workspace).run_once("duration-contract-worker", ["CPU"])
    assert outcome is not None and outcome["result"]["job_state"] == "SUCCEEDED"
    assert "目标成片时长为 60 秒" in str(captured_call["prompt"])
    assert "至少返回 4 个镜头" in str(captured_call["prompt"])
    assert "必须覆盖的 P 编号全集是 [1]" in str(captured_call["prompt"])
    schema = captured_call["schema"]
    assert isinstance(schema, dict)
    assert schema["properties"]["source_passages"]["items"]["properties"]["paragraph_no"]["maximum"] == 1
    assert schema["properties"]["scenes"]["items"]["properties"]["source_paragraph_nos"]["items"]["maximum"] == 1
    draft = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))[0]
    assert draft["confidence"]["target_episode_id"] == episode["id"]
    assert draft["confidence"]["target_duration_seconds"] == 60
    assert draft["confidence"]["total_duration_seconds"] == 60
    assert draft["confidence"]["duration_contract_status"] == "PASS"


def test_durable_breakdown_rejects_an_oversized_single_model_request(workspace, database, monkeypatch) -> None:
    long_source = "# 长篇原文\n\n" + "\n\n".join(f"第 {index} 段：" + "山河故人" * 25 for index in range(1, 61))
    _, imported, profile_version_id = _durable_breakdown_setup(
        workspace,
        database,
        monkeypatch,
        "durable_breakdown_source_limit",
        long_source,
    )

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "oversized-breakdown-source"},
            json={"profile_version_id": profile_version_id},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BREAKDOWN_SOURCE_RANGE_TOO_LARGE"


def test_scene_local_source_ids_produce_complete_exact_passage_coverage() -> None:
    model_output = _valid_breakdown_output()
    model_output.pop("source_passages")
    scenes = model_output["scenes"]
    assert isinstance(scenes, list) and isinstance(scenes[0], dict)
    scenes[0]["source_paragraph_nos"] = [2]
    normalized = _normalize_scene_source_passages(model_output)
    draft, evidence = _validate_breakdown_output(normalized, "# 第一场\n\n侦探走进书房。拿起桌上的钥匙。")
    assert "source_paragraph_nos" not in draft["scenes"][0]
    assert evidence["source_passages"][0]["paragraph_no"] == 2
    assert evidence["source_passages"][0]["quote"] == "侦探走进书房。拿起桌上的钥匙。"


def test_draft_timings_are_audibly_normalized_without_changing_shot_content() -> None:
    model_output = _duration_matched_breakdown_output()
    scenes = model_output["scenes"]
    assert isinstance(scenes, list) and isinstance(scenes[0], dict)
    shots = scenes[0]["shots"]
    assert isinstance(shots, list)
    for shot in shots:
        shot["duration_seconds"] = 5
    normalized, evidence = _normalize_breakdown_durations(model_output, 60)
    normalized_shots = normalized["scenes"][0]["shots"]
    assert [shot["duration_seconds"] for shot in normalized_shots] == [15, 15, 15, 15]
    assert [shot["visual"] for shot in normalized_shots] == [f"书房镜头 {index}" for index in range(1, 5)]
    assert evidence["duration_adjustment_status"] == "NORMALIZED_TO_TARGET"
    assert evidence["model_total_duration_seconds"] == 20
    assert evidence["normalized_total_duration_seconds"] == 60


@pytest.mark.parametrize(
    ("target_duration_seconds", "model_shot_durations"),
    [
        # A model can return a result inside the historical +/-20% acceptance
        # window while still missing the configured target.  The persisted
        # replan must use the target whenever the shot-count bounds allow it.
        (120, [14.25] * 8),
        (90, [10.625] * 8),
    ],
)
def test_breakdown_timings_honor_configured_target_inside_tolerance(
    target_duration_seconds: int,
    model_shot_durations: list[float],
) -> None:
    model_output = _duration_matched_breakdown_output()
    scenes = model_output["scenes"]
    assert isinstance(scenes, list) and isinstance(scenes[0], dict)
    scenes[0]["shots"] = [
        {
            "shot_no": shot_no,
            "visual": f"书房镜头 {shot_no}",
            "action": "侦探检查钥匙",
            "dialogue": "",
            "duration_seconds": duration,
        }
        for shot_no, duration in enumerate(model_shot_durations, start=1)
    ]

    normalized, evidence = _normalize_breakdown_durations(model_output, target_duration_seconds)

    normalized_shots = normalized["scenes"][0]["shots"]
    assert isinstance(normalized_shots, list)
    total = sum(float(shot["duration_seconds"]) for shot in normalized_shots)
    assert total == pytest.approx(target_duration_seconds)
    assert evidence["model_total_duration_seconds"] == pytest.approx(sum(model_shot_durations))
    assert evidence["normalized_total_duration_seconds"] == pytest.approx(target_duration_seconds)
    assert evidence["duration_adjustment_target_seconds"] == target_duration_seconds


def test_draft_timings_use_the_nearest_feasible_duration_inside_tolerance() -> None:
    model_output = _duration_matched_breakdown_output()
    scenes = model_output["scenes"]
    assert isinstance(scenes, list) and isinstance(scenes[0], dict)
    shots = scenes[0]["shots"]
    assert isinstance(shots, list)
    for shot in shots:
        shot["duration_seconds"] = 5

    normalized, evidence = _normalize_breakdown_durations(model_output, 70)

    normalized_shots = normalized["scenes"][0]["shots"]
    assert [shot["duration_seconds"] for shot in normalized_shots] == [15, 15, 15, 15]
    assert evidence["normalized_total_duration_seconds"] == 60
    assert evidence["duration_adjustment_target_seconds"] == 60


def test_durable_breakdown_rejects_duration_mismatch_without_persisting_draft(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_duration_mismatch",
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: _valid_breakdown_output(),
    )
    with TestClient(create_app(workspace)) as client:
        queued = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "duration-contract-fail"},
            json={"profile_version_id": profile_version_id, "episode_id": episode["id"]},
        )
        assert queued.status_code == 202
        job_id = str(queued.json()["job"]["id"])

    outcome = LocalMediaWorker(database, workspace).run_once("duration-mismatch-worker", ["CPU"])
    assert outcome is not None
    assert outcome["error"] == "LOCAL_LLM_DURATION_UNSATISFIABLE"
    assert outcome["result"]["job_state"] == "FAILED"
    assert JobService(database, workspace).get_job(job_id)["last_error_code"] == "LOCAL_LLM_DURATION_UNSATISFIABLE"
    assert LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"])) == []


def test_failed_breakdown_job_requires_explicit_retry_and_reuses_same_job(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_retry",
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: (_ for _ in ()).throw(
            DomainRuleError("LOCAL_LLM_TEST_FAILURE", "模拟本地模型失败")
        ),
    )
    with TestClient(create_app(workspace)) as client:
        queued = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "durable-retry-1"},
            json={"profile_version_id": profile_version_id},
        )
        assert queued.status_code == 202
        job_id = str(queued.json()["job"]["id"])

    first = LocalMediaWorker(database, workspace).run_once("breakdown-retry-worker", ["CPU"])
    assert first is not None
    assert first["error"] == "LOCAL_LLM_TEST_FAILURE"
    assert first["result"]["job_state"] == "FAILED"
    assert LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"])) == []

    with TestClient(create_app(workspace)) as client:
        retried = client.post(f"/api/v1/jobs/{job_id}:retry")
        assert retried.status_code == 200
        assert retried.json()["job"]["id"] == job_id
        assert retried.json()["job"]["state"] == "QUEUED"

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: _valid_breakdown_output(),
    )
    second = LocalMediaWorker(database, workspace).run_once("breakdown-retry-worker", ["CPU"])
    assert second is not None
    assert second["job"]["id"] == job_id
    assert second["result"]["job_state"] == "SUCCEEDED"
    persisted = JobService(database, workspace).get_job(job_id)
    assert len(persisted["attempts"]) == 2
    assert [item["state"] for item in persisted["attempts"]] == ["FAILED", "SUCCEEDED"]
    assert len(LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))) == 1


def test_running_breakdown_cancel_is_honored_before_draft_persistence(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_cancel",
    )
    with TestClient(create_app(workspace)) as client:
        queued = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "durable-cancel-1"},
            json={"profile_version_id": profile_version_id},
        )
        assert queued.status_code == 202
    job_id = str(queued.json()["job"]["id"])

    def cancel_during_model_call(self, prompt, text, **_kwargs):
        cancelled = JobService(database, workspace).cancel(job_id)
        assert cancelled["state"] == "CANCEL_REQUESTED"
        return _valid_breakdown_output()

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        cancel_during_model_call,
    )
    outcome = LocalMediaWorker(database, workspace).run_once("breakdown-cancel-worker", ["CPU"])
    assert outcome is not None
    assert outcome["error"] == "JOB_CANCELLED"
    assert outcome["result"]["job_state"] == "CANCELLED"
    assert JobService(database, workspace).get_job(job_id)["state"] == "CANCELLED"
    assert LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"])) == []


def test_breakdown_retry_after_post_persist_crash_reuses_deterministic_draft(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_replay",
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: _valid_breakdown_output(),
    )
    with TestClient(create_app(workspace)) as client:
        queued = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "durable-replay-1"},
            json={"profile_version_id": profile_version_id},
        )
        assert queued.status_code == 202
    job_id = str(queued.json()["job"]["id"])

    worker = LocalMediaWorker(database, workspace)
    original_atomic_file = worker._atomic_file
    monkeypatch.setattr(
        worker,
        "_atomic_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.ENOSPC, "simulated report write failure")),
    )
    failed = worker.run_once("breakdown-replay-worker", ["CPU"])
    assert failed is not None
    assert failed["error"] == "DISK_FULL"
    assert failed["result"]["job_state"] == "FAILED"
    drafts = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    assert len(drafts) == 1
    draft_id = str(drafts[0]["id"])

    JobService(database, workspace).retry(job_id)
    monkeypatch.setattr(worker, "_atomic_file", original_atomic_file)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, **_kwargs: (_ for _ in ()).throw(AssertionError("idempotent replay must not call Ollama twice")),
    )
    recovered = worker.run_once("breakdown-replay-worker", ["CPU"])
    assert recovered is not None
    assert recovered["result"]["job_state"] == "SUCCEEDED"
    assert recovered["job"]["id"] == job_id
    final_drafts = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    assert [str(item["id"]) for item in final_drafts] == [draft_id]
