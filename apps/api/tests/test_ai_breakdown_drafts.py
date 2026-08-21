from __future__ import annotations

import errno
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService, _validate_breakdown_output
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
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
        lambda self, prompt, text: expected_output,
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


def _durable_breakdown_setup(workspace, database, monkeypatch, code: str):
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
    source.write_text("# 第一场\n\n侦探走进书房。拿起桌上的钥匙。", encoding="utf-8")
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


def test_failed_breakdown_job_requires_explicit_retry_and_reuses_same_job(workspace, database, monkeypatch) -> None:
    project, imported, profile_version_id = _durable_breakdown_setup(
        workspace, database, monkeypatch, "durable_retry",
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text: (_ for _ in ()).throw(
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
        lambda self, prompt, text: _valid_breakdown_output(),
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

    def cancel_during_model_call(self, prompt, text):
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
        lambda self, prompt, text: _valid_breakdown_output(),
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
        lambda self, prompt, text: (_ for _ in ()).throw(AssertionError("idempotent replay must not call Ollama twice")),
    )
    recovered = worker.run_once("breakdown-replay-worker", ["CPU"])
    assert recovered is not None
    assert recovered["result"]["job_state"] == "SUCCEEDED"
    assert recovered["job"]["id"] == job_id
    final_drafts = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    assert [str(item["id"]) for item in final_drafts] == [draft_id]
