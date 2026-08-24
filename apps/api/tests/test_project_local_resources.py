from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _create_project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="resource_picker",
        title="Resource Picker",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_project_local_resources_are_purpose_scoped_and_never_expose_absolute_paths(workspace, database) -> None:
    project = _create_project(workspace, database)
    project_root = workspace.projects_root / "resource_picker"
    color_root = project_root / "00_admin" / "color"
    licenses_root = project_root / "00_admin" / "licenses"
    color_root.mkdir(parents=True, exist_ok=True)
    licenses_root.mkdir(parents=True, exist_ok=True)
    (color_root / "cinema.cube").write_text("TITLE cinema", encoding="utf-8")
    (color_root / "ignore.json").write_text("{}", encoding="utf-8")
    (licenses_root / "model.json").write_text('{"license":"owned"}', encoding="utf-8")
    (licenses_root / "ignore.exe").write_bytes(b"no")

    service = ProjectService(database, workspace.projects_root)
    lut = service.list_local_resources(str(project["id"]), "LUT")
    evidence = service.list_local_resources(str(project["id"]), "LICENSE_EVIDENCE")

    assert [item["path_rel"] for item in lut["items"]] == ["00_admin/color/cinema.cube"]
    assert [item["path_rel"] for item in evidence["items"]] == ["00_admin/licenses/model.json"]
    assert all(not item["path_rel"].startswith(str(workspace.projects_root)) for item in lut["items"] + evidence["items"])
    assert lut["read_only"] is True and lut["mutated"] is False and lut["network_contacted"] is False


def test_project_local_resource_http_contract_validates_kind(workspace, database) -> None:
    project = _create_project(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/local-resources", params={"kind": "LUT"})
        invalid = client.get(f"/api/v1/projects/{project['id']}/local-resources", params={"kind": "EXECUTABLE"})
    assert response.status_code == 200
    assert response.json()["kind"] == "LUT"
    assert invalid.status_code == 422

