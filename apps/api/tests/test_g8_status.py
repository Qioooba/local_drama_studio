from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_drama.application.canvas import ProductionCanvasService
from local_drama.application.g8_readiness import G8ReadinessService
from local_drama.application.g9_readiness import G9ReadinessService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline_status import TimelineStatusService
from local_drama.main import create_app


def test_g8_timeline_status_is_read_only_and_truthful_for_empty_episode(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g8_status", title="G8 status", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    with database.connect() as connection:
        before = int(connection.execute("SELECT COUNT(*) FROM timeline_revisions").fetchone()[0])
    status = TimelineStatusService(database).inspect(str(episode["id"]))
    assert status["timeline"]["revision_count"] == 0
    assert status["subtitles"]["revision_count"] == 0
    assert status["audio"]["binding_count"] == 0
    assert status["renders"]["count"] == 0
    assert status["delivery"]["count"] == 0
    assert status["read_only"] is True
    assert status["runtime_contacted"] is False and status["network_contacted"] is False and status["mutated"] is False
    with database.connect() as connection:
        assert int(connection.execute("SELECT COUNT(*) FROM timeline_revisions").fetchone()[0]) == before


def test_g8_timeline_status_route_rejects_unknown_episode(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/episodes/missing/timeline-status")
    assert response.status_code == 404


def test_g8_readiness_is_read_only_and_reports_formal_exit_blockers(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g8_gate", title="G8 gate", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    before = database.path.read_bytes()

    result = G8ReadinessService(database).inspect(str(project["id"]), str(episode["id"]))

    assert result["status"] == "IN_PROGRESS"
    assert result["next_required_action"] == "THREE_REAL_SHOTS"
    assert result["checks"][0]["passed"] is False
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert result["mutated"] is False
    assert database.path.read_bytes() == before
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/gates/g8?episode_id={episode['id']}")
    assert response.status_code == 200
    assert response.json()["readiness"]["next_required_action"] == "THREE_REAL_SHOTS"


def test_g9_readiness_separates_production_facts_from_scale_fixture(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g9_gate", title="G9 gate", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    result = G9ReadinessService(database).inspect(str(project["id"]), str(episode["id"]))

    assert result["status"] == "IN_PROGRESS"
    assert result["next_required_action"] == "PREFLIGHT_PERSISTENCE"
    assert result["evidence"]["production_total_shots"] == 0
    assert "fixture" in result["evidence"]["automated_fixture"]
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert result["mutated"] is False


def test_g9_readiness_requires_matching_three_viewport_browser_evidence(workspace, database, tmp_path) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g9_evidence", title="G9 evidence", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=20_000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    for number in range(1, 21):
        service.create_shot(str(episode["id"]), f"S{number:03d}", 1000, "SCALE_UAT")
    graph = ProductionCanvasService(database).graph("EPISODE", str(episode["id"]), cursor=0, limit=300)
    ProductionCanvasService(database).preflight("EPISODE", str(episode["id"]), "NODE", graph["nodes"][0]["id"], None, 1)
    evidence_root = tmp_path / "g9"
    evidence_root.mkdir()
    base = {
        "status": "PASS",
        "project_id": str(project["id"]),
        "episode_id": str(episode["id"]),
        "production_evidence": True,
        "fixture_mode": False,
        "runtime_contacted": False,
        "network_contacted": False,
    }
    performance_viewports = [
        {"viewport": viewport, "status": "PASS", "node_count": 100, "horizontal_overflow_px": 0, "console_errors": [], "page_errors": [], "failed_responses": []}
        for viewport in ("1440x900", "1280x800", "1024x768")
    ]
    accessibility_viewports = [
        {
            "viewport": viewport,
            "status": "PASS",
            "semantic_canvas": True,
            "keyboard_node_list": True,
            "search_filter": True,
            "route_synchronized_selection": True,
            "selected_state": True,
            "horizontal_overflow_px": 0,
            "console_errors": [],
            "page_errors": [],
            "failed_responses": [],
        }
        for viewport in ("1440x900", "1280x800", "1024x768")
    ]
    (evidence_root / "g9-production-canvas-uat-2026-08-15.json").write_text(
        json.dumps({**base, "viewports": performance_viewports}), encoding="utf-8"
    )
    (evidence_root / "g9-production-accessibility-uat-2026-08-15.json").write_text(
        json.dumps({**base, "viewports": accessibility_viewports}), encoding="utf-8"
    )

    result = G9ReadinessService(database, evidence_root=evidence_root).inspect(str(project["id"]), str(episode["id"]))

    assert result["status"] == "PASS"
    assert result["next_required_action"] is None
    assert result["evidence"]["performance_uat"] is not None
    assert result["evidence"]["accessibility_route_uat"] is not None
