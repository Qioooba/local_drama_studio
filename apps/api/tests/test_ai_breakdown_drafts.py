from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _persisted_draft(workspace, database):
    project = ProjectService(database, workspace.projects_root).create_project(code="ai_draft", title="AI draft", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    source = workspace.work_root / "ai-draft.md"
    source.write_text("# 第一场\n\n母亲打开信件。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    draft_id, now = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    draft = {"scenes": [{"scene_no": 1, "title": "开场", "summary": "母亲读信", "characters": ["母亲"], "shots": [{"shot_no": 1, "visual": "近景", "action": "打开信件", "dialogue": "", "duration_seconds": 4}]}]}
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?, 'local-llm',1,'v2')""",
            (draft_id, project["id"], imported["source_document_version_id"], imported["import_session_id"], json.dumps(draft), json.dumps({"source": "model_output"}), now, now),
        )
    return project, draft_id


def test_breakdown_draft_projection_never_applies_or_overwrites_authority(workspace, database) -> None:
    project, draft_id = _persisted_draft(workspace, database)
    with database.connect() as connection:
        before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("scenes", "shots", "creative_entries")}
    items = LocalLLMService(database, workspace).list_breakdown_drafts(str(project["id"]))
    with database.connect() as connection:
        after = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("scenes", "shots", "creative_entries")}
    assert before == after
    assert items[0]["id"] == draft_id
    assert items[0]["status"] == "DRAFT_READY"
    assert items[0]["application_status"] == "NOT_APPLIED"
    assert items[0]["automatic_apply"] is False
    assert items[0]["requires_human_action"] is True


def test_breakdown_draft_api_is_read_only_and_explicit(workspace, database) -> None:
    project, _ = _persisted_draft(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/script-breakdown-drafts")
    assert response.status_code == 200
    assert response.json()["automatic_apply"] is False
    assert response.json()["requires_human_action"] is True
    assert len(response.json()["items"]) == 1
