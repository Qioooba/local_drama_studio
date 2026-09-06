from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.commands.generation_preferences import GenerationPreferenceCommandService
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.main import create_app


def test_zero_shot_episode_breakdown_is_generated_and_applied_automatically(
    workspace, database, monkeypatch
) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="episode_agent_prepare",
        title="Episode Agent prepare",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    source = workspace.work_root / "episode-agent-prepare.md"
    source.write_text("侦探走进书房，拿起桌上的钥匙。", encoding="utf-8")
    documents = DocumentImportService(database, workspace)
    imported = documents.import_document(str(project["id"]), source)
    documents.commit(
        str(imported["import_session_id"]),
        str(imported["preview_hash"]),
        source_paragraph_start=1,
        source_paragraph_end=1,
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET source_range_json=? WHERE id=?",
            (json.dumps({"start_paragraph": 1, "end_paragraph": 1}), episode["id"]),
        )

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {"status": "PASS", "model": "qwen2.5:7b", "runtime": "ollama"},
    )
    llm = LocalLLMService(database, workspace)
    profile = llm.sync_candidate("qwen2.5:7b")
    llm.publish(str(profile["profile_version_id"]))
    # The explicit project preference must win over a more recently published
    # model, and queued execution must retain these settings after an edit.
    newer = llm.sync_candidate("qwen2.5:14b")
    llm.publish(str(newer["profile_version_id"]))
    with database.transaction() as connection:
        GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)).put(
            project_id=str(project["id"]), owner_type="PROJECT", owner_id=str(project["id"]),
            capability="LLM_STORY_PARSE", resolution_mode="EXPLICIT",
            execution_profile_version_id=str(profile["profile_version_id"]),
            settings={"max_tokens": 8192, "temperature": 0.15}, reason="test explicit story settings",
        )
    calls = []
    def output(self, prompt, text, **kwargs):
        calls.append({"model": self.model, **kwargs})
        return {
            "scenes": [{"scene_no": 1, "title": "书房调查", "summary": "侦探发现钥匙",
                "characters": ["侦探"], "source_paragraph_nos": [1],
                "shots": [{"shot_no": n, "visual": f"书房镜头 {n}", "action": "侦探检查钥匙",
                           "dialogue": "", "duration_seconds": 15} for n in range(1, 5)]}],
            "confidence": {"overall": 0.95, "notes": []}, "questions": [],
        }
    monkeypatch.setattr("local_drama.infrastructure.local_llm.LocalLLMClient.chat_json", output)

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/episodes/{episode['id']}/production:prepare",
            json={"idempotency_key": "prepare-one-episode"},
        )
    assert response.status_code == 202
    assert response.json()["preparation"]["status"] == "QUEUED"
    with database.transaction() as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?",
            (response.json()["preparation"]["job_id"],)).fetchone()[0])
        assert snapshot["profile_version_id"] == profile["profile_version_id"]
        assert snapshot["inference_options"]["max_tokens"] == 8192
        GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)).put(
            project_id=str(project["id"]), owner_type="PROJECT", owner_id=str(project["id"]),
            capability="LLM_STORY_PARSE", resolution_mode="EXPLICIT",
            execution_profile_version_id=str(newer["profile_version_id"]),
            settings={"max_tokens": 4096}, reason="changed after queueing", expected_revision=1,
        )

    outcome = LocalMediaWorker(database, workspace).run_once("episode-agent-worker", ["CPU"])
    assert outcome is not None
    assert outcome.get("error") is None, outcome.get("error")
    assert outcome["result"]["job_state"] == "SUCCEEDED"
    assert calls[-1]["model"] == "qwen2.5:7b"
    assert calls[-1]["inference_options"]["max_tokens"] == 8192
    assert calls[-1]["inference_options"]["temperature"] == 0.15
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)
        ).fetchone()[0] == 4
        draft = connection.execute(
            "SELECT status FROM script_breakdown_drafts WHERE project_id=?", (project["id"],)
        ).fetchone()
    assert draft is not None and draft["status"] == "APPLIED"
