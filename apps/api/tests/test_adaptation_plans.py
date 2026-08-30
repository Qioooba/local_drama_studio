from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.generation_model_catalog import action_for_capability
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="adaptation_plan",
        title="Adaptation planning",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )


def _long_source(workspace):
    source = workspace.work_root / "long-novel.txt"
    chapter_body = "林默穿过雨夜的长街，手中照骨灯映出旧城暗影。"
    source.write_text(
        "\n\n".join(
            f"第{index}章 雨夜\n\n{chapter_body * 35}" for index in range(1, 43)
        ),
        encoding="utf-8",
    )
    return source


def _published_analysis_profile(database, *, remote: bool = False) -> str:
    profile_id = "adaptation-analysis-profile"
    version_id = "adaptation-analysis-profile-v1"
    base_url = "https://llm.example.test/v1" if remote else "http://127.0.0.1:11434"
    provider = "OPENAI_COMPAT" if remote else "OLLAMA_LOOPBACK"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO execution_profiles
               (id,code,title,created_at,updated_at,created_by,revision,schema_version)
               VALUES (?, 'adaptation-analysis', 'Adaptation analysis', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test', 1, 'v2')""",
            (profile_id,),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
               (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
                parameter_schema_json,status,manifest_sha256,capability_json,worker_policy,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES (?,?,1,'LLM_STORY_PARSE',?,'{}','{}','PUBLISHED',NULL,?,'ONE_LOCAL_LLM_TASK',
                       CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')""",
            (
                version_id,
                profile_id,
                '{"model":"analysis-test","provider":"' + provider + '"}',
                '{"model":"analysis-test","provider":"' + provider + '","base_url":"' + base_url + '"}',
            ),
        )
    return version_id


def test_long_source_preflight_creates_idempotent_recoverable_plan(workspace, database) -> None:
    project = _project(workspace, database)
    profile_version_id = _published_analysis_profile(database, remote=True)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), _long_source(workspace))

    with TestClient(create_app(workspace)) as client:
        sources = client.get(f"/api/v2/projects/{project['id']}/source-versions")
        assert sources.status_code == 200, sources.text
        assert sources.json()["items"][0]["source_document_version_id"] == imported["source_document_version_id"]

        preflight = client.post(
            f"/api/v2/projects/{project['id']}/adaptation-plans:preflight",
            json={
                "source_document_version_id": imported["source_document_version_id"],
                "target_duration_ms": 120_000,
            },
        )
        assert preflight.status_code == 200, preflight.text
        diagnosis = preflight.json()["diagnosis"]
        assert diagnosis["type"] == "NOVEL_LONG_FORM"
        assert diagnosis["recommended_mode"] == "COMPLETE_WORK"
        assert 90 <= diagnosis["estimated_episode_range"]["minimum"] <= 110
        assert diagnosis["estimated_episode_range"]["minimum"] <= diagnosis["estimated_episode_range"]["maximum"] <= 125
        assert preflight.json()["execution"]["requires_episode"] is False
        assert preflight.json()["execution"]["creates_media"] is False

        payload = {
            "source_document_version_id": imported["source_document_version_id"],
            "mode": "COMPLETE_WORK",
            "target_duration_ms": 120_000,
            "episode_strategy": "AI_ESTIMATE",
            "season_strategy": "AI_SUGGESTED",
        }
        created = client.post(
            f"/api/v2/projects/{project['id']}/adaptation-plans",
            json=payload,
            headers={"Idempotency-Key": "adaptation-plan-001"},
        )
        assert created.status_code == 201, created.text
        result = created.json()
        assert result["artifact_status"] == "DRAFT"
        assert result["run_status"] == "PREPARED"
        assert result["idempotent"] is False

        repeated = client.post(
            f"/api/v2/projects/{project['id']}/adaptation-plans",
            json=payload,
            headers={"Idempotency-Key": "adaptation-plan-001"},
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["idempotent"] is True
        assert repeated.json()["plan_id"] == result["plan_id"]

        plans = client.get(f"/api/v2/projects/{project['id']}/adaptation-plans")
        assert plans.status_code == 200, plans.text
        assert plans.json()["items"][0]["id"] == result["plan_id"]
        assert plans.json()["items"][0]["episode_count"] == 0

        workspace_response = client.get(f"/api/v2/adaptation-plans/{result['plan_id']}/workspace")
        assert workspace_response.status_code == 200, workspace_response.text
        workspace_payload = workspace_response.json()
        assert workspace_payload["plan"]["mode"] == "COMPLETE_WORK"
        assert workspace_payload["run"]["status"] == "PREPARED"
        assert workspace_payload["episodes"] == []
        assert workspace_payload["next_action"] == "PREPARE_ANALYSIS"

        manifested = client.post(f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-manifest")
        assert manifested.status_code == 200, manifested.text
        assert manifested.json()["run_status"] == "PREPARED"
        assert manifested.json()["total_nodes"] > 4
        assert manifested.json()["idempotent"] is False

        repeated_manifest = client.post(f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-manifest")
        assert repeated_manifest.status_code == 200, repeated_manifest.text
        assert repeated_manifest.json()["idempotent"] is True
        assert repeated_manifest.json()["total_nodes"] == manifested.json()["total_nodes"]

        manifested_workspace = client.get(f"/api/v2/adaptation-plans/{result['plan_id']}/workspace")
        assert manifested_workspace.status_code == 200, manifested_workspace.text
        assert manifested_workspace.json()["run"]["total_nodes"] == manifested.json()["total_nodes"]
        assert manifested_workspace.json()["next_action"] == "CONFIGURE_ANALYSIS_PROFILE"
        assert {node["stage"] for node in manifested_workspace.json()["analysis_nodes"]} == {
            "CHUNK_MAP", "ARC_REDUCE", "SEASON_PLAN", "EPISODE_BOUNDARY", "VALIDATE"
        }
        assert all(node["state"] == "PLANNED" for node in manifested_workspace.json()["analysis_nodes"])
        assert manifested_workspace.json()["episodes"] == []

        readiness = client.get(
            f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-readiness",
            params={"profile_version_id": profile_version_id},
        )
        assert readiness.status_code == 200, readiness.text
        readiness_payload = readiness.json()
        assert readiness_payload["execution"]["state"] == "READY"
        assert readiness_payload["execution"]["provider_remote"] is True
        assert readiness_payload["execution"]["requires_remote_outbound_confirmation"] is True
        assert readiness_payload["execution"]["planned_node_count"] == manifested.json()["total_nodes"]
        assert readiness_payload["execution"]["estimated_input_tokens"] > 0
        assert readiness_payload["read_only"] is True

        blocked_submit = client.post(
            f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-runs",
            json={"profile_version_id": profile_version_id, "allow_remote_outbound": False},
            headers={"Idempotency-Key": "adaptation-analysis-001"},
        )
        assert blocked_submit.status_code == 422, blocked_submit.text
        assert blocked_submit.json()["error"]["code"] == "OUTBOUND_CONFIRMATION_REQUIRED"

        submitted = client.post(
            f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-runs",
            json={"profile_version_id": profile_version_id, "allow_remote_outbound": True},
            headers={"Idempotency-Key": "adaptation-analysis-001"},
        )
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["run_status"] == "QUEUED"
        assert submitted.json()["provider_remote"] is True
        assert submitted.json()["job_count"] == manifested.json()["total_nodes"]
        assert len(submitted.json()["created_job_ids"]) == manifested.json()["total_nodes"]

        repeated_submit = client.post(
            f"/api/v2/adaptation-plans/{result['plan_id']}/analysis-runs",
            json={"profile_version_id": profile_version_id, "allow_remote_outbound": True},
            headers={"Idempotency-Key": "adaptation-analysis-001"},
        )
        assert repeated_submit.status_code == 202, repeated_submit.text
        assert repeated_submit.json()["idempotent"] is True
        assert repeated_submit.json()["created_job_ids"] == []

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM adaptation_plans").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_plan_revisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_plan_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_plan_run_nodes").fetchone()[0] > 4
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > 4
        assert connection.execute("SELECT COUNT(*) FROM job_dependencies").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE type='ADAPTATION_ANALYSIS_LOCAL_LLM'").fetchone()[0] > 4
        assert connection.execute("SELECT COUNT(*) FROM source_document_units").fetchone()[0] == 84
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADAPTATION_PLAN_CREATED'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADAPTATION_ANALYSIS_MANIFESTED'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADAPTATION_ANALYSIS_QUEUED'").fetchone()[0] == 1


def test_single_episode_mode_requires_explicit_target_only_in_legacy_breakdown_not_plan_preflight(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "short-script.txt"
    source.write_text("雨夜。\n\n林默推门进入。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)

    with TestClient(create_app(workspace)) as client:
        preflight = client.post(
            f"/api/v2/projects/{project['id']}/adaptation-plans:preflight",
            json={
                "source_document_version_id": imported["source_document_version_id"],
                "target_duration_ms": 120_000,
            },
        )
    assert preflight.status_code == 200
    assert preflight.json()["diagnosis"]["recommended_mode"] == "SINGLE_EPISODE"
    assert preflight.json()["execution"]["requires_episode"] is False


def test_episode_plan_capability_is_a_text_planning_route() -> None:
    assert action_for_capability("LLM_EPISODE_PLAN") == "TEXT_PLANNING"
    assert action_for_capability("LLM_STORYBOARD") == "TEXT_PLANNING"


def test_analysis_dag_rechecks_inputs_records_invocations_and_writes_review_draft(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database)
    profile_version_id = _published_analysis_profile(database)
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), _long_source(workspace))
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            f"/api/v2/projects/{project['id']}/adaptation-plans",
            json={
                "source_document_version_id": imported["source_document_version_id"],
                "mode": "COMPLETE_WORK",
                "target_duration_ms": 120_000,
                "episode_strategy": "AI_ESTIMATE",
                "season_strategy": "AI_SUGGESTED",
            },
            headers={"Idempotency-Key": "adaptation-map-worker-plan"},
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["plan_id"]
        assert client.post(f"/api/v2/adaptation-plans/{plan_id}/analysis-manifest").status_code == 200
        submitted = client.post(
            f"/api/v2/adaptation-plans/{plan_id}/analysis-runs",
            json={"profile_version_id": profile_version_id, "allow_remote_outbound": False},
            headers={"Idempotency-Key": "adaptation-map-worker-run"},
        )
        assert submitted.status_code == 202, submitted.text

    def fake_analysis_response(self, system_prompt, user_prompt, **kwargs):
        if "events（数组）" in system_prompt:
            return {
                "summary": "雨夜中的人物行动。",
                "events": [{"summary": "主角穿过长街"}],
                "characters": ["林默"],
                "open_threads": ["照骨灯的来历"],
                "confidence": 0.8,
            }
        if "arcs（数组）" in system_prompt:
            return {"arcs": [{"title": "雨夜谜局", "summary": "林默追查照骨灯", "dramatic_promise": "揭开旧城谜团", "open_threads": ["照骨灯"]}]}
        if "seasons（数组）" in system_prompt:
            return {"seasons": [{"title": "第一季：雨夜", "release_intent": "完成谜局第一阶段", "arc_titles": ["雨夜谜局"]}]}
        if "episodes（数组）" in system_prompt:
            upstream = json.loads(user_prompt)["upstream"]
            map_keys = [item["node_key"] for item in upstream if item["stage"] == "CHUNK_MAP"]
            return {
                "episodes": [{
                    "title": "照骨灯初现", "logline": "林默在雨夜发现照骨灯", "opening_carry": "雨夜旧城",
                    "core_conflict": "林默需要追查灯的来源", "payoff": "发现关键线索", "ending_hook": "暗影逼近",
                    "source_node_keys": map_keys[:1], "arc_ordinal": 1, "season_ordinal": 1, "estimated_duration_ms": 120_000,
                }]
            }
        return {"status": "PASS", "issues": [], "summary": "待审核规划完整"}

    monkeypatch.setattr(LocalLLMClient, "chat_json", fake_analysis_response)
    worker = LocalMediaWorker(database, workspace)
    outcome = worker.run_once("adaptation-map-worker", ["CPU"])
    assert outcome is not None
    assert outcome["job"]["type"] == "ADAPTATION_ANALYSIS_LOCAL_LLM"
    assert outcome["result"]["job_state"] == "SUCCEEDED"
    remaining = worker.run_until_idle("adaptation-map-worker", max_jobs=60)
    assert remaining
    assert all(item.get("result", {}).get("job_state") == "SUCCEEDED" for item in remaining)
    with TestClient(create_app(workspace)) as client:
        review_workspace = client.get(f"/api/v2/adaptation-plans/{plan_id}/workspace")
        assert review_workspace.status_code == 200, review_workspace.text
        assert review_workspace.json()["plan"]["artifact_status"] == "IN_REVIEW"
        assert review_workspace.json()["next_action"] == "REVIEW_PLAN"
        assert review_workspace.json()["episodes"][0]["evidence_count"] == 1
        approved = client.post(
            f"/api/v2/adaptation-plans/{plan_id}:approve",
            json={"expected_content_sha256": review_workspace.json()["revision"]["content_sha256"]},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["artifact_status"] == "APPROVED"
        materialization_preflight = client.get(f"/api/v2/adaptation-plans/{plan_id}/materialization-preflight")
        assert materialization_preflight.status_code == 200, materialization_preflight.text
        assert materialization_preflight.json()["ready"] is True
        assert materialization_preflight.json()["strategy"] == "APPEND_NEW"
        assert materialization_preflight.json()["existing_structure"]["season_count"] == 1
        assert materialization_preflight.json()["would_create"]["episode_count"] == 1
        materialized = client.post(
            f"/api/v2/adaptation-plans/{plan_id}/materializations",
            json={"expected_content_sha256": review_workspace.json()["revision"]["content_sha256"], "confirm_append": True},
            headers={"Idempotency-Key": "adaptation-materialize-001"},
        )
        assert materialized.status_code == 201, materialized.text
        assert materialized.json()["strategy"] == "APPEND_NEW"
        assert len(materialized.json()["items"]) == 1
        repeated_materialized = client.post(
            f"/api/v2/adaptation-plans/{plan_id}/materializations",
            json={"expected_content_sha256": review_workspace.json()["revision"]["content_sha256"], "confirm_append": True},
            headers={"Idempotency-Key": "adaptation-materialize-001"},
        )
        assert repeated_materialized.status_code == 201, repeated_materialized.text
        assert repeated_materialized.json()["idempotent"] is True
    with database.connect() as connection:
        node = connection.execute(
            "SELECT output_json FROM adaptation_plan_run_nodes WHERE job_id=?",
            (outcome["job"]["id"],),
        ).fetchone()
        assert node is not None
        assert '"planning_state":"SUCCEEDED"' in str(node["output_json"])
        invocation = connection.execute(
            "SELECT status,run_node_id FROM llm_invocations WHERE run_node_id IS NOT NULL ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert invocation is not None
        assert invocation["status"] == "SUCCEEDED"
        assert connection.execute("SELECT COUNT(*) FROM adaptation_story_arcs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_season_groups").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_episode_items").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM adaptation_episode_source_spans").fetchone()[0] == 1
        assert connection.execute("SELECT artifact_status FROM adaptation_plans WHERE id=?", (plan_id,)).fetchone()[0] == "MATERIALIZED"
        assert connection.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADAPTATION_PLAN_APPROVED'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='ADAPTATION_PLAN_MATERIALIZED'").fetchone()[0] == 1


def test_analysis_knowledge_block_filters_and_fails_open(database, workspace, monkeypatch) -> None:
    import hashlib

    from local_drama.application.adaptation_analysis_execution import AdaptationAnalysisExecutionService

    class _Hit:
        def __init__(self, source_id: str, start: int, end: int) -> None:
            self.source_document_version_id = source_id
            self.source_start = start
            self.source_end = end
            self.ordinal = 0
            self.index_run_id = "run-1"
            self.score = 0.9

    service = AdaptationAnalysisExecutionService(
        repository=None, settings=workspace, llm=None, database=database,
    )
    source_id = "src-1"
    snapshot = {"source_document_version_id": source_id, "stage": "ARC_REDUCE"}
    context = {"source_text": "前文埋下的伏笔：血月每隔十年出现一次。与知识无关的正文段落。"}
    user_input = "归纳故事弧"

    class _FakeRetrieval:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def search(self, *, project_id: str, query: str, limit: int = 8):
            return "profile-1", (
                _Hit(source_id, 0, 15),   # same source → injected
                _Hit("src-other", 0, 30),  # cross-source → dropped
            )

    monkeypatch.setattr(
        "local_drama.model_platform.application.project_knowledge_retrieval.ProjectKnowledgeRetrievalService",
        _FakeRetrieval,
    )
    monkeypatch.setattr(service, "_project_id_for_source", lambda _source_id: "proj-1")
    block = service._knowledge_block(snapshot, context, user_input)
    assert block is not None
    assert "血月" in block
    assert "与知识无关" not in block
    assert "以上方正文为准" in block

    # Fail-open: retrieval service raising (index not ready / GPU busy) yields None.
    def _boom(*args, **kwargs):
        raise RuntimeError("index not ready")

    monkeypatch.setattr(
        "local_drama.model_platform.application.project_knowledge_retrieval.ProjectKnowledgeRetrievalService",
        _boom,
    )
    assert service._knowledge_block(snapshot, context, user_input) is None

    # No database wired → disabled entirely.
    no_source = AdaptationAnalysisExecutionService(repository=None, settings=workspace, llm=None, database=database)
    monkeypatch.setattr(no_source, "_project_id_for_source", lambda _source_id: None)
    assert no_source._knowledge_block(snapshot, context, user_input) is None

    bare = AdaptationAnalysisExecutionService(repository=None, settings=workspace, llm=None, database=None)
    assert bare._knowledge_block(snapshot, context, user_input) is None
    assert hashlib.sha256(b"").hexdigest()  # hashlib still imported in module scope
