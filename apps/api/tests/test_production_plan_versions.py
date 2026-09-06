from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.projects import ProjectService
from local_drama.domain.production_spec import canonical_production_plan_code
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Production plan versioning",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _plan(width: int, height: int) -> dict[str, object]:
    ratio = "9:16" if width < height else "16:9"
    return {
        "schema_version": "localdrama.production-plan.v2",
        "presentation": {
            "aspect_ratio": ratio,
            "width": width,
            "height": height,
            "fps": {"numerator": 24, "denominator": 1},
        },
        "generation": {
            "upscale": {
                "enabled": True,
                "required": True,
                "stage": "COMPOSE_QC",
                "executor": "builtin:ffmpeg",
                "target": "PRESENTATION_SPEC",
                "fit": "LETTERBOX",
            }
        },
    }


def test_plan_save_reuses_project_identity_and_keeps_auditable_revisions(workspace, database) -> None:
    project = _project(workspace, database, "plan_version_project")
    project_id = str(project["id"])
    service = ConfigurationService(database)

    first = service.create_plan_binding(project_id, "old-16x9-display-code", "横屏旧规格", _plan(2560, 1440))
    second = service.create_plan_binding(project_id, "project-production-plan", "竖屏 2K 规格", _plan(1440, 2560))
    third = service.create_plan_binding(project_id, "another-display-intent", "竖屏 2K 修订", _plan(1440, 2560))

    canonical_code = canonical_production_plan_code(project_id)
    assert first["code"] == canonical_code
    assert second["code"] == canonical_code
    assert third["code"] == canonical_code
    assert first["production_plan_id"] == second["production_plan_id"] == third["production_plan_id"]
    assert first["version_no"] == 1
    assert second["version_no"] == 2
    assert third["version_no"] == 3

    with database.connect() as connection:
        plans = connection.execute("SELECT id, code, title FROM production_plans WHERE code=?", (canonical_code,)).fetchall()
        versions = connection.execute(
            "SELECT version_no, status, plan_json FROM production_plan_versions WHERE production_plan_id=? ORDER BY version_no",
            (first["production_plan_id"],),
        ).fetchall()
        active_count = connection.execute(
            "SELECT COUNT(*) FROM production_plan_versions WHERE production_plan_id=? AND status='ACTIVE'",
            (first["production_plan_id"],),
        ).fetchone()[0]
        binding = connection.execute(
            "SELECT production_plan_version_id FROM project_plan_bindings WHERE project_id=?", (project_id,)
        ).fetchone()

    assert len(plans) == 1
    assert plans[0]["title"] == "竖屏 2K 修订"
    assert [(row["version_no"], row["status"]) for row in versions] == [(1, "RETIRED"), (2, "RETIRED"), (3, "ACTIVE")]
    assert json.loads(versions[1]["plan_json"])["presentation"]["aspect_ratio"] == "9:16"
    assert active_count == 1
    assert binding["production_plan_version_id"] == third["production_plan_version_id"]


def test_production_plan_route_repeated_save_is_not_a_unique_constraint_500(workspace, database) -> None:
    project = _project(workspace, database, "plan_route_project")
    project_id = str(project["id"])
    payload = {"code": "project-production-plan", "title": "竖屏 2K", "plan": _plan(1440, 2560)}

    with TestClient(create_app(workspace)) as client:
        first = client.post(f"/api/v1/projects/{project_id}/production-plan", json=payload)
        second = client.post(f"/api/v1/projects/{project_id}/production-plan", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["binding"]["production_plan_id"] == second.json()["binding"]["production_plan_id"]
    assert first.json()["binding"]["version_no"] == 1
    assert second.json()["binding"]["version_no"] == 2


def test_plan_identity_is_unique_per_project_and_concurrent_saves_serialize(workspace, database) -> None:
    first_project = _project(workspace, database, "plan_unique_project_a")
    second_project = _project(workspace, database, "plan_unique_project_b")
    service = ConfigurationService(database)

    first = service.create_plan_binding(str(first_project["id"]), "project-production-plan", "A", _plan(1440, 2560))
    second = service.create_plan_binding(str(second_project["id"]), "project-production-plan", "B", _plan(1440, 2560))
    assert first["code"] != second["code"]

    concurrent_project = _project(workspace, database, "plan_concurrent_project")
    concurrent_id = str(concurrent_project["id"])

    def save(index: int) -> dict[str, object]:
        return service.create_plan_binding(
            concurrent_id,
            "project-production-plan",
            f"Concurrent {index}",
            _plan(1440, 2560),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, (1, 2)))

    assert {item["version_no"] for item in results} == {1, 2}
    assert len({item["production_plan_id"] for item in results}) == 1
    with database.connect() as connection:
        plan_count = connection.execute(
            "SELECT COUNT(*) FROM production_plans WHERE code=?", (canonical_production_plan_code(concurrent_id),)
        ).fetchone()[0]
        active_count = connection.execute(
            "SELECT COUNT(*) FROM production_plan_versions WHERE production_plan_id=? AND status='ACTIVE'",
            (results[0]["production_plan_id"],),
        ).fetchone()[0]
    assert plan_count == 1
    assert active_count == 1
