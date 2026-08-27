from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from local_drama.application.frame_bridges import FrameBridgeCommandService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_frame_bridge_binds_real_last_frame_to_outgoing_boundary(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="bridge_tail", title="Bridge tail", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=2000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first = projects.create_shot(str(episode["id"]), "S001", 1000)
    second = projects.create_shot(str(episode["id"]), "S002", 1000)
    source = workspace.work_root / "bridge-tail.mp4"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=32x32:d=1", "-pix_fmt", "yuv420p", "-y", str(source)], check=True, capture_output=True)
    media = MediaService(database, workspace).import_file(str(project["id"]), source, owner_type="SHOT", owner_id=str(first["id"]), media_kind="VIDEO", purpose="SHOT_VIDEO")
    current_source = workspace.work_root / "bridge-current.png"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", "-y", str(current_source)], check=True, capture_output=True)
    current_media = MediaService(database, workspace).import_file(str(project["id"]), current_source, owner_type="SHOT", owner_id=str(second["id"]), media_kind="IMAGE", purpose="KEYFRAME")
    timeline = TimelineService(database, workspace)
    last = timeline.create_frame_anchor(str(media["media_version_id"]), position_mode="LAST_FRAME", role_hint="LAST_FRAME")
    first_frame = timeline.create_frame_anchor(str(media["media_version_id"]), position_mode="FIRST_FRAME", role_hint="FIRST_FRAME")
    transition = timeline.create_transition_constraint(str(first["id"]), str(second["id"]), "START_FROM_PREVIOUS_LAST", enforcement="ADVISORY")
    commands = FrameBridgeCommandService(database)

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-source-frame",
            json={"expected_boundary_revision": 1, "frame_anchor_id": last["id"], "idempotency_key": "bridge-source-1"},
        )
        replay = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-source-frame",
            json={"expected_boundary_revision": 1, "frame_anchor_id": last["id"], "idempotency_key": "bridge-source-1"},
        )
        mismatch = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-source-frame",
            json={"expected_boundary_revision": 1, "frame_anchor_id": first_frame["id"], "idempotency_key": "bridge-source-1"},
        )
        retired = client.post(
            f"/api/v1/frame-bridges/{transition['id']}/source-frame",
            json={"expected_boundary_revision": 1, "frame_anchor_id": last["id"]},
        )
    assert response.status_code == 200, response.text
    result = response.json()["frame_bridge"]

    assert result["from_anchor_id"] == last["id"]
    assert result["boundary_revision"] == 2
    assert result["compatibility_status"] == "PENDING_REVIEW"
    assert result["idempotent_replay"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["frame_bridge"]["idempotent_replay"] is True
    assert replay.json()["frame_bridge"]["boundary_revision"] == 2
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["error"]["code"] == "FRAME_BRIDGE_IDEMPOTENCY_MISMATCH"
    assert retired.status_code == 405
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='FRAME_BRIDGE_SOURCE_FRAME_SET' AND subject_id=?",
            (transition["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE type='FrameBridgeChanged' AND subject_id=?",
            (transition["id"],),
        ).fetchone()[0] == 1
    with pytest.raises(DomainRuleError) as wrong_role:
        commands.set_source_frame(
            str(transition["id"]),
            expected_boundary_revision=int(result["boundary_revision"]),
            frame_anchor_id=str(first_frame["id"]),
            idempotency_key="bridge-wrong-role",
        )
    assert wrong_role.value.code == "FRAME_BRIDGE_SOURCE_ROLE_INVALID"

    with TestClient(create_app(workspace)) as client:
        current = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-current-frame",
            json={
                "expected_boundary_revision": 2,
                "media_version_id": current_media["media_version_id"],
                "idempotency_key": "bridge-current-1",
            },
        )
        assert current.status_code == 200, current.text
        assert current.json()["frame_bridge"]["boundary_revision"] == 3
        assert current.json()["frame_bridge"]["to_anchor_id"]

        inherited = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:inherit",
            json={"expected_boundary_revision": 3, "idempotency_key": "bridge-inherit-1"},
        )
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()["frame_bridge"]["boundary_revision"] == 4
        assert inherited.json()["frame_bridge"]["inherited_from_anchor_id"] == last["id"]

        locked = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-lock",
            json={"expected_boundary_revision": 4, "locked": True, "idempotency_key": "bridge-lock-1"},
        )
        lock_replay = client.post(
            f"/api/v2/frame-bridges/{transition['id']}:set-lock",
            json={"expected_boundary_revision": 4, "locked": True, "idempotency_key": "bridge-lock-1"},
        )
    assert locked.status_code == 200, locked.text
    assert locked.json()["frame_bridge"]["locked"] is True
    assert locked.json()["frame_bridge"]["boundary_revision"] == 5
    assert lock_replay.status_code == 200, lock_replay.text
    assert lock_replay.json()["frame_bridge"]["idempotent_replay"] is True
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE type='FrameBridgeChanged' AND subject_id=?",
            (transition["id"],),
        ).fetchone()[0] == 4
