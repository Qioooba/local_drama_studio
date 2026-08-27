from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.projects import ProjectService
from local_drama.infrastructure.database.episode_production_repository import SqliteEpisodeProductionReadRepository
from local_drama.main import create_app
from tests.test_director_fields import _published_camera_profile
from tests.test_shot_studio_commands_v2 import _keyframe


def _episode(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="episode_production_v2",
        title="Episode Production v2",
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


def test_episode_production_v2_is_typed_bounded_and_uses_only_canonical_job_stage(workspace, database) -> None:
    project, episode, shot = _episode(workspace, database)
    jobs = JobService(database)
    legacy = jobs.create_job(
        str(project["id"]), "VIDEO_GENERATION", "SHOT", str(shot["id"]), "GPU",
        {}, "episode-production-v2-legacy",
        scope_episode_id=str(episode["id"]), scope_shot_id=str(shot["id"]),
        stage_code="LEGACY_UNCLASSIFIED",
    )
    canonical = jobs.create_job(
        str(project["id"]), "OPAQUE_EXECUTION", "SHOT", str(shot["id"]), "GPU",
        {}, "episode-production-v2-canonical",
        scope_episode_id=str(episode["id"]), scope_shot_id=str(shot["id"]),
        stage_code="VIDEO",
    )

    with TestClient(create_app(workspace)) as client:
        overview = client.get(f"/api/v2/episodes/{episode['id']}/production/overview")
        assert overview.status_code == 200, overview.text
        assert set(overview.json()) == {"overview", "read_only", "request_shape"}
        assert overview.json()["overview"]["active_job_count"] == 1

        response = client.get(f"/api/v2/episodes/{episode['id']}/production/shots?limit=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {
            "items", "cursor", "limit", "total", "next_cursor", "filters",
            "read_only", "request_shape",
        }
        assert payload["limit"] == 1
        row = payload["items"][0]
        assert [stage["stage_code"] for stage in row["stages"]] == [
            "SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC"
        ]
        video = next(stage for stage in row["stages"] if stage["stage_code"] == "VIDEO")
        assert video["state"] == "RUNNING"
        assert video["active_job_id"] == canonical["id"]
        assert video["active_job_id"] != legacy["id"]

        filtered = client.get(
            f"/api/v2/episodes/{episode['id']}/production/shots",
            params={"state": "BLOCKED"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1

        changes = client.get(
            f"/api/v2/episodes/{episode['id']}/production/changes",
            params={"after": 0, "limit": 1},
        )
        assert changes.status_code == 200, changes.text
        assert changes.json()["items"]
        assert changes.json()["has_more"] is True


def test_episode_production_v2_rejects_invalid_filter_and_unknown_episode(workspace, database) -> None:
    _project, episode, _shot = _episode(workspace, database)
    with TestClient(create_app(workspace)) as client:
        invalid = client.get(
            f"/api/v2/episodes/{episode['id']}/production/shots",
            params={"state": "mystery"},
        )
        assert invalid.status_code == 422
        missing = client.get("/api/v2/episodes/missing/production/overview")
        assert missing.status_code == 404
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert paths["/api/v2/episodes/{episode_id}/production/shots"]["get"]["operationId"] == "listEpisodeProductionShotsV2"


def test_episode_production_freshness_follows_only_the_canonical_working_variant(workspace, database) -> None:
    project, episode, shot = _episode(workspace, database)
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    with TestClient(create_app(workspace)) as client:
        with database.connect() as connection:
            profile = connection.execute(
                "SELECT id,capability FROM execution_profile_versions WHERE id=?", (profile_id,)
            ).fetchone()
        assert profile is not None
        intent = GenerationService(database, workspace).create_intent(
            str(project["id"]), "SHOT", str(shot["id"]), str(profile["capability"]), "working lineage"
        )
        current_variant_id = str(uuid.uuid4())
        historical_variant_id = str(uuid.uuid4())
        now = "2026-08-26T00:00:00+00:00"
        with database.transaction() as connection:
            for variant_id, variant_no, stale in (
                (current_variant_id, 1, 0),
                (historical_variant_id, 2, 1),
            ):
                connection.execute(
                    """INSERT INTO generation_variants
                    (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
                     capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
                     recipe_hash,status,is_stale,stale_reason,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,'BASE',NULL,'freshness test',NULL,?,'{}','EXPLICIT',7,?,?,'DRAFT',?,?,?,?,'test',1,'v2')""",
                    (
                        variant_id, intent["id"], variant_no, profile["id"],
                        f"{variant_no}" * 64, f"{variant_no + 2}" * 64, stale,
                        "historical candidate is stale" if stale else None, now, now,
                    ),
                )

        current_media = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "current-lineage")
        historical_media = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "historical-lineage")
        with database.transaction() as connection:
            connection.execute(
                "UPDATE media_assets SET owner_type='GENERATION_VARIANT',owner_id=? WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)",
                (current_variant_id, current_media),
            )
            connection.execute(
                "UPDATE media_assets SET owner_type='GENERATION_VARIANT',owner_id=? WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)",
                (historical_variant_id, historical_media),
            )
        adopted = client.post(f"/api/v2/media-versions/{current_media}:adopt")
        assert adopted.status_code == 200, adopted.text

        current = client.get(f"/api/v2/episodes/{episode['id']}/production/shots").json()["items"][0]
        assert all(edge["state"] == "CURRENT" for edge in current["freshness_edges"])
        assert "historical candidate is stale" not in {edge["reason"] for edge in current["freshness_edges"]}
        assert next(stage for stage in current["stages"] if stage["stage_code"] == "SHOT_IMAGE")["state"] == "READY"

        with database.transaction() as connection:
            connection.execute(
                "UPDATE generation_variants SET is_stale=1,stale_reason='current prompt lineage changed' WHERE id=?",
                (current_variant_id,),
            )
        stale = client.get(f"/api/v2/episodes/{episode['id']}/production/shots").json()["items"][0]
        assert "current prompt lineage changed" in {edge["reason"] for edge in stale["freshness_edges"] if edge["state"] == "STALE"}
        assert next(stage for stage in stale["stages"] if stage["stage_code"] == "SHOT_IMAGE")["state"] == "STALE"
        assert {blocker["code"] for blocker in stale["blockers"]} >= {"WORKING_MEDIA_STALE"}


def test_working_lineage_edges_preserve_prompt_profile_reference_and_asset_state_reasons() -> None:
    row = {
        "media_version_id": "media-1", "media_revision": 4,
        "variant_id": "variant-1", "variant_revision": 2, "is_stale": 0, "stale_reason": None,
        "source_prompt_revision_id": "prompt-1", "source_prompt_revision_no": 1,
        "current_prompt_revision_id": "prompt-2", "current_prompt_revision_no": 2,
        "source_profile_version_id": "profile-1", "source_profile_version_no": 1,
        "current_profile_version_id": "profile-2", "current_profile_version_no": 2,
        "source_reference_id": "reference-1", "source_reference_status": "ARCHIVED",
        "source_reference_revision": 3, "current_reference_id": "reference-2",
        "current_reference_revision": 1, "source_asset_state_id": "state-1",
        "source_asset_state_revision": 1, "current_asset_state_id": "state-2",
        "current_asset_state_revision": 2,
    }
    edges = SqliteEpisodeProductionReadRepository._lineage_edges([row])
    stale_reasons = {edge["reason"] for edge in edges if edge["state"] == "STALE"}
    assert stale_reasons == {
        "PROMPT_CHANGED", "PROFILE_CHANGED", "ASSET_REFERENCE_CHANGED", "ASSET_STATE_CHANGED"
    }
    assert next(edge for edge in edges if edge["reason"] == "PROMPT_CHANGED") == {
        "source_revision": "prompt:prompt-1:r1",
        "target_revision": "prompt:prompt-2:r2",
        "state": "STALE",
        "reason": "PROMPT_CHANGED",
    }


def test_episode_production_v2_run_transitions_are_revision_safe_and_idempotent(workspace, database) -> None:
    project, episode, _shot = _episode(workspace, database)
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="EPISODE_PRODUCTION_V2_TRANSITIONS",
        title="typed transitions",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode-production", "type": "EPISODE_PRODUCTION_TASK", "metadata": {"episode_id": episode["id"]}}],
        batch_items=[{"key": "one", "payload": {"action": "VIDEO_GENERATION", "episode_id": episode["id"]}}],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1 << 30,
        human_gate="BEFORE_RUN",
    )
    run = automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="typed-run-start"
    )
    assert run["status"] == "PAUSED_HITL", run

    with TestClient(create_app(workspace)) as client:
        initial_resume = client.post(
            f"/api/v2/production-runs/{run['id']}:resume",
            json={"expected_revision": run["revision"], "note": "开始执行", "idempotency_key": "typed-initial-resume"},
        )
        assert initial_resume.status_code == 200, initial_resume.text
        running = initial_resume.json()["run"]
        assert running["status"] == "RUNNING"
        pause_payload = {
            "expected_revision": running["revision"],
            "reason": "检查第一批候选",
            "idempotency_key": "typed-pause",
        }
        paused = client.post(f"/api/v2/production-runs/{run['id']}:pause", json=pause_payload)
        assert paused.status_code == 200, paused.text
        paused_run = paused.json()["run"]
        assert paused_run["status"] == "PAUSED_HITL"
        assert paused_run["outcome"] == "PAUSED"
        assert paused_run["idempotent_replay"] is False
        assert paused_run["affected_job_count"] == 0

        replay = client.post(f"/api/v2/production-runs/{run['id']}:pause", json=pause_payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["run"] == {**paused_run, "idempotent_replay": True}

        conflict = client.post(
            f"/api/v2/production-runs/{run['id']}:resume",
            json={"expected_revision": running["revision"], "note": "继续", "idempotency_key": "stale-resume"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "EPISODE_PRODUCTION_RUN_REVISION_CONFLICT"

        resumed = client.post(
            f"/api/v2/production-runs/{run['id']}:resume",
            json={"expected_revision": paused_run["revision"], "note": "继续", "idempotency_key": "typed-resume"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["run"]["status"] == "RUNNING"
        assert resumed.json()["run"]["affected_job_count"] == 0

        cancelled = client.post(
            f"/api/v2/production-runs/{run['id']}:cancel",
            json={
                "expected_revision": resumed.json()["run"]["revision"],
                "idempotency_key": "typed-cancel",
            },
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["run"]["status"] == "CANCELLED"
        with database.connect() as connection:
            events = connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE type='EpisodeProductionRunChanged' AND subject_id=?",
                (run["id"],),
            ).fetchone()[0]
        assert events == 4
