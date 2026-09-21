import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.local_llm import LocalLLMService
from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.application.projects import ProjectService
from local_drama.application.story_pipeline_ai import FullStoryAIGenerationService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.service_composition import build_pipeline_orchestrator
from local_drama.main import create_app


def _project_counts(database, project_id: str) -> dict[str, int]:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT
            (SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?) episodes,
            (SELECT COUNT(*) FROM creative_entries WHERE project_id=? AND kind='SERIES_BIBLE') bibles,
            (SELECT COUNT(*) FROM creative_entries WHERE project_id=? AND kind IN ('CHARACTER','SCENE','PROP')) dossiers,
            (SELECT COUNT(*) FROM story_assets WHERE project_id=? AND status='ACTIVE') assets,
            (SELECT COUNT(*) FROM story_asset_proposals WHERE project_id=? AND status='PENDING') proposals,
            (SELECT COUNT(*) FROM script_breakdown_drafts WHERE project_id=? AND status='DRAFT_READY') breakdowns,
            (SELECT COUNT(*) FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?) shots""",
            (project_id, project_id, project_id, project_id, project_id, project_id, project_id),
        ).fetchone()
    return {key: int(row[key]) for key in row.keys()}


def test_episode_specs_keep_import_api_paragraph_numbering() -> None:
    chaptered = PipelineOrchestratorService._episode_specs(
        "# 第一章\n\n第一段正文\n\n第二段正文\n\n# 第二章\n\n第三段正文"
    )
    assert chaptered[0]["source_start_paragraph"] == 1
    assert chaptered[0]["source_end_paragraph"] == 3
    assert "# 第一章" in chaptered[0]["source_text"]
    assert "# 第二章" not in chaptered[0]["source_text"]
    assert chaptered[1]["source_start_paragraph"] == 4
    assert "# 第二章" in chaptered[1]["source_text"]

    plain = PipelineOrchestratorService._episode_specs("第一段\n\n第二段")
    assert plain[0]["source_start_paragraph"] == 1
    assert plain[0]["source_end_paragraph"] == 2


@pytest.mark.parametrize("chapter_count", [61, 120, 121])
def test_source_coverage_marks_units_after_batch_60_unprocessed(chapter_count: int) -> None:
    text = "\n\n".join(
        f"# 第{index}章\n\n第{index}章正文，尾部事件-{index}。"
        for index in range(1, chapter_count + 1)
    )
    specs = PipelineOrchestratorService._episode_specs(text)
    assert len(specs) == chapter_count
    coverage = PipelineOrchestratorService._source_coverage(
        source_sha256="source-hash",
        authorized_scope={
            "start_paragraph": 1,
            "end_paragraph": chapter_count * 2,
            "paragraph_count": chapter_count * 2,
        },
        all_specs=specs,
        selected_specs=specs[:60],
        completed_count=60,
    )
    assert coverage["status"] == "PARTIAL"
    assert coverage["resume"]["reason"] == "BATCH_EPISODE_LIMIT"
    assert coverage["resume"]["resume_unit_number"] == 61
    assert coverage["unprocessed_ranges"][-1]["end_paragraph"] == chapter_count * 2


@pytest.mark.parametrize(
    "text",
    [
        "# 第一章\n\n" + ("超长章节😀" * 5_000) + "唯一尾部事件",
        ("无章节超长单段😀" * 4_000) + "唯一尾部事件",
    ],
    ids=["long_chapter", "long_paragraph"],
)
def test_source_coverage_exposes_24000_character_tail(text: str) -> None:
    specs = PipelineOrchestratorService._episode_specs(text)
    coverage = PipelineOrchestratorService._source_coverage(
        source_sha256="emoji-hash",
        authorized_scope={
            "start_paragraph": 1,
            "end_paragraph": specs[-1]["source_end_paragraph"],
            "paragraph_count": specs[-1]["source_end_paragraph"],
        },
        all_specs=specs,
        selected_specs=specs,
        completed_count=len(specs),
    )
    assert coverage["status"] == "PARTIAL"
    truncated = next(
        item for item in coverage["unprocessed_ranges"]
        if item["reason"] == "EPISODE_INPUT_CHARACTER_LIMIT"
    )
    assert truncated["resume_character_offset_in_unit"] == 24_000
    assert truncated["unprocessed_character_count"] > 0


def test_episode_prompt_submits_exactly_24000_unicode_characters() -> None:
    source = ("😀" * 24_000) + "唯一尾部事件"
    prompt = FullStoryAIGenerationService._episode_prompt(
        {"number": 1, "source_text": source}, "测试风格", 120
    )
    submitted = prompt.split("原稿：\n", 1)[1]
    assert len(submitted) == 24_000
    assert submitted == "😀" * 24_000
    assert "唯一尾部事件" not in submitted


def test_source_coverage_merges_overlap_and_respects_authorized_first_ten_chapters() -> None:
    overlap = [
        {"number": 1, "source_start_paragraph": 1, "source_end_paragraph": 4, "source_text": "a"},
        {"number": 2, "source_start_paragraph": 3, "source_end_paragraph": 6, "source_text": "b"},
    ]
    merged = PipelineOrchestratorService._source_coverage(
        source_sha256="overlap-hash",
        authorized_scope={"start_paragraph": 1, "end_paragraph": 6, "paragraph_count": 6},
        all_specs=overlap,
        selected_specs=overlap,
        completed_count=2,
    )
    assert merged["covered_paragraph_count"] == 6
    assert merged["completed_paragraph_intervals"] == [
        {"start_paragraph": 1, "end_paragraph": 6}
    ]

    text = "\n\n".join(
        f"# 第{index}章\n\n第{index}章正文。" for index in range(1, 21)
    )
    first_ten = PipelineOrchestratorService._episode_specs(
        text, start_paragraph=1, end_paragraph=20
    )
    assert len(first_ten) == 10
    authorized = PipelineOrchestratorService._source_coverage(
        source_sha256="selected-hash",
        authorized_scope={"start_paragraph": 1, "end_paragraph": 20, "paragraph_count": 20},
        all_specs=first_ten,
        selected_specs=first_ten,
        completed_count=10,
    )
    assert authorized["status"] == "FULL"
    assert authorized["unprocessed_ranges"] == []

    failed_window = PipelineOrchestratorService._source_coverage(
        source_sha256="failed-hash",
        authorized_scope={"start_paragraph": 1, "end_paragraph": 6, "paragraph_count": 6},
        all_specs=overlap,
        selected_specs=overlap,
        completed_count=1,
    )
    assert failed_window["covered_paragraph_count"] == 4
    assert failed_window["unprocessed_ranges"][0]["reason"] == "WINDOW_NOT_COMPLETED"


def test_pipeline_rejects_checkpoint_when_source_sha_snapshot_changes(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_changed_cursor",
        title="Changed cursor",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    service = build_pipeline_orchestrator(database, workspace)
    run = service.start_pipeline(
        str(project["id"]),
        raw_text="# 第一章\n\n角色走入山谷，发现唯一事件正在远处发生，并决定继续调查。",
    )
    with database.transaction() as connection:
        snapshot = json.loads(
            connection.execute(
                "SELECT input_snapshot_json FROM pipeline_runs WHERE id=?", (run["run_id"],)
            ).fetchone()[0]
        )
        snapshot["source_sha256"] = "changed-source-sha"
        connection.execute(
            "UPDATE pipeline_runs SET input_snapshot_json=? WHERE id=?",
            (json.dumps(snapshot), run["run_id"]),
        )

    with pytest.raises(DomainRuleError) as error:
        service.execute_draft_generation(run["run_id"])
    assert error.value.code == "PIPELINE_SOURCE_CHANGED"
    assert service.get_pipeline(str(project["id"]), run["run_id"])["state"] == "FAILED"


def test_story_pipeline_generates_lightweight_plan_then_auto_ready_records(workspace, database, mock_story_pipeline_ai) -> None:
    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_test",
        title="照骨灯测试短剧",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    source_text = (
        "# 第一章 归来\n\n破败废墟中，林渊握紧照骨古剑，凝视远处的九转金丹。\n"
        "林渊：哪怕只有一丝希望，我也要逆天改命。\n\n"
        "# 第二章 宗门\n\n天剑宗主殿内，掌门注视着林渊。\n掌门：你终于回来了。"
    )
    before = _project_counts(database, project_id)

    with TestClient(create_app(workspace)) as client:
        preflight = client.post(
            f"/api/v1/projects/{project_id}/pipeline:preflight",
            json={"raw_text": source_text, "target_episode_duration_seconds": 120},
        )
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()["safe_mode"] is True
        assert preflight.json()["ai"]["ready"] is True
        assert "大模型" in preflight.json()["effects"]["generation"]

        started = client.post(
            f"/api/v1/projects/{project_id}/pipeline:start",
            json={
                "raw_text": source_text,
                "visual_style": "国风仙侠 电影级写实 (Cinematic Realistic)",
                "target_episode_duration_seconds": 120,
            },
        )
        assert started.status_code == 200, started.text
        run = started.json()["run"]
        assert run["state"] == "RUNNING"
        assert run["job_id"]

        with database.connect() as connection:
            committed = connection.execute(
                """SELECT i.id,i.status FROM import_sessions i
                WHERE i.source_document_version_id=? ORDER BY i.updated_at DESC LIMIT 1""",
                (run["source_document_version_id"],),
            ).fetchone()
        assert committed is not None and committed["status"] == "COMMITTED"

        # Queueing and generation do not materialize production story data.
        assert _project_counts(database, project_id) == before

        worker_result = LocalMediaWorker(database, workspace).run_once("story-pipeline-test", ["CPU"])
        assert worker_result is not None
        assert worker_result.get("error") is None

        status = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}")
        assert status.status_code == 200
        draft_run = status.json()["run"]
        assert draft_run["state"] == "SUCCEEDED"
        assert draft_run["stage"] == "REVIEW_READY"
        assert draft_run["draft"]["schema_version"] == "pipeline.story-plan.v3"
        assert draft_run["draft"]["generation"]["generation_mode"] == "LLM_STAGED"
        assert draft_run["draft"]["generation"]["media_generation_started"] is False
        assert draft_run["draft"]["assets"]["characters"][0]["visual_prompt"]
        assert draft_run["draft"]["breakdowns"] == []
        assert draft_run["draft"]["source_coverage"]["status"] == "FULL"
        assert draft_run["quality_report"]["status"] in {"READY", "REVIEW_REQUIRED"}
        assert draft_run["quality_report"]["rule_version"] == "pipeline-quality/v2"
        assert all(
            check["applicable"] is True and check["severity"] in {"BLOCKER", "WARNING", "INFO"}
            for check in draft_run["quality_report"]["checks"]
        )
        assert draft_run["episodes_count"] >= 1
        assert draft_run["shots_count"] == 0
        assert _project_counts(database, project_id) == before

        history = client.get(f"/api/v1/projects/{project_id}/pipeline/runs")
        assert history.status_code == 200, history.text
        summary = history.json()["runs"][0]
        assert summary["run_id"] == run["run_id"]
        assert summary["episodes_count"] == draft_run["episodes_count"]
        assert "draft" not in summary
        assert "assets" not in summary

        stale_preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={
                "expected_revision": draft_run["revision"] - 1,
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
            },
        )
        assert stale_preview.status_code == 422
        assert stale_preview.json()["error"]["code"] == "PIPELINE_REVISION_CONFLICT"

        original_draft = draft_run["draft"]
        blocked_draft = json.loads(json.dumps(original_draft))
        blocked_draft["assets"]["characters"] = []
        with database.transaction() as connection:
            connection.execute(
                "UPDATE pipeline_runs SET draft_json=? WHERE id=?",
                (json.dumps(blocked_draft, ensure_ascii=False), run["run_id"]),
            )
        blocked_preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
            },
        )
        assert blocked_preview.status_code == 200
        assert blocked_preview.json()["can_apply"] is False
        assert blocked_preview.json()["quality_report"]["status"] == "BLOCKED"
        blocked_apply = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": blocked_preview.json()["impact"]["impact_sha256"],
            },
        )
        assert blocked_apply.status_code == 422
        assert blocked_apply.json()["error"]["code"] == "PIPELINE_QUALITY_BLOCKED"
        assert _project_counts(database, project_id) == before
        with database.transaction() as connection:
            connection.execute(
                "UPDATE pipeline_runs SET draft_json=? WHERE id=?",
                (json.dumps(original_draft, ensure_ascii=False), run["run_id"]),
            )

        impact_preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
            },
        )
        assert impact_preview.status_code == 200, impact_preview.text
        assert impact_preview.json()["impact"]["writes_performed"] is False
        assert _project_counts(database, project_id) == before
        rejected_impact = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": "0" * 64,
            },
        )
        assert rejected_impact.status_code == 422
        assert rejected_impact.json()["error"]["code"] == "PIPELINE_APPLY_IMPACT_CONFLICT"
        assert _project_counts(database, project_id) == before

        with database.transaction() as connection:
            source_row = connection.execute(
                "SELECT text_sha256 FROM source_document_versions WHERE id=?",
                (run["source_document_version_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE source_document_versions SET text_sha256=? WHERE id=?",
                ("f" * 64, run["source_document_version_id"]),
            )
        changed_source_apply = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": impact_preview.json()["impact"]["impact_sha256"],
            },
        )
        assert changed_source_apply.status_code == 422
        assert changed_source_apply.json()["error"]["code"] == "PIPELINE_SOURCE_CHANGED"
        assert _project_counts(database, project_id) == before
        with database.transaction() as connection:
            connection.execute(
                "UPDATE source_document_versions SET text_sha256=? WHERE id=?",
                (source_row["text_sha256"], run["source_document_version_id"]),
            )
        applied = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": impact_preview.json()["impact"]["impact_sha256"],
            },
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["run"]["apply_state"] == "APPLIED"

    after = _project_counts(database, project_id)
    assert after["episodes"] >= before["episodes"]
    assert after["bibles"] == 1
    assert after["dossiers"] == before["dossiers"]
    assert after["breakdowns"] == before["breakdowns"]
    assert after["proposals"] == before["proposals"]
    # Core reusable assets are created automatically; per-episode shots remain deferred.
    assert after["assets"] > before["assets"]
    assert after["shots"] == before["shots"]
    with database.connect() as connection:
        episode_scope = json.loads(
            connection.execute(
                """SELECT e.source_range_json FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY e.display_order,e.id LIMIT 1""",
                (project_id,),
            ).fetchone()[0]
        )
        source_hash = connection.execute(
            "SELECT text_sha256 FROM source_document_versions WHERE id=?",
            (run["source_document_version_id"],),
        ).fetchone()[0]
    assert episode_scope["source_document_version_id"] == run["source_document_version_id"]
    assert episode_scope["import_session_id"] == committed["id"]
    assert episode_scope["text_sha256"] == source_hash


def test_authorized_pipeline_apply_continues_in_backend_after_restart_and_replays(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipeline_authorized_continuation",
        title="Authorized continuation",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = build_pipeline_orchestrator(database, workspace)
    run = service.start_pipeline(
        project_id,
        raw_text="# 第一章\n\n林渊进入山谷寻找古剑，发现守门人并决定继续前行。",
        application_authorization={
            "endpoint": "APPLY_SELECTED_SECTIONS",
            "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
        },
    )
    continuation_id = run["apply_continuation"]["job_id"]
    assert run["apply_continuation"]["state"] == "QUEUED"

    first_process = LocalMediaWorker(database, workspace)
    draft_result = first_process.run_once("pipeline-draft-process", ["CPU"])
    assert draft_result and draft_result.get("error") is None
    generated = service.get_pipeline(project_id, run["run_id"])
    assert generated["state"] == "SUCCEEDED"
    assert generated["apply_state"] == "NOT_APPLIED"

    with database.transaction() as connection:
        original_draft_json = connection.execute(
            "SELECT draft_json FROM pipeline_runs WHERE id=?", (run["run_id"],)
        ).fetchone()[0]
        changed_draft = json.loads(original_draft_json)
        changed_draft["story_plan"]["episodes"][0]["title"] = "生成后被修改的标题"
        connection.execute(
            "UPDATE pipeline_runs SET draft_json=?,revision=revision+1 WHERE id=?",
            (json.dumps(changed_draft, ensure_ascii=False), run["run_id"]),
        )
    with pytest.raises(DomainRuleError) as changed_error:
        service.continue_authorized_application(run["run_id"])
    assert changed_error.value.code == "PIPELINE_AUTHORIZED_DRAFT_CHANGED"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE pipeline_runs SET draft_json=?,revision=revision+1 WHERE id=?",
            (original_draft_json, run["run_id"]),
        )

    # A fresh worker process can claim the durable dependent command without a browser.
    second_process = LocalMediaWorker(database, workspace)
    apply_result = second_process.run_once("pipeline-apply-process", ["CPU"])
    assert apply_result and apply_result.get("error") is None
    applied = service.get_pipeline(project_id, run["run_id"])
    assert applied["apply_state"] == "APPLIED"
    assert applied["apply_continuation"]["job_id"] == continuation_id
    assert applied["apply_continuation"]["state"] == "SUCCEEDED"

    replay = service.continue_authorized_application(run["run_id"])
    assert replay["idempotent_replay"] is True
    assert replay["run"]["revision"] == applied["revision"]


def test_authorized_source_pipeline_hands_off_once_to_durable_whole_drama_session(
    workspace, database, mock_story_pipeline_ai, monkeypatch,
) -> None:
    del mock_story_pipeline_ai
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "model": "qwen-test",
            "runtime": "ollama",
        },
    )
    profile = LocalLLMService(database, workspace).sync_candidate("qwen-test")
    LocalLLMService(database, workspace).publish(str(profile["profile_version_id"]))
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipeline_to_production_session",
        title="Source to review continuation",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = build_pipeline_orchestrator(database, workspace)
    run = service.start_pipeline(
        project_id,
        raw_text="# 第一章\n\n林枫进入坠仙谷寻找九阳神丹，并发现顾清雪留下的示警。",
        application_authorization={
            "endpoint": "APPLY_SELECTED_SECTIONS",
            "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
        },
        production_authorization={
            "endpoint": "WAITING_REVIEW",
            "production_mode": "DRAFT",
            "checkpoint_policy": "ON_EXCEPTION",
            "tts_enabled": False,
            "max_parallel_episodes": 1,
            "min_free_disk_bytes": 1,
        },
    )
    assert run["production_continuation"]["state"] == "PENDING"

    plan_result = LocalMediaWorker(database, workspace).run_once("source-plan-worker", ["CPU"])
    assert plan_result is not None and plan_result.get("error") is None
    apply_result = LocalMediaWorker(database, workspace).run_once("source-apply-worker", ["CPU"])
    assert apply_result is not None and apply_result.get("error") is None

    continued = service.get_pipeline(project_id, run["run_id"])
    session_id = continued["production_continuation"]["session_id"]
    assert session_id
    assert continued["production_continuation"]["session_status"] == "RUNNING"
    with database.connect() as connection:
        sessions = connection.execute(
            "SELECT id,status,scope_type FROM production_sessions WHERE project_id=?",
            (project_id,),
        ).fetchall()
        items = connection.execute(
            "SELECT state,current_stage FROM production_session_items WHERE session_id=?",
            (session_id,),
        ).fetchall()
        human_approvals = connection.execute(
            "SELECT COUNT(*) FROM review_decisions WHERE decision IN ('APPROVED','REJECTED')"
        ).fetchone()[0]
    assert [dict(item) for item in sessions] == [
        {"id": session_id, "status": "RUNNING", "scope_type": "WHOLE_DRAMA"}
    ]
    assert len(items) >= 1
    assert all(item["state"] in {"WAITING", "RUNNING", "BLOCKED"} for item in items)
    assert human_approvals == 0

    replay = service.continue_authorized_application(run["run_id"])
    assert replay["idempotent_replay"] is True
    assert replay["production"]["idempotent_replay"] is True
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM production_sessions WHERE project_id=?", (project_id,)
        ).fetchone()[0] == 1


def test_authorized_pipeline_apply_can_be_cancelled_before_application(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipeline_cancel_continuation",
        title="Cancel continuation",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = build_pipeline_orchestrator(database, workspace)
    run = service.start_pipeline(
        project_id,
        raw_text="# 第一章\n\n角色在雨夜抵达车站，发现遗失的信件并开始追查。",
        application_authorization={
            "endpoint": "APPLY_SELECTED_SECTIONS",
            "sections": ["STORY_PLAN"],
        },
    )
    LocalMediaWorker(database, workspace).run_once("pipeline-cancel-draft", ["CPU"])
    cancelled = service.cancel_pipeline(project_id, run["run_id"])
    assert cancelled["state"] == "SUCCEEDED"
    assert cancelled["apply_state"] == "NOT_APPLIED"
    assert cancelled["application_authorization"]["endpoint"] == "DRAFT_ONLY"
    assert cancelled["application_authorization"]["revoked_at"]
    assert cancelled["apply_continuation"]["state"] == "CANCELLED"


def test_pipeline_apply_preview_preserves_produced_episode_and_reports_context_changes(
    workspace, database,
) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipeline_impact_preview",
        title="Pipeline impact preview",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    ProjectService(database, workspace.projects_root).create_shot(
        str(episode["id"]), "EPISODE_001-01-01", 3_000,
    )
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_assets
               (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES ('existing-hanli',?,'CHARACTER','CHAR_HANLI','韩立','已有角色',NULL,'{}','ACTIVE',
                       CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')""",
            (project["id"],),
        )
    draft = {
        "story_plan": {
            "episodes": [
                {"number": 1, "code": "EPISODE_001", "title": "新标题"},
                {"number": 2, "code": "EPISODE_002", "title": "第二集"},
            ]
        },
        "story_bible": {"title": "新总纲"},
        "assets": {
            "characters": [{"name": "韩立"}],
            "scenes": [],
            "props": [{"name": "掌天瓶"}],
        },
    }
    before = _project_counts(database, str(project["id"]))
    with database.connect() as connection:
        impact = PipelineOrchestratorService._pipeline_application_impact(
            connection,
            project_id=str(project["id"]),
            run_id="preview-only",
            run_revision=3,
            draft=draft,
            selected=["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
        )
    assert impact["writes_performed"] is False
    assert [item["code"] for item in impact["episodes"]["preserve"]] == ["EPISODE_001"]
    assert [item["code"] for item in impact["episodes"]["add"]] == ["EPISODE_002"]
    assert impact["assets"]["reuse"] == ["CHARACTER:韩立"]
    assert impact["assets"]["add"] == ["PROP:掌天瓶"]
    assert impact["produced_episode_context_changes"] == ["EPISODE_001"]
    assert impact["requires_confirmation"] is True
    assert _project_counts(database, str(project["id"])) == before
