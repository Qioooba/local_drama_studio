from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str = "g4_project") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="G4 review project",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )


def _video(workspace, name: str, color: str = "blue") -> Path:
    output = workspace.work_root / name
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:d=1",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _checks(template: dict[str, object]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]  # type: ignore[index]


def test_review_selection_machine_qc_stale_and_batch_invariants(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot = project_service.create_shot(str(episode["id"]), "S001", 1000)

    media_service = MediaService(database, workspace)
    media = media_service.import_file(
        project_id,
        _video(workspace, "g4-blue.mp4"),
        owner_type="SHOT",
        owner_id=str(shot["id"]),
        stage="PROXY",
    )
    asset_id = str(media["media_asset_id"])
    proxy_version = media_service.derive_version(asset_id, str(media["media_version_id"]), "PROXY")
    formal_version = media_service.derive_version(asset_id, str(proxy_version["id"]), "FORMAL")
    review_service = ReviewService(database, workspace)
    review_service.ensure_templates()
    templates = {str(item["code"]): item for item in review_service.templates()}
    proxy_template = templates["proxy_video"]
    formal_template = templates["formal_video"]

    selection = review_service.select_version(str(proxy_version["id"]), "PROXY_WINNER")
    assert selection["status"] == "SELECTED"
    proxy_qc = review_service.machine_check(str(proxy_version["id"]))
    assert proxy_qc["status"] == "PASS"
    approved_proxy = review_service.submit_review(
        str(proxy_version["id"]),
        str(proxy_template["id"]),
        "APPROVED",
        expected_subject_revision=2,
        checks=_checks(proxy_template),
    )
    assert approved_proxy["decision"] == "APPROVED"
    assert review_service.review_context(str(proxy_version["id"]))["subject_revision"] == 3
    duplicate_approval = review_service.submit_review(
        str(proxy_version["id"]),
        str(proxy_template["id"]),
        "APPROVED",
        expected_subject_revision=3,
        checks=_checks(proxy_template),
    )
    assert duplicate_approval["duplicate"] is True
    assert duplicate_approval["id"] == approved_proxy["id"]
    assert len(review_service.review_context(str(proxy_version["id"]))["reviews"]) == 1

    formal_selection = review_service.select_version(str(formal_version["id"]), "FORMAL_SELECTION")
    assert formal_selection["selection_type"] == "FORMAL_SELECTION"
    formal_qc = review_service.machine_check(str(formal_version["id"]))
    assert formal_qc["status"] == "PASS"
    approved_formal = review_service.submit_review(
        str(formal_version["id"]),
        str(formal_template["id"]),
        "APPROVED",
        expected_subject_revision=4,
        checks=_checks(formal_template),
    )
    assert approved_formal["decision"] == "APPROVED"

    missing_qc_version = media_service.derive_version(asset_id, str(formal_version["id"]), "FORMAL")
    with pytest.raises(DomainRuleError, match="正式媒体必须先通过机器 QC"):
        review_service.submit_review(
            str(missing_qc_version["id"]),
            str(formal_template["id"]),
            "APPROVED",
            expected_subject_revision=5,
            checks=_checks(formal_template),
        )

    stale_plan_version = media_service.derive_version(asset_id, str(proxy_version["id"]), "PROXY")
    plan = review_service.batch_preflight(
        project_id,
        [{"media_version_id": str(stale_plan_version["id"]), "template_version_id": str(proxy_template["id"])}],
    )
    review_service.select_version(str(stale_plan_version["id"]), "PROXY_WINNER")
    with pytest.raises(DomainRuleError, match="批量预检后对象发生变化"):
        review_service.batch_commit(str(plan["plan_token"]), "APPROVED", _checks(proxy_template))

    project_service.create_shot_revision(str(shot["id"]), {"subject_action": "walk"}, freeze=True)
    reviews = review_service.list_reviews("MEDIA_VERSION", str(formal_version["id"]))
    assert reviews[0]["is_stale"] == 1
    assert reviews[0]["stale_reason"] == "shot_revision_changed"


def test_review_api_exposes_real_templates_inbox_context_and_selection(workspace, database) -> None:
    project = _project(workspace, database, "g4_api")
    project_id = str(project["id"])
    media = MediaService(database, workspace).import_file(project_id, _video(workspace, "g4-api.mp4", "green"), stage="PROXY")
    version_id = str(media["media_version_id"])
    with TestClient(create_app(workspace)) as client:
        templates = client.get("/api/v1/review-templates")
        assert templates.status_code == 200
        proxy = next(item for item in templates.json()["items"] if item["code"] == "proxy_video")
        inbox = client.get(f"/api/v1/reviews/inbox?project_id={project_id}")
        assert inbox.status_code == 200
        assert any(item["media_version_id"] == version_id for item in inbox.json()["items"])
        context = client.get(f"/api/v1/subjects/MEDIA_VERSION/{version_id}/review-context")
        assert context.status_code == 200
        assert context.json()["template"]["id"] == proxy["id"]
        selected = client.post(f"/api/v1/media-versions/{version_id}:select", json={"selection_type": "PROXY_WINNER"})
        assert selected.status_code == 200
        assert selected.json()["selection"]["media_version_id"] == version_id


def test_review_inbox_cross_project_filters_age_priority_blocking_episode_and_stable_cursor(workspace, database) -> None:
    """FR-REV-001 read model filters before pagination without mutating review state."""
    first = _project(workspace, database, "g4_inbox_first")
    second = _project(workspace, database, "g4_inbox_second")
    projects = ProjectService(database, workspace.projects_root)
    first_episode = projects.list_episodes(str(projects.list_seasons(str(first["id"]))[0]["id"]))[0]
    second_episode = projects.list_episodes(str(projects.list_seasons(str(second["id"]))[0]["id"]))[0]
    media_service = MediaService(database, workspace)
    old = media_service.import_file(
        str(first["id"]),
        _video(workspace, "g4-inbox-old.mp4", "red"),
        owner_type="EPISODE",
        owner_id=str(first_episode["id"]),
        stage="PROXY",
    )
    fresh = media_service.import_file(
        str(second["id"]),
        _video(workspace, "g4-inbox-fresh.mp4", "yellow"),
        owner_type="EPISODE",
        owner_id=str(second_episode["id"]),
        stage="PROXY",
    )
    formal = media_service.import_file(
        str(second["id"]),
        _video(workspace, "g4-inbox-formal.mp4", "green"),
        owner_type="EPISODE",
        owner_id=str(second_episode["id"]),
        stage="FORMAL",
    )
    old_id = str(old["media_version_id"])
    fresh_id = str(fresh["media_version_id"])
    formal_id = str(formal["media_version_id"])
    old_timestamp = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    with database.transaction() as connection:
        connection.execute("UPDATE media_versions SET created_at=?, updated_at=? WHERE id=?", (old_timestamp, old_timestamp, old_id))

    with TestClient(create_app(workspace)) as client:
        all_items = client.get("/api/v1/reviews/inbox?limit=100").json()["items"]
        all_ids = [item["media_version_id"] for item in all_items]
        assert old_id in all_ids and fresh_id in all_ids and formal_id in all_ids
        assert {item["project_id"] for item in all_items} == {str(first["id"]), str(second["id"])}

        old_items = client.get("/api/v1/reviews/inbox?age=OLD").json()["items"]
        assert [item["media_version_id"] for item in old_items] == [old_id]
        assert old_items[0]["age_hours"] > 24 * 7
        assert old_items[0]["episode_id"] == str(first_episode["id"])
        assert old_items[0]["episode_code"] == first_episode["code"]

        fresh_items = client.get(f"/api/v1/reviews/inbox?project_id={second['id']}&age=NEW&episode_id={second_episode['id']}").json()["items"]
        assert {item["media_version_id"] for item in fresh_items} == {fresh_id, formal_id}
        assert all(item["project_id"] == str(second["id"]) for item in fresh_items)

        high_items = client.get("/api/v1/reviews/inbox?priority=HIGH").json()["items"]
        assert formal_id in {item["media_version_id"] for item in high_items}
        assert all(item["priority"] == "HIGH" for item in high_items)
        normal_items = client.get("/api/v1/reviews/inbox?priority=NORMAL").json()["items"]
        assert fresh_id in {item["media_version_id"] for item in normal_items}
        assert all(item["priority"] == "NORMAL" for item in normal_items)

        blocked_items = client.get("/api/v1/reviews/inbox?blocking=BLOCKED").json()["items"]
        assert formal_id in {item["media_version_id"] for item in blocked_items}
        assert all(item["is_blocked"] == 1 for item in blocked_items)
        ready_items = client.get("/api/v1/reviews/inbox?blocking=READY").json()["items"]
        assert fresh_id in {item["media_version_id"] for item in ready_items}
        assert all(item["is_blocked"] == 0 for item in ready_items)

        first_page = client.get("/api/v1/reviews/inbox?limit=1").json()
        assert first_page["next_cursor"] == 1
        second_page = client.get(f"/api/v1/reviews/inbox?limit=1&cursor={first_page['next_cursor']}").json()
        assert second_page["items"]
        assert first_page["items"][0]["media_version_id"] != second_page["items"][0]["media_version_id"]
        assert first_page["items"][0]["inbox_at"] <= second_page["items"][0]["inbox_at"]

        invalid = client.get("/api/v1/reviews/inbox?blocking=UNKNOWN")
        assert invalid.status_code == 422
