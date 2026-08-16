from __future__ import annotations

import subprocess
from pathlib import Path

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService


def _video(workspace, name: str) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def test_formal_selection_commit_persists_selection_without_binding_error(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="formal_selection_commit",
        title="Formal selection commit",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1_000,
        allow_unconfigured_capabilities=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        _video(workspace, "formal-selection.mp4"),
        media_kind="VIDEO",
        stage="FORMAL",
    )
    media_id = str(media["media_version_id"])
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "formal_video")
    reviews.machine_check(media_id)
    reviews.select_version(media_id, "FORMAL_SELECTION")
    checks = [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]
    reviews.submit_review(media_id, str(template["id"]), "APPROVED", 2, checks)
    plan = reviews.formal_selection_preflight(str(project["id"]), [media_id])
    assert plan["status"] == "READY"
    committed = reviews.commit_formal_selection(str(project["id"]), [media_id], str(plan["plan_hash"]))
    assert committed["status"] == "COMMITTED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT selection_type FROM selections WHERE media_version_id=? ORDER BY created_at DESC LIMIT 1",
            (media_id,),
        ).fetchone()
    assert row is not None
    assert row["selection_type"] == "FORMAL_SELECTION"
