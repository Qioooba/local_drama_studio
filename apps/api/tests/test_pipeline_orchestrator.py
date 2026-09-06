from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.application.worker import LocalMediaWorker
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
                """SELECT i.status FROM import_sessions i
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
        assert draft_run["quality_report"]["status"] in {"READY", "REVIEW_REQUIRED"}
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

        applied = client.post(
            f"/api/v1/projects/{project_id}/pipeline/{run['run_id']}:apply",
            json={
                "expected_revision": draft_run["revision"],
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
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
