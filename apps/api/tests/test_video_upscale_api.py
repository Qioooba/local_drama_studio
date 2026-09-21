from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.projects import ProjectService
from local_drama.application.video_upscale.sources import EpisodeDeliverySourceService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, *, code: str = "upscale_api", episode_count: int = 3):
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="超分 API 测试",
        episode_count=episode_count,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _register_compose(database, episode_id: str, *, width: int, height: int, approved: bool) -> str:
    now = datetime.now(UTC).isoformat()
    timeline_id = str(uuid.uuid4())
    render_id = str(uuid.uuid4())
    probe = {
        "duration_ms": 1000,
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "avg_frame_rate": "24/1",
                "r_frame_rate": "24/1",
                "pix_fmt": "yuv420p",
            }
        ],
    }
    with database.transaction() as connection:
        revision_no = int(
            connection.execute(
                "SELECT COALESCE(MAX(revision_no),0)+1 FROM timeline_revisions WHERE episode_id=?",
                (episode_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """INSERT INTO timeline_revisions
            (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'{}','{}',?,'FROZEN',?,?,?,1,'v2')""",
            (timeline_id, episode_id, revision_no, hashlib.sha256(timeline_id.encode()).hexdigest(), now, now, "test"),
        )
        connection.execute(
            """INSERT INTO episode_render_versions
            (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,
             duration_ms,mime_type,input_snapshot_json,ffmpeg_command_json,execution_log_text,
             created_at,updated_at,created_by,revision,schema_version,render_kind)
            VALUES (?,?,?,?,?,?, 'VERIFIED',1000,'video/mp4','{}','{}','',?,?,?,1,'v2','COMPOSE')""",
            (
                render_id,
                episode_id,
                timeline_id,
                f"05_outputs/{render_id}.mp4",
                "b" * 64,
                json.dumps(probe),
                now,
                now,
                "test",
            ),
        )
        if approved:
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,
                 supersedes_decision_id,subject_revision,is_stale,stale_reason,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,'test-template','APPROVED','approved',NULL,1,0,NULL,?,?,?,1,'v2')""",
                (str(uuid.uuid4()), render_id, now, now, "test"),
            )
    return render_id


def _register_upscale_candidate(
    database,
    episode_id: str,
    root_id: str,
    *,
    approved: bool,
    width: int = 1920,
    height: int = 1080,
) -> str:
    now = datetime.now(UTC).isoformat()
    render_id = str(uuid.uuid4())
    with database.transaction() as connection:
        timeline_id = connection.execute(
            "SELECT timeline_revision_id FROM episode_render_versions WHERE id=?",
            (root_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO episode_render_versions
            (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,duration_ms,mime_type,
             input_snapshot_json,ffmpeg_command_json,execution_log_text,render_kind,parent_render_version_id,
             upscale_run_id,derivation_fingerprint,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'VERIFIED',1000,'video/mp4','{}','{}','','SUPER_RESOLUTION',?,?,?,?,?,?,1,'video-upscale.v1')""",
            (
                render_id,
                episode_id,
                timeline_id,
                f"05_outputs/upscale/{render_id}.mp4",
                uuid.uuid4().hex * 2,
                json.dumps({"streams": [{"codec_type": "video", "width": width, "height": height}]}),
                root_id,
                str(uuid.uuid4()),
                uuid.uuid4().hex * 2,
                now,
                now,
                "test",
            ),
        )
        connection.execute(
            """INSERT INTO machine_check_runs
            (id,subject_type,subject_id,policy_version,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'EPISODE_RENDER_VERSION',?,'video-upscale-output.v1','PASS',?,?,?,1,'video-upscale-qc.v1')""",
            (str(uuid.uuid4()), render_id, now, now, "test"),
        )
        if approved:
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,
                 supersedes_decision_id,subject_revision,is_stale,stale_reason,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,'test-template','APPROVED','approved',NULL,1,0,NULL,?,?,?,1,'v2')""",
                (str(uuid.uuid4()), render_id, now, now, "test"),
            )
    return render_id


def test_builtin_presets_and_project_default_are_versioned(workspace, database) -> None:
    project = _project(workspace, database, episode_count=1)
    with TestClient(create_app(workspace)) as client:
        presets = client.get("/api/v1/video-upscale-presets", params={"project_id": project["id"]})
        assert presets.status_code == 200, presets.text
        items = presets.json()["items"]
        assert {item["code"] for item in items} == {
            "ANIME_1080_DETAIL",
            "ANIME_1080_LOW_VRAM",
            "ANIME_1080_STANDARD",
            "GENERAL_1080",
        }
        assert all(item["builtin"] for item in items)
        assert all(not item["available"] for item in items)

        current = client.get(f"/api/v1/projects/{project['id']}/video-upscale-settings")
        assert current.status_code == 200, current.text
        inherited = current.json()["settings"]
        assert inherited["revision"] == 0 and inherited["inherited"] is True
        assert inherited["preset_id"] == "builtin-upscale-anime-1080-standard"

        updated = client.put(
            f"/api/v1/projects/{project['id']}/video-upscale-settings",
            json={
                "preset_version_id": "builtin-upscale-anime-1080-low-vram-v1",
                "pipeline_overrides": {},
                "model_overrides": {"tile_size": 256},
                "expected_revision": 0,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["settings"]["revision"] == 1
        assert updated.json()["settings"]["inherited"] is False
        assert updated.json()["settings"]["overrides"]["model"] == {"tile_size": 256}

        stale = client.put(
            f"/api/v1/projects/{project['id']}/video-upscale-settings",
            json={
                "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                "pipeline_overrides": {},
                "model_overrides": {},
                "expected_revision": 0,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"


def test_delivery_episode_projection_requires_latest_compose_approval(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episodes = service.list_episodes(str(season["id"]))
    first_render = _register_compose(database, str(episodes[0]["id"]), width=854, height=480, approved=True)
    _register_compose(database, str(episodes[1]["id"]), width=480, height=832, approved=False)

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/delivery-episodes")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page"]["total"] == 3
        first, second, third = payload["items"]
        assert first["selectable"] is True
        assert first["compose"]["id"] == first_render
        assert first["recommended_source"]["kind"] == "EPISODE_RENDER"
        assert first["recommended_source"]["geometry"]["target"] == {
            "width": 1920,
            "height": 1080,
            "orientation": "LANDSCAPE",
            "fit": "CONTAIN",
        }
        assert second["selectable"] is False
        assert second["blockers"][0]["code"] == "UPSCALE_SOURCE_APPROVAL_REQUIRED"
        assert third["selectable"] is False
        assert third["blockers"][0]["code"] == "UPSCALE_SOURCE_COMPOSE_REQUIRED"

        selection = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "ALL_ELIGIBLE", "episode_ids": []},
        )
        assert selection.status_code == 200, selection.text
        resolved = selection.json()["selection"]
        assert resolved["count"] == 1
        assert resolved["items"][0]["episode_id"] == episodes[0]["id"]
        assert len(resolved["selection_hash"]) == 64
        assert len(resolved["blocked"]) == 2


def test_delivery_file_is_preferred_only_after_human_approval(workspace, database) -> None:
    project = _project(workspace, database, episode_count=1)
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    compose_id = _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
    now = datetime.now(UTC).isoformat()
    package_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO delivery_packages
            (id,episode_render_version_id,target_version_id,rel_path,status,manifest_sha256,
             human_review_status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?, 'VERIFIED',?,'PENDING',?,?,?,1,'v3')""",
            (package_id, compose_id, str(uuid.uuid4()), "06_delivery/pending", "e" * 64, now, now, "test"),
        )
        connection.execute(
            """INSERT INTO delivery_files
            (id,delivery_package_id,rel_path,sha256,byte_size) VALUES (?,?,?,?,?)""",
            (str(uuid.uuid4()), package_id, "06_delivery/pending/episode.mp4", "f" * 64, 1024),
        )

    with TestClient(create_app(workspace)) as client:
        pending = client.get(f"/api/v1/projects/{project['id']}/delivery-episodes")
        assert pending.status_code == 200, pending.text
        pending_item = pending.json()["items"][0]
        assert pending_item["recommended_source"]["kind"] == "EPISODE_RENDER"
        assert [warning["code"] for warning in pending_item["warnings"]] == [
            "SOURCE_DELIVERY_HUMAN_APPROVAL_REQUIRED"
        ]

        with database.transaction() as connection:
            connection.execute(
                "UPDATE delivery_packages SET human_review_status='APPROVED',revision=revision+1 WHERE id=?",
                (package_id,),
            )
        approved = client.get(f"/api/v1/projects/{project['id']}/delivery-episodes")
        assert approved.status_code == 200, approved.text
        assert approved.json()["items"][0]["recommended_source"]["kind"] == "DELIVERY_FILE"
        assert approved.json()["items"][0]["recommended_source"]["delivery_human_review_status"] == "APPROVED"

        preferred = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "EXPLICIT", "episode_ids": [episode["id"]], "source_policy": "PREFER_FINAL_DELIVERY"},
        )
        compose_only = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "EXPLICIT", "episode_ids": [episode["id"]], "source_policy": "APPROVED_COMPOSE"},
        )
        assert preferred.status_code == 200, preferred.text
        assert compose_only.status_code == 200, compose_only.text
        assert preferred.json()["selection"]["items"][0]["source"]["kind"] == "DELIVERY_FILE"
        assert compose_only.json()["selection"]["items"][0]["source"]["kind"] == "EPISODE_RENDER"
        assert preferred.json()["selection"]["selection_hash"] != compose_only.json()["selection"]["selection_hash"]


def test_new_super_resolution_candidate_does_not_replace_latest_compose(workspace, database) -> None:
    project = _project(workspace, database, episode_count=1)
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    compose_id = _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        timeline_id = connection.execute(
            "SELECT timeline_revision_id FROM episode_render_versions WHERE id=?", (compose_id,)
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO episode_render_versions
            (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,
             duration_ms,mime_type,input_snapshot_json,ffmpeg_command_json,execution_log_text,
             created_at,updated_at,created_by,revision,schema_version,render_kind,parent_render_version_id,
             upscale_run_id,derivation_fingerprint)
            VALUES (?,?,?,?,?,'{}','VERIFIED',1000,'video/mp4','{}','{}','',?,?,?,1,'video-upscale.v1',
                    'SUPER_RESOLUTION',?,?,?)""",
            (
                str(uuid.uuid4()),
                str(episode["id"]),
                timeline_id,
                "05_outputs/upscaled.mp4",
                "c" * 64,
                now,
                now,
                "test",
                compose_id,
                str(uuid.uuid4()),
                "d" * 64,
            ),
        )

    with TestClient(create_app(workspace)) as client:
        projection = client.get(f"/api/v1/projects/{project['id']}/delivery-episodes")
        assert projection.status_code == 200, projection.text
        item = projection.json()["items"][0]
        assert item["compose"]["id"] == compose_id
        assert item["derived_version_count"] == 1

        status = client.get(f"/api/v1/episodes/{episode['id']}/timeline-status")
        assert status.status_code == 200, status.text
        assert status.json()["status"]["renders"]["latest"]["id"] == compose_id
        assert status.json()["status"]["renders"]["derived_count"] == 1


def test_newer_upscale_candidates_do_not_change_adopted_version_or_latest_compose(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_multiple_candidates", episode_count=1)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    root = _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
    first = _register_upscale_candidate(database, str(episode["id"]), root, approved=True)
    target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "fhd-landscape",
        "横屏 1080p",
        "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/fhd", "width": 1920, "height": 1080, "subtitles": "NONE"},
    )
    item = {
        "episode_id": episode["id"],
        "target_slot": target["version_id"],
        "selected_render_id": first,
        "expected_selection_revision": 0,
    }
    with TestClient(create_app(workspace)) as client:
        plan = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [item]},
        ).json()["plan"]
        committed = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": [item], "plan_hash": plan["plan_hash"]},
        )
        assert committed.status_code == 200, committed.text

        _register_upscale_candidate(database, str(episode["id"]), root, approved=True)
        _register_upscale_candidate(database, str(episode["id"]), root, approved=False)
        versions = client.get(f"/api/v1/episodes/{episode['id']}/delivery-versions").json()["versions"]
        status = client.get(f"/api/v1/episodes/{episode['id']}/timeline-status").json()["status"]

    assert versions["current_root_compose_render_id"] == root
    assert len([render for render in versions["items"] if render["render_kind"] == "SUPER_RESOLUTION"]) == 3
    assert versions["selections"][0]["selected_render_id"] == first
    assert status["renders"]["latest"]["id"] == root
    assert status["renders"]["derived_count"] == 3

    replacement_root = _register_compose(
        database,
        str(episode["id"]),
        width=854,
        height=480,
        approved=True,
    )
    assert replacement_root != root
    with TestClient(create_app(workspace)) as client:
        stale_versions = client.get(f"/api/v1/episodes/{episode['id']}/delivery-versions").json()["versions"]
        stale_plan = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [{**item, "expected_selection_revision": 1}]},
        )
    selected = next(render for render in stale_versions["items"] if render["id"] == first)
    assert selected["source_current"] is False and selected["adoptable"] is False
    assert stale_versions["selections"][0]["selected_render_id"] == first
    assert stale_plan.status_code == 422
    assert stale_plan.json()["error"]["code"] == "UPSCALE_SOURCE_STALE"
    with pytest.raises(DomainRuleError) as delivery_blocked:
        BackgroundOperationService(database, workspace).delivery_plan(first, target["version_id"])
    assert delivery_blocked.value.code == "UPSCALE_SOURCE_STALE"


def test_plan_is_durable_cpu_job_and_reports_missing_runtime_and_source(workspace, database) -> None:
    project = _project(workspace, database, episode_count=1)
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)

    with TestClient(create_app(workspace)) as client:
        selected = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "EXPLICIT", "episode_ids": [episode["id"]]},
        )
        assert selected.status_code == 200, selected.text
        selection = selected.json()["selection"]
        created = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-plans",
            json={
                "selection_hash": selection["selection_hash"],
                "episode_ids": [episode["id"]],
                "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                "execution_profile_version_id": None,
            },
        )
        assert created.status_code == 202, created.text
        first = created.json()
        assert first["plan"]["status"] == "CHECKING"
        assert first["job"]["type"] == "VIDEO_UPSCALE_PREFLIGHT"

        replay = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-plans",
            json={
                "selection_hash": selection["selection_hash"],
                "episode_ids": [episode["id"]],
                "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                "execution_profile_version_id": None,
            },
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["plan"]["id"] == first["plan"]["id"]
        assert replay.json()["job"]["id"] == first["job"]["id"]
        assert replay.json()["idempotent_replay"] is True

        work = LocalMediaWorker(database, workspace).run_once("upscale-preflight-test", ["CPU"])
        assert work is not None
        assert work["result"]["job_state"] == "SUCCEEDED"
        checked = client.get(f"/api/v1/video-upscale-plans/{first['plan']['id']}")
        assert checked.status_code == 200, checked.text
        plan = checked.json()["plan"]
        assert plan["status"] == "BLOCKED"
        codes = {blocker["code"] for blocker in plan["items"][0]["blockers"]}
        assert "UPSCALE_SOURCE_FILE_MISSING" in codes
        assert "UPSCALE_PROFILE_REQUIRED" in codes


def test_approved_qc_passed_upscale_version_requires_explicit_atomic_adoption(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_adopt", episode_count=1)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    compose_id = _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
    now = datetime.now(UTC).isoformat()
    derived_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    qc_id = str(uuid.uuid4())
    target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "fhd-landscape",
        "横屏 1080p",
        "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/fhd", "width": 1920, "height": 1080, "subtitles": "NONE"},
    )
    with TestClient(create_app(workspace)) as client:
        with database.transaction() as connection:
            timeline_id = connection.execute(
                "SELECT timeline_revision_id FROM episode_render_versions WHERE id=?", (compose_id,)
            ).fetchone()[0]
            template_id = connection.execute(
                "SELECT id FROM review_templates WHERE code='episode_upscale' ORDER BY version_no DESC LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO episode_render_versions
                (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,duration_ms,mime_type,
                 input_snapshot_json,ffmpeg_command_json,execution_log_text,render_kind,parent_render_version_id,
                 upscale_run_id,derivation_fingerprint,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,'VERIFIED',1000,'video/mp4','{}','{}','','SUPER_RESOLUTION',?,?,?,?,?,?,1,'video-upscale.v1')""",
                (
                    derived_id,
                    episode["id"],
                    timeline_id,
                    "05_outputs/upscale/result.mp4",
                    "e" * 64,
                    json.dumps({"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}),
                    compose_id,
                    str(uuid.uuid4()),
                    "f" * 64,
                    now,
                    now,
                    "test",
                ),
            )
            connection.execute(
                """INSERT INTO machine_check_runs
                (id,subject_type,subject_id,policy_version,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,'video-upscale-output.v1','PASS',?,?,?,1,'video-upscale-qc.v1')""",
                (qc_id, derived_id, now, now, "test"),
            )
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,subject_revision,is_stale,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,?,'APPROVED','checked',1,0,?,?,?,1,'v2')""",
                (approval_id, derived_id, template_id, now, now, "test"),
            )

        versions = client.get(f"/api/v1/episodes/{episode['id']}/delivery-versions")
        assert versions.status_code == 200, versions.text
        derived = next(item for item in versions.json()["versions"]["items"] if item["id"] == derived_id)
        assert derived["adoptable"] is True
        assert versions.json()["versions"]["selections"] == []

        request_item = {
            "episode_id": episode["id"],
            "target_slot": target["version_id"],
            "selected_render_id": derived_id,
            "expected_selection_revision": 0,
        }
        planned = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [request_item]},
        )
        assert planned.status_code == 200, planned.text
        plan_hash = planned.json()["plan"]["plan_hash"]
        committed = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": [request_item], "plan_hash": plan_hash},
        )
        assert committed.status_code == 200, committed.text
        selection = committed.json()["commit"]["selections"][0]
        assert selection["selected_render_id"] == derived_id
        assert selection["approval_id"] == approval_id
        assert selection["revision"] == 1

        stale_commit = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": [request_item], "plan_hash": plan_hash},
        )
        assert stale_commit.status_code == 409
        assert stale_commit.json()["error"]["code"] == "REVISION_CONFLICT"


def test_batch_adoption_is_atomic_until_every_item_is_valid(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_adopt_atomic", episode_count=2)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    roots = [
        _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
        for episode in episodes
    ]
    candidates = [
        _register_upscale_candidate(database, str(episodes[0]["id"]), roots[0], approved=True),
        _register_upscale_candidate(database, str(episodes[1]["id"]), roots[1], approved=False),
    ]
    target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "fhd-landscape",
        "横屏 1080p",
        "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/fhd", "width": 1920, "height": 1080, "subtitles": "NONE"},
    )
    items = [
        {
            "episode_id": episode["id"],
            "target_slot": target["version_id"],
            "selected_render_id": candidate,
            "expected_selection_revision": 0,
        }
        for episode, candidate in zip(episodes, candidates, strict=True)
    ]

    with TestClient(create_app(workspace)) as client:
        blocked = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": items},
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["code"] == "EPISODE_RENDER_APPROVAL_REQUIRED"
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM episode_delivery_selections").fetchone()[0] == 0

        now = datetime.now(UTC).isoformat()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,
                 supersedes_decision_id,subject_revision,is_stale,stale_reason,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,'test-template','APPROVED','approved',NULL,1,0,NULL,?,?,?,1,'v2')""",
                (str(uuid.uuid4()), candidates[1], now, now, "test"),
            )
        planned = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": items},
        )
        assert planned.status_code == 200, planned.text
        committed = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": items, "plan_hash": planned.json()["plan"]["plan_hash"]},
        )
        assert committed.status_code == 200, committed.text
        assert {item["selected_render_id"] for item in committed.json()["commit"]["selections"]} == set(candidates)


def test_episode_upscale_batch_review_is_per_episode_and_atomic(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_review_atomic", episode_count=2)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    roots = [
        _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
        for episode in episodes
    ]
    candidates = [
        _register_upscale_candidate(database, str(episode["id"]), root, approved=False)
        for episode, root in zip(episodes, roots, strict=True)
    ]
    project_root = workspace.resolve_project_root(str(project["root_rel"]))
    for index, candidate in enumerate(candidates, start=1):
        payload = f"independent-upscale-review-{index}".encode()
        rel_path = f"05_outputs/upscale/{candidate}.mp4"
        output_path = project_root / rel_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        with database.transaction() as connection:
            connection.execute(
                "UPDATE episode_render_versions SET sha256=? WHERE id=?",
                (hashlib.sha256(payload).hexdigest(), candidate),
            )

    with TestClient(create_app(workspace)) as client:
        templates = client.get("/api/v1/review-templates").json()["items"]
        template = next(item for item in templates if item["code"] == "episode_upscale")
        checks = [
            {"item_id": item["id"], "result": "PASS", "comment": f"逐集确认 {item['id']}"}
            for item in template["items"]
        ]
        incomplete_items = [
            {
                "render_id": candidate,
                "template_version_id": template["id"],
                "decision": "APPROVED",
                "expected_subject_revision": 1,
                "checks": checks if index == 0 else checks[:-1],
                "comment": f"第 {index + 1} 集独立复核",
            }
            for index, candidate in enumerate(candidates)
        ]
        before = None
        with database.connect() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM review_decisions WHERE subject_id IN (?,?)",
                tuple(candidates),
            ).fetchone()[0]
        blocked = client.post(
            f"/api/v1/projects/{project['id']}/episode-render-review-batches:plan",
            json={"items": incomplete_items},
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["code"] == "REVIEW_CHECKS_INCOMPLETE"
        assert blocked.json()["error"]["details"]["render_id"] == candidates[1]
        with database.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM review_decisions WHERE subject_id IN (?,?)",
                tuple(candidates),
            ).fetchone()[0] == before

        complete_items = [
            {**item, "checks": [{**check, "comment": f"第 {index + 1} 集 {check['item_id']}"} for check in checks]}
            for index, item in enumerate(incomplete_items)
        ]
        planned = client.post(
            f"/api/v1/projects/{project['id']}/episode-render-review-batches:plan",
            json={"items": complete_items},
        )
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]
        assert plan["would_create_review_count"] == 2
        assert plan["mutated_reviews"] is False
        with database.transaction() as connection:
            connection.execute(
                "UPDATE episode_render_versions SET revision=revision+1 WHERE id=?",
                (candidates[1],),
            )
        stale = client.post(
            "/api/v1/episode-render-review-batches:commit",
            json={"plan_token": plan["plan_token"], "plan_hash": plan["plan_hash"]},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "EPISODE_RENDER_REVIEW_BATCH_STALE"
        with database.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM review_decisions WHERE subject_id IN (?,?)",
                tuple(candidates),
            ).fetchone()[0] == before

        complete_items[1]["expected_subject_revision"] = 2
        replanned = client.post(
            f"/api/v1/projects/{project['id']}/episode-render-review-batches:plan",
            json={"items": complete_items},
        )
        assert replanned.status_code == 200, replanned.text
        repaired_plan = replanned.json()["plan"]
        committed = client.post(
            "/api/v1/episode-render-review-batches:commit",
            json={
                "plan_token": repaired_plan["plan_token"],
                "plan_hash": repaired_plan["plan_hash"],
            },
        )
        assert committed.status_code == 200, committed.text
        result = committed.json()["commit"]
        assert result["status"] == "COMMITTED"
        assert result["review_count"] == 2
        assert result["atomic"] is True
        with database.connect() as connection:
            reviews = connection.execute(
                """SELECT subject_id,subject_revision,decision FROM review_decisions
                WHERE subject_id IN (?,?) ORDER BY subject_id""",
                tuple(candidates),
            ).fetchall()
            assert len(reviews) == before + 2
            assert {str(row["subject_id"]) for row in reviews} == set(candidates)
            assert {str(row["decision"]) for row in reviews} == {"APPROVED"}


def test_delivery_selection_rejects_geometry_mismatch_before_mutation(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_adopt_geometry", episode_count=1)
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    root = _register_compose(database, str(episode["id"]), width=854, height=480, approved=True)
    candidate = _register_upscale_candidate(database, str(episode["id"]), root, approved=True)
    landscape_target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "fhd-landscape",
        "横屏 1080p",
        "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/landscape", "width": 1920, "height": 1080, "subtitles": "NONE"},
    )
    target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "fhd-portrait",
        "竖屏 1080p",
        "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/portrait", "width": 1080, "height": 1920, "subtitles": "NONE"},
    )
    item = {
        "episode_id": episode["id"],
        "target_slot": target["version_id"],
        "selected_render_id": candidate,
        "expected_selection_revision": 0,
    }

    with TestClient(create_app(workspace)) as client:
        blocked = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [item]},
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["code"] == "DELIVERY_SELECTION_TARGET_GEOMETRY_MISMATCH"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM episode_delivery_selections").fetchone()[0] == 0

    matching_item = {**item, "target_slot": landscape_target["version_id"]}
    with TestClient(create_app(workspace)) as client:
        planned = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [matching_item]},
        )
        assert planned.status_code == 200, planned.text
        committed = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": [matching_item], "plan_hash": planned.json()["plan"]["plan_hash"]},
        )
        assert committed.status_code == 200, committed.text
    operations = BackgroundOperationService(database, workspace)
    with pytest.raises(DomainRuleError) as ordinary_delivery:
        operations.delivery_plan(candidate, landscape_target["version_id"])
    assert ordinary_delivery.value.code == "DELIVERY_TARGET_VERSION_INACTIVE"
    batch_delivery = operations.delivery_plan(
        candidate,
        landscape_target["version_id"],
        allow_inactive_target=True,
    )
    assert batch_delivery["inputs"]["allow_inactive_target"] is True


def test_delivery_episode_projection_is_bounded_for_two_hundred_episodes(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_perf_200", episode_count=200)
    select_counts: list[int] = []

    class CountingDatabase:
        @contextmanager
        def connect(self):
            statements: list[str] = []
            with database.connect() as connection:
                connection.set_trace_callback(
                    lambda sql: statements.append(sql) if sql.lstrip().upper().startswith("SELECT") else None
                )
                try:
                    yield connection
                finally:
                    connection.set_trace_callback(None)
                    select_counts.append(len(statements))

        def transaction(self, *, immediate: bool = True):
            return database.transaction(immediate=immediate)

    service = EpisodeDeliverySourceService(CountingDatabase())
    durations: list[float] = []
    for _ in range(10):
        started = perf_counter()
        page = service.list_episodes(str(project["id"]), limit=50)
        durations.append(perf_counter() - started)
        assert len(page["items"]) == 50
        assert page["page"]["total"] == 200
        assert page["page"]["next_cursor"] == 50
    assert max(select_counts) <= 6
    p95 = sorted(durations)[-1]
    assert p95 <= 0.5, f"warm list target exceeded: p95={p95:.3f}s, selects={select_counts}"


def test_cleanup_api_requires_the_frozen_preview_hash(workspace, database) -> None:
    project = _project(workspace, database, code="upscale_cleanup_api", episode_count=1)
    with TestClient(create_app(workspace)) as client:
        preview = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-cleanup:plan",
            json={"retention_days": 7},
        )
        assert preview.status_code == 200, preview.text
        plan = preview.json()["plan"]
        assert plan["candidate_count"] == 0
        assert plan["mutated"] is False
        stale = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-cleanup:commit",
            json={
                "retention_days": 7,
                "eligible_before": plan["eligible_before"],
                "plan_hash": "0" * 64,
            },
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "UPSCALE_CLEANUP_PLAN_STALE"
