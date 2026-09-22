"""SS-08: malformed project-package JSON must be a stable 4xx, never a 500.

Every case asserts the unified error contract, no database row, and no leftover
file (neither an inbox package nor a staging/temporary artifact).
"""

from __future__ import annotations

import hashlib
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from local_drama.application.project_packages import PACKAGE_SCHEMA, ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _crafted_directory(workspace):
    directory = workspace.work_root / "malformed-package-cases"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _package(workspace, database, code: str = "malformed_source"):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="畸形包源",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return project


def _write_package(directory, name: str, manifest: object, state: object) -> bytes:
    directory.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode()
    state_bytes = json.dumps(state, ensure_ascii=False).encode()
    path = directory / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project-state.json", state_bytes)
        archive.writestr("package-manifest.json", manifest_bytes)
    return path.read_bytes()


def _manifest(entries: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": PACKAGE_SCHEMA,
        "project_id": "00000000-0000-0000-0000-000000000000",
        "project_code": "malformed_remote",
        "state_sha256": hashlib.sha256(b"{}").hexdigest(),
        "entry_count": len(entries),
        "expanded_bytes": sum(int(item["byte_size"]) for item in entries),
        "entries": entries,
    }
    base.update(overrides)
    return base


def _minimal_state() -> dict[str, object]:
    return {
        "schema_version": "localdrama.project-state.v3",
        "project": {"id": "p", "code": "malformed_remote", "title": "t", "target_duration_ms": 60_000},
        "seasons": [],
        "episodes": [],
        "scenes": [],
        "shots": [],
        "profile_bindings": [],
        "delivery_targets": [],
    }


def _state_entry(state: dict[str, object]) -> dict[str, object]:
    payload = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return {"path": "project-state.json", "byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _case_manifest_is_list() -> tuple[str, object, object]:
    return "manifest-is-list.ldspkg", [], _minimal_state()


def _case_manifest_is_string() -> tuple[str, object, object]:
    return "manifest-is-string.ldspkg", "not-an-object", _minimal_state()


def _case_manifest_is_null() -> tuple[str, object, object]:
    return "manifest-is-null.ldspkg", None, _minimal_state()


def _case_state_is_list() -> tuple[str, object, object]:
    return "state-is-list.ldspkg", _manifest([_state_entry(_minimal_state())]), []


def _case_state_is_string() -> tuple[str, object, object]:
    return "state-is-string.ldspkg", _manifest([_state_entry(_minimal_state())]), "broken"


def _case_entries_not_array() -> tuple[str, object, object]:
    manifest = _manifest([_state_entry(_minimal_state())])
    manifest["entries"] = "nope"
    return "entries-not-array.ldspkg", manifest, _minimal_state()


def _case_entry_not_object() -> tuple[str, object, object]:
    state_entry = _state_entry(_minimal_state())
    manifest = _manifest([state_entry])
    manifest["entries"] = [state_entry, "nope"]
    manifest["entry_count"] = 2
    return "entry-not-object.ldspkg", manifest, _minimal_state()


def _case_entry_negative_size() -> tuple[str, object, object]:
    return "entry-negative-size.ldspkg", _manifest([{"path": "project-state.json", "byte_size": -5, "sha256": "a" * 64}]), _minimal_state()


def _case_entry_bad_sha() -> tuple[str, object, object]:
    return "entry-bad-sha.ldspkg", _manifest([{"path": "project-state.json", "byte_size": 10, "sha256": "zz"}]), _minimal_state()


def _case_missing_project_identity() -> tuple[str, object, object]:
    return "missing-identity.ldspkg", _manifest([_state_entry(_minimal_state())], project_id=""), _minimal_state()


def _case_state_item_not_object() -> tuple[str, object, object]:
    return "state-item-not-object.ldspkg", _manifest([_state_entry(_minimal_state())]), {**_minimal_state(), "shots": ["not-an-object"]}


def _case_state_missing_project() -> tuple[str, object, object]:
    state = _minimal_state()
    state.pop("project")
    return "state-without-project.ldspkg", _manifest([_state_entry(state)]), state


MALFORMED_CASES = [
    _case_manifest_is_list,
    _case_manifest_is_string,
    _case_manifest_is_null,
    _case_state_is_list,
    _case_state_is_string,
    _case_entries_not_array,
    _case_entry_not_object,
    _case_entry_negative_size,
    _case_entry_bad_sha,
    _case_missing_project_identity,
    _case_state_item_not_object,
    _case_state_missing_project,
]


@pytest.mark.parametrize("case", MALFORMED_CASES, ids=[case.__name__ for case in MALFORMED_CASES])
def test_malformed_package_upload_is_a_stable_4xx_without_residue(workspace, database, case) -> None:
    _package(workspace, database)
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    name, manifest, state = case()
    if manifest is None and name == "manifest-is-null.ldspkg":
        pass
    elif manifest is None:
        manifest = _manifest([_state_entry(state if isinstance(state, dict) else _minimal_state())])
    crafted = _crafted_directory(workspace) / name
    crafted.write_bytes(_write_package(crafted.parent, name, manifest, state))

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/project-packages:upload",
            content=crafted.read_bytes(),
            headers={"X-File-Name": name, "Content-Type": "application/octet-stream"},
        )

    assert response.status_code in {400, 409, 422}, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()["error"]
    assert body["code"] in {
        "PROJECT_PACKAGE_INVALID",
        "PROJECT_PACKAGE_STATE_INVALID",
        "PROJECT_PACKAGE_METADATA_TOO_LARGE",
        "PROJECT_PACKAGE_METADATA_TOO_DEEP",
        "PROJECT_PACKAGE_TOO_MANY_RECORDS",
        "PROJECT_PACKAGE_DUPLICATE_PATH",
        "PROJECT_PACKAGE_MANIFEST_MISMATCH",
        "PROJECT_PACKAGE_SCHEMA_UNSUPPORTED",
    }, body
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_package_imports").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_package_imports WHERE status='COMPLETED'").fetchone()[0] == 0
    # The rejected upload is never promoted into the inbox and leaves no partial file.
    assert name not in [item.name for item in inbox.glob("*.ldspkg")]
    assert not list(inbox.glob(".partial-*"))
    assert not list((workspace.data_root / "imports" / "project-packages" / "staged").glob(".partial-*"))


def test_oversized_metadata_member_is_rejected_before_parsing(workspace, database, monkeypatch) -> None:
    from local_drama.application import project_packages

    _package(workspace, database)
    monkeypatch.setattr(project_packages, "MAX_METADATA_JSON_BYTES", 64)
    state = _minimal_state()
    crafted = _crafted_directory(workspace) / "oversized.ldspkg"
    crafted.write_bytes(_write_package(crafted.parent, "oversized.ldspkg", _manifest([_state_entry(state)]), state))

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/project-packages:upload",
            content=crafted.read_bytes(),
            headers={"X-File-Name": "oversized.ldspkg", "Content-Type": "application/octet-stream"},
        )

    assert response.status_code in {400, 409, 422}, response.text
    assert response.json()["error"]["code"] == "PROJECT_PACKAGE_METADATA_TOO_LARGE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_package_imports").fetchone()[0] == 0


def test_deeply_nested_state_is_rejected_structurally(workspace, database) -> None:
    _package(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    nested: object = "leaf"
    for _ in range(80):
        nested = {"next": nested}
    state = {**_minimal_state(), "shots": [], "plan_extra": nested}
    crafted = _crafted_directory(workspace) / "deep.ldspkg"
    crafted.write_bytes(_write_package(crafted.parent, "deep.ldspkg", _manifest([_state_entry(state)]), state))

    with pytest.raises(DomainRuleError) as error:
        service.inspect_path(crafted)

    assert error.value.code == "PROJECT_PACKAGE_METADATA_TOO_DEEP"


def test_duplicate_archive_path_is_rejected(workspace, database) -> None:
    _package(workspace, database)
    state = _minimal_state()
    state_bytes = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    entry = _state_entry(state)
    manifest = _manifest([entry])
    path = _crafted_directory(workspace) / "duplicate.ldspkg"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project-state.json", state_bytes)
        archive.writestr("project-state.json", state_bytes)
        archive.writestr("package-manifest.json", json.dumps(manifest).encode())

    with pytest.raises(DomainRuleError) as error:
        ProjectPackageService(database, workspace.projects_root, workspace.data_root).inspect_path(path)

    assert error.value.code == "PROJECT_PACKAGE_DUPLICATE_PATH"


def test_legitimate_unicode_package_still_imports(workspace, database) -> None:
    project = _package(workspace, database, "malformed_unicode")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    (workspace.projects_root / "malformed_unicode" / "01_story" / "source_documents" / "中文 原稿.md").write_text("# 正文\n", encoding="utf-8")
    exported = service.export(str(project["id"]))

    assert service.inspect_path(workspace.projects_root / "malformed_unicode" / str(exported["rel_path"]))["status"] == "READY_REBIND_EXISTING"
