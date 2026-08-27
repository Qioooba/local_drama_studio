from __future__ import annotations

import hashlib
import json
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.experiments import ExperimentService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.prompts import PromptService
from local_drama.application.reviews import ReviewService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )


def _image(workspace, database, project_id: str, name: str) -> str:
    source = workspace.work_root / name
    color = hashlib.sha256(name.encode()).hexdigest()[:6]
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=0x{color}:s=160x90:d=0.1", "-frames:v", "1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _unprobed_image(workspace, database, project_id: str, name: str) -> str:
    source = workspace.work_root / name
    source.write_bytes(f"intentionally-unprobeable:{name}".encode())
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _real_image(workspace, database, project_id: str, name: str, size: str) -> str:
    source = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=blue:s={size}:d=0.1", "-frames:v", "1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _shot_image(workspace, database, project_id: str, shot_id: str, name: str) -> dict[str, object]:
    source = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=0.1", "-frames:v", "1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return MediaService(database, workspace).import_file(
        project_id, source, purpose="KEYFRAME", owner_type="SHOT", owner_id=shot_id, media_kind="IMAGE", stage="KEYFRAME"
    )


def _shot_video(workspace, database, project_id: str, shot_id: str, name: str) -> dict[str, object]:
    source = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return MediaService(database, workspace).import_file(
        project_id, source, purpose="SHOT_VIDEO", owner_type="SHOT", owner_id=shot_id, media_kind="VIDEO", stage="PROXY"
    )


def _three_frame_video(workspace, database, project_id: str, name: str) -> dict[str, object]:
    source = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=1:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    return MediaService(database, workspace).import_file(project_id, source, purpose="SHOT_VIDEO", media_kind="VIDEO")


def _plan(
    profile_version_id: str,
    media_version_id: str,
    *,
    parent: str | None = None,
    seed: int = 7,
    prompt_revision_id: str | None = None,
) -> VariantPlan:
    return VariantPlan(
        variant_type="EXACT_REPLAY" if parent else "BASE",
        parent_variant_id=parent,
        branch_reason="replay" if parent else "base",
        prompt_revision_id=prompt_revision_id,
        profile_version_id=profile_version_id,
        parameter_set={"frames": 81, "steps": 20, "SEED": seed},
        seed_policy="EXPLICIT",
        explicit_seed=seed,
        bindings=(VariantInput("FIRST_FRAME", media_version_id),),
    )


def _published_profile(workspace, database) -> str:
    ProfileService(database, workspace.manifest_path).sync_manifest()
    profile_version_id = str(ProfileService(database, workspace.manifest_path).list_profiles()[0]["version_id"])
    with database.transaction() as connection:
        now = "2026-08-13T00:00:00Z"
        workflow_id = str(uuid.uuid4())
        workflow_version_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'Variant test workflow', ?, ?, 'test', 1, 'v2')",
            (workflow_id, f"variant-test-{profile_version_id}", now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, status, contract_json, content_json, package_rel_path,
             node_bindings_json, runtime_contract_json, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'PUBLISHED', '{}', ?, NULL, ?, '{}', ?, ?, ?, 'test', 1, 'v2')""",
            (
                workflow_version_id,
                workflow_id,
                "a" * 64,
                json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "", "seed": 0}}}),
                json.dumps(
                    {
                        "FIRST_FRAME": {"node_id": "1", "input": "image", "type": "image"},
                        "SEED": {"node_id": "1", "input": "seed", "type": "integer"},
                    }
                ),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?, input_contract_json=?, revision=revision+1 WHERE id=?",
            (workflow_version_id, json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), profile_version_id),
        )
    return profile_version_id


def _publish_profile_contract(database, profile_version_id: str) -> None:
    with database.transaction() as connection:
        workflow_version_id = connection.execute("SELECT id FROM workflow_versions WHERE created_by='test' ORDER BY created_at DESC LIMIT 1").fetchone()["id"]
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?, input_contract_json=?, revision=revision+1 WHERE id=?",
            (workflow_version_id, json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), profile_version_id),
        )


def test_list_profiles_exposes_fixed_workflow_production_tier(workspace, database) -> None:
    profile_version_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        workflow_version_id = connection.execute(
            "SELECT workflow_version_id FROM execution_profile_versions WHERE id=?",
            (profile_version_id,),
        ).fetchone()["workflow_version_id"]
        connection.execute(
            "UPDATE workflow_versions SET contract_json=? WHERE id=?",
            (json.dumps({"production_tier": "fast", "dynamic_production_tiers": False}), workflow_version_id),
        )

    listed = ProfileService(database, workspace.manifest_path).list_profiles()
    selected = next(item for item in listed if item["version_id"] == profile_version_id)
    assert selected["workflow_tier"] == "FAST"
    assert selected["dynamic_production_tiers"] is False


def test_resource_estimate_only_uses_explicit_profile_policy_values() -> None:
    declared = GenerationService._resource_estimate(
        {
            "estimated_duration_seconds_per_take": 4.5,
            "estimated_vram_bytes_per_take": 8 * 1024**3,
            "estimated_disk_bytes_per_take": 12 * 1024**2,
        }
    )
    assert declared["status"] == "DECLARED"
    assert declared["source"] == "PROFILE_RESOURCE_POLICY"
    assert declared["per_take"] == {
        "duration_seconds": 4.5,
        "vram_bytes": 8 * 1024**3,
        "disk_bytes": 12 * 1024**2,
    }
    unknown = GenerationService._resource_estimate({"gpu_heavy_concurrency": 1})
    assert unknown["status"] == "UNKNOWN"
    assert unknown["unknown"] == ["duration_seconds", "vram_bytes", "disk_bytes"]
    assert unknown["per_take"] == {"duration_seconds": None, "vram_bytes": None, "disk_bytes": None}


def test_input_slots_accepts_legacy_required_inputs_metadata_envelope() -> None:
    """Pre-slot manifests shipped ``required_inputs`` node lists as metadata.

    The published h3-native-t2v Profile in existing local databases uses this
    exact shape; contract validation must treat those keys as metadata instead
    of failing every preflight with PROFILE_INPUT_CONTRACT_INVALID.
    """
    legacy = {
        "required_inputs": ["RHMiniMaxH3DirectTextEncoderLoader", "RHMiniMaxH3T2VATarget"],
        "transport": "LOOPBACK_HTTP",
    }
    assert GenerationService._input_slots(legacy) == {}

    current = {"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}, "transport": "LOOPBACK_HTTP"}
    assert set(GenerationService._input_slots(current)) == {"FIRST_FRAME"}

    with pytest.raises(DomainRuleError, match="带约束的对象"):
        GenerationService._input_slots({"input_slots": {"FIRST_FRAME": ["not", "an", "object"]}})


def _copy_profile_version(database, source_version_id: str, *, status: str = "PUBLISHED") -> str:
    profile_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = "2026-08-13T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'Profile branch test target', ?, ?, 'test', 1, 'v2')",
            (profile_id, f"profile-branch-{profile_id}", now, now),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id,
             model_bundle_json, input_contract_json, parameter_schema_json, status, created_at, updated_at,
             created_by, revision, schema_version, manifest_sha256, capability_json, worker_policy)
            SELECT ?, ?, 1, capability, runtime_version_id, workflow_version_id, model_bundle_json,
             input_contract_json, parameter_schema_json, ?, ?, ?, 'test', 1, schema_version,
             manifest_sha256, capability_json, worker_policy
            FROM execution_profile_versions WHERE id=?""",
            (version_id, profile_id, status, now, now, source_version_id),
        )
    return version_id


def test_formal_i2v_freezes_current_keyframe_approval_in_binding_and_job_snapshot(workspace, database) -> None:
    project = _project(workspace, database, "i2v_approval_snapshot")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    keyframe = _shot_image(workspace, database, project_id, str(shot["id"]), "approval-snapshot.png")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V_PROXY", "approved first frame only")
    plan = _plan(profile_id, str(keyframe["media_version_id"]))

    with pytest.raises(DomainRuleError) as blocked:
        generation.preflight_variant(str(intent["id"]), plan)
    assert blocked.value.code == "APPROVED_KEYFRAME_REQUIRED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)).fetchone()[0] == 0

    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    approval = reviews.submit_review(
        str(keyframe["media_version_id"]),
        str(template["id"]),
        "APPROVED",
        1,
        [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]],
    )
    preflight = generation.preflight_variant(str(intent["id"]), plan)
    assert preflight["resource_estimate"]["status"] == "UNKNOWN"
    assert preflight["resource_estimate"]["source"] == "PROFILE_RESOURCE_POLICY"
    assert preflight["resource_estimate"]["per_take"] == {"duration_seconds": None, "vram_bytes": None, "disk_bytes": None}
    assert preflight["dependencies"]["approvals"] == [{"role": "FIRST_FRAME", "ordinal": 0, "source_approval_id": approval["id"]}]
    assert (
        preflight["recipe_hash"]
        == hashlib.sha256(
            json.dumps(
                {"execution": generation._execution_recipe(plan), "approvals": preflight["dependencies"]["approvals"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    planned = generation.create_variant(str(intent["id"]), plan)
    with database.connect() as connection:
        planned_binding = connection.execute("SELECT source_approval_id FROM variant_input_bindings WHERE variant_id=?", (planned["id"],)).fetchone()
    assert planned_binding["source_approval_id"] == approval["id"]
    submitted = generation.submit_confirmed_variant(
        str(intent["id"]),
        plan,
        str(preflight["plan_hash"]),
        "i2v-approval-snapshot",
    )
    with database.connect() as connection:
        binding = connection.execute("SELECT source_approval_id FROM variant_input_bindings WHERE variant_id=?", (submitted["variant"]["id"],)).fetchone()
        job = connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (submitted["job"]["id"],)).fetchone()
    assert binding["source_approval_id"] == approval["id"]
    snapshot = json.loads(job["input_snapshot_json"])
    assert snapshot["media_bindings"][0]["source_approval_id"] == approval["id"]
    assert snapshot["recipe_hash"] == submitted["variant"]["recipe_hash"] == preflight["recipe_hash"]


def test_experiment_cell_dispatches_real_child_variant_and_tracks_terminal_job(workspace, database) -> None:
    project = _project(workspace, database, "experiment_dispatch")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    keyframe = _shot_image(workspace, database, project_id, str(shot["id"]), "experiment-dispatch.png")
    profile_id = _published_profile(workspace, database)
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    reviews.submit_review(
        str(keyframe["media_version_id"]),
        str(template["id"]),
        "APPROVED",
        1,
        [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]],
    )
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V_PROXY", "matrix base")
    base_plan = _plan(profile_id, str(keyframe["media_version_id"]), seed=7)
    base_preflight = generation.preflight_variant(str(intent["id"]), base_plan)
    base = generation.submit_confirmed_variant(str(intent["id"]), base_plan, str(base_preflight["plan_hash"]), "experiment-base")

    experiments = ExperimentService(database)
    experiment = experiments.create_plan(str(intent["id"]), "seed matrix", {"seed": [8]})
    assert experiment["axes"]["base_variant_id"] == base["variant"]["id"]
    expanded = experiments.confirm(str(experiment["id"]), str(experiment["plan_hash"]), limit=1)
    orchestration_job_id = str(expanded["expanded"][0]["job_id"])

    dispatched = LocalMediaWorker(database, workspace).run_once("experiment-worker", ["CPU"])
    assert dispatched is not None
    assert dispatched["job"]["id"] == orchestration_job_id
    assert dispatched["result"]["job_state"] == "SUCCEEDED"
    assert dispatched["artifact"]["kind"] == "EXPERIMENT_CELL_REPORT"
    current = experiments.get_plan(str(experiment["id"]))
    cell = current["cells"][0]
    assert cell["variant_id"] != base["variant"]["id"]
    assert cell["job_id"] != orchestration_job_id
    child = JobService(database, workspace).get_job(str(cell["job_id"]))
    child_variant = generation.get_variant(str(cell["variant_id"]))
    assert child["type"] == "GENERATION_VARIANT"
    assert child["state"] == "QUEUED"
    assert child_variant["parent_variant_id"] == base["variant"]["id"]
    assert child_variant["variant_type"] == "RESAMPLE_NEW_SEED"
    assert child_variant["explicit_seed"] == 8

    jobs = JobService(database, workspace)
    for _ in range(2):
        claim = jobs.claim("experiment-gpu", ["GPU_H3"])
        assert claim is not None
        jobs.complete(
            str(claim["attempt"]["id"]),
            str(claim["attempt"]["lease_token"]),
            "experiment-gpu",
            success=True,
        )
    completed = experiments.get_plan(str(experiment["id"]))
    assert completed["status"] == "COMPLETED"
    assert completed["cells"][0]["status"] == "SUCCEEDED"


def test_approved_first_frame_change_previews_and_atomically_propagates_stale(workspace, database) -> None:
    project = _project(workspace, database, "continuity_stale")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first_shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    second_shot = projects.create_shot(str(episode["id"]), "S002", 1000)
    old_frame = _shot_image(workspace, database, project_id, str(second_shot["id"]), "old-frame.png")
    new_frame = MediaService(database, workspace).derive_version(str(old_frame["media_asset_id"]), str(old_frame["media_version_id"]), "KEYFRAME")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(second_shot["id"]), "I2V", "continuity dependency")
    variant = generation.create_variant(str(intent["id"]), _plan(profile_id, str(old_frame["media_version_id"])))
    transition = TimelineService(database, workspace).create_transition_constraint(
        str(first_shot["id"]), str(second_shot["id"]), "END_AT_NEXT_FIRST", enforcement="REQUIRED"
    )
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    checks = [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]

    initial = reviews.preview_approval_impact(str(old_frame["media_version_id"]))
    assert initial["would_mark_stale"] is False
    reviews.submit_review(str(old_frame["media_version_id"]), str(template["id"]), "APPROVED", 1, checks)
    with TestClient(create_app(workspace)) as client:
        preview = client.get(f"/api/v1/media-versions/{new_frame['id']}/approval-impact")
    assert preview.status_code == 200, preview.text
    impact = preview.json()["impact"]
    assert impact["transition_constraint_ids"] == [transition["id"]]
    assert impact["dependent_variant_ids"] == [variant["id"]]

    with pytest.raises(DomainRuleError) as missing_confirmation:
        reviews.submit_review(str(new_frame["id"]), str(template["id"]), "APPROVED", 2, checks)
    assert missing_confirmation.value.code == "CONTINUITY_IMPACT_CONFIRMATION_REQUIRED"
    with pytest.raises(DomainRuleError) as stale_confirmation:
        reviews.submit_review(str(new_frame["id"]), str(template["id"]), "APPROVED", 2, checks, continuity_plan_hash="0" * 64)
    assert stale_confirmation.value.code == "CONTINUITY_IMPACT_STALE"
    with database.connect() as connection:
        assert connection.execute("SELECT is_stale FROM shot_transition_constraints WHERE id=?", (transition["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT is_stale FROM generation_variants WHERE id=?", (variant["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM review_decisions WHERE subject_id=?", (new_frame["id"],)).fetchone()[0] == 0

    result = reviews.submit_review(str(new_frame["id"]), str(template["id"]), "APPROVED", 2, checks, continuity_plan_hash=str(impact["plan_hash"]))
    assert result["continuity_impact"]["would_mark_stale"] is True
    with database.connect() as connection:
        constraint = connection.execute(
            "SELECT compatibility_status, is_stale, stale_reason, boundary_revision FROM shot_transition_constraints WHERE id=?",
            (transition["id"],),
        ).fetchone()
        dependent = connection.execute("SELECT is_stale, stale_reason FROM generation_variants WHERE id=?", (variant["id"],)).fetchone()
        outbox = connection.execute("SELECT payload_json FROM outbox_events WHERE type='CONTINUITY_STALE_PROPAGATED'").fetchone()
        audit = connection.execute("SELECT action FROM audit_events WHERE action='CONTINUITY_STALE_PROPAGATED'").fetchone()
    assert tuple(constraint) == ("STALE", 1, "approved_first_frame_changed", 2)
    assert tuple(dependent) == (1, "approved_first_frame_changed")
    assert json.loads(outbox["payload_json"])["dependent_variant_ids"] == [variant["id"]]
    assert audit is not None
    validation = TimelineService(database, workspace).validate_transition_constraint(str(transition["id"]))
    assert validation["status"] == "STALE"
    assert validation["blockers"][0]["code"] == "TRANSITION_BOUNDARY_STALE"


def test_experimental_selection_does_not_propagate_continuity_stale(workspace, database) -> None:
    project = _project(workspace, database, "continuity_experiment")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first_shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    second_shot = projects.create_shot(str(episode["id"]), "S002", 1000)
    frame = _shot_image(workspace, database, project_id, str(second_shot["id"]), "experimental-frame.png")
    transition = TimelineService(database, workspace).create_transition_constraint(
        str(first_shot["id"]), str(second_shot["id"]), "END_AT_NEXT_FIRST", enforcement="REQUIRED"
    )

    ReviewService(database, workspace).select_version(str(frame["media_version_id"]), "KEYFRAME")
    with database.connect() as connection:
        row = connection.execute("SELECT is_stale, boundary_revision FROM shot_transition_constraints WHERE id=?", (transition["id"],)).fetchone()
        events = connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type='CONTINUITY_STALE_PROPAGATED'").fetchone()[0]
    assert tuple(row) == (0, 1)
    assert events == 0


def test_approved_video_winner_change_stales_last_frame_anchor_transition_and_downstream_variant(workspace, database) -> None:
    project = _project(workspace, database, "winner_anchor_stale")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first_shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    second_shot = projects.create_shot(str(episode["id"]), "S002", 1000)
    old_video = _shot_video(workspace, database, project_id, str(first_shot["id"]), "old-winner.mp4")
    new_video = MediaService(database, workspace).derive_version(str(old_video["media_asset_id"]), str(old_video["media_version_id"]), "PROXY")
    timeline = TimelineService(database, workspace)
    anchor = timeline.create_frame_anchor(str(old_video["media_version_id"]), source_time_us=500_000, role_hint="LAST_FRAME")
    transition = timeline.create_transition_constraint(
        str(first_shot["id"]),
        str(second_shot["id"]),
        "START_FROM_PREVIOUS_LAST",
        from_anchor_id=str(anchor["id"]),
        enforcement="REQUIRED",
    )
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(second_shot["id"]), "I2V", "continue previous motion")
    dependent = generation.create_variant(str(intent["id"]), _plan(profile_id, str(anchor["extracted_media_version_id"])))
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "proxy_video")
    checks = [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]

    reviews.submit_review(str(old_video["media_version_id"]), str(template["id"]), "APPROVED", 1, checks)
    impact = reviews.preview_approval_impact(str(new_video["id"]))
    assert impact["stale_reason"] == "approved_video_winner_changed"
    assert impact["frame_anchor_ids"] == [anchor["id"]]
    assert impact["transition_constraint_ids"] == [transition["id"]]
    assert impact["dependent_variant_ids"] == [dependent["id"]]

    result = reviews.submit_review(
        str(new_video["id"]),
        str(template["id"]),
        "APPROVED",
        2,
        checks,
        continuity_plan_hash=str(impact["plan_hash"]),
    )
    assert result["continuity_impact"]["frame_anchor_count"] == 1
    with database.connect() as connection:
        anchor_row = connection.execute("SELECT is_stale, stale_reason FROM frame_anchors WHERE id=?", (anchor["id"],)).fetchone()
        transition_row = connection.execute(
            "SELECT is_stale, stale_reason, compatibility_status FROM shot_transition_constraints WHERE id=?", (transition["id"],)
        ).fetchone()
        variant_row = connection.execute("SELECT is_stale, stale_reason FROM generation_variants WHERE id=?", (dependent["id"],)).fetchone()
    assert tuple(anchor_row) == (1, "approved_video_winner_changed")
    assert tuple(transition_row) == (1, "approved_video_winner_changed", "STALE")
    assert tuple(variant_row) == (1, "approved_video_winner_changed")

    with TestClient(create_app(workspace)) as client:
        stale_parent = client.post(
            f"/api/v1/generation-variants/{dependent['id']}:derive-plan",
            json={"operation": "RESAMPLE_NEW_SEED", "explicit_seed": 99, "branch_reason": "must reject stale parent"},
        )
        stale_input = client.post(
            "/api/v1/generation-variants:plan",
            json={
                "intent_id": intent["id"],
                "variant_type": "BASE",
                "branch_reason": "must reject stale anchor input",
                "profile_version_id": profile_id,
                "parameter_set": {"frames": 81, "SEED": 100},
                "seed_policy": "EXPLICIT",
                "explicit_seed": 100,
                "bindings": [{"role": "FIRST_FRAME", "media_version_id": anchor["extracted_media_version_id"], "ordinal": 0}],
            },
        )
        stale_transition = client.post(
            "/api/v1/shot-transitions",
            json={
                "from_shot_id": first_shot["id"],
                "to_shot_id": second_shot["id"],
                "constraint_type": "START_FROM_PREVIOUS_LAST",
                "from_anchor_id": anchor["id"],
                "enforcement": "REQUIRED",
            },
        )
    assert stale_parent.status_code == 422
    assert stale_parent.json()["error"]["code"] == "VARIANT_PARENT_STALE"
    assert stale_input.status_code == 422
    assert stale_input.json()["error"]["code"] == "FRAME_ANCHOR_STALE_INPUT"
    assert stale_transition.status_code == 422
    assert stale_transition.json()["error"]["code"] == "FRAME_ANCHOR_STALE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM shot_transition_constraints WHERE from_shot_id=? AND to_shot_id=?",
                (first_shot["id"], second_shot["id"]),
            ).fetchone()[0]
            == 1
        )


def test_variant_lineage_requires_same_intent_and_exact_replay_snapshot(workspace, database) -> None:
    first_project = _project(workspace, database, "variant_first")
    second_project = _project(workspace, database, "variant_second")
    first_project_id = str(first_project["id"])
    second_project_id = str(second_project["id"])
    first_media_id = _image(workspace, database, first_project_id, "first.png")
    second_media_id = _image(workspace, database, second_project_id, "second.png")
    profile_version_id = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    first_intent = service.create_intent(first_project_id, "SHOT", first_project_id, "I2V", "traceable base")
    second_intent = service.create_intent(second_project_id, "SHOT", second_project_id, "I2V", "isolated branch")
    base = service.create_variant(str(first_intent["id"]), _plan(profile_version_id, first_media_id))

    with pytest.raises(DomainRuleError) as error:
        service.create_variant(
            str(second_intent["id"]),
            _plan(profile_version_id, second_media_id, parent=str(base["id"])),
        )
    assert error.value.code == "VARIANT_PARENT_INTENT_MISMATCH"

    with pytest.raises(DomainRuleError) as error:
        service.create_variant(
            str(first_intent["id"]),
            _plan(profile_version_id, first_media_id, parent=str(base["id"]), seed=8),
        )
    assert error.value.code == "EXACT_REPLAY_SNAPSHOT_MISMATCH"

    replay = service.create_variant(
        str(first_intent["id"]),
        _plan(profile_version_id, first_media_id, parent=str(base["id"])),
    )
    assert replay["recipe_hash"] == base["recipe_hash"]
    assert [item["id"] for item in service.lineage(str(replay["id"]))] == [base["id"], replay["id"]]


def test_failed_generation_job_retry_adds_attempt_not_variant_or_take(workspace, database) -> None:
    project = _project(workspace, database, "variant_operational_retry")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "retry-source.png")
    profile_version_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "retry must not resample")
    variant = generation.create_variant(str(intent["id"]), _plan(profile_version_id, media_version_id))
    jobs = JobService(database, workspace)
    job = jobs.create_job(
        project_id,
        "CPU_VARIANT_CONTRACT_TEST",
        "GENERATION_VARIANT",
        str(variant["id"]),
        "CPU",
        {"variant_id": variant["id"], "contract_test": "TC-VAR-003"},
        "tc-var-003-operational-retry",
        max_attempts=1,
    )
    first_claim = jobs.claim("tc-var-003-worker-1", ["CPU"])
    assert first_claim is not None
    assert first_claim["job"]["id"] == job["id"]
    jobs.complete(
        str(first_claim["attempt"]["id"]),
        str(first_claim["attempt"]["lease_token"]),
        "tc-var-003-worker-1",
        success=False,
        error_code="LOCAL_CONTRACT_FAILURE",
        error_detail_redacted="intentional CPU-only failure",
    )

    with TestClient(create_app(workspace)) as client:
        retried = client.post(f"/api/v1/jobs/{job['id']}:retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["job"]["id"] == job["id"]
    assert retried.json()["job"]["state"] == "QUEUED"
    second_claim = jobs.claim("tc-var-003-worker-2", ["CPU"])
    assert second_claim is not None
    assert second_claim["job"]["id"] == job["id"]
    assert second_claim["attempt"]["attempt_no"] == 2
    jobs.complete(
        str(second_claim["attempt"]["id"]),
        str(second_claim["attempt"]["lease_token"]),
        "tc-var-003-worker-2",
        success=True,
    )

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_type='GENERATION_VARIANT' AND subject_id=?", (variant["id"],)).fetchone()[0] == 1
        attempts = connection.execute("SELECT attempt_no, state FROM job_attempts WHERE job_id=? ORDER BY attempt_no", (job["id"],)).fetchall()
        assert [(row["attempt_no"], row["state"]) for row in attempts] == [(1, "FAILED"), (2, "SUCCEEDED")]
        source_asset_id = connection.execute("SELECT media_asset_id FROM media_versions WHERE id=?", (variant["bindings"][0]["media_version_id"],)).fetchone()[
            0
        ]
        assert connection.execute("SELECT COUNT(*) FROM media_versions WHERE media_asset_id=?", (source_asset_id,)).fetchone()[0] == 1


def test_submitted_variant_freezes_workflow_and_local_model_execution_snapshot(workspace, database) -> None:
    """GEN-003: exact replay has an auditable local execution authority."""
    project = _project(workspace, database, "variant_execution_snapshot")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "execution-snapshot.png")
    profile_version_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "freeze execution authority")
    plan = _plan(profile_version_id, media_version_id)
    preflight = generation.preflight_variant(str(intent["id"]), plan)
    submitted = generation.submit_confirmed_variant(str(intent["id"]), plan, str(preflight["plan_hash"]), "execution-snapshot-submit")

    snapshot = submitted["job"]["input_snapshot"]["execution_snapshot"]
    assert snapshot["workflow_version_id"] == preflight["dependencies"]["workflow_version_id"]
    assert snapshot["workflow_content_hash"] == preflight["dependencies"]["workflow_content_hash"]
    assert snapshot["model_bundle_hash"] == preflight["dependencies"]["model_bundle_hash"]
    assert snapshot["snapshot_hash"] == preflight["dependencies"]["profile_execution_snapshot_hash"]
    assert snapshot["manifest_sha256"] == preflight["dependencies"]["profile_manifest_sha256"]
    assert snapshot["model_bundle"].get("artifact_ids") is not None

    replay = generation.derive_variant_plan(str(submitted["variant"]["id"]), "EXACT_REPLAY", branch_reason="GEN-003 exact replay")
    assert replay["diff"]["changed_fields"] == []
    assert replay["dependencies"]["profile_execution_snapshot_hash"] == snapshot["snapshot_hash"]

    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET model_bundle_json=? WHERE id=?",
            (json.dumps({"artifact_ids": ["changed-local-model"]}), profile_version_id),
        )
    with pytest.raises(DomainRuleError) as changed:
        generation.derive_variant_plan(str(submitted["variant"]["id"]), "EXACT_REPLAY", branch_reason="must reject changed model")
    assert changed.value.code == "EXACT_REPLAY_EXECUTION_SNAPSHOT_MISMATCH"


def test_variant_binding_requires_registered_media_from_intent_project(workspace, database) -> None:
    first_project = _project(workspace, database, "variant_media_first")
    second_project = _project(workspace, database, "variant_media_second")
    first_project_id = str(first_project["id"])
    second_media_id = _image(workspace, database, str(second_project["id"]), "foreign.png")
    profile_version_id = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(first_project_id, "SHOT", first_project_id, "I2V", "project-scoped input")

    with pytest.raises(DomainRuleError) as error:
        service.create_variant(
            str(intent["id"]),
            _plan(profile_version_id, second_media_id),
        )
    assert error.value.code == "VARIANT_INPUT_PROJECT_MISMATCH"


def test_variant_api_preflight_is_non_persistent_and_hash_gates_creation(workspace, database) -> None:
    project = _project(workspace, database, "variant_api")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "api.png")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "I2V", "API plan first")
    prompt = PromptService(database).create_prompt(project_id, "SHOT", project_id, "I2V", "API prompt", "walk slowly")
    payload = {
        "intent_id": intent["id"],
        "variant_type": "BASE",
        "branch_reason": "API base",
        "prompt_revision_id": prompt["revision"]["id"],
        "profile_version_id": profile_version_id,
        "parameter_set": {"frames": 81, "steps": 20, "SEED": 7},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [{"role": "FIRST_FRAME", "media_version_id": media_version_id, "ordinal": 0}],
    }

    with TestClient(create_app(workspace)) as client:
        _publish_profile_contract(database, profile_version_id)
        planned = client.post("/api/v1/generation-variants:plan", json=payload)
        assert planned.status_code == 200
        plan = planned.json()["plan"]
        assert plan["would_persist_variant"] is False
        assert plan["would_create_job"] is False
        assert client.get(f"/api/v1/generation-intents/{intent['id']}/variants").json()["items"] == []

        stale = client.post("/api/v1/generation-variants", json={**payload, "plan_hash": "0" * 64})
        assert stale.status_code == 422
        assert stale.json()["error"]["code"] == "VARIANT_PLAN_STALE"
        assert client.get(f"/api/v1/generation-intents/{intent['id']}/variants").json()["items"] == []

        created = client.post("/api/v1/generation-variants", json={**payload, "plan_hash": plan["plan_hash"]})
        assert created.status_code == 201
        variant = created.json()["variant"]
        assert client.get(f"/api/v1/generation-variants/{variant['id']}").status_code == 200
        lineage = client.get(f"/api/v1/generation-variants/{variant['id']}/lineage")
        assert [item["id"] for item in lineage.json()["items"]] == [variant["id"]]
        assert len(client.get(f"/api/v1/generation-intents/{intent['id']}/variants").json()["items"]) == 1


def test_camera_plan_must_match_profile_and_is_frozen_in_job_snapshot(workspace, database) -> None:
    project = _project(workspace, database, "camera_variant_snapshot")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "camera-input.png")
    profile_version_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        row = connection.execute("SELECT parameter_schema_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        schema = json.loads(row["parameter_schema_json"] or "{}")
        schema.setdefault("capabilities", {})["camera"] = {"support": "NATIVE"}
        connection.execute(
            "UPDATE execution_profile_versions SET parameter_schema_json=?, revision=revision+1 WHERE id=?", (json.dumps(schema), profile_version_id)
        )
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "camera snapshot")
    camera = {
        "mode": "NATIVE",
        "shot_type": "CLOSEUP",
        "movement": "PUSH_IN",
        "prompt_text": "",
        "direction": "FORWARD",
        "intensity": 0.5,
        "curve": "LINEAR",
        "profile_version_id": profile_version_id,
    }
    base = _plan(profile_version_id, media_version_id)
    plan = VariantPlan(**{**base.__dict__, "parameter_set": {**base.parameter_set, "camera_plan": camera}})
    preflight = generation.preflight_variant(str(intent["id"]), plan)
    submitted = generation.submit_confirmed_variant(str(intent["id"]), plan, str(preflight["plan_hash"]), "camera-variant-submit")
    with database.connect() as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (submitted["job"]["id"],)).fetchone()[0])
    assert snapshot["semantic_inputs"]["camera_plan"] == camera

    stale_camera = {**camera, "mode": "PROMPT_FALLBACK", "prompt_text": "camera: push in"}
    stale = VariantPlan(**{**base.__dict__, "parameter_set": {**base.parameter_set, "camera_plan": stale_camera}})
    with pytest.raises(DomainRuleError) as error:
        generation.preflight_variant(str(intent["id"]), stale)
    assert error.value.code == "CAMERA_PLAN_RESOLUTION_STALE"


def test_tampered_variant_input_is_blocked_before_variant_or_job_creation(workspace, database) -> None:
    project = _project(workspace, database, "tampered_variant_input")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "tampered-input.png")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "I2V", "reject corrupted source")
    media, source_path = MediaService(database, workspace).content_path(media_version_id)
    source_path.write_bytes(source_path.read_bytes() + b"tamper")
    payload = {
        "intent_id": intent["id"],
        "variant_type": "BASE",
        "branch_reason": "must fail before persistence",
        "profile_version_id": profile_version_id,
        "parameter_set": {"frames": 81, "SEED": 7},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [{"role": "FIRST_FRAME", "media_version_id": media_version_id, "ordinal": 0}],
    }

    with TestClient(create_app(workspace)) as client:
        blocked = client.post("/api/v1/generation-variants:plan", json=payload)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "SOURCE_INTEGRITY_FAILED"
    assert blocked.json()["error"]["details"]["expected_sha256"] == media["sha256"]
    with database.connect() as connection:
        assert connection.execute("SELECT integrity_status FROM media_versions WHERE id=?", (media_version_id,)).fetchone()[0] == "CORRUPT"
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_id=?", (intent["id"],)).fetchone()[0] == 0


def test_variant_create_rechecks_integrity_after_successful_preflight(workspace, database) -> None:
    project = _project(workspace, database, "tampered_after_preflight")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "tampered-after-plan.png")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "I2V", "recheck plan input at commit")
    payload = {
        "intent_id": intent["id"],
        "variant_type": "BASE",
        "branch_reason": "plan then tamper",
        "profile_version_id": profile_version_id,
        "parameter_set": {"frames": 81, "SEED": 7},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [{"role": "FIRST_FRAME", "media_version_id": media_version_id, "ordinal": 0}],
    }

    with TestClient(create_app(workspace)) as client:
        planned = client.post("/api/v1/generation-variants:plan", json=payload)
        assert planned.status_code == 200, planned.text
        plan_hash = planned.json()["plan"]["plan_hash"]
        _, source_path = MediaService(database, workspace).content_path(media_version_id)
        source_path.write_bytes(source_path.read_bytes() + b"tamper-between-plan-and-create")
        blocked = client.post("/api/v1/generation-variants", json={**payload, "plan_hash": plan_hash})

    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "SOURCE_INTEGRITY_FAILED"
    with database.connect() as connection:
        assert connection.execute("SELECT integrity_status FROM media_versions WHERE id=?", (media_version_id,)).fetchone()[0] == "CORRUPT"
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_id=?", (intent["id"],)).fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='GENERATION_VARIANT_CREATED' AND subject_id=?", (intent["id"],)).fetchone()[0]
            == 0
        )


def test_tampered_video_is_blocked_before_frame_anchor_artifact(workspace, database) -> None:
    project = _project(workspace, database, "tampered_anchor_source")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    video = _shot_video(workspace, database, project_id, str(shot["id"]), "tampered-anchor.mp4")
    media, source_path = MediaService(database, workspace).content_path(str(video["media_version_id"]))
    source_path.write_bytes(source_path.read_bytes() + b"tamper")

    with TestClient(create_app(workspace)) as client:
        blocked = client.post(
            f"/api/v1/media-versions/{video['media_version_id']}:create-frame-anchor",
            json={"source_time_us": 500_000, "role_hint": "LAST_FRAME"},
        )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "SOURCE_INTEGRITY_FAILED"
    assert blocked.json()["error"]["details"]["expected_sha256"] == media["sha256"]
    with database.connect() as connection:
        assert connection.execute("SELECT integrity_status FROM media_versions WHERE id=?", (video["media_version_id"],)).fetchone()[0] == "CORRUPT"
        assert connection.execute("SELECT COUNT(*) FROM frame_anchors WHERE source_media_version_id=?", (video["media_version_id"],)).fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM media_assets WHERE purpose='FRAME_ANCHOR' AND owner_id=?", (video["media_version_id"],)).fetchone()[0] == 0
        )


@pytest.mark.parametrize("tampered_role", ["source", "extracted"])
def test_transition_validation_rechecks_existing_anchor_files(workspace, database, tampered_role: str) -> None:
    project = _project(workspace, database, f"anchor_recheck_{tampered_role}")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first_shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    second_shot = projects.create_shot(str(episode["id"]), "S002", 1000)
    video = _shot_video(workspace, database, project_id, str(first_shot["id"]), f"anchor-recheck-{tampered_role}.mp4")
    timeline = TimelineService(database, workspace)
    anchor = timeline.create_frame_anchor(str(video["media_version_id"]), source_time_us=500_000, role_hint="LAST_FRAME")
    transition = timeline.create_transition_constraint(
        str(first_shot["id"]),
        str(second_shot["id"]),
        "START_FROM_PREVIOUS_LAST",
        from_anchor_id=str(anchor["id"]),
        enforcement="REQUIRED",
    )
    tampered_id = str(video["media_version_id"]) if tampered_role == "source" else str(anchor["extracted_media_version_id"])
    _, tampered_path = MediaService(database, workspace).content_path(tampered_id)
    tampered_path.write_bytes(tampered_path.read_bytes() + b"tamper-after-anchor")

    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/shot-transitions/{transition['id']}:validate")
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert validation["status"] == "BLOCKED"
    blocker = next(item for item in validation["blockers"] if item["code"] == "FRAME_ANCHOR_INTEGRITY_FAILED")
    assert blocker["media_role"] == tampered_role
    assert blocker["media_version_id"] == tampered_id
    assert blocker["reason"] == "SOURCE_INTEGRITY_FAILED"
    with database.connect() as connection:
        assert connection.execute("SELECT integrity_status FROM media_versions WHERE id=?", (tampered_id,)).fetchone()[0] == "CORRUPT"
        assert connection.execute("SELECT compatibility_status FROM shot_transition_constraints WHERE id=?", (transition["id"],)).fetchone()[0] == "BLOCKED"


def test_variant_preflight_requires_published_workflow_semantic_binding(workspace, database) -> None:
    project = _project(workspace, database, "variant_workflow_gate")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "workflow.png")
    profile_version_id = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", project_id, "I2V", "workflow binding gate")
    plan = _plan(profile_version_id, media_version_id)

    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET workflow_version_id=NULL WHERE id=?", (profile_version_id,))
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), plan)
    assert error.value.code == "PROFILE_WORKFLOW_REQUIRED"
    assert service.list_variants(str(intent["id"])) == []

    with database.transaction() as connection:
        workflow_version_id = connection.execute("SELECT id FROM workflow_versions WHERE created_by='test' ORDER BY created_at DESC LIMIT 1").fetchone()["id"]
        connection.execute("UPDATE execution_profile_versions SET workflow_version_id=? WHERE id=?", (workflow_version_id, profile_version_id))
        connection.execute("UPDATE workflow_versions SET node_bindings_json='{}' WHERE id=?", (workflow_version_id,))
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), plan)
    assert error.value.code == "WORKFLOW_SEMANTIC_BINDING_REQUIRED"
    assert service.list_variants(str(intent["id"])) == []

    with database.transaction() as connection:
        connection.execute(
            "UPDATE workflow_versions SET node_bindings_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "FIRST_FRAME": {"node_id": "missing", "input": "image"},
                        "SEED": {"node_id": "1", "input": "seed"},
                    }
                ),
                workflow_version_id,
            ),
        )
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), plan)
    assert error.value.code == "WORKFLOW_BINDING_INVALID"
    assert service.list_variants(str(intent["id"])) == []


def test_variant_derive_plan_proves_resample_and_exact_replay_semantics(workspace, database) -> None:
    project = _project(workspace, database, "variant_derive")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "derive.png")
    profile_version_id = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", project_id, "I2V", "derive semantics")
    base = service.create_variant(str(intent["id"]), _plan(profile_version_id, media_version_id))

    resample = service.derive_variant_plan(str(base["id"]), "RESAMPLE_NEW_SEED", explicit_seed=8, branch_reason="new movement")
    assert resample["would_persist_variant"] is False
    assert resample["would_create_job"] is False
    assert resample["diff"]["changed_fields"] == ["explicit_seed", "parameter_set.SEED"]
    assert resample["draft"]["prompt_revision_id"] == base["prompt_revision_id"]
    assert resample["draft"]["profile_version_id"] == base["capability_profile_version_id"]
    assert len(service.list_variants(str(intent["id"]))) == 1

    with pytest.raises(DomainRuleError) as error:
        service.derive_variant_plan(str(base["id"]), "RESAMPLE_NEW_SEED", explicit_seed=7, branch_reason="same seed")
    assert error.value.code == "RESAMPLE_SEED_UNCHANGED"

    with TestClient(create_app(workspace)) as client:
        _publish_profile_contract(database, profile_version_id)
        replay_response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={"operation": "EXACT_REPLAY", "branch_reason": "diagnostic replay"},
        )
        assert replay_response.status_code == 200
        replay_plan = replay_response.json()["plan"]
        assert replay_plan["diff"]["changed_fields"] == []
        created = client.post(
            "/api/v1/generation-variants",
            json={**replay_plan["draft"], "plan_hash": replay_plan["plan_hash"]},
        )
        assert created.status_code == 201
        replay = created.json()["variant"]
        assert replay["recipe_hash"] == base["recipe_hash"]
        assert [item["id"] for item in service.lineage(str(replay["id"]))] == [base["id"], replay["id"]]


def test_provider_random_resubmit_freezes_unique_nonce_and_never_claims_reproducibility(workspace, database) -> None:
    project = _project(workspace, database, "provider_random")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "provider-random.png")
    profile_version_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET parameter_schema_json=?, revision=revision+1 WHERE id=?",
            (json.dumps({"seed": {"support": "OPTIONAL", "determinism": "NONDETERMINISTIC"}}), profile_version_id),
        )
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "provider random resubmit")
    base = generation.create_variant(str(intent["id"]), _plan(profile_version_id, media_version_id))

    with TestClient(create_app(workspace)) as client:
        first_response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={"operation": "RESUBMIT_PROVIDER_RANDOM", "branch_reason": "provider random option one"},
        )
        second_response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={"operation": "RESUBMIT_PROVIDER_RANDOM", "branch_reason": "provider random option two"},
        )
        assert first_response.status_code == 200, first_response.text
        assert second_response.status_code == 200, second_response.text
        first = first_response.json()["plan"]
        second = second_response.json()["plan"]
        assert first["draft"]["seed_policy"] == "PROVIDER_RANDOM"
        assert first["draft"]["explicit_seed"] is None
        assert first["draft"]["provider_random_nonce"] != second["draft"]["provider_random_nonce"]
        assert first["recipe_hash"] != second["recipe_hash"]
        assert first["reproducibility"] == {
            "level": "NON_REPRODUCIBLE",
            "determinism": "NONDETERMINISTIC",
            "seed_support": "OPTIONAL",
            "claim": "不保证重复结果",
        }
        assert first["diff"]["changed_fields"] == ["seed_policy", "explicit_seed", "provider_random_nonce"]
        assert first["would_persist_variant"] is False
        assert first["would_create_job"] is False
        assert len(generation.list_variants(str(intent["id"]))) == 1

        created_response = client.post(
            "/api/v1/generation-variants",
            json={**first["draft"], "plan_hash": first["plan_hash"]},
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()["variant"]
        assert created["provider_random_nonce"] == first["draft"]["provider_random_nonce"]
        assert created["recipe_hash"] == first["recipe_hash"]
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_id=?", (created["id"],)).fetchone()[0] == 0

        with database.transaction() as connection:
            connection.execute(
                "UPDATE execution_profile_versions SET parameter_schema_json=?, revision=revision+1 WHERE id=?",
                (json.dumps({"seed": {"support": "REQUIRED", "determinism": "BEST_EFFORT"}}), profile_version_id),
            )
        blocked = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={"operation": "RESUBMIT_PROVIDER_RANDOM", "branch_reason": "unsupported random"},
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "PROFILE_PROVIDER_RANDOM_UNSUPPORTED"


def test_three_seed_batch_plans_only_seed_diffs_without_persistence(workspace, database) -> None:
    project = _project(workspace, database, "variant_seed_batch")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "seed-batch.png")
    profile_version_id = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", project_id, "I2V", "three seed batch")
    base = service.create_variant(str(intent["id"]), _plan(profile_version_id, media_version_id))

    with TestClient(create_app(workspace)) as client:
        _publish_profile_contract(database, profile_version_id)
        response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-seed-batch",
            json={"seeds": [8, 9, 10], "branch_reason": "three movement options"},
        )
        assert response.status_code == 200
        batch = response.json()["batch"]
        assert batch["count"] == 3
        assert batch["would_persist_variants"] is False
        assert batch["would_create_jobs"] is False
        assert {item["draft"]["explicit_seed"] for item in batch["plans"]} == {8, 9, 10}
        assert all(item["diff"]["changed_fields"] == ["explicit_seed", "parameter_set.SEED"] for item in batch["plans"])
        assert len({item["plan_hash"] for item in batch["plans"]}) == 3
        assert len(service.list_variants(str(intent["id"]))) == 1

        duplicate = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-seed-batch",
            json={"seeds": [8, 8], "branch_reason": "duplicate"},
        )
        assert duplicate.status_code == 422
        assert duplicate.json()["error"]["code"] == "RESAMPLE_SEED_DUPLICATE"


def test_prompt_and_source_branches_only_change_declared_field(workspace, database) -> None:
    project = _project(workspace, database, "variant_branches")
    project_id = str(project["id"])
    first_media_id = _image(workspace, database, project_id, "branch-first.png")
    second_media_id = _image(workspace, database, project_id, "branch-second.png")
    profile_version_id = _published_profile(workspace, database)
    prompt = PromptService(database).create_prompt(project_id, "SHOT", project_id, "I2V", "Shot prompt", "walk slowly", {"camera": "STATIC"})
    prompt_revision_id = str(prompt["revision"]["id"])
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", project_id, "I2V", "branch semantics")
    base = service.create_variant(str(intent["id"]), _plan(profile_version_id, first_media_id, prompt_revision_id=prompt_revision_id))

    prompt_branch_revision = PromptService(database).branch_revision(prompt_revision_id, "walk quickly", {"camera": "STATIC"})
    prompt_branch_plan = VariantPlan(
        **{
            **_plan(profile_version_id, first_media_id, parent=str(base["id"]), prompt_revision_id=str(prompt_branch_revision["id"])).__dict__,
            "variant_type": "PROMPT_BRANCH",
        }
    )
    prompt_branch = service.create_variant(str(intent["id"]), prompt_branch_plan)
    assert base["prompt_revision_id"] == prompt_revision_id
    assert prompt_branch["prompt_revision_id"] == prompt_branch_revision["id"]
    assert PromptService(database).get_revision(prompt_revision_id)["content_text"] == "walk slowly"

    invalid_prompt_branch = VariantPlan(
        **{
            **prompt_branch_plan.__dict__,
            "explicit_seed": 99,
            "parameter_set": {**prompt_branch_plan.parameter_set, "SEED": 99},
        }
    )
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), invalid_prompt_branch)
    assert error.value.code == "PROMPT_BRANCH_SCOPE_INVALID"

    source_plan = VariantPlan(
        **{
            **_plan(profile_version_id, second_media_id, parent=str(base["id"]), prompt_revision_id=prompt_revision_id).__dict__,
            "variant_type": "SOURCE_IMAGE_BRANCH",
        }
    )
    source_branch = service.create_variant(str(intent["id"]), source_plan)
    assert source_branch["bindings"][0]["media_version_id"] == second_media_id
    assert base["bindings"][0]["media_version_id"] == first_media_id
    assert base["status"] == "PLANNED"


def test_prompt_and_source_derive_plan_api_enforces_scope_before_creation(workspace, database) -> None:
    project = _project(workspace, database, "variant_branch_plans")
    project_id = str(project["id"])
    first_media_id = _image(workspace, database, project_id, "branch-plan-first.png")
    second_media_id = _image(workspace, database, project_id, "branch-plan-second.png")
    profile_version_id = _published_profile(workspace, database)
    prompt_service = PromptService(database)
    prompt = prompt_service.create_prompt(project_id, "SHOT", project_id, "I2V", "Plan prompt", "walk slowly")
    parent_prompt_id = str(prompt["revision"]["id"])
    child_prompt = prompt_service.branch_revision(parent_prompt_id, "walk quickly")
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "branch plans")
    base = generation.create_variant(str(intent["id"]), _plan(profile_version_id, first_media_id, prompt_revision_id=parent_prompt_id))

    with TestClient(create_app(workspace)) as client:
        _publish_profile_contract(database, profile_version_id)
        prompt_response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "PROMPT_BRANCH",
                "prompt_revision_id": child_prompt["id"],
                "branch_reason": "faster action",
            },
        )
        assert prompt_response.status_code == 200
        prompt_plan = prompt_response.json()["plan"]
        assert prompt_plan["diff"]["changed_fields"] == ["prompt_revision_id"]
        prompt_created = client.post("/api/v1/generation-variants", json={**prompt_plan["draft"], "plan_hash": prompt_plan["plan_hash"]})
        assert prompt_created.status_code == 201

        source_response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "SOURCE_IMAGE_BRANCH",
                "first_frame_media_version_id": second_media_id,
                "branch_reason": "new source",
            },
        )
        assert source_response.status_code == 200
        source_plan = source_response.json()["plan"]
        assert source_plan["diff"]["changed_fields"] == ["bindings.FIRST_FRAME[0]"]
        source_created = client.post("/api/v1/generation-variants", json={**source_plan["draft"], "plan_hash": source_plan["plan_hash"]})
        assert source_created.status_code == 201

        count = len(generation.list_variants(str(intent["id"])))
        invalid = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "SOURCE_IMAGE_BRANCH",
                "first_frame_media_version_id": second_media_id,
                "prompt_revision_id": child_prompt["id"],
                "branch_reason": "illegal mixed change",
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VARIANT_DERIVATION_SCOPE_INVALID"
        assert len(generation.list_variants(str(intent["id"]))) == count


def test_profile_branch_plan_only_changes_published_profile_without_persistence(workspace, database) -> None:
    project = _project(workspace, database, "profile_branch_plan")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "profile-branch.png")
    source_profile_id = _published_profile(workspace, database)
    target_profile_id = _copy_profile_version(database, source_profile_id)
    candidate_profile_id = _copy_profile_version(database, source_profile_id, status="CANDIDATE")
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "profile comparison")
    base = generation.create_variant(str(intent["id"]), _plan(source_profile_id, media_version_id))

    def counts() -> tuple[int, int]:
        with database.connect() as connection:
            return (
                int(connection.execute("SELECT COUNT(*) FROM generation_variants").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            )

    before_counts = counts()
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "PROFILE_BRANCH",
                "profile_version_id": target_profile_id,
                "branch_reason": "A/B profile comparison",
            },
        )
        assert response.status_code == 200, response.text
        plan = response.json()["plan"]
        assert plan["would_persist_variant"] is False
        assert plan["would_create_job"] is False
        assert plan["draft"]["profile_version_id"] == target_profile_id
        assert plan["diff"]["changed_fields"] == ["profile_version_id"]
        assert plan["diff"]["before"] == {"profile_version_id": source_profile_id}
        assert plan["diff"]["after"] == {"profile_version_id": target_profile_id}
        assert set(plan["diff"]["preserved_fields"]) == {
            "prompt_revision_id",
            "parameter_set",
            "seed_policy",
            "explicit_seed",
            "bindings",
            "provider_random_nonce",
        }
        expected_preserved = {
            "prompt_revision_id": base["prompt_revision_id"],
            "parameter_set": json.loads(str(base["parameter_set_json"])),
            "seed_policy": base["seed_policy"],
            "explicit_seed": base["explicit_seed"],
            "bindings": [{"role": item["role"], "media_version_id": item["media_version_id"], "ordinal": item["ordinal"]} for item in base["bindings"]],
            "provider_random_nonce": base["provider_random_nonce"],
        }
        for field in plan["diff"]["preserved_fields"]:
            assert plan["draft"][field] == expected_preserved[field]
        assert counts() == before_counts

        candidate = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "PROFILE_BRANCH",
                "profile_version_id": candidate_profile_id,
                "branch_reason": "must reject candidate",
            },
        )
        assert candidate.status_code == 409
        assert candidate.json()["error"]["code"] == "PROFILE_NOT_PUBLISHED"

        mixed = client.post(
            f"/api/v1/generation-variants/{base['id']}:derive-plan",
            json={
                "operation": "PROFILE_BRANCH",
                "profile_version_id": target_profile_id,
                "explicit_seed": 99,
                "branch_reason": "must reject mixed scope",
            },
        )
        assert mixed.status_code == 422
        assert mixed.json()["error"]["code"] == "VARIANT_DERIVATION_SCOPE_INVALID"
        assert counts() == before_counts


def test_profile_reroll_readjudicates_camera_plan_to_target_profile(workspace, database) -> None:
    project = _project(workspace, database, "profile_reroll_camera")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "profile-reroll-camera.png")
    source_profile_id = _published_profile(workspace, database)
    target_profile_id = _copy_profile_version(database, source_profile_id)
    camera_schema = json.dumps(
        {
            "seed": {"required": True, "determinism": "profile_declared"},
            "capabilities": {"camera": {"support": "PROMPT_FALLBACK", "prompt_fallback": True}},
        }
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET parameter_schema_json=? WHERE id IN (?,?)",
            (camera_schema, source_profile_id, target_profile_id),
        )
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "camera profile comparison")
    source_plan = _plan(source_profile_id, media_version_id)
    source_plan = VariantPlan(
        **{
            **source_plan.__dict__,
            "parameter_set": {
                **source_plan.parameter_set,
                "camera_plan": {
                    "mode": "PROMPT_FALLBACK",
                    "shot_type": "MEDIUM",
                    "movement": "PUSH_IN",
                    "prompt_text": "camera: push in",
                    "direction": "FORWARD",
                    "intensity": 0.5,
                    "curve": "LINEAR",
                    "profile_version_id": source_profile_id,
                },
            },
        }
    )
    parent = generation.create_variant(str(intent["id"]), source_plan)

    result = generation.reroll_variant(
        str(parent["id"]),
        reason_code="MODEL_COMPARE",
        reason_note="target profile camera adjudication",
        explicit_seed=source_plan.explicit_seed,
        profile_version_id=target_profile_id,
        bindings=None,
        idempotency_key="profile-reroll-camera-target",
    )

    child = result["variant"]
    child_parameters = json.loads(str(child["parameter_set_json"]))
    assert child["variant_type"] == "PROFILE_BRANCH"
    assert child["capability_profile_version_id"] == target_profile_id
    assert child_parameters["camera_plan"] == {
        "mode": "PROMPT_FALLBACK",
        "shot_type": "MEDIUM",
        "movement": "PUSH_IN",
        "prompt_text": "camera: push in",
        "direction": "FORWARD",
        "intensity": 0.5,
        "curve": "LINEAR",
        "profile_version_id": target_profile_id,
    }
    assert result["job"]["execution_profile_version_id"] == target_profile_id


def test_v2_shot_generation_reroll_enforces_scope_and_idempotency(workspace, database) -> None:
    project = _project(workspace, database, "v2_shot_reroll")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT-001", 4_000)
    other_shot = projects.create_shot(str(episode["id"]), "SHOT-002", 4_000)
    media_version_id = _image(workspace, database, project_id, "v2-shot-reroll.png")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V", "v2 shot reroll")
    plan = _plan(profile_id, media_version_id)
    parent = generation.create_variant(str(intent["id"]), plan)
    payload = {
        "operation": "REROLL",
        "stage_code": "VIDEO",
        "parent_variant_id": str(parent["id"]),
        "reason_code": "COMPOSITION_FIX",
        "explicit_seed": 891,
        "idempotency_key": "v2-shot-reroll-command",
    }

    with TestClient(create_app(workspace)) as client:
        rejected = client.post(f"/api/v2/shots/{other_shot['id']}/generations", json=payload)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "SHOT_VARIANT_SCOPE_MISMATCH"

        first = client.post(f"/api/v2/shots/{shot['id']}/generations", json=payload)
        assert first.status_code == 201, first.text
        replay = client.post(f"/api/v2/shots/{shot['id']}/generations", json=payload)
        assert replay.status_code == 201, replay.text
        assert replay.json() == first.json()
        assert first.json()["reroll"] == {"retry": False, "parent_variant_id": parent["id"]}
        assert first.json()["variant"]["status"] == "QUEUED"
        assert first.json()["job"]["state"] == "QUEUED"
        with database.connect() as connection:
            canonical_job = connection.execute(
                """SELECT subject_kind,scope_project_id,scope_episode_id,scope_shot_id,stage_code
                FROM jobs WHERE id=?""",
                (first.json()["job"]["id"],),
            ).fetchone()
        assert dict(canonical_job) == {
            "subject_kind": "GENERATION_VARIANT",
            "scope_project_id": project_id,
            "scope_episode_id": episode["id"],
            "scope_shot_id": shot["id"],
            "stage_code": "VIDEO",
        }

        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert paths["/api/v2/shots/{shot_id}/generations"]["post"]["operationId"] == "submitShotGenerationV2"


def test_v2_shot_base_generation_preflight_submit_scope_revision_and_idempotency(workspace, database) -> None:
    project = _project(workspace, database, "v2_shot_base")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT-001", 4_000)
    other_shot = projects.create_shot(str(episode["id"]), "SHOT-002", 4_000)
    media_version_id = _image(workspace, database, project_id, "v2-shot-base.png")
    profile_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(
        project_id,
        "SHOT",
        str(shot["id"]),
        "I2V",
        "v2 shot base",
    )
    preflight_payload = {
        "operation": "BASE",
        "stage_code": "VIDEO",
        "intent_id": str(intent["id"]),
        "variant_type": "BASE",
        "parent_variant_id": None,
        "branch_reason": "first shot candidate",
        "prompt_revision_id": None,
        "profile_version_id": profile_id,
        "parameter_set": {"frames": 81, "steps": 20, "SEED": 7},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [{"role": "FIRST_FRAME", "media_version_id": media_version_id, "ordinal": 0}],
        "expected_shot_revision": int(shot["revision"]),
    }

    def counts() -> tuple[int, int]:
        with database.connect() as connection:
            return (
                int(connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)).fetchone()[0]),
            )

    with TestClient(create_app(workspace)) as client:
        before = counts()
        rejected_scope = client.post(
            f"/api/v2/shots/{other_shot['id']}/generations:preflight",
            json=preflight_payload,
        )
        assert rejected_scope.status_code == 422
        assert rejected_scope.json()["error"]["code"] == "SHOT_GENERATION_INTENT_SCOPE_MISMATCH"

        preflight_response = client.post(
            f"/api/v2/shots/{shot['id']}/generations:preflight",
            json=preflight_payload,
        )
        assert preflight_response.status_code == 200, preflight_response.text
        preflight = preflight_response.json()["preflight"]
        assert preflight["status"] == "READY"
        assert preflight["shot_id"] == shot["id"]
        assert preflight["shot_revision"] == shot["revision"]
        assert preflight["would_persist_variant"] is False
        assert preflight["would_create_job"] is False
        assert len(preflight["plan_hash"]) == 64
        assert len(preflight["variant_plan_hash"]) == 64
        assert counts() == before

        submit_payload = {
            **preflight_payload,
            "plan_hash": preflight["plan_hash"],
            "idempotency_key": "v2-shot-base-command",
        }
        first = client.post(f"/api/v2/shots/{shot['id']}/generations", json=submit_payload)
        assert first.status_code == 201, first.text
        assert first.json()["operation"] == "BASE"
        assert first.json()["idempotent_replay"] is False
        assert first.json()["variant"]["status"] == "QUEUED"
        assert first.json()["job"]["state"] == "QUEUED"

        replay = client.post(f"/api/v2/shots/{shot['id']}/generations", json=submit_payload)
        assert replay.status_code == 201, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["variant"] == first.json()["variant"]
        assert replay.json()["job"] == first.json()["job"]
        assert counts() == (before[0] + 1, before[1] + 1)

        mismatched = client.post(
            f"/api/v2/shots/{shot['id']}/generations",
            json={**submit_payload, "explicit_seed": 8, "parameter_set": {**submit_payload["parameter_set"], "SEED": 8}},
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert counts() == (before[0] + 1, before[1] + 1)

        mismatched_stage = client.post(
            f"/api/v2/shots/{shot['id']}/generations",
            json={**submit_payload, "stage_code": "SHOT_IMAGE"},
        )
        assert mismatched_stage.status_code == 409
        assert mismatched_stage.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert counts() == (before[0] + 1, before[1] + 1)

        stale_preflight = client.post(
            f"/api/v2/shots/{shot['id']}/generations:preflight",
            json=preflight_payload,
        ).json()["preflight"]
        with database.transaction() as connection:
            connection.execute("UPDATE shots SET revision=revision+1 WHERE id=?", (shot["id"],))
        stale_submit = client.post(
            f"/api/v2/shots/{shot['id']}/generations",
            json={
                **preflight_payload,
                "plan_hash": stale_preflight["plan_hash"],
                "idempotency_key": "v2-shot-base-stale-command",
            },
        )
        assert stale_submit.status_code == 409
        assert stale_submit.json()["error"]["code"] == "SHOT_REVISION_CONFLICT"
        assert counts() == (before[0] + 1, before[1] + 1)

        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert paths["/api/v2/shots/{shot_id}/generations:preflight"]["post"]["operationId"] == "preflightShotGenerationV2"


def test_direct_variant_plan_cannot_bypass_resample_profile_or_random_scope(workspace, database) -> None:
    project = _project(workspace, database, "direct_branch_scope")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "direct-scope.png")
    source_profile_id = _published_profile(workspace, database)
    target_profile_id = _copy_profile_version(database, source_profile_id)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "I2V", "commit scope enforcement")
    base_plan = _plan(source_profile_id, media_version_id)
    base = generation.create_variant(str(intent["id"]), base_plan)

    def counts() -> tuple[int, int]:
        with database.connect() as connection:
            return (
                int(connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)).fetchone()[0]),
            )

    before = counts()
    resample_override = VariantPlan(
        **{
            **base_plan.__dict__,
            "variant_type": "RESAMPLE_NEW_SEED",
            "parent_variant_id": str(base["id"]),
            "branch_reason": "illegal parameter override",
            "parameter_set": {"frames": 99, "steps": 20, "SEED": 8},
            "explicit_seed": 8,
        }
    )
    with pytest.raises(DomainRuleError) as resample_error:
        generation.preflight_variant(str(intent["id"]), resample_override)
    assert resample_error.value.code == "RESAMPLE_SCOPE_INVALID"

    profile_override = VariantPlan(
        **{
            **base_plan.__dict__,
            "variant_type": "PROFILE_BRANCH",
            "parent_variant_id": str(base["id"]),
            "branch_reason": "illegal seed override",
            "profile_version_id": target_profile_id,
            "explicit_seed": 8,
            "parameter_set": {**base_plan.parameter_set, "SEED": 8},
        }
    )
    with pytest.raises(DomainRuleError) as profile_error:
        generation.preflight_variant(str(intent["id"]), profile_override)
    assert profile_error.value.code == "PROFILE_BRANCH_SCOPE_INVALID"
    with pytest.raises(DomainRuleError) as profile_create_error:
        generation.create_variant(str(intent["id"]), profile_override)
    assert profile_create_error.value.code == "PROFILE_BRANCH_SCOPE_INVALID"

    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET parameter_schema_json=?, revision=revision+1 WHERE id=?",
            (json.dumps({"seed": {"support": "OPTIONAL", "determinism": "NONDETERMINISTIC"}}), source_profile_id),
        )
    random_override = VariantPlan(
        **{
            **base_plan.__dict__,
            "variant_type": "RESUBMIT_PROVIDER_RANDOM",
            "parent_variant_id": str(base["id"]),
            "branch_reason": "illegal parameter override",
            "parameter_set": {"frames": 99, "steps": 20},
            "seed_policy": "PROVIDER_RANDOM",
            "explicit_seed": None,
            "provider_random_nonce": str(uuid.uuid4()),
        }
    )
    with pytest.raises(DomainRuleError) as random_error:
        generation.preflight_variant(str(intent["id"]), random_override)
    assert random_error.value.code == "PROVIDER_RANDOM_SCOPE_INVALID"
    assert counts() == before


def test_frame_anchor_resolves_real_first_current_last_pts_and_rejects_out_of_range(workspace, database) -> None:
    project = _project(workspace, database, "frame_anchor_pts")
    project_id = str(project["id"])
    video = _three_frame_video(workspace, database, project_id, "three-frames.mp4")
    video_id = str(video["media_version_id"])
    source = MediaService(database, workspace).get_version(video_id)

    with TestClient(create_app(workspace)) as client:
        first_response = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"position_mode": "FIRST_FRAME", "role_hint": "FIRST_FRAME"},
        )
        current_response = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"source_time_us": 1_500_000, "role_hint": "CURRENT_FRAME"},
        )
        last_response = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"position_mode": "LAST_FRAME", "role_hint": "LAST_FRAME"},
        )
        for response in (first_response, current_response, last_response):
            assert response.status_code == 201, response.text
        first = first_response.json()["frame_anchor"]
        current = current_response.json()["frame_anchor"]
        last = last_response.json()["frame_anchor"]
        assert (first["source_frame_index"], first["resolved_time_us"]) == (0, 0)
        assert (current["requested_time_us"], current["source_frame_index"], current["resolved_time_us"]) == (
            1_500_000,
            1,
            1_000_000,
        )
        assert (last["source_frame_index"], last["resolved_time_us"]) == (2, 2_000_000)
        assert all(item["source_sha256"] == source["sha256"] for item in (first, current, last))
        assert all(item["extraction_method"] == "FFPROBE_PTS_FRAME_INDEX" for item in (first, current, last))
        assert len({first["sha256"], current["sha256"], last["sha256"]}) == 3

        with database.connect() as connection:
            before_assets = int(connection.execute("SELECT COUNT(*) FROM media_assets WHERE project_id=?", (project_id,)).fetchone()[0])
        out_of_range = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"source_time_us": 3_000_000, "role_hint": "CURRENT_FRAME"},
        )
        ambiguous = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"source_frame_index": 1, "position_mode": "LAST_FRAME", "role_hint": "LAST_FRAME"},
        )
        assert out_of_range.status_code == 422
        assert out_of_range.json()["error"]["code"] == "FRAME_ANCHOR_POSITION_OUT_OF_RANGE"
        assert ambiguous.status_code == 422
        assert ambiguous.json()["error"]["code"] == "FRAME_ANCHOR_POSITION_REQUIRED"
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM media_assets WHERE project_id=?", (project_id,)).fetchone()[0] == before_assets
            assert connection.execute("SELECT COUNT(*) FROM frame_anchors").fetchone()[0] == 3
        assert not list((workspace.work_root / "frame_anchor_extract").glob("anchor-*.png"))


def test_prompt_revision_api_freezes_history_and_rejects_unchanged_branch(workspace, database) -> None:
    project = _project(workspace, database, "prompt_api")
    project_id = str(project["id"])
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            "/api/v1/prompts",
            json={
                "project_id": project_id,
                "owner_type": "SHOT",
                "owner_id": project_id,
                "purpose": "I2V",
                "title": "API prompt",
                "content_text": "walk slowly",
                "structured": {"camera": "STATIC"},
            },
        )
        assert created.status_code == 201
        parent = created.json()["revision"]
        unchanged = client.post(
            f"/api/v1/prompt-revisions/{parent['id']}:branch",
            json={"content_text": "walk slowly", "structured": {"camera": "STATIC"}},
        )
        assert unchanged.status_code == 422
        assert unchanged.json()["error"]["code"] == "PROMPT_BRANCH_UNCHANGED"
        branch = client.post(
            f"/api/v1/prompt-revisions/{parent['id']}:branch",
            json={"content_text": "walk quickly", "structured": {"camera": "DOLLY_IN"}},
        )
        assert branch.status_code == 201
        child = branch.json()["revision"]
        assert child["parent_revision_id"] == parent["id"]
        assert child["revision_no"] == 2
        assert child["content_hash"] != parent["content_hash"]
        unchanged_parent = client.get(f"/api/v1/prompt-revisions/{parent['id']}").json()["revision"]
        assert unchanged_parent["content_text"] == "walk slowly"
        assert unchanged_parent["status"] == "FROZEN"


def test_first_last_preflight_blocks_unprobed_and_incompatible_frames(workspace, database) -> None:
    project = _project(workspace, database, "variant_first_last")
    project_id = str(project["id"])
    first_id = _real_image(workspace, database, project_id, "first-real.png", "160x90")
    matching_end_id = _real_image(workspace, database, project_id, "end-real.png", "320x180")
    wrong_end_id = _real_image(workspace, database, project_id, "end-wrong.png", "90x160")
    profile_version_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        profile = connection.execute("SELECT workflow_version_id FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        connection.execute(
            "UPDATE execution_profile_versions SET input_contract_json=? WHERE id=?",
            (json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}, "END_FRAME": {"min": 1, "max": 1}}}), profile_version_id),
        )
        connection.execute(
            "UPDATE workflow_versions SET node_bindings_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "FIRST_FRAME": {"node_id": "1", "input": "image"},
                        "END_FRAME": {"node_id": "1", "input": "end_image"},
                        "SEED": {"node_id": "1", "input": "seed"},
                    }
                ),
                profile["workflow_version_id"],
            ),
        )
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", project_id, "I2V", "first last gate")

    def first_last(end_id: str) -> VariantPlan:
        return VariantPlan(
            variant_type="FIRST_LAST_KEYFRAMES",
            parent_variant_id=None,
            branch_reason="first last",
            prompt_revision_id=None,
            profile_version_id=profile_version_id,
            parameter_set={"frames": 81, "SEED": 7},
            seed_policy="EXPLICIT",
            explicit_seed=7,
            bindings=(VariantInput("FIRST_FRAME", first_id), VariantInput("END_FRAME", end_id)),
        )

    ready = service.preflight_variant(str(intent["id"]), first_last(matching_end_id))
    assert ready["status"] == "READY"
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), first_last(wrong_end_id))
    assert error.value.code == "FRAME_ASPECT_RATIO_MISMATCH"

    unprobed_id = _unprobed_image(workspace, database, project_id, "unprobed.png")
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), first_last(unprobed_id))
    assert error.value.code == "FRAME_DIMENSIONS_REQUIRED"


def test_unsupported_first_last_profile_returns_actionable_capability_error(workspace, database) -> None:
    project = _project(workspace, database, "unsupported_first_last")
    project_id = str(project["id"])
    first_id = _real_image(workspace, database, project_id, "unsupported-first.png", "160x90")
    end_id = _real_image(workspace, database, project_id, "unsupported-end.png", "160x90")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "I2V", "require explicit first-last support")
    payload = {
        "intent_id": intent["id"],
        "variant_type": "FIRST_LAST_KEYFRAMES",
        "branch_reason": "unsupported end frame must be actionable",
        "profile_version_id": profile_version_id,
        "parameter_set": {"frames": 81, "SEED": 7},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [
            {"role": "FIRST_FRAME", "media_version_id": first_id, "ordinal": 0},
            {"role": "END_FRAME", "media_version_id": end_id, "ordinal": 0},
        ],
    }

    with TestClient(create_app(workspace)) as client:
        blocked = client.post("/api/v1/generation-variants:plan", json=payload)
    assert blocked.status_code == 422
    error = blocked.json()["error"]
    assert error["code"] == "PROFILE_CAPABILITY_UNSUPPORTED"
    assert error["details"] == {
        "profile_version_id": profile_version_id,
        "unsupported_roles": ["END_FRAME"],
        "supported_roles": ["FIRST_FRAME"],
        "required_capability": "FIRST_LAST_KEYFRAMES",
    }
    assert "ExecutionProfileVersion" in error["suggested_action"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_id=?", (intent["id"],)).fetchone()[0] == 0


def test_transition_constraint_rejects_cross_project_shots(workspace, database) -> None:
    first_project = _project(workspace, database, "transition_first")
    second_project = _project(workspace, database, "transition_second")
    project_service = ProjectService(database, workspace.projects_root)
    first_episode = project_service.list_episodes(str(project_service.list_seasons(str(first_project["id"]))[0]["id"]))[0]
    second_episode = project_service.list_episodes(str(project_service.list_seasons(str(second_project["id"]))[0]["id"]))[0]
    first_shot = project_service.create_shot(str(first_episode["id"]), "S001", 1000)
    second_shot = project_service.create_shot(str(second_episode["id"]), "S001", 1000)

    with pytest.raises(DomainRuleError) as error:
        TimelineService(database, workspace).create_transition_constraint(str(first_shot["id"]), str(second_shot["id"]), "LAST_TO_FIRST")
    assert error.value.code == "TRANSITION_PROJECT_MISMATCH"


def test_advanced_variant_modes_require_profile_capabilities_and_semantic_roles(workspace, database) -> None:
    """GEN-010: high-level modes cannot fall back to generic input slots."""
    with pytest.raises(DomainRuleError) as missing:
        GenerationService._validate_variant_capability("VIDEO_TO_VIDEO", {}, {"SOURCE_VIDEO"})
    assert missing.value.code == "PROFILE_VARIANT_UNSUPPORTED"

    with pytest.raises(DomainRuleError) as role_missing:
        GenerationService._validate_variant_capability("VIDEO_EXTEND", {"video_extend": {"enabled": True, "support": "NATIVE"}}, {"FIRST_FRAME"})
    assert role_missing.value.code == "VARIANT_INPUT_ROLE_REQUIRED"

    GenerationService._validate_variant_capability("MOTION_CONTROL", {"motion_path": {"enabled": True, "support": "NATIVE"}}, {"MOTION_PATH"})


def test_media_dependency_freezes_source_revision_and_lineage_metadata(workspace, database) -> None:
    project = _project(workspace, database, "source_revision_snapshot")
    project_id = str(project["id"])
    media_id = _image(workspace, database, project_id, "source-revision.png")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(project_id, "SHOT", project_id, "I2V", "revision snapshot")
    plan = _plan(profile_version_id, media_id)
    preflight = GenerationService(database, workspace).preflight_variant(str(intent["id"]), plan)
    dependency = preflight["dependencies"]["media"][0]
    with database.connect() as connection:
        row = connection.execute(
            "SELECT mv.version_no, mv.parent_version_id, mv.stage, ma.owner_type, ma.owner_id, ma.purpose, ma.approved_version_id, ma.selected_version_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?",
            (media_id,),
        ).fetchone()
    assert dependency["id"] == media_id
    assert dependency["version_no"] == int(row["version_no"])
    assert dependency["parent_version_id"] == row["parent_version_id"]
    assert dependency["stage"] == row["stage"]
    assert dependency["owner_type"] == row["owner_type"]
    assert dependency["owner_id"] == row["owner_id"]
    assert dependency["purpose"] == row["purpose"]
    assert dependency["approved_version_id"] == row["approved_version_id"]


def test_shared_boundary_transition_requires_two_immutable_anchors(workspace, database) -> None:
    project = _project(workspace, database, "shared_boundary_contract")
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first = projects.create_shot(str(episode["id"]), "S001", 1000)
    second = projects.create_shot(str(episode["id"]), "S002", 1000)
    timeline = TimelineService(database, workspace)
    constraint = timeline.create_transition_constraint(str(first["id"]), str(second["id"]), "SHARED_BOUNDARY_FRAME", enforcement="REQUIRED")
    validation = timeline.validate_transition_constraint(str(constraint["id"]))
    assert validation["status"] == "BLOCKED"
    assert validation["blockers"] == [{"code": "SHARED_BOUNDARY_ANCHORS_REQUIRED"}]

    with pytest.raises(DomainRuleError) as invalid:
        timeline.create_transition_constraint(str(first["id"]), str(second["id"]), "UNSUPPORTED_BOUNDARY")
    assert invalid.value.code == "TRANSITION_TYPE_INVALID"


def test_generation_accepts_active_cross_project_grant_and_blocks_withdrawn_source(workspace, database) -> None:
    source = _project(workspace, database, "generation_grant_source")
    target = _project(workspace, database, "generation_grant_target")
    source_media_id = _image(workspace, database, str(source["id"]), "grant-generation.png")
    assets = WorkspaceAssetService(database, workspace)
    authorization = assets.authorize_media_version(str(source["id"]), source_media_id)
    grant = assets.create_grant(str(target["id"]), str(authorization["id"]), "DERIVED")
    profile_version_id = _published_profile(workspace, database)
    intent = GenerationService(database, workspace).create_intent(str(target["id"]), "PROJECT", str(target["id"]), "I2V", "shared source")
    plan = _plan(profile_version_id, source_media_id)
    dependencies = GenerationService(database, workspace).preflight_variant(str(intent["id"]), plan)["dependencies"]["media"][0]
    assert dependencies["project_asset_grant"]["id"] == grant["id"]
    assets.revoke_authorization(str(source["id"]), source_media_id, "source withdrawn")
    with pytest.raises(DomainRuleError) as revoked:
        GenerationService(database, workspace).preflight_variant(str(intent["id"]), plan)
    assert revoked.value.code == "ASSET_GRANT_NOT_USABLE"


def test_generation_freezes_exact_identity_pack_in_variant_and_job_then_tracks_stale(workspace, database) -> None:
    project = _project(workspace, database, "identity_pack_generation_snapshot")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S_IDENTITY", 4_000)
    character = StoryAssetService(database, workspace).create_asset(
        project_id,
        "CHARACTER",
        "HERO_IDENTITY",
        "主角身份包",
        "用于生成快照验证",
    )
    assets = WorkspaceAssetService(database, workspace)
    views = {slot_kind: _image(workspace, database, project_id, f"identity-{slot_kind.lower()}.png") for slot_kind in ("FRONT", "LEFT", "RIGHT")}
    for media_version_id in views.values():
        assets.authorize_media_version(project_id, media_version_id)

    packs = CharacterIdentityPackService(database)
    pack = packs.create_pack(project_id, str(character["id"]), "BASE", "基础定妆")
    v1_id = str(pack["versions"][0]["id"])
    for slot_kind, media_version_id in views.items():
        packs.set_version_slot(v1_id, slot_kind, media_version_id)
    v1 = packs.approve_pack_version(v1_id, comment="人工确认角色三视图 v1")
    packs.bind_shot_identity_pack(str(shot["id"]), str(character["id"]), v1_id)

    first_frame_id = _image(workspace, database, project_id, "identity-first-frame.png")
    profile_version_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V", "冻结身份包输入")
    plan = _plan(profile_version_id, first_frame_id)
    preflight = generation.preflight_variant(str(intent["id"]), plan)
    identity_snapshot = preflight["dependencies"]["identity_pack_snapshot"]
    assert identity_snapshot["shot_id"] == shot["id"]
    assert [item["pack_version_id"] for item in identity_snapshot["packs"]] == [v1_id]
    assert {item["slot_kind"] for item in identity_snapshot["packs"][0]["slots"]} >= {
        "FRONT",
        "LEFT",
        "RIGHT",
    }
    assert identity_snapshot["packs"][0]["content_hash"] == v1["content_hash"]

    planned = generation.create_variant(str(intent["id"]), plan)
    assert planned["identity_pack_snapshot"]["snapshot_hash"] == identity_snapshot["snapshot_hash"]
    submitted = generation.submit_confirmed_variant(
        str(intent["id"]),
        plan,
        str(preflight["plan_hash"]),
        "identity-pack-job-snapshot",
    )
    with database.connect() as connection:
        job_row = connection.execute(
            "SELECT input_snapshot_json FROM jobs WHERE id=?",
            (submitted["job"]["id"],),
        ).fetchone()
    job_snapshot = json.loads(str(job_row["input_snapshot_json"]))
    assert job_snapshot["identity_packs"] == identity_snapshot
    assert submitted["variant"]["identity_pack_snapshot"] == identity_snapshot

    new_front_id = _image(workspace, database, project_id, "identity-front-v2.png")
    assets.authorize_media_version(project_id, new_front_id)
    v2 = packs.create_version_draft(str(pack["id"]), from_version_id=v1_id)
    packs.set_version_slot(str(v2["id"]), "FRONT", new_front_id)
    packs.approve_pack_version(str(v2["id"]), comment="人工确认角色三视图 v2")

    planned_after = generation.get_variant(str(planned["id"]))
    submitted_after = generation.get_variant(str(submitted["variant"]["id"]))
    assert planned_after["is_stale"] == 1
    assert submitted_after["is_stale"] == 1
    assert str(planned_after["stale_reason"]).startswith("identity_pack_superseded:")
    assert planned_after["identity_pack_snapshot"]["snapshot_hash"] == identity_snapshot["snapshot_hash"]
    assert submitted_after["identity_pack_snapshot"]["snapshot_hash"] == identity_snapshot["snapshot_hash"]
    with pytest.raises(DomainRuleError) as stale_binding:
        generation.preflight_variant(str(intent["id"]), plan)
    assert stale_binding.value.code == "IDENTITY_PACK_BINDING_STALE"
