from __future__ import annotations

import hashlib
import json
import subprocess
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


def _make_video(workspace, code: str, *, seconds: int = 2, color: str = "navy", size: str = "160x90") -> Path:
    path = workspace.work_root / f"{code}.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:d={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_audio(workspace, code: str, *, seconds: int = 2) -> Path:
    path = workspace.work_root / f"{code}.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-y", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def _fixture(workspace, database, code: str = "jianying_export") -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="剪映导出测试",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        width=1080,
        height=1920,
        target_duration_ms=2_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    video_path = _make_video(workspace, f"{code}_v")
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
        {"source": "jianying-export-test"},
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


def _draft(project_root: Path, exported: dict[str, object]) -> dict[str, object]:
    export_root = project_root / str(exported["rel_path"])
    draft_path = next(export_root.rglob("draft_content.json"))
    return json.loads(draft_path.read_text(encoding="utf-8"))


def test_jianying_export_structure_and_material_references(workspace, database) -> None:
    project, media, timeline = _fixture(workspace, database)
    before = _row_counts(database)

    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="jianying")

    assert _row_counts(database) == before
    assert exported["database_mutated"] is False
    assert exported["runtime_contacted"] is False
    assert exported["network_contacted"] is False
    project_root = workspace.projects_root / str(project["root_rel"])
    draft = _draft(project_root, exported)

    assert draft["schema_version"] == "localdrama.jianying-draft.v1"
    assert draft["best_effort"] is True
    assert draft["draft_name"] == "EPISODE_001-v1"
    assert draft["duration"] == 1_500_000
    canvas = draft["canvas_config"]
    assert canvas["width"] == 1080
    assert canvas["height"] == 1920
    assert canvas["ratio"] == "16:9"
    assert canvas["fps"] == 24

    assert len(draft["materials"]["videos"]) == 1
    video_material = draft["materials"]["videos"][0]
    assert video_material["material_name"] == "jianying_export_v.mp4"
    assert video_material["duration"] == 2_000_000
    assert video_material["width"] == 160
    assert video_material["height"] == 90
    assert video_material["path"].startswith("./media/")
    assert draft["materials"]["audios"] == []
    assert draft["materials"]["texts"] == []

    assert [track["type"] for track in draft["tracks"]] == ["video"]
    video_track = draft["tracks"][0]
    assert len(video_track["segments"]) == 1
    segment = video_track["segments"][0]
    assert segment["material_id"] == video_material["id"]
    assert segment["target_timerange"] == {"start": 500_000, "end": 1_500_000}
    assert segment["source_timerange"] == {"duration": 1_000_000, "start": 250_000}

    draft_dir = project_root / str(exported["rel_path"]) / "EPISODE_001-v1.draft"
    assert (draft_dir / "draft_content.json").is_file()
    media_file = draft_dir / video_material["path"].lstrip("./")
    assert media_file.is_file()
    assert _sha256(media_file) == media["sha256"]


def test_jianying_export_manifest_hash_and_reuse_without_database_mutation(workspace, database) -> None:
    project, _, timeline = _fixture(workspace, database, "jianying_manifest")
    before = _row_counts(database)
    service = TimelineExportService(database, workspace)

    exported = service.export_revision(str(timeline["id"]), format="jianying")
    assert _row_counts(database) == before
    project_root = workspace.projects_root / str(project["root_rel"])
    manifest = json.loads((project_root / str(exported["manifest_rel_path"])).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "localdrama.timeline-export.v1"
    assert manifest["format"] == "jianying"
    assert manifest["writer_version"] == 3
    assert manifest["export_hash"] == exported["export_hash"]
    assert manifest["media_copy"] == "BUNDLED"
    assert manifest["timeline_revision_id"] == timeline["id"]
    rel_paths = {file["rel_path"] for file in manifest["files"]}
    assert any(path.endswith("draft_content.json") for path in rel_paths)
    assert any("/media/" in path for path in rel_paths)
    export_root = project_root / str(exported["rel_path"])
    for file in manifest["files"]:
        path = export_root / file["rel_path"]
        assert path.stat().st_size == file["byte_size"]
        assert _sha256(path) == file["sha256"]

    reused = service.export_revision(str(timeline["id"]), format="jianying")
    assert reused["reused"] is True
    assert reused["export_hash"] == exported["export_hash"]
    assert _row_counts(database) == before


def test_jianying_export_audio_track_from_audio_items(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="jianying_audio",
        title="剪映音频导出测试",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        width=1080,
        height=1920,
        target_duration_ms=2_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    media_service = MediaService(database, workspace)
    video = media_service.import_file(str(project["id"]), _make_video(workspace, "jianying_audio_v"), media_kind="VIDEO")
    audio = media_service.import_file(str(project["id"]), _make_audio(workspace, "jianying_audio_a"), media_kind="AUDIO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": video["media_version_id"], "start_us": 0, "end_us": 1_000_000, "parameters": {}},
            {"track_type": "AUDIO", "media_version_id": audio["media_version_id"], "start_us": 200_000, "end_us": 1_200_000, "parameters": {}},
        ],
        {"source": "jianying-audio-test"},
    )

    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="jianying")
    draft = _draft(workspace.projects_root / str(project["root_rel"]), exported)

    assert [track["type"] for track in draft["tracks"]] == ["video", "audio"]
    audio_track = draft["tracks"][1]
    assert len(audio_track["segments"]) == 1
    audio_segment = audio_track["segments"][0]
    assert audio_segment["target_timerange"] == {"start": 200_000, "end": 1_200_000}
    assert len(draft["materials"]["audios"]) == 1
    audio_material = draft["materials"]["audios"][0]
    assert audio_material["id"] == audio_segment["material_id"]
    assert audio_material["path"].startswith("./media/audio_")
    assert "width" not in audio_material


def test_jianying_export_text_track_from_subtitle_cues(workspace, database) -> None:
    project, _, timeline = _fixture(workspace, database, "jianying_text")
    with database.connect() as connection:
        subtitle_id = "subtitle-jianying-text"
        connection.execute(
            """INSERT INTO subtitle_revisions
            (id, episode_id, revision_no, format, content_text, content_hash, input_snapshot_json, status,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 'SRT', '第一句台词', ?, '{}', 'DRAFT', ?, ?, 'local-user', 1, 'v2')""",
            (subtitle_id, timeline["episode_id"], "subtitle-hash", "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO subtitle_cues (id, subtitle_revision_id, cue_no, start_us, end_us, text, style_json)
            VALUES (?, ?, 1, 600000, 1200000, '第一句台词', '{"font_size": 40}')""",
            ("cue-1", subtitle_id),
        )

    exported = TimelineExportService(database, workspace).export_revision(
        str(timeline["id"]), format="jianying", subtitle_revision_id=subtitle_id
    )
    draft = _draft(workspace.projects_root / str(project["root_rel"]), exported)

    assert [track["type"] for track in draft["tracks"]] == ["video", "text"]
    assert draft["localdrama"]["subtitle_revision_id"] == subtitle_id
    assert draft["localdrama"]["subtitle_revision_hash"] == "subtitle-hash"
    text_material = draft["materials"]["texts"][0]
    assert text_material["content"] == "第一句台词"
    assert text_material["style"] == {"font_size": 40}
    assert text_material["time"] == {"start": 600_000, "duration": 600_000}
    text_segment = draft["tracks"][1]["segments"][0]
    assert text_segment["material_id"] == text_material["id"]
    assert text_segment["target_timerange"] == {"start": 600_000, "end": 1_200_000}
    assert text_segment["source_timerange"] == {"duration": 600_000}


def test_jianying_export_default_and_aliases_keep_standard_behavior(workspace, database) -> None:
    project, _, timeline = _fixture(workspace, database, "jianying_default")
    service = TimelineExportService(database, workspace)

    default = service.export_revision(str(timeline["id"]))
    assert len(default["files"]) == 2
    assert any(file["rel_path"].endswith(".otio") for file in default["files"])
    assert any(file["rel_path"].endswith(".edl") for file in default["files"])
    manifest_path = workspace.projects_root / str(project["root_rel"]) / str(default["manifest_rel_path"])
    assert "format" not in json.loads(manifest_path.read_text(encoding="utf-8"))
    for alias in ("otio", "edl"):
        aliased = service.export_revision(str(timeline["id"]), format=alias)
        assert aliased["export_hash"] == default["export_hash"]
        assert aliased["reused"] is True


def test_jianying_export_rejects_invalid_format(workspace, database) -> None:
    _, _, timeline = _fixture(workspace, database, "jianying_badfmt")

    with pytest.raises(DomainRuleError) as caught:
        TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="avi")
    assert caught.value.code == "TIMELINE_EXPORT_FORMAT_UNSUPPORTED"


def _episode_id(workspace, database, project_id: str) -> str:
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    return str(episode["id"])


def test_jianying_export_rejects_timeline_without_items(workspace, database) -> None:
    project, _, _ = _fixture(workspace, database, "jianying_empty")
    empty_revision_id = "timeline-empty-revision"
    episode_id = _episode_id(workspace, database, str(project["id"]))
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO timeline_revisions
            (id, episode_id, revision_no, content_json, input_snapshot_json, revision_hash, status,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 2, '[]', '{}', 'empty-hash', 'DRAFT', ?, ?, 'local-user', 1, 'v2')""",
            (empty_revision_id, episode_id, "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        )

    with pytest.raises(DomainRuleError) as caught:
        TimelineExportService(database, workspace).export_revision(empty_revision_id, format="jianying")
    assert caught.value.code == "TIMELINE_ITEMS_REQUIRED"


def test_jianying_export_http_route(workspace, database) -> None:
    _, _, timeline = _fixture(workspace, database, "jianying_api")

    with TestClient(create_app(workspace)) as client:
        response = client.post(f'/api/v1/timeline-revisions/{timeline["id"]}:export?format=jianying')

    assert response.status_code == 200
    exported = response.json()["export"]
    assert exported["status"] == "EXPORTED"
    assert any(file["rel_path"].endswith("draft_content.json") for file in exported["files"])

    with TestClient(create_app(workspace)) as client:
        bad = client.post(f'/api/v1/timeline-revisions/{timeline["id"]}:export?format=mov')
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "TIMELINE_EXPORT_FORMAT_UNSUPPORTED"
