"""Unit tests for ExplainerStoryBreakdownService."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from local_drama.api.schemas.explainers import ExplainerBreakdownStoryRequest
from local_drama.application.explainers.story_breakdown import ExplainerStoryBreakdownService
from local_drama.config import Settings
from local_drama.domain.explainers.contracts import ProductKind
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-story-breakdown-test"


def _seed_project(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'EXP-001', '恶魔岛越狱案', 'DRAFT', 'v2', ?, 180000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, status, target_seconds,
            duration_mode, tolerance_percent, automation_mode, inference_mode, research_mode,
            input_payload_json, revision, created_at, updated_at)
            VALUES ('video-test-1', ?, '恶魔岛越狱案', '历史大案', 'FACTUAL_EXPLAINER', 'DRAFT', 180,
            'TARGET', 10, 'REVIEW_BEFORE_RENDER', 'LOCAL_ONLY', 'OFFLINE_IMPORT',
            '{}', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
            (PROJECT_ID,),
        )


def test_explainer_story_breakdown_service_success(database: Database, workspace: Settings):
    _seed_project(database)

    service = ExplainerStoryBreakdownService(database, workspace)

    # Mock client.chat_json
    mock_client = MagicMock()
    mock_client.model = "Qwen3.8-27B-UD-Q4_K_M"
    mock_client.provider = "LLAMA_CPP_MANAGED"
    mock_client.chat_json.return_value = {
        "outline": ["第一幕：密室空床", "第二幕：冰冷湾流"],
        "segments": [
            {
                "canonical_segment_id": "seg_001",
                "display_text": "1962年6月11日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生了一起震惊全美的越狱案。",
                "spoken_text": "一九六二年六月十一日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生了一起震惊全美的越狱案。",
                "statement_type": "FACT",
                "pronunciation_map": [
                    {"display": "1962", "spoken": "一九六二"},
                    {"display": "6", "spoken": "六"},
                    {"display": "11", "spoken": "十一"},
                ],
                "pause_after_ms": 300,
                "chapter_code": "ch_01",
            },
            {
                "canonical_segment_id": "seg_002",
                "display_text": "3名重犯利用偷藏的铁勺耗费数月挖穿通风管道，从此消失在迷雾之中。",
                "spoken_text": "三名重犯利用偷藏的铁勺耗费数月挖穿通风管道，从此消失在迷雾之中。",
                "statement_type": "FACT",
                "pronunciation_map": [
                    {"display": "3", "spoken": "三"},
                ],
                "pause_after_ms": 250,
                "chapter_code": "ch_01",
            },
        ],
    }

    service._resolve_client = MagicMock(return_value=(mock_client, "fake-profile-version-id"))

    request_payload = ExplainerBreakdownStoryRequest(
        story_text="1962年6月11日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生了一起震惊全美的越狱案。3名重犯利用偷藏的铁勺耗费数月挖穿通风管道，从此消失在迷雾之中。",
        target_seconds=180,
        style="深度影视解说与真实故事还原",
        title="恶魔岛越狱案",
    )

    result = service.breakdown_story(PROJECT_ID, request_payload)

    assert result["status"] == "PASS"
    assert result["model_used"] == "Qwen3.8-27B-UD-Q4_K_M"
    assert result["segment_count"] == 2
    assert len(result["segments"]) == 2
    assert result["segments"][0]["display_text"].startswith("1962年")
    assert result["segments"][0]["spoken_text"].startswith("一九六二年")
    assert len(result["segments"][0]["pronunciation_map_json"]) > 0

    # Verify database persistence
    with database.connect() as conn:
        video = conn.execute("SELECT * FROM explainer_videos WHERE project_id = ?", (PROJECT_ID,)).fetchone()
        assert video["current_script_revision_id"] is not None
        assert video["current_script_revision_id"] == str(result["script_revision"]["id"])
        input_data = json.loads(video["input_payload_json"])
        assert "1962年" in input_data["pasted_text"]


def test_explainer_breakdown_http_endpoint(database: Database, workspace: Settings, monkeypatch):
    from fastapi.testclient import TestClient
    from local_drama.main import create_app

    _seed_project(database)

    mock_client = MagicMock()
    mock_client.model = "Qwen3.8-27B-UD-Q4_K_M"
    mock_client.provider = "LLAMA_CPP_MANAGED"
    mock_client.chat_json.return_value = {
        "outline": ["第一幕：密室空床"],
        "segments": [
            {
                "canonical_segment_id": "seg_001",
                "display_text": "1962年6月11日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生越狱。",
                "spoken_text": "一九六二年六月十一日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生越狱。",
                "statement_type": "FACT",
                "pronunciation_map": [
                    {"display": "1962", "spoken": "一九六二"},
                    {"display": "6", "spoken": "六"},
                    {"display": "11", "spoken": "十一"},
                ],
                "pause_after_ms": 300,
            }
        ],
    }

    monkeypatch.setattr(
        ExplainerStoryBreakdownService,
        "_resolve_client",
        lambda self, profile_id=None: (mock_client, "fake-profile-version-id"),
    )

    app = create_app(workspace)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/explainers/{PROJECT_ID}/breakdown-story",
            json={
                "story_text": "1962年6月11日深夜，位于旧金山湾中央的恶魔岛联邦监狱发生越狱。",
                "target_seconds": 180,
                "style": "深度影视解说",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "PASS"
        assert data["model_used"] == "Qwen3.8-27B-UD-Q4_K_M"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["display_text"].startswith("1962年")
        assert data["segments"][0]["spoken_text"].startswith("一九六二年")

