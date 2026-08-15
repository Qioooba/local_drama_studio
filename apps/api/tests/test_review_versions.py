from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str = "review_version_project") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Review template version project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _video(workspace, name: str) -> Path:
    output = workspace.work_root / name
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _checks(template: dict[str, object]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]  # type: ignore[index]


def test_review_template_versions_are_immutable_and_history_explainable(workspace, database) -> None:
    project = _project(workspace, database)
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "review-template-version.mp4"), stage="PROXY")
    media_id = str(media["media_version_id"])
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    original = next(item for item in reviews.templates() if item["code"] == "proxy_video" and item["version_no"] == 1)
    original_items = list(original["items"])
    machine = reviews.machine_check(media_id)
    assert machine["status"] == "PASS"
    first = reviews.submit_review(media_id, str(original["id"]), "APPROVED", 1, _checks(original))
    assert first["decision"] == "APPROVED"

    updated_items = [*original_items, {"id": "creative_intent", "label": "创意意图", "required": False}]
    latest = reviews.create_template_version("proxy_video", "MEDIA_VERSION", updated_items)
    assert latest["version_no"] == 2
    assert latest["id"] != original["id"]
    all_versions = [item for item in reviews.templates() if item["code"] == "proxy_video"]
    assert [item["version_no"] for item in all_versions] == [1, 2]
    assert next(item for item in all_versions if item["version_no"] == 1)["items"] == original_items
    history = reviews.list_reviews("MEDIA_VERSION", media_id)
    assert history[0]["review_template_version_id"] == original["id"]

    # Repeating the same definition is idempotent and does not create v3.
    duplicate = reviews.create_template_version("proxy_video", "MEDIA_VERSION", updated_items)
    assert duplicate["duplicate"] is True
    assert duplicate["version_no"] == 2
    assert len([item for item in reviews.templates() if item["code"] == "proxy_video"]) == 2


def test_machine_qc_does_not_create_human_approval_and_batch_errors_are_explicit(workspace, database) -> None:
    project = _project(workspace, database, "review_machine_separation")
    media_service = MediaService(database, workspace)
    media = media_service.import_file(str(project["id"]), _video(workspace, "review-machine-separation.mp4"), stage="PROXY")
    media_id = str(media["media_version_id"])
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "proxy_video")
    machine = reviews.machine_check(media_id)
    assert machine["status"] == "PASS"
    assert reviews.list_reviews("MEDIA_VERSION", media_id) == []
    with pytest.raises(DomainRuleError, match="拒绝批量审核必须填写原因"):
        # A batch rejection is still a human decision and requires an
        # auditable reason; machine PASS alone cannot manufacture approval.
        plan = reviews.batch_preflight(str(project["id"]), [{"media_version_id": media_id, "template_version_id": str(template["id"])}])
        reviews.batch_commit(str(plan["plan_token"]), "REJECTED", _checks(template))
    assert reviews.list_reviews("MEDIA_VERSION", media_id) == []


def test_review_rejects_template_for_wrong_media_stage(workspace, database) -> None:
    project = _project(workspace, database, "review_template_guard")
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "review-template-guard.mp4"), stage="PROXY")
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    image_template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    with pytest.raises(DomainRuleError, match="审核模板必须匹配媒体类型与阶段"):
        reviews.submit_review(
            str(media["media_version_id"]),
            str(image_template["id"]),
            "NEEDS_CHANGES",
            1,
            _checks(image_template),
            comment="wrong template should be rejected",
        )
def test_review_template_version_api_exposes_append_only_contract(workspace, database) -> None:
    _project(workspace, database, "review_template_api")
    with TestClient(create_app(workspace)) as client:
        templates = client.get("/api/v1/review-templates")
        assert templates.status_code == 200
        original = next(item for item in templates.json()["items"] if item["code"] == "proxy_video")
        response = client.post(
            "/api/v1/review-templates",
            json={
                "code": "proxy_video",
                "subject_type": "MEDIA_VERSION",
                "items": [*original["items"], {"id": "api_note", "label": "API note", "required": False}],
            },
        )
        assert response.status_code == 201
        assert response.json()["template"]["version_no"] == int(original["version_no"]) + 1
        versions = [item for item in client.get("/api/v1/review-templates").json()["items"] if item["code"] == "proxy_video"]
        assert len(versions) == 2
        assert versions[0]["items"] != versions[1]["items"]
