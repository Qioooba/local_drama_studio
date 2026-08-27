from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_exports import TimelineExportService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _fixture(workspace, database, code: str = "timeline_export") -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="时间线导出测试",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=2_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    video_path = workspace.work_root / f"{code}.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=160x90:d=2",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), video_path, media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [
            {
                "track_type": "VIDEO",
                "media_version_id": media["media_version_id"],
                "start_us": 500_000,
                "end_us": 1_500_000,
                "parameters": {"source_start_us": 250_000},
            }
        ],
        {"source": "timeline-export-test"},
    )
    return project, media, timeline


def _row_counts(database) -> dict[str, int]:
    with database.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}


def test_timeline_export_writes_otio_edl_and_verified_manifest_without_database_mutation(workspace, database) -> None:
    project, media, timeline = _fixture(workspace, database)
    before = _row_counts(database)

    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]))

    assert _row_counts(database) == before
    assert exported["database_mutated"] is False
    assert exported["runtime_contacted"] is False
    assert exported["network_contacted"] is False
    project_root = workspace.projects_root / str(project["root_rel"])
    export_root = project_root / str(exported["rel_path"])
    manifest = json.loads((project_root / str(exported["manifest_rel_path"])).read_text(encoding="utf-8"))
    assert manifest["timeline_revision_id"] == timeline["id"]
    assert manifest["revision_hash"] == timeline["revision_hash"]
    assert len(manifest["files"]) == 2
    for file in manifest["files"]:
        path = export_root / file["rel_path"]
        assert path.stat().st_size == file["byte_size"]
        assert _sha256(path) == file["sha256"]
    otio_path = next(export_root / file["rel_path"] for file in manifest["files"] if file["rel_path"].endswith(".otio"))
    edl_path = next(export_root / file["rel_path"] for file in manifest["files"] if file["rel_path"].endswith(".edl"))
    otio = json.loads(otio_path.read_text(encoding="utf-8"))
    clip = otio["tracks"]["children"][0]["children"][1]
    assert otio["OTIO_SCHEMA"] == "Timeline.1"
    reference = clip["media_references"][clip["active_media_reference_key"]]
    assert reference["metadata"]["sha256"] == media["sha256"]
    assert reference["target_url"].startswith("../../../../")
    assert reference["target_url"].endswith("timeline_export.mp4")
    assert clip["source_range"]["start_time"]["value"] == 6.0
    edl = edl_path.read_text(encoding="utf-8")
    assert "FCM: NON-DROP FRAME" in edl
    assert "00:00:00:06 00:00:01:06 00:00:00:12 00:00:01:12" in edl
    assert str(media["media_version_id"]) in edl

    reused = TimelineExportService(database, workspace).export_revision(str(timeline["id"]))
    assert reused["reused"] is True
    assert reused["export_hash"] == exported["export_hash"]
    assert _row_counts(database) == before


def test_timeline_export_rejects_tamper_and_source_change_without_touching_revision(workspace, database) -> None:
    project, media, timeline = _fixture(workspace, database, "timeline_tamper")
    service = TimelineExportService(database, workspace)
    exported = service.export_revision(str(timeline["id"]))
    project_root = workspace.projects_root / str(project["root_rel"])
    export_root = project_root / str(exported["rel_path"])
    edl = next(export_root.glob("*.edl"))
    edl.write_text("tampered", encoding="utf-8")

    with pytest.raises(DomainRuleError) as caught:
        service.export_revision(str(timeline["id"]))
    assert caught.value.code == "TIMELINE_EXPORT_TAMPERED"

    moved = export_root.with_name("moved-tampered-export")
    export_root.rename(moved)
    source = project_root / str(media["rel_path"])
    source.write_bytes(source.read_bytes() + b"changed")
    before = _row_counts(database)
    with pytest.raises(DomainRuleError) as changed:
        service.export_revision(str(timeline["id"]))
    assert changed.value.code == "SOURCE_INTEGRITY_FAILED"
    assert _row_counts(database) == before
    assert TimelineService(database, workspace).get_timeline(str(timeline["id"]))["revision_hash"] == timeline["revision_hash"]
    assert not list((project_root / "05_timelines").rglob(".timeline-export.partial-*"))


def test_timeline_export_http_route(workspace, database) -> None:
    _, _, timeline = _fixture(workspace, database, "timeline_api")

    with TestClient(create_app(workspace)) as client:
        response = client.post(f'/api/v1/timeline-revisions/{timeline["id"]}:export')
        exported = response.json()["export"]
        download = client.get(
            f'/api/v1/timeline-revisions/{timeline["id"]}/export:download',
            params={"rel_path": exported["rel_path"]},
        )

    assert response.status_code == 200
    assert response.json()["export"]["status"] == "EXPORTED"
    assert len(response.json()["export"]["files"]) == 2
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert any(name.endswith(".otio") for name in archive.namelist())
        assert any(name.endswith(".edl") for name in archive.namelist())
