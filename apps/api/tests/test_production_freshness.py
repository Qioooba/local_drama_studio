from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.queries.production_freshness import ProductionFreshnessService
from local_drama.main import create_app


class _Facts:
    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = facts
        self.calls: list[dict[str, Any]] = []

    def load_facts(self, *, scope_type: str, scope_id: str, limit: int) -> dict[str, Any]:
        self.calls.append({"scope_type": scope_type, "scope_id": scope_id, "limit": limit})
        return self.facts


def _facts() -> dict[str, Any]:
    common = {"project_id": "p1", "episode_id": "e1", "shot_id": "s1"}
    return {
        "scope": {"type": "SHOT", "id": "s1", **common},
        "variants": [{
            **common, "variant_id": "v1", "is_stale": 0, "stale_reason": None,
            "source_reference_id": "ref-old", "source_reference_status": "ARCHIVED", "source_revision": 2,
            "current_reference_id": "ref-current", "current_revision": 1,
            "source_asset_state_id": "base", "source_state_revision": 1,
            "current_asset_state_id": "injured", "current_state_revision": 3,
            "source_prompt_revision_id": "prompt-r1", "source_prompt_revision_no": 1,
            "current_prompt_revision_id": "prompt-r2", "current_prompt_revision_no": 2,
            "source_profile_version_id": "profile-v1", "source_profile_version_no": 1,
            "current_profile_version_id": "profile-v2", "current_profile_version_no": 2,
        }],
        "frame_bridges": [{
            **common, "frame_anchor_id": "fa1", "variant_id": "v1", "is_stale": 1,
            "stale_reason": "approved_video_winner_changed", "source_media_version_id": "mv-old",
            "source_media_version_no": 1, "current_media_version_id": "mv-new", "current_media_version_no": 2,
        }],
        "timeline": [{
            **common, "timeline_item_id": "ti1", "variant_id": "v1", "timeline_status": "FROZEN",
            "media_version_id": "mv-old", "media_version_no": 1,
            "current_media_version_id": "mv-new", "current_media_version_no": 2,
            "source_post_process_recipe_id": "recipe-v1", "source_post_process_version_no": 1,
            "current_post_process_recipe_id": "recipe-v2", "current_post_process_version_no": 2,
        }],
        "query_count": 6,
        "truncated": False,
    }


def test_freshness_aggregates_asset_state_reference_frame_and_timeline_without_mutation() -> None:
    repository = _Facts(_facts())
    report = ProductionFreshnessService(repository).evaluate(scope_type="shot", scope_id="s1", limit=20)

    assert repository.calls == [{"scope_type": "SHOT", "scope_id": "s1", "limit": 21}]
    assert [item["fact_type"] for item in report["items"]] == ["VARIANT", "FRAME_BRIDGE", "TIMELINE"]
    assert {reason["code"] for reason in report["items"][0]["reasons"]} == {
        "ASSET_REFERENCE_CHANGED", "ASSET_STATE_CHANGED", "PROMPT_CHANGED", "PROFILE_CHANGED",
    }
    assert {reason["code"] for reason in report["items"][1]["reasons"]} >= {"FRAME_BRIDGE_CHANGED", "ASSET_REFERENCE_CHANGED"}
    assert {reason["code"] for reason in report["items"][2]["reasons"]} >= {"SELECTION_CHANGED", "POST_PROCESS_CHANGED", "ASSET_REFERENCE_CHANGED"}
    assert report["items"][0]["source"] == {"entity_type": "ASSET_REFERENCE", "entity_id": "ref-old", "revision": 2}
    assert report["items"][0]["current"] == {"entity_type": "ASSET_REFERENCE", "entity_id": "ref-current", "revision": 1}
    assert report["audit"] == {"read_only": True, "writes_performed": 0, "query_count": 6, "query_limit": 20}
    assert report["local_only"] is True and report["network_contacted"] is False


def test_freshness_deduplicates_variant_dependencies_and_enforces_global_limit() -> None:
    facts = _facts()
    facts["variants"].append({**facts["variants"][0], "source_reference_id": "ref-other"})
    repository = _Facts(facts)
    report = ProductionFreshnessService(repository).evaluate(scope_type="SHOT", scope_id="s1", limit=2)

    assert [item["id"] for item in report["items"]] == ["v1", "fa1"]
    assert report["summary"] == {"returned": 2, "stale": 2, "current": 0, "truncated": True}
    assert repository.calls[0]["limit"] == 3


def test_freshness_routes_are_scoped_bounded_and_read_only(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="freshness_contract", title="Freshness", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    with TestClient(create_app(workspace)) as client:
        # Lifespan may install missing built-in review templates.  Snapshot
        # after startup so this assertion isolates the freshness GET itself.
        with database.connect() as connection:
            audits_before = int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        response = client.get(f"/api/v1/projects/{project_id}/production-freshness?limit=7")
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == {"type": "PROJECT", "id": project_id, "project_id": project_id, "episode_id": None, "shot_id": None}
        assert body["summary"] == {"returned": 0, "stale": 0, "current": 0, "truncated": False}
        assert body["audit"]["read_only"] is True
        assert body["audit"]["writes_performed"] == 0
        assert body["audit"]["query_limit"] == 7
        assert client.get(f"/api/v1/projects/{project_id}/production-freshness?limit=501").status_code == 422
        assert client.get("/api/v1/shots/missing/production-freshness").status_code == 404
        assert client.get(f"/api/v1/episodes/{episode['id']}/production-freshness").status_code == 200
        assert client.get(f"/api/v1/shots/{shot['id']}/production-freshness").status_code == 200
        with database.connect() as connection:
            assert int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]) == audits_before


def test_freshness_openapi_has_all_scopes_and_typed_response(workspace) -> None:
    schema = create_app(workspace).openapi()
    for path in (
        "/api/v1/projects/{project_id}/production-freshness",
        "/api/v1/episodes/{episode_id}/production-freshness",
        "/api/v1/shots/{shot_id}/production-freshness",
    ):
        response = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response == {"$ref": "#/components/schemas/ProductionFreshnessResponse"}
