from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.commands.generation_preferences import GenerationPreferenceCommandService
from local_drama.application.product_context import ProductContextQueryService, derive_episode_production_status
from local_drama.application.profiles import ProfileService
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.product_context_repository import SqliteProductContextReadRepository
from local_drama.main import create_app


def _ready_milestones() -> dict[str, dict[str, object]]:
    return {
        key: {"ready": True, "count": 1}
        for key in (
            "episode_count", "production_plan_count", "published_profile_binding_count",
            "reviewable_story_draft_count", "active_story_asset_count", "shot_intent_count",
            "shot_generation_job_count",
        )
    }


def test_overview_derives_episode_state_from_evidence_for_multiple_projects() -> None:
    facts = {
        "project-a": {
            "project": {"id": "project-a", "code": "A", "title": "项目 A", "status": "ACTIVE", "revision": 1},
            "seasons": [{"id": "season-a", "code": "S01", "title": "第一季", "episodes": [{
                "id": "episode-a", "code": "EP01", "title": "已交付", "production_status": "NOT_STARTED",
                "preview_render_id": "render-a", "preview_media_version_id": None,
                "_render_review_decision": "APPROVED", "_render_review_stale": False,
                "_delivery_status": "VERIFIED", "_delivery_human_review_status": "APPROVED",
            }]}],
            "milestones": _ready_milestones(), "recent_activity": [],
        },
        "project-b": {
            "project": {"id": "project-b", "code": "B", "title": "项目 B", "status": "ACTIVE", "revision": 1},
            "seasons": [{"id": "season-b", "code": "S01", "title": "第一季", "episodes": [{
                "id": "episode-b", "code": "EP01", "title": "进行中", "production_status": "DRAFT",
                "preview_render_id": "render-b", "preview_media_version_id": None,
                "_render_review_decision": "REJECTED", "_render_review_stale": False,
                "_delivery_status": None, "_delivery_human_review_status": None,
            }]}],
            "milestones": _ready_milestones(), "recent_activity": [],
        },
    }

    class Reader:
        def project_overview_facts(self, project_id: str):
            return facts[project_id]

    service = ProductContextQueryService(Reader())
    delivered = service.project_overview("project-a")["seasons"][0]["episodes"][0]
    in_progress = service.project_overview("project-b")["seasons"][0]["episodes"][0]

    assert delivered["production_status"] == "DELIVERED"
    assert in_progress["production_status"] == "IN_PROGRESS"
    assert all(not key.startswith("_") for key in delivered)
    assert all(not key.startswith("_") for key in in_progress)


def test_episode_status_keeps_legacy_state_without_production_evidence() -> None:
    assert derive_episode_production_status({"production_status": "PLANNED"}) == "PLANNED"
    assert derive_episode_production_status({
        "production_status": "DRAFT", "preview_render_id": "render-1",
        "_render_review_decision": "APPROVED", "_render_review_stale": True,
    }) == "IN_PROGRESS"


def test_historical_delivery_with_current_production_attention_needs_update() -> None:
    """A stale upstream production state must override an old delivery proof."""
    assert derive_episode_production_status({
        "production_status": "DELIVERED",
        "preview_render_id": "historical-render",
        "_render_review_decision": "APPROVED",
        "_render_review_stale": False,
        "_delivery_status": "VERIFIED",
        "_delivery_human_review_status": "APPROVED",
        "_production_attention": True,
    }) == "NEEDS_UPDATE"


def test_project_overview_marks_historical_delivery_when_current_shots_are_stale() -> None:
    class ProductionReader:
        def overview_facts(self, episode_id: str):
            assert episode_id == "episode-under-test"
            return {
                "replan_required": False,
                "shot_count": 8,
                "active_job_count": 0,
                "state_counts": {"READY": 0, "STALE": 8},
            }

    repository = object.__new__(SqliteProductContextReadRepository)
    assert repository._episode_requires_update(ProductionReader(), "episode-under-test") is True


def test_v2_context_and_overview_are_typed_bounded_projections(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        created = client.post("/api/v1/projects", json={
            "code": "overview_v2", "title": "聚合首页", "episode_count": 1,
            "target_duration_ms": 60000, "aspect_ratio": "9:16", "width": 1080, "height": 1920,
            "fps": {"numerator": 24, "denominator": 1}, "primary_language": "zh-CN",
            "subtitle_mode": "NONE", "allow_unconfigured_capabilities": True,
        })
        assert created.status_code == 201
        project = created.json()["project"]
        catalog = client.get(f"/api/v1/projects/{project['id']}/episode-catalog").json()["catalog"]
        episode = catalog["seasons"][0]["episodes"][0]

        context_response = client.get("/api/v2/app-context", params={"project_id": project["id"], "episode_id": episode["id"]})
        assert context_response.status_code == 200
        context = context_response.json()
        assert context["project"]["title"] == "聚合首页"
        assert context["episode"]["id"] == episode["id"]
        assert context["task_summary"] == {"active_worker_count": 0, "attention_job_count": 0}
        assert context["read_only"] is True and context["runtime_contacted"] is False and context["mutated"] is False

        overview_response = client.get(f"/api/v2/projects/{project['id']}/overview")
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["project"]["title"] == "聚合首页"
        assert overview["next_action"]["reason_code"] == "reviewable_story_draft_count"
        assert overview["next_action"]["target"]["kind"] == "STORY"
        assert len(overview["seasons"]) == 1 and len(overview["seasons"][0]["episodes"]) == 1
        assert overview["seasons"][0]["episodes"][0]["preview_render_id"] is None
        assert overview["seasons"][0]["episodes"][0]["preview_media_version_id"] is None
        assert overview["network_contacted"] is False and overview["mutated"] is False


def test_v2_context_rejects_cross_project_episode(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        projects = []
        for index in (1, 2):
            response = client.post("/api/v1/projects", json={
                "code": f"context_{index}", "title": f"项目 {index}", "episode_count": 1,
                "target_duration_ms": 60000, "aspect_ratio": "9:16", "width": 1080, "height": 1920,
                "fps": {"numerator": 24, "denominator": 1}, "primary_language": "zh-CN",
                "subtitle_mode": "NONE", "allow_unconfigured_capabilities": True,
            })
            projects.append(response.json()["project"])
        episode = client.get(f"/api/v1/projects/{projects[1]['id']}/episode-catalog").json()["catalog"]["seasons"][0]["episodes"][0]
        mismatch = client.get("/api/v2/app-context", params={"project_id": projects[0]["id"], "episode_id": episode["id"]})
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "EPISODE_PROJECT_MISMATCH"


def test_overview_counts_an_executable_project_generation_preference(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        created = client.post("/api/v1/projects", json={
            "code": "overview_capability", "title": "能力聚合", "episode_count": 2,
            "target_duration_ms": 120000, "aspect_ratio": "9:16", "width": 480, "height": 854,
            "fps": {"numerator": 24, "denominator": 1}, "primary_language": "zh-CN",
            "subtitle_mode": "NONE", "allow_unconfigured_capabilities": True,
        })
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["id"]

    synced = ProfileService(database, workspace.manifest_path).sync_manifest()
    profile = next(item for item in synced["profiles"] if item["capability"] == "VIDEO_I2V")
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (profile["version_id"],))
        GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)).put(
            project_id=project_id, owner_type="PROJECT", owner_id=project_id,
            capability="VIDEO_I2V", resolution_mode="AUTO", reason="测试项目默认视频能力",
        )

    with TestClient(create_app(workspace)) as client:
        overview_response = client.get(f"/api/v2/projects/{project_id}/overview")
    assert overview_response.status_code == 200, overview_response.text
    overview = overview_response.json()
    assert "PUBLISHED_PROFILE_BINDING_COUNT" not in {item["code"] for item in overview["blockers"]}
