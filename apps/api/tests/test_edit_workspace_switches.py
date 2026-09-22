"""FE-07 backend contract: the edit-workspace read must report the four frozen switches.

A revision created with non-default switches must read back exactly those
values, and a revision that never recorded them must report ``null`` (unknown)
rather than a substituted default — otherwise the editor would show the user a
setting they never chose and could not detect that the switch is dirty.
"""

from __future__ import annotations

import json
import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.main import create_app

SWITCH_FIELDS = (
    "include_dialogue",
    "include_music_and_sfx",
    "include_source_audio",
    "include_subtitles",
)


def _fixture(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="edit_switches",
        title="Edit switches",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    source = workspace.work_root / "edit-switches.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=teal:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]), source, purpose="SHOT_VIDEO", owner_type="SHOT",
        owner_id=str(shot["id"]), media_kind="VIDEO", stage="FORMAL",
    )
    ReviewService(database, workspace).select_version(str(media["media_version_id"]), "FORMAL_SELECTION")
    return project, episode, shot, media


def _payload(fact: dict, *, key: str, switches: dict[str, bool] | None) -> dict:
    clips = fact["video_clips"]
    payload: dict[str, object] = {
        "clips": [
            {
                "shot_id": item["shot_id"],
                "media_version_id": item["media_version_id"],
                "duration_us": item["end_us"] - item["start_us"],
                "source_start_us": item["source_start_us"],
                "transition_in": item["transition_in"],
            }
            for item in clips
        ],
        "expected_latest_revision_id": fact["latest_revision"]["id"] if fact["latest_revision"] else None,
        "expected_upstream_fingerprint": fact["upstream_fingerprint"],
        "idempotency_key": key,
    }
    if switches is not None:
        payload.update(switches)
    return payload


def test_edit_workspace_read_returns_the_frozen_switch_values(workspace, database) -> None:
    """Non-default switches round-trip through the read projection exactly."""

    _project, episode, _shot, _media = _fixture(workspace, database)
    # Deliberately non-default on every field, including the ones whose schema
    # default is True, so a silently substituted default is detectable.
    chosen = {
        "include_dialogue": False,
        "include_music_and_sfx": False,
        "include_source_audio": True,
        "include_subtitles": False,
    }
    with TestClient(create_app(workspace)) as client:
        initial = client.get(f"/api/v2/episodes/{episode['id']}/post/edit")
        assert initial.status_code == 200, initial.text
        fact = initial.json()["workspace"]
        assert fact["latest_revision"] is None

        created = client.post(
            f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts",
            json=_payload(fact, key="switches-non-default", switches=chosen),
        )
        assert created.status_code == 201, created.text

        current = client.get(f"/api/v2/episodes/{episode['id']}/post/edit")
        assert current.status_code == 200, current.text
        latest = current.json()["workspace"]["latest_revision"]
        assert latest is not None
        for field, expected in chosen.items():
            assert latest[field] is expected, f"{field}: expected {expected!r}, got {latest[field]!r}"

        # The same values must be visible in the history projection.
        history_entry = next(item for item in current.json()["workspace"]["history"] if item["id"] == latest["id"])
        for field, expected in chosen.items():
            assert history_entry[field] is expected


def test_edit_workspace_read_reports_absent_switches_as_null_not_false(workspace, database) -> None:
    """A revision that never recorded a switch reports unknown, not a default."""

    _project, episode, _shot, _media = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        initial = client.get(f"/api/v2/episodes/{episode['id']}/post/edit")
        fact = initial.json()["workspace"]

        # Create the draft with the schema defaults, then strip the four keys
        # from the stored snapshot to model a revision written before the
        # switches existed.
        created = client.post(
            f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts",
            json=_payload(fact, key="switches-legacy", switches=None),
        )
        assert created.status_code == 201, created.text
        revision_id = created.json()["timeline"]["id"]

    with database.transaction() as connection:
        row = connection.execute("SELECT input_snapshot_json FROM timeline_revisions WHERE id=?", (revision_id,)).fetchone()
        snapshot = json.loads(str(row["input_snapshot_json"]))
        for field in SWITCH_FIELDS:
            snapshot.pop(field, None)
        # Prove the keys really are gone, so a NULL result cannot be a lucky default.
        assert not any(field in snapshot for field in SWITCH_FIELDS)
        connection.execute(
            "UPDATE timeline_revisions SET input_snapshot_json=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), revision_id),
        )

    with TestClient(create_app(workspace)) as client:
        latest = client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]["latest_revision"]
    assert latest is not None
    for field in SWITCH_FIELDS:
        assert field in latest, f"{field} must be present in the read projection"
        assert latest[field] is None, f"{field} must be reported as unknown (null), got {latest[field]!r}"


def test_read_projection_distinguishes_false_from_absent(workspace, database) -> None:
    """``False`` and "not recorded" must not collapse into the same value."""

    _project, episode, _shot, _media = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        fact = client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]
        created = client.post(
            f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts",
            json=_payload(fact, key="switches-explicit-false", switches={
                "include_dialogue": False,
                "include_music_and_sfx": False,
                "include_source_audio": False,
                "include_subtitles": False,
            }),
        )
        assert created.status_code == 201, created.text
        latest = client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]["latest_revision"]

    for field in SWITCH_FIELDS:
        assert latest[field] is False, f"an explicit False must survive as False (got {latest[field]!r})"
        assert latest[field] is not None
