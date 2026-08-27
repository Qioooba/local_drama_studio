from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
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


def _audio(workspace, name: str) -> Path:
    output = workspace.work_root / name
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "pcm_s16le",
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
    assert all(item["media_version_id"] != str(proxy_version["id"]) for item in review_service.inbox(project_id))
    assert any(item["media_version_id"] == str(proxy_version["id"]) for item in review_service.inbox(project_id, include_resolved=True))
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
    ready_formal_plan = review_service.formal_selection_preflight(project_id, [str(formal_version["id"])])
    assert ready_formal_plan["status"] == "READY"

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

    shot_studio_command_service(database).save_draft_revision(str(shot["id"]), {"subject_action": "walk"}, freeze=True)
    reviews = review_service.list_reviews("MEDIA_VERSION", str(formal_version["id"]))
    assert reviews[0]["is_stale"] == 1
    assert reviews[0]["stale_reason"] == "shot_revision_changed"
    # Selections are immutable historical records.  The formerly-selected
    # formal version cannot pass the current formal-selection gate after its
    # upstream ShotRevision changes, while the original selection remains
    # queryable for audit rather than being deleted or silently rewritten.
    stale_formal_plan = review_service.formal_selection_preflight(project_id, [str(formal_version["id"])])
    assert stale_formal_plan["status"] == "BLOCKED"
    assert "LATEST_HUMAN_APPROVAL_REQUIRED" in stale_formal_plan["items"][0]["blockers"]
    with database.connect() as connection:
        selection_history = connection.execute("SELECT media_version_id, selection_type FROM selections WHERE id=?", (formal_selection["id"],)).fetchone()
    assert tuple(selection_history) == (str(formal_version["id"]), "FORMAL_SELECTION")


def test_review_api_exposes_real_templates_and_legacy_inbox_read(workspace, database) -> None:
    project = _project(workspace, database, "g4_api")
    project_id = str(project["id"])
    media = MediaService(database, workspace).import_file(project_id, _video(workspace, "g4-api.mp4", "green"), stage="PROXY")
    version_id = str(media["media_version_id"])
    with TestClient(create_app(workspace)) as client:
        templates = client.get("/api/v1/review-templates")
        assert templates.status_code == 200
        assert any(item["code"] == "proxy_video" for item in templates.json()["items"])
        inbox = client.get(f"/api/v1/reviews/inbox?project_id={project_id}")
        assert inbox.status_code == 200
        assert any(item["media_version_id"] == version_id for item in inbox.json()["items"])


def test_review_inbox_resolves_dialogue_text_revision_audio_to_episode_and_shot(workspace, database) -> None:
    project = _project(workspace, database, "g4_dialogue_audio_lineage")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    episode = projects.list_episodes(str(projects.list_seasons(project_id)[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S-AUDIO-001", 1000)
    line = DialogueService(database, workspace).create_line(
        str(episode["id"]),
        code="DL-AUDIO-001",
        speaker="林默",
        text="雾港来信。",
        pronunciation={},
        shot_id=str(shot["id"]),
    )
    text_revision_id = str(line["text_revisions"][-1]["id"])
    media = MediaService(database, workspace).import_file(
        project_id,
        _audio(workspace, "g4-dialogue-lineage.wav"),
        owner_type="DIALOGUE_TEXT_REVISION",
        owner_id=text_revision_id,
        stage="FORMAL",
    )
    version_id = str(media["media_version_id"])

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/reviews/inbox?project_id={project_id}&episode_id={episode['id']}&media_kind=AUDIO")
        assert response.status_code == 200
        item = next(row for row in response.json()["items"] if row["media_version_id"] == version_id)
        assert item["episode_id"] == str(episode["id"])
        assert item["episode_code"] == episode["code"]
        assert item["shot_id"] == str(shot["id"])
        assert item["shot_code"] == "S-AUDIO-001"


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


def test_review_inbox_excludes_rejected_by_default_and_includes_when_requested(workspace, database) -> None:
    project = _project(workspace, database, "g4_rejected_inbox")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    media_service = MediaService(database, workspace)
    audio_file = _audio(workspace, "g4-reject-bgm.wav")
    media = media_service.import_file(
        project_id,
        audio_file,
        owner_type="EPISODE",
        owner_id=str(episode["id"]),
        stage="IMPORTED",
        media_kind="AUDIO",
    )
    version_id = str(media["media_version_id"])

    review_service = ReviewService(database, workspace)
    review_service.ensure_templates()
    templates = {str(item["code"]): item for item in review_service.templates()}
    audio_template = templates["audio_mix"]

    # Initially in inbox
    with TestClient(create_app(workspace)) as client:
        default_inbox = client.get(f"/api/v1/reviews/inbox?project_id={project_id}").json()["items"]
        assert any(item["media_version_id"] == version_id for item in default_inbox)

    # Submit REJECTED decision with required comment
    review_service.submit_review(
        version_id,
        template_version_id=str(audio_template["id"]),
        decision="REJECTED",
        checks=_checks(audio_template),
        expected_subject_revision=1,
        comment="响度低于标准，需要重新生成",
    )

    with TestClient(create_app(workspace)) as client:
        # Default inbox should exclude the REJECTED item
        default_inbox_after = client.get(f"/api/v1/reviews/inbox?project_id={project_id}").json()["items"]
        assert not any(item["media_version_id"] == version_id for item in default_inbox_after)

        # Explicit include_resolved=true should return it
        resolved_inbox = client.get(f"/api/v1/reviews/inbox?project_id={project_id}&include_resolved=true").json()["items"]
        assert any(item["media_version_id"] == version_id for item in resolved_inbox)
        rejected_item = next(item for item in resolved_inbox if item["media_version_id"] == version_id)
        assert rejected_item["decision"] == "REJECTED"
