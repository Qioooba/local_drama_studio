"""SS-04: a project package's identity covers business state *and* payload bytes.

Adding a licence/LUT/text file must produce a NEW package version, identical
content must reuse the existing one, and an old package must never be
overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService


def _project(workspace, database, code: str = "identity_source") -> dict[str, object]:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(
        code=code,
        title="包身份源",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=90_000,
        allow_unconfigured_capabilities=True,
    )
    (workspace.projects_root / code / "01_story" / "source_documents" / "novel.md").write_text("# 原稿\n", encoding="utf-8")
    return project


def _package_path(workspace, code: str, rel_path: str) -> Path:
    return workspace.projects_root / code / rel_path


def test_untouched_project_reuses_the_existing_package(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)

    first = service.export(str(project["id"]))
    second = service.export(str(project["id"]))

    assert first["status"] == "EXPORTED" and first["reused"] is False
    assert second["reused"] is True
    assert second["rel_path"] == first["rel_path"]
    assert second["sha256"] == first["sha256"]
    assert len(list((workspace.projects_root / "identity_source" / "exports" / "project-packages").glob("*.ldspkg"))) == 1


def test_new_licence_file_creates_a_new_package_and_keeps_the_old_one(workspace, database) -> None:
    project = _project(workspace, database, "identity_licence")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    projects = ProjectService(database, workspace.projects_root)

    before = service.export(str(project["id"]))
    licence = workspace.data_root / "licence.txt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("授权文件", encoding="utf-8")
    projects.import_local_resource(str(project["id"]), "LICENSE_EVIDENCE", licence, "licence.txt")
    after = service.export(str(project["id"]))

    assert after["reused"] is False
    assert after["rel_path"] != before["rel_path"]
    old_package = _package_path(workspace, "identity_licence", str(before["rel_path"]))
    new_package = _package_path(workspace, "identity_licence", str(after["rel_path"]))
    assert old_package.is_file() and new_package.is_file()
    assert old_package.stat().st_size == before["byte_size"]
    # Re-exporting the new content reuses the new package, not the old one.
    repeated = service.export(str(project["id"]))
    assert repeated["reused"] is True and repeated["rel_path"] == after["rel_path"]


def test_edited_payload_content_creates_a_new_package(workspace, database) -> None:
    project = _project(workspace, database, "identity_edit")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)

    before = service.export(str(project["id"]))
    manuscript = workspace.projects_root / "identity_edit" / "01_story" / "source_documents" / "novel.md"
    manuscript.write_text("# 修改后的原稿\n", encoding="utf-8")
    after = service.export(str(project["id"]))

    assert after["rel_path"] != before["rel_path"]
    assert _package_path(workspace, "identity_edit", str(before["rel_path"])).is_file()


def test_mtime_only_change_reuses_the_package(workspace, database) -> None:
    project = _project(workspace, database, "identity_mtime")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)

    first = service.export(str(project["id"]))
    touched = workspace.projects_root / "identity_mtime" / "project.json"
    os.utime(touched, (1_600_000_000, 1_600_000_000))
    second = service.export(str(project["id"]))

    assert second["reused"] is True
    assert second["rel_path"] == first["rel_path"]
    assert second["sha256"] == first["sha256"]


def test_concurrent_identical_exports_agree_on_one_package(workspace, database) -> None:
    import concurrent.futures

    project = _project(workspace, database, "identity_concurrent")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(service.export, str(project["id"])), pool.submit(service.export, str(project["id"]))]]

    assert {item["rel_path"] for item in results} == {results[0]["rel_path"]}
    assert {item["sha256"] for item in results} == {results[0]["sha256"]}
    assert len(list((workspace.projects_root / "identity_concurrent" / "exports" / "project-packages").glob("*.ldspkg"))) == 1
    assert not list((workspace.projects_root / "identity_concurrent" / "exports" / "project-packages").glob(".partial-*"))


def test_payload_manifest_is_normalised_and_content_addressed(workspace, database) -> None:
    import json
    import zipfile

    project = _project(workspace, database, "identity_manifest")
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    package = _package_path(workspace, "identity_manifest", str(exported["rel_path"]))

    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        state_entry = next(item for item in manifest["entries"] if item["path"] == "project-state.json")
        payload_paths = [item["path"] for item in manifest["entries"] if item["path"].startswith("payload/")]

    assert manifest["state_sha256"] == state_entry["sha256"]
    assert payload_paths == sorted(payload_paths)
    assert manifest["entry_count"] == len(manifest["entries"])
    assert manifest["expanded_bytes"] == sum(item["byte_size"] for item in manifest["entries"])
    # The exported file name is derived from state + payload, not from the state
    # hash alone: the payload manifest participates in the identity.
    name = Path(str(exported["rel_path"])).name
    identity = name.removeprefix("identity_manifest-").removesuffix(".ldspkg")
    assert len(identity) == 32 and all(character in "0123456789abcdef" for character in identity)
    assert identity[:12] != manifest["state_sha256"][:12]
