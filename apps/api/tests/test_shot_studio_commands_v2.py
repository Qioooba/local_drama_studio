from __future__ import annotations

import hashlib
import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS
from local_drama.main import create_app
from tests.test_director_fields import _published_camera_profile


def _workspace_shot(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT-001", 4_000)
    return project, episode, shot


def _ready_fields(profile_id: str) -> dict[str, object]:
    fields: dict[str, object] = {
        field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field for field in REQUIRED_SHOT_FIELDS
    }
    fields["camera_plan"] = {
        "mode": "NATIVE",
        "shot_type": "CLOSEUP",
        "movement": "PUSH_IN",
        "prompt_text": "",
        "direction": "FORWARD",
        "intensity": 0.5,
        "curve": "EASE_IN_OUT",
        "profile_version_id": profile_id,
    }
    return fields


def _keyframe(workspace, database, project_id: str, shot_id: str, token: str = "first") -> str:
    source = workspace.work_root / f"working-keyframe-{token}.png"
    color = hashlib.sha256(f"{shot_id}:{token}".encode()).hexdigest()[:6]
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{color}:s=160x90:d=0.1",
            "-frames:v",
            "1",
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    return str(
        MediaService(database, workspace).import_file(
            project_id,
            source,
            owner_type="SHOT",
            owner_id=shot_id,
            media_kind="IMAGE",
            stage="KEYFRAME",
        )["media_version_id"]
    )


def test_v2_draft_and_atomic_ready_are_typed_and_conflict_safe(workspace, database) -> None:
    _project, _episode, shot = _workspace_shot(workspace, database, "shot_studio_v2_draft")
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    ready = _ready_fields(profile_id)
    changed = {**ready, "subject_action": "原子保存后的动作"}

    with TestClient(create_app(workspace)) as client:
        saved = client.put(
            f"/api/v2/shots/{shot['id']}/draft",
            json={"fields": ready, "expected_revision_no": 1},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["shot_revision"]["revision_no"] == 2
        assert saved.json()["shot"]["status"] == "DIRECTED"

        conflict = client.put(
            f"/api/v2/shots/{shot['id']}/draft",
            json={"fields": changed, "expected_revision_no": 1},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"

        marked = client.post(
            f"/api/v2/shots/{shot['id']}:mark-ready",
            json={"draft": changed, "freeze": True, "expected_revision_no": 2},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["shot_revision"]["revision_no"] == 3
        assert marked.json()["shot_revision"]["is_frozen"] is True
        assert marked.json()["shot"]["status"] == "READY"

        openapi = client.get("/api/v1/openapi.json").json()["paths"]
        assert "/api/v2/shots/{shot_id}/draft" in openapi
        assert openapi["/api/v2/shots/{shot_id}/draft"]["put"]["operationId"] == "putShotDraftV2"
        assert "/api/v1/projects/shots/{shot_id}/revisions" not in openapi
        assert "/api/v1/projects/shots/{shot_id}:mark-production-ready" not in openapi
        assert "/api/v1/projects/shots/{shot_id}:save-and-ready" not in openapi
        assert client.post(f"/api/v1/projects/shots/{shot['id']}/revisions", json={"fields": ready}).status_code in {404, 405}
        assert client.post(f"/api/v1/projects/shots/{shot['id']}:mark-production-ready").status_code in {404, 405}
        assert client.post(f"/api/v1/projects/shots/{shot['id']}:save-and-ready", json={"fields": ready}).status_code in {404, 405}


def test_v2_shot_generation_intent_is_scoped_audited_and_idempotent(workspace, database) -> None:
    project, episode, shot = _workspace_shot(workspace, database, "shot_studio_v2_intent")
    payload = {
        "purpose": "I2V_PROXY",
        "creative_goal": "角色向镜头走近，保持构图连续",
        "idempotency_key": "shot-generation-intent-command",
    }

    with TestClient(create_app(workspace)) as client:
        first = client.post(f"/api/v2/shots/{shot['id']}/generation-intents", json=payload)
        assert first.status_code == 201, first.text
        assert first.json()["intent"]["owner_id"] == shot["id"]
        assert first.json()["intent"]["project_id"] == project["id"]
        assert first.json()["intent"]["idempotent_replay"] is False

        replay = client.post(f"/api/v2/shots/{shot['id']}/generation-intents", json=payload)
        assert replay.status_code == 201
        assert replay.json()["intent"]["id"] == first.json()["intent"]["id"]
        assert replay.json()["intent"]["idempotent_replay"] is True

        mismatch = client.post(
            f"/api/v2/shots/{shot['id']}/generation-intents",
            json={**payload, "creative_goal": "不同目标"},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        studio = client.get(f"/api/v2/episodes/{episode['id']}/shots/{shot['id']}/studio")
        assert studio.status_code == 200
        assert studio.json()["current_shot"]["generation_intents"] == [first.json()["intent"]]

    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM generation_intents WHERE owner_type='SHOT' AND owner_id=?",
                (shot["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='SHOT_GENERATION_INTENT_CREATED' AND subject_id=?",
                (first.json()["intent"]["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE type='SHOT_GENERATION_INTENT_CREATED' AND subject_id=?",
                (shot["id"],),
            ).fetchone()[0]
            == 1
        )


def test_v2_working_adoption_derives_type_replays_and_rejects_formal(workspace, database) -> None:
    project, _episode, shot = _workspace_shot(workspace, database, "shot_studio_v2_adopt")
    media_id = _keyframe(workspace, database, str(project["id"]), str(shot["id"]))

    with TestClient(create_app(workspace)) as client:
        first = client.post(f"/api/v2/media-versions/{media_id}:adopt")
        assert first.status_code == 200, first.text
        assert first.json()["adoption"]["slot_type"] == "KEYFRAME"
        assert first.json()["adoption"]["selection_type"] == "KEYFRAME"
        assert first.json()["adoption"]["replayed"] is False

        replacement_id = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "replacement")
        replacement = client.post(f"/api/v2/media-versions/{replacement_id}:adopt")
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["adoption"]["id"] == first.json()["adoption"]["id"]
        assert replacement.json()["adoption"]["replayed"] is False

        studio = client.get(f"/api/v2/episodes/{_episode['id']}/shots/{shot['id']}/studio")
        assert studio.status_code == 200, studio.text
        assert studio.json()["current_shot"]["current_media"]["media_version_id"] == replacement_id

        with database.connect() as connection:
            slots = connection.execute(
                "SELECT slot_type,media_version_id FROM shot_working_media_slots WHERE shot_id=?",
                (shot["id"],),
            ).fetchall()
            legacy_writes = connection.execute(
                """SELECT COUNT(*) FROM selections se JOIN media_versions mv ON mv.id=se.media_version_id
                WHERE mv.id IN (?,?)""",
                (media_id, replacement_id),
            ).fetchone()[0]
            selected_pointers = connection.execute(
                """SELECT COUNT(*) FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                WHERE mv.id IN (?,?) AND ma.selected_version_id IS NOT NULL""",
                (media_id, replacement_id),
            ).fetchone()[0]
        assert [(row["slot_type"], row["media_version_id"]) for row in slots] == [("KEYFRAME", replacement_id)]
        assert legacy_writes == 0
        assert selected_pointers == 0

        replay = client.post(f"/api/v2/media-versions/{replacement_id}:adopt")
        assert replay.status_code == 200
        assert replay.json()["adoption"]["id"] == first.json()["adoption"]["id"]
        assert replay.json()["adoption"]["replayed"] is True

        with database.transaction() as connection:
            connection.execute("UPDATE media_versions SET stage='FORMAL' WHERE id=?", (media_id,))
        rejected = client.post(f"/api/v2/media-versions/{media_id}:adopt")
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "FORMAL_VERSION_REVIEW_ONLY"
