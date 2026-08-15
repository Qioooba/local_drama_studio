from __future__ import annotations

import subprocess

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService


def _video(workspace, name: str) -> str:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=1",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-y",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return str(output)


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="formal_video_qc",
        title="Formal video QC",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1_000,
        allow_unconfigured_capabilities=True,
    )


def test_formal_video_machine_qc_persists_probe_dimensions_fps_duration_and_codec(workspace, database) -> None:
    project = _project(workspace, database)
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "formal-qc.mp4"), stage="FORMAL")

    result = ReviewService(database, workspace).machine_check(str(media["media_version_id"]))

    assert result["status"] == "PASS"
    assert result["policy_version"] == "g6_formal_video_qc_v1"
    checks = {str(item["item_id"]): item for item in result["results"]}
    assert {"decode", "dimensions", "fps", "duration", "codec"} <= set(checks)
    assert checks["dimensions"]["details"] == {"width": 320, "height": 180, "source": "ffprobe"}
    assert checks["fps"]["details"]["value"] == 24.0
    assert checks["duration"]["details"]["duration_ms"] > 0
    assert checks["codec"]["details"]["codec_name"]


def test_formal_video_machine_qc_blocks_missing_probe_facts(workspace, database) -> None:
    project = _project(workspace, database)
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "formal-qc-missing.mp4"), stage="FORMAL")
    with database.transaction() as connection:
        connection.execute("UPDATE media_versions SET duration_ms=NULL, fps_num=NULL, fps_den=NULL, probe_json=? WHERE id=?", ('{"probe_status":"PASS","streams":[]}', media["media_version_id"]))

    result = ReviewService(database, workspace).machine_check(str(media["media_version_id"]))

    assert result["status"] == "FAIL"
    checks = {str(item["item_id"]): item for item in result["results"]}
    assert checks["dimensions"]["result"] == "FAIL"
    assert checks["fps"]["result"] == "FAIL"
    assert checks["duration"]["result"] == "FAIL"
    assert checks["codec"]["result"] == "FAIL"
