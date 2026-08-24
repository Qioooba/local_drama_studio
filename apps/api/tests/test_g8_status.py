from __future__ import annotations

import json
import subprocess

from fastapi.testclient import TestClient

from local_drama.application.canvas import ProductionCanvasService
from local_drama.application.g8_readiness import G8ReadinessService
from local_drama.application.g9_readiness import G9ReadinessService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
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


def test_g8_counts_timeline_shots_for_generation_variant_owned_videos(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="g8_variant_video", title="G8 variant video", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=3000, allow_unconfigured_capabilities=True)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    shots = [service.create_shot(str(episode["id"]), f"SHOT_{index:03d}", 1000, "MEDIUM") for index in range(1, 4)]
    source = workspace.work_root / "g8-variant-video.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True, capture_output=True,
    )
    media_ids: list[str] = []
    for shot in shots:
        imported = MediaService(database, workspace).import_file(
            str(project["id"]), source, purpose="CANDIDATE", owner_type="SHOT", owner_id=str(shot["id"]), media_kind="VIDEO", stage="PROXY",
        )
        media_ids.append(str(imported["media_version_id"]))
        with database.transaction() as connection:
            connection.execute(
                "UPDATE media_assets SET owner_type='GENERATION_VARIANT',owner_id=? WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)",
                (f"variant-{shot['id']}", imported["media_version_id"]),
            )
    timeline_service = TimelineService(database, workspace)
    video_items = [
        {"track_type": "VIDEO", "media_version_id": media_id, "start_us": index * 1_000_000, "end_us": (index + 1) * 1_000_000, "parameters": {"shot_id": str(shots[index]["id"]), "shot_code": str(shots[index]["code"])}}
        for index, media_id in enumerate(media_ids)
    ]
    timeline_service.create_timeline_revision(
        str(episode["id"]),
        video_items,
        {"schema_version": "test"}, status="FROZEN",
    )

    result = G8ReadinessService(database).inspect(str(project["id"]), str(episode["id"]))
    assert result["checks"][0]["passed"] is True
    assert result["checks"][0]["count"] == 3
    assert result["next_required_action"] == "DIALOGUE_BGM_SFX"

    audio_source = workspace.work_root / "g8-timeline-audio.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(audio_source)],
        check=True, capture_output=True,
    )
    audio = MediaService(database, workspace).import_file(
        str(project["id"]), audio_source, purpose="G8_AUDIO", owner_type="EPISODE", owner_id=str(episode["id"]), media_kind="AUDIO", stage="FORMAL",
    )
    project_root = workspace.projects_root / str(project["root_rel"])
    license_path = project_root / "00_admin" / "licenses" / "g8-audio.json"
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text('{"owner":"test"}', encoding="utf-8")
    bindings = [
        timeline_service.bind_audio(
            str(episode["id"]), str(audio["media_version_id"]), track_type, 0, 1_000_000,
            license_evidence_path_rel="00_admin/licenses/g8-audio.json",
        )
        for track_type in ("DIALOGUE", "BGM", "SFX")
    ]
    # Merely creating episode bindings must not claim that the already-frozen
    # revision rendered them.
    bindings_only = G8ReadinessService(database).inspect(str(project["id"]), str(episode["id"]))
    assert bindings_only["checks"][1]["passed"] is False

    timeline_service.create_timeline_revision(
        str(episode["id"]),
        [*video_items, *[
            {"track_type": binding["track_type"], "media_version_id": binding["media_version_id"], "start_us": 0, "end_us": 1_000_000, "parameters": {"audio_binding_id": binding["id"]}}
            for binding in bindings
        ]],
        {"schema_version": "test-with-audio"}, status="FROZEN",
    )
    timeline_audio = G8ReadinessService(database).inspect(str(project["id"]), str(episode["id"]))
    assert timeline_audio["checks"][1]["passed"] is True
    assert timeline_audio["checks"][1]["observed_tracks"] == ["BGM", "DIALOGUE", "SFX"]


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
