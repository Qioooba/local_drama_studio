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


def test_episode_specs_include_the_prologue_before_the_first_chapter() -> None:
    """Text between the authorised start and the first chapter must be planned.

    The prologue (序章/引子/untitled opening) is real manuscript. Before the fix
    the chapter loop started at the first recognised chapter, so this text never
    reached any unit while the run still reported FULL.
    """
    text = "\n\n".join(
        [
            "序幕：青灯在桥下的独有事实。",
            "第一章 雨夜",
            "第一章正文。",
        ]
    )
    specs = PipelineOrchestratorService._episode_specs(text)
    planned = "\n".join(spec["source_text"] for spec in specs)
    assert "青灯在桥下的独有事实" in planned, "prologue text must reach a unit"
    assert "第一章正文" in planned
    prologue = specs[0]
    assert prologue["source_start_paragraph"] == 1
    assert prologue["source_end_paragraph"] == 1
    assert prologue["source_text"] == "序幕：青灯在桥下的独有事实。"
    assert specs[1]["source_start_paragraph"] == 2


def test_source_coverage_never_reports_full_while_a_paragraph_is_uncovered() -> None:
    """FULL is a set comparison, not a count comparison."""
    text = "\n\n".join(["序幕独有事实。", "第一章", "正文一。", "正文二。"])
    specs = PipelineOrchestratorService._episode_specs(text)
    authorized = {"start_paragraph": 1, "end_paragraph": 4, "paragraph_count": 4}
    complete = PipelineOrchestratorService._source_coverage(
        source_sha256="h",
        authorized_scope=authorized,
        all_specs=specs,
        selected_specs=specs,
        completed_count=len(specs),
    )
    assert complete["status"] == "FULL"
    assert complete["coverage_gaps"] == []
    assert complete["completed_paragraph_intervals"] == [
        {"start_paragraph": 1, "end_paragraph": 4}
    ]

    # Dropping the prologue unit leaves paragraph 1 uncovered. The old count-only
    # rule reported FULL here because completed units equalled planned units.
    prologue_dropped = PipelineOrchestratorService._source_coverage(
        source_sha256="h",
        authorized_scope=authorized,
        all_specs=specs,
        selected_specs=specs[1:],
        completed_count=len(specs) - 1,
    )
    assert prologue_dropped["status"] == "PARTIAL"
    assert prologue_dropped["coverage_complete"] is False
    assert prologue_dropped["coverage_gaps"] == [
        {"start_paragraph": 1, "end_paragraph": 1, "reason": "AUTHORIZED_RANGE_NOT_COVERED"}
    ]

    # A trailing authorised paragraph that no unit covers is also an explicit gap.
    trailing = PipelineOrchestratorService._source_coverage(
        source_sha256="h",
        authorized_scope={"start_paragraph": 1, "end_paragraph": 6, "paragraph_count": 6},
        all_specs=specs,
        selected_specs=specs,
        completed_count=len(specs),
    )
    assert trailing["status"] == "PARTIAL"
    assert trailing["coverage_gaps"] == [
        {"start_paragraph": 5, "end_paragraph": 6, "reason": "AUTHORIZED_RANGE_NOT_COVERED"}
    ]


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
def test_long_unit_is_split_into_bounded_windows_covering_the_tail(text: str) -> None:
    """A unit longer than the prompt budget becomes several bounded windows.

    The previous behaviour cut the unit at 24,000 characters and reported the
    remainder as unprocessed. Bounded windows must instead carry every character
    of the unit, so the trailing fact still reaches the model and the authorised
    range is provably complete.
    """
    specs = PipelineOrchestratorService._episode_specs(text)
    assert specs, "bounded windows must exist for a long unit"
    for spec in specs:
        assert spec["source_character_count"] == len(spec["source_text"])
        assert len(spec["source_text"]) <= 24_000
    # Every window belongs to the same episode unit exactly once.
    assert {int(spec["unit_number"]) for spec in specs} == {1}
    assert [spec["window_index"] for spec in specs] == list(range(1, len(specs) + 1))
    assert all(spec["window_count"] == len(specs) for spec in specs)
    assert "唯一尾部事件" in specs[-1]["source_text"]
    # Concatenating the bounded windows in order reproduces the original unit text
    # character for character, so no prose was dropped by the windowing step.
    assert "".join(spec["source_text"] for spec in specs) == text

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
    assert coverage["status"] == "FULL"
    assert coverage["coverage_complete"] is True
    assert coverage["coverage_gaps"] == []
    assert coverage["unprocessed_ranges"] == []
    assert not [
        item for item in coverage["unprocessed_ranges"]
        if item["reason"] == "EPISODE_INPUT_CHARACTER_LIMIT"
    ]


def test_episode_prompt_submits_exactly_24000_unicode_characters() -> None:
    source = ("😀" * 24_000) + "唯一尾部事件"
    prompt = FullStoryAIGenerationService._episode_prompt(
        {"number": 1, "source_text": source}, "测试风格", 120
    )
    submitted = prompt.split("原稿：\n", 1)[1]
    assert len(submitted) == 24_000
    assert submitted == "😀" * 24_000
    assert "唯一尾部事件" not in submitted


def test_bounded_windows_preserve_body_text_when_boundary_falls_between_paragraphs() -> None:
    """A window boundary between two paragraphs must keep the blank-line separator.

    Regression: the separator used to be dropped whenever a window filled up exactly
    at a paragraph break, so the tail of a long chapter silently lost characters.
    """
    # Six roughly 4,000-character paragraphs in one chapter exceed the 24,000
    # character input budget, so the unit must be split while keeping the blank
    # line that separates each paragraph.
    paragraphs = "# 第一章\n\n" + "\n\n".join(
        f"第{index}段开始。" + (f"第{index}段正文。" * 700) for index in range(1, 7)
    )
    specs = PipelineOrchestratorService._episode_specs(paragraphs)
    assert len(specs) > 1, "the fixture must actually produce several windows"
    assert [spec["window_index"] for spec in specs] == list(range(1, len(specs) + 1))
    assert {int(spec["unit_number"]) for spec in specs} == {1}
    assert "".join(spec["source_text"] for spec in specs) == paragraphs
    for spec in specs:
        assert len(spec["source_text"]) <= 24_000
        assert spec["source_character_count"] == len(spec["source_text"])


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


def test_auto_authorized_continuation_gets_its_own_apply_job_for_the_new_revision(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """PR-05: the second authorized batch must not replay the first batch's apply job.

    The continuation job key was ``pipeline-apply:{run_id}``, so after batch 1 was
    applied the key replayed batch 1's already-SUCCEEDED job: the new revision's
    episodes were never applied, and the authorization then refused the draft as
    changed.  Each authorized revision now gets its own dependency job.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipeline_authorized_batches",
        title="Authorized batches",
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
    first_apply_job = str(run["apply_continuation"]["job_id"])
    assert first_apply_job

    assert LocalMediaWorker(database, workspace).run_once("pipeline-b1", ["CPU"]) is not None
    applied = service.get_pipeline(project_id, run["run_id"])
    with database.connect() as connection:
        jobs = connection.execute(
            "SELECT id FROM jobs WHERE subject_id=? AND type IN (SELECT code FROM job_stage_definitions WHERE code='STORY_PIPELINE')",
            (run["run_id"],),
        ).fetchall()
    del jobs
    with database.transaction() as connection:
        # The second batch publishes a NEW draft revision under the same grant; the
        # previous revision stays recorded as the applied one.
        row = connection.execute(
            "SELECT draft_json FROM pipeline_runs WHERE id=?", (run["run_id"],)
        ).fetchone()
        previous_draft = json.loads(str(row["draft_json"]))
        applied_hash = service._draft_revision_hash(previous_draft)  # noqa: SLF001
        previous_draft["story_plan"]["episodes"].append(
            {"number": 2, "code": "EP02", "title": "第二集", "summary": "续接批次新增的一集。"}
        )
        connection.execute(
            """UPDATE pipeline_runs SET draft_json=?,apply_state='APPLIED',applied_revision_hash=?,
            applied_episode_numbers_json='[1]',revision=revision+1 WHERE id=?""",
            (json.dumps(previous_draft, ensure_ascii=False), applied_hash, run["run_id"]),
        )
    del applied

    continuation = service.continue_authorized_application(run["run_id"])
    new_apply_job = str(continuation["run"]["apply_continuation"]["job_id"])
    # A continuation click that finds a newer revision re-scopes the authorization and
    # hands the new revision its own command.
    assert continuation.get("idempotent_replay") is not True
    assert new_apply_job and new_apply_job != first_apply_job
    assert continuation["run"]["apply_state"] == "APPLIED"
    # The new revision's command is a real, durable, independently replayable Job.
    with database.connect() as connection:
        staged = connection.execute(
            "SELECT type,state,input_snapshot_json,idempotency_key FROM jobs WHERE id=?",
            (new_apply_job,),
        ).fetchone()
    assert staged is not None
    assert str(staged["state"]) in {"QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED"}
    assert str(staged["idempotency_key"]).startswith(f"pipeline-apply:{run['run_id']}:")
    assert json.loads(str(staged["input_snapshot_json"]))["authorized_sections"] == [
        "STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"
    ]


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


def _long_second_chapter_text() -> str:
    """The audit's manuscript: a short chapter 1 plus a 31,500-character chapter 2.

    ``_LONG_UNIT_SLICE_CHARACTERS`` is 3,500, so this chapter becomes several
    bounded input windows of the SAME episode unit.
    """

    paragraph = "第二段正文，" * 100  # 600 characters
    long_chapter = "\n\n".join(f"{paragraph}{index:03d}" for index in range(52))  # ~31,300 characters
    return f"# 第一章 短章\n\n第一章正文。\n\n# 第二章 长章\n\n{long_chapter}"


def test_one_long_chapter_yields_several_windows_but_one_unit() -> None:
    """PR-01: windows are a model-batch unit; the episode unit stays single."""

    specs = PipelineOrchestratorService._episode_specs(_long_second_chapter_text())
    numbers = [int(spec["number"]) for spec in specs]
    # The second chapter really is split into several bounded input windows...
    assert numbers.count(2) >= 2, numbers
    assert numbers[0] == 1
    # ...but every window still reports the SAME unit number, and the windows of
    # that unit cover the whole chapter contiguously.
    unit_two = [spec for spec in specs if int(spec["number"]) == 2]
    assert [int(spec["window_index"]) for spec in unit_two] == list(range(1, len(unit_two) + 1))
    assert {int(spec["window_count"]) for spec in unit_two} == {len(unit_two)}
    assert int(unit_two[0]["source_start_paragraph"]) < int(unit_two[-1]["source_end_paragraph"])
    merged = PipelineOrchestratorService._merge_unit_windows(
        [{"number": number, "title": f"第{number}集"} for number in numbers], specs
    )
    assert [int(item["number"]) for item in merged] == [1, 2]
    assert len(merged[1]["source_windows"]) == len(unit_two)
    assert merged[1]["source_character_count"] > int(unit_two[0]["source_character_count"])


def test_a_long_chapter_is_one_episode_in_the_draft_and_applies(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """The audit reproduction: preview says ``can_apply`` and apply must succeed.

    Before the fix the draft carried four window-level episodes numbered
    ``[1, 2, 2, 2]``; preview reported ``can_apply=true`` and the real apply raised
    ``sqlite3.IntegrityError: UNIQUE constraint failed: episodes.season_id,
    episodes.code``.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_long_chapter",
        title="长章分集",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    source_text = _long_second_chapter_text()
    before = _project_counts(database, project_id)

    with TestClient(create_app(workspace)) as client:
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
        assert LocalMediaWorker(database, workspace).run_once("story-pipeline-long", ["CPU"]) is not None
        draft_run = client.get(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}"
        ).json()["run"]
        assert draft_run["state"] == "SUCCEEDED"
        draft = draft_run["draft"]
        episodes = draft["story_plan"]["episodes"]
        numbers = [int(item["number"]) for item in episodes]
        codes = [str(item["code"]) for item in episodes]
        # One episode per unit, and never a duplicate number or code.
        assert numbers == [1, 2], numbers
        assert len(set(numbers)) == len(numbers)
        assert len(set(codes)) == len(codes)
        assert draft_run["quality_report"]["status"] in {"READY", "REVIEW_REQUIRED"}
        assert all(check["passed"] for check in draft_run["quality_report"]["checks"] if check["severity"] == "BLOCKER")
        # The completed windows of the long unit are all mapped onto the planned
        # episode, so nothing was dropped by the aggregation.
        coverage = draft["source_coverage"]
        assert coverage["status"] == "FULL"
        assert {int(item["unit_number"]) for item in coverage["completed_ranges"]} == {1, 2}
        assert len([item for item in coverage["completed_ranges"] if int(item["unit_number"]) == 2]) >= 2
        # The merged episode keeps the WHOLE chapter range, not just its last window.
        second = next(item for item in episodes if int(item["number"]) == 2)
        window_two = [item for item in coverage["completed_ranges"] if int(item["unit_number"]) == 2]
        assert int(second["source_start_paragraph"]) == min(int(item["start_paragraph"]) for item in window_two)
        assert int(second["source_end_paragraph"]) == max(int(item["end_paragraph"]) for item in window_two)

        preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={"expected_revision": draft_run["revision"], "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["can_apply"] is True, preview.json()
        applied = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": preview.json()["impact"]["impact_sha256"],
            },
        )
        assert applied.status_code == 200, applied.text
        created = applied.json()["created"]
        # Two units, but unit 1 is the project's own initial episode: exactly one new
        # episode row, never one per input window.
        assert created["episodes"] == 1, created

    with database.connect() as connection:
        rows = connection.execute(
            """SELECT e.code, e.number, e.source_range_json FROM episodes e
            JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY e.number""",
            (project_id,),
        ).fetchall()
    codes = [str(row["code"]) for row in rows]
    assert len(codes) == len(set(codes)), codes
    assert len(rows) == 2, codes
    # The long chapter's episode keeps the whole chapter range, not just the last
    # window's slice, so the second half of the manuscript is not silently lost.
    second_range = json.loads(str(rows[1]["source_range_json"]))
    assert int(second_range["start_paragraph"]) < int(second_range["end_paragraph"]), second_range
    counts = _project_counts(database, project_id)
    assert counts["episodes"] == 2
    assert counts["episodes"] > before["episodes"]


def test_a_failed_continuation_is_requeued_instead_of_reporting_queued(
    workspace, database, mock_story_pipeline_ai, monkeypatch,
) -> None:
    """PR-04: a continuation replay must reflect the Job's LIVE state.

    The audit's sequence was: batch 1 succeeds, ``continue`` runs batch 2, an
    injected model error fails batch 2, and ``continue`` is called again.  The
    command key ``pipeline-continue:{run}:{next_window}`` had not advanced, so the
    idempotency layer replayed the ORIGINAL response — which still said QUEUED — and
    the caller wrote the run back to RUNNING while the Job stayed FAILED.
    ``JobService.claim()`` then returned ``None`` and ``retry_pipeline`` refused
    (it requires FAILED), so the workbench was permanently occupied.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_continue_retry",
        title="续接失败重试",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    chapter_count = 61
    source_text = "\n\n".join(
        f"# 第{index}章\n\n第{index}章正文，尾部事件-{index}。" for index in range(1, chapter_count + 1)
    )
    service = build_pipeline_orchestrator(database, workspace)
    with TestClient(create_app(workspace)) as client:
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
        assert LocalMediaWorker(database, workspace).run_once("story-retry-batch-1", ["CPU"]) is not None
        first = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert first["state"] == "SUCCEEDED"
        coverage = first["draft"]["source_coverage"]
        cursor = first["analysis_cursor"]

        queued = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": first["revision"],
                "expected_source_sha256": coverage["source_sha256"],
                "expected_next_window_index": cursor["next_window_index"],
            },
        )
        assert queued.status_code == 200, queued.text
        second_job_id = str(queued.json()["run"]["job_id"])
        assert second_job_id

        # The second batch fails for a retryable reason.
        def failing_generate(self, **kwargs):
            del self, kwargs
            raise DomainRuleError("PIPELINE_LLM_GENERATION_FAILED", "注入的可恢复模型异常")

        monkeypatch.setattr(FullStoryAIGenerationService, "generate", failing_generate)
        assert LocalMediaWorker(database, workspace).run_once("story-retry-batch-2", ["CPU"]) is not None
        failed = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert failed["state"] == "FAILED", failed["state"]
        with database.connect() as connection:
            live_job_state = str(
                connection.execute("SELECT state FROM jobs WHERE id=?", (second_job_id,)).fetchone()["state"]
            )
        assert live_job_state in {"FAILED", "NEEDS_ATTENTION"}, live_job_state

        # The user clicks continue again on the same cursor.
        retried = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": failed["revision"],
                "expected_source_sha256": coverage["source_sha256"],
                "expected_next_window_index": cursor["next_window_index"],
            },
        )
        assert retried.status_code == 200, retried.text
        body = retried.json()["run"]
        # The response reports the Job's REAL state, and the batch was requeued.
        assert body["job_state"] in {"QUEUED", "CLAIMED", "RUNNING"}, body
        assert body["recovery_action"] == "RETRIED_FAILED_BATCH", body
        assert body["state"] == "RUNNING"
        with database.connect() as connection:
            requeued = connection.execute("SELECT state FROM jobs WHERE id=?", (second_job_id,)).fetchone()
        assert str(requeued["state"]) in {"QUEUED", "CLAIMED", "RUNNING"}, str(requeued["state"])
        # The retry really cleared the previous failure and made the batch claimable.
        with database.connect() as connection:
            cleared = connection.execute(
                "SELECT cancel_requested_at, last_error_code FROM jobs WHERE id=?", (second_job_id,)
            ).fetchone()
        assert cleared["cancel_requested_at"] is None
    assert service is not None


def test_a_run_is_reconciled_when_its_job_died(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """A RUNNING projection must converge on its Job's terminal state.

    The audit's run could not be retried because ``retry_pipeline`` requires
    ``FAILED`` while the projection stayed ``RUNNING`` forever.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_reconcile",
        title="状态对账",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = build_pipeline_orchestrator(database, workspace)
    with TestClient(create_app(workspace)) as client:
        started = client.post(
            f"/api/v1/projects/{project_id}/pipeline:start",
            json={
                "raw_text": "# 第一章 归来\n\n林渊握紧照骨古剑，凝视远处的九转金丹，决定逆天改命。",
                "visual_style": "国风仙侠 电影级写实 (Cinematic Realistic)",
                "target_episode_duration_seconds": 120,
            },
        )
        assert started.status_code == 200, started.text
        run = started.json()["run"]
        job_id = str(run["job_id"])
        # Simulate a Job that died without the run's projection being updated.
        with database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state='FAILED',last_error_code='PIPELINE_LLM_GENERATION_FAILED',last_error_detail_redacted='模型异常' WHERE id=?",
                (job_id,),
            )
            connection.execute("UPDATE pipeline_runs SET state='RUNNING' WHERE id=?", (run["run_id"],))
        reconciled = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert reconciled["state"] == "FAILED", reconciled["state"]
        # The workbench now offers the normal retry entry again.
        retried = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:retry",
            json={"expected_revision": reconciled["revision"]},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["run"]["state"] == "RUNNING"
    assert service is not None


def test_a_partially_applied_run_can_apply_the_continuation_delta(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """PR-05: applying batch 1 must not close the run to batch 2.

    The audit applied the first chapter's draft, continued analysis, and then could
    neither preview nor apply the new episodes: ``apply_state='APPLIED'`` was a
    run-level boolean, so the run answered ``PIPELINE_STATE_INVALID`` on preview and
    ``PIPELINE_ALREADY_APPLIED`` on apply while the formal project still only held
    ``[1]``.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_partial_apply",
        title="分批应用",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    chapter_count = 61
    source_text = "\n\n".join(
        f"# 第{index}章\n\n第{index}章正文，尾部事件-{index}。" for index in range(1, chapter_count + 1)
    )
    with TestClient(create_app(workspace)) as client:
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
        assert LocalMediaWorker(database, workspace).run_once("story-partial-batch-1", ["CPU"]) is not None
        first = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert first["draft"]["source_coverage"]["status"] == "PARTIAL"

        preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={"expected_revision": first["revision"], "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["can_apply"] is True
        applied = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": first["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": preview.json()["impact"]["impact_sha256"],
            },
        )
        assert applied.status_code == 200, applied.text
        first_applied_count = applied.json()["created"]["episodes"]
        assert first_applied_count > 0

        # Mark one episode as already in production: its identity must never change.
        with database.transaction() as connection:
            produced = connection.execute(
                """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY e.number LIMIT 1""",
                (project_id,),
            ).fetchone()
            produced_id = str(produced["id"])
            connection.execute(
                "UPDATE episodes SET title='已确认标题', production_status='IN_PRODUCTION' WHERE id=?",
                (produced_id,),
            )

        applied_run = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert applied_run["apply_state"] == "APPLIED"
        assert applied_run["applied_revision_hash"]
        applied_numbers_before = list(applied_run["applied_episode_numbers"])

        # Continue the remaining windows.
        cursor = applied_run["analysis_cursor"]
        assert cursor["has_more_windows"] is True
        queued = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": applied_run["revision"],
                "expected_source_sha256": applied_run["draft"]["source_coverage"]["source_sha256"],
                "expected_next_window_index": cursor["next_window_index"],
            },
        )
        assert queued.status_code == 200, queued.text
        assert LocalMediaWorker(database, workspace).run_once("story-partial-batch-2", ["CPU"]) is not None
        continued = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]
        assert continued["draft"]["source_coverage"]["status"] == "FULL"
        assert continued["apply_state"] == "APPLIED"

        # The NEW revision can be previewed and applied: this is exactly what the
        # audit could not do.
        delta_preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={"expected_revision": continued["revision"], "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"]},
        )
        assert delta_preview.status_code == 200, delta_preview.text
        watermark = delta_preview.json()["apply_watermark"]
        assert watermark["already_applied"] is False
        assert watermark["applied_revision_hash"] == applied_run["applied_revision_hash"]
        assert watermark["draft_revision_hash"] != watermark["applied_revision_hash"]
        assert delta_preview.json()["can_apply"] is True
        # The already-applied episodes are not re-added.
        assert {item["number"] for item in delta_preview.json()["impact"]["episodes"]["add"]} == {
            number for number in range(1, chapter_count + 1)
        } - set(applied_numbers_before) - {
            int(item["number"]) for item in delta_preview.json()["impact"]["episodes"]["update"]
        } - {
            int(item["number"]) for item in delta_preview.json()["impact"]["episodes"]["preserve"]
        }

        delta = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": continued["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": delta_preview.json()["impact"]["impact_sha256"],
            },
        )
        assert delta.status_code == 200, delta.text
        assert delta.json()["created"]["episodes"] > 0
        final = delta.json()["run"]
        assert final["applied_revision_hash"] == watermark["draft_revision_hash"]
        assert set(final["applied_episode_numbers"]) >= set(applied_numbers_before)

        # Re-applying the SAME revision is a no-op, never a second write.
        same = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": final["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
                "expected_impact_sha256": delta_preview.json()["impact"]["impact_sha256"],
            },
        )
        assert same.status_code == 422
        assert same.json()["error"]["code"] == "PIPELINE_ALREADY_APPLIED"

    with database.connect() as connection:
        preserved = connection.execute(
            "SELECT title, production_status FROM episodes WHERE id=?", (produced_id,)
        ).fetchone()
        total = connection.execute(
            """SELECT COUNT(*) AS n FROM episodes e JOIN seasons s ON s.id=e.season_id
            WHERE s.project_id=?""",
            (project_id,),
        ).fetchone()
    # The produced episode keeps its confirmed title and production status.
    assert str(preserved["title"]) == "已确认标题"
    assert str(preserved["production_status"]) == "IN_PRODUCTION"
    # Every planned unit now exists in the formal project.
    assert int(total["n"]) >= chapter_count


def test_a_run_applied_before_the_watermark_is_backfilled_and_not_re_applied(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """PR-05 migration: an old APPLIED run keeps its "already applied" meaning.

    The watermark migration cannot compute a content hash in SQL, so a run applied by
    an earlier build has ``apply_state='APPLIED'`` and no hash.  Treating that as
    "nothing applied" would let a re-apply rewrite episodes the user already confirmed,
    so the current draft is adopted as the applied revision and the watermark is
    persisted on first read.
    """

    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_watermark_backfill",
        title="水位回填",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    with TestClient(create_app(workspace)) as client:
        started = client.post(
            f"/api/v1/projects/{project_id}/pipeline:start",
            json={
                "raw_text": "# 第一章 归来\n\n林渊握紧照骨古剑，凝视远处的九转金丹，决定逆天改命。",
                "visual_style": "国风仙侠 电影级写实 (Cinematic Realistic)",
                "target_episode_duration_seconds": 120,
            },
        )
        assert started.status_code == 200, started.text
        run = started.json()["run"]
        assert LocalMediaWorker(database, workspace).run_once("story-watermark", ["CPU"]) is not None
        current = client.get(f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}").json()["run"]

        # Simulate a run applied by the pre-watermark build: APPLIED with no hash.
        with database.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_runs SET apply_state='APPLIED',applied_revision_hash=NULL,
                applied_episode_numbers_json='[]' WHERE id=?""",
                (run["run_id"],),
            )

        preview = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply-preview",
            json={"expected_revision": current["revision"], "sections": ["STORY_PLAN"]},
        )
        assert preview.status_code == 200, preview.text
        watermark = preview.json()["apply_watermark"]
        # The adopted watermark makes this revision count as already applied.
        assert watermark["already_applied"] is True
        assert preview.json()["can_apply"] is False

    with database.connect() as connection:
        row = connection.execute(
            "SELECT applied_revision_hash,applied_episode_numbers_json FROM pipeline_runs WHERE id=?",
            (run["run_id"],),
        ).fetchone()
    assert row["applied_revision_hash"], "the watermark must be persisted on first read"
    assert json.loads(str(row["applied_episode_numbers_json"])) != []


def test_continue_analysis_finishes_the_remaining_windows_of_a_long_novel(
    workspace, database, mock_story_pipeline_ai,
) -> None:
    """A 61-chapter manuscript must be completable through continue-analysis.

    Before the fix the run stopped after ``PIPELINE_EPISODE_BATCH_LIMIT`` units and
    reported PARTIAL with no executable way to continue: ``:resume`` answered 422
    PIPELINE_RESUME_UNSUPPORTED and ``:retry`` answered 409 PIPELINE_STATE_INVALID.
    """
    del mock_story_pipeline_ai
    project = ProjectService(database, workspace.projects_root).create_project(
        code="pipe_continue_61",
        title="61章续接",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    chapter_count = 61
    source_text = "\n\n".join(
        f"# 第{index}章\n\n第{index}章正文，尾部事件-{index}。" for index in range(1, chapter_count + 1)
    )
    with TestClient(create_app(workspace)) as client:
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
        assert client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:resume"
        ).status_code == 422

        assert LocalMediaWorker(database, workspace).run_once("story-pipeline-batch-1", ["CPU"]) is not None
        first = client.get(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}"
        ).json()["run"]
        assert first["state"] == "SUCCEEDED"
        coverage = first["draft"]["source_coverage"]
        assert coverage["status"] == "PARTIAL"
        assert coverage["coverage_complete"] is False
        cursor = first["analysis_cursor"]
        assert cursor["has_more_windows"] is True
        assert cursor["next_window_index"] == cursor["completed_window_count"]
        first_planned = first["episodes_count"]
        first_episode_titles = [item["title"] for item in first["episodes"]]
        # The first batch stops at the documented episode limit, so the last chapter
        # of the manuscript is unreachable until the cursor is continued.
        assert first_planned == 60
        assert first_episode_titles[-1] == f"第{chapter_count - 1}章"
        assert f"第{chapter_count}章" not in first_episode_titles

        completed = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": first["revision"],
                "expected_source_sha256": coverage["source_sha256"],
                "expected_next_window_index": cursor["next_window_index"] - 1,
            },
        )
        assert completed.status_code == 409, completed.text
        assert completed.json()["error"]["code"] == "PIPELINE_CURSOR_STALE"
        assert completed.json()["error"]["details"]["server_next_window_index"] == cursor["next_window_index"]

        queued = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": first["revision"],
                "expected_source_sha256": coverage["source_sha256"],
                "expected_next_window_index": cursor["next_window_index"],
            },
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["run"]["state"] == "RUNNING"

        # A second command while the batch is queued must not double-run it.
        again = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": queued.json()["run"]["revision"],
                "expected_source_sha256": coverage["source_sha256"],
            },
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "PIPELINE_ALREADY_RUNNING"

        assert LocalMediaWorker(database, workspace).run_once("story-pipeline-batch-2", ["CPU"]) is not None
        final = client.get(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}"
        ).json()["run"]
        assert final["state"] == "SUCCEEDED"
        final_coverage = final["draft"]["source_coverage"]
        assert final_coverage["status"] == "FULL"
        assert final_coverage["coverage_complete"] is True
        assert final_coverage["coverage_gaps"] == []
        assert final_coverage["unprocessed_ranges"] == []
        assert final_coverage["covered_paragraph_count"] == final_coverage["authorized_paragraph_count"]
        assert final_coverage["completed_paragraph_intervals"] == [
            {"start_paragraph": 1, "end_paragraph": chapter_count * 2}
        ]
        # The units planned by the first batch are retained verbatim; the second
        # batch may only append the units it was asked to plan.
        assert final["episodes_count"] >= first_planned
        assert [item["title"] for item in final["episodes"]][:len(first_episode_titles)] == first_episode_titles
        assert final["analysis_cursor"]["has_more_windows"] is False

        exhausted = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:continue-analysis",
            json={
                "expected_revision": final["revision"],
                "expected_source_sha256": final_coverage["source_sha256"],
            },
        )
        assert exhausted.status_code == 409
        assert exhausted.json()["error"]["code"] == "PIPELINE_COVERAGE_COMPLETE"

