from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.contact_sheets import ContactSheetExportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _episode_with_shot(workspace, database, code: str) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="联系表测试项目",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    return project, episode, shot


def _real_video(workspace, name: str = "contact-sheet.mp4") -> Path:
    output = workspace.work_root / name
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:d=1",
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


def _selected_video(workspace, database, project: dict[str, object], shot: dict[str, object]) -> dict[str, object]:
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        _real_video(workspace),
        purpose="PROXY",
        owner_type="SHOT",
        owner_id=str(shot["id"]),
        media_kind="VIDEO",
        stage="PROXY",
    )
    ReviewService(database, workspace).select_version(str(media["media_version_id"]), "PROXY_WINNER")
    return media


def _row_counts(database) -> dict[str, int]:
    with database.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}


def test_contact_sheet_requires_a_current_selection(workspace, database) -> None:
    _, episode, _ = _episode_with_shot(workspace, database, "contact_empty")

    with pytest.raises(DomainRuleError) as caught:
        ContactSheetExportService(database, workspace).export_episode(str(episode["id"]))

    assert caught.value.code == "CONTACT_SHEET_SELECTION_REQUIRED"


def test_contact_sheet_exports_verified_original_and_small_thumbnail_without_database_writes(workspace, database) -> None:
    project, episode, shot = _episode_with_shot(workspace, database, "contact_export")
    selected = _selected_video(workspace, database, project, shot)
    before = _row_counts(database)

    exported = ContactSheetExportService(database, workspace).export_episode(str(episode["id"]))

    assert _row_counts(database) == before
    assert exported["status"] == "EXPORTED"
    assert exported["database_mutated"] is False
    assert exported["runtime_contacted"] is False
    assert exported["network_contacted"] is False
    assert exported["reused"] is False
    project_root = workspace.projects_root / str(project["root_rel"])
    assert exported["artifact"]["kind"] == "DIRECTORY"
    assert Path(exported["artifact"]["server_absolute_path"]) == (project_root / str(exported["rel_path"])).resolve()
    assert exported["artifact"]["download_filename"].endswith(".zip")
    assert exported["artifact"]["download_url"].startswith(f"/api/v1/episodes/{episode['id']}/contact-sheet:download")
    manifest = json.loads((project_root / str(exported["manifest_rel_path"])).read_text(encoding="utf-8"))
    assert manifest["export_hash"] == exported["export_hash"]
    assert manifest["items"][0]["sha256"] == selected["sha256"]
    thumbnail = project_root / str(exported["rel_path"]) / manifest["items"][0]["thumbnail_rel_path"]
    probe = subprocess.run(
        [workspace.ffprobe_path, "-v", "error", "-show_entries", "stream=width", "-of", "json", str(thumbnail)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(json.loads(probe.stdout)["streams"][0]["width"]) <= 320

    reused = ContactSheetExportService(database, workspace).export_episode(str(episode["id"]))
    assert reused["export_hash"] == exported["export_hash"]
    assert reused["rel_path"] == exported["rel_path"]
    assert reused["reused"] is True
    assert _row_counts(database) == before


def test_contact_sheet_rejects_changed_source_and_tampered_existing_export(workspace, database) -> None:
    project, episode, shot = _episode_with_shot(workspace, database, "contact_tamper")
    selected = _selected_video(workspace, database, project, shot)
    service = ContactSheetExportService(database, workspace)
    exported = service.export_episode(str(episode["id"]))
    project_root = workspace.projects_root / str(project["root_rel"])
    manifest_path = project_root / str(exported["manifest_rel_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thumbnail = project_root / str(exported["rel_path"]) / manifest["items"][0]["thumbnail_rel_path"]
    thumbnail.write_bytes(b"tampered")

    with pytest.raises(DomainRuleError) as caught:
        service.export_episode(str(episode["id"]))
    assert caught.value.code == "CONTACT_SHEET_EXPORT_TAMPERED"

    source = project_root / str(selected["rel_path"])
    source.write_bytes(source.read_bytes() + b"changed")
    manifest_path.parent.rename(manifest_path.parent.with_name("moved-tampered-export"))
    with pytest.raises(DomainRuleError) as changed:
        service.export_episode(str(episode["id"]))
    assert changed.value.code == "SOURCE_INTEGRITY_FAILED"


def test_contact_sheet_http_route_returns_real_export(workspace, database) -> None:
    project, episode, shot = _episode_with_shot(workspace, database, "contact_api")
    _selected_video(workspace, database, project, shot)

    with TestClient(create_app(workspace)) as client:
        response = client.post(f'/api/v1/episodes/{episode["id"]}/contact-sheet:export')
        exported = response.json()["export"]
        download = client.get(
            f'/api/v1/episodes/{episode["id"]}/contact-sheet:download',
            params={"rel_path": exported["rel_path"]},
        )

    assert response.status_code == 200
    assert response.json()["export"]["item_count"] == 1
    assert response.json()["export"]["network_contacted"] is False
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert any(name.endswith("contact-sheet.html") for name in archive.namelist())
        assert any(name.endswith("manifest.json") for name in archive.namelist())
