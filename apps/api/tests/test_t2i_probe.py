from __future__ import annotations

import base64
import json
import uuid

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.t2i_probe import T2IProbePlanService
from local_drama.domain.errors import DomainRuleError

# 1x1 transparent PNG; ffprobe decodes it as a valid image stream.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _insert_image_profile(database, *, status: str = "DRAFT", capability: str = "IMAGE_CONCEPT") -> str:
    profile_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = "2026-08-23T00:00:00+00:00"
    contracts = {
        "input_contract": {"transport": "LOOPBACK_HTTP", "input_slots": {}},
        "parameter_schema": {
            "seed": {"determinism": "EXPLICIT"},
            "capabilities": {
                "extend": {"support": "UNSUPPORTED", "required_inputs": []},
                "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
                "reference": {"support": "UNSUPPORTED", "required_inputs": []},
                "motion": {"support": "UNSUPPORTED", "required_inputs": []},
            },
        },
        "output_contract": {"media_kind": "IMAGE", "container": "png"},
        "resource_policy": {"gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"},
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)"
            " VALUES (?, 'sdxl-t2i-test', 'SDXL T2I Test', ?, ?, 'test', 1, 'v2')",
            (profile_id, now, now),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
             parameter_schema_json, output_contract_json, resource_policy_json, status, manifest_sha256,
             capability_json, worker_policy, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, '{}', ?, ?, ?, ?, ?, ?, '{}', 'ONE_H3_WORKER_ONE_GPU_TASK', ?, ?, ?, 1, 'v2')""",
            (
                version_id,
                profile_id,
                capability,
                json.dumps(contracts["input_contract"]),
                json.dumps(contracts["parameter_schema"]),
                json.dumps(contracts["output_contract"]),
                json.dumps(contracts["resource_policy"]),
                status,
                "0" * 64,
                now,
                now,
                "test",
            ),
        )
    return version_id


def _insert_published_t2i_workflow(database, *, capability: str = "SDXL_T2I_CANDIDATE", include_first_frame: bool = False) -> str:
    workflow_id = str(uuid.uuid4())
    now = "2026-08-23T00:00:00+00:00"
    with database.transaction() as connection:
        parent_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version)"
            " VALUES (?, 'sdxl_t2i_wf', 'SDXL T2I', ?, ?, 'test', 1, 'v2')",
            (parent_id, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, package_rel_path, content_json, contract_json,
             node_bindings_json, runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'workflow.json', '{}', ?, ?, ?, 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (
                workflow_id,
                parent_id,
                "4" * 64,
                json.dumps({"capability": capability, "input_slots": {}}),
                json.dumps({
                    "PROMPT": {"node_id": "2", "input": "text"},
                    "SEED": {"node_id": "4", "input": "seed"},
                    "OUTPUT_PREFIX": {"node_id": "6", "input": "filename_prefix"},
                    **({"FIRST_FRAME": {"node_id": "7", "input": "image"}} if include_first_frame else {}),
                }),
                json.dumps({"transport": "LOOPBACK_HTTP"}),
                now,
                now,
                now,
            ),
        )
    return workflow_id


def test_multi_view_probe_uses_explicit_turnaround_prompt(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="multi_view_probe", title="Multi view probe", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    draft_id = _insert_image_profile(database, capability="IMAGE_MULTI_VIEW")
    workflow_id = _insert_published_t2i_workflow(
        database, capability="IMAGE_MULTI_VIEW", include_first_frame=True,
    )
    profiles.validate_contract_version(draft_id)
    profiles.validate_compatibility(draft_id)
    source_path = workspace.work_root / "character.png"
    source_path.write_bytes(_PNG_1X1)
    source = MediaService(database, workspace).import_file(
        str(project["id"]), source_path, purpose="ASSET_REFERENCE", media_kind="IMAGE",
    )

    plan = T2IProbePlanService(database).plan(
        str(project["id"]), draft_id, workflow_id, str(source["media_version_id"]),
    )

    assert plan["status"] == "READY"
    prompt = plan["snapshot"]["semantic_inputs"]["PROMPT"]
    assert "exactly three separate full-body views" in prompt
    assert "front view, left side profile, and back view" in prompt
    assert "no extra person" in prompt


@pytest.mark.parametrize("count", [1, 2, 3])
def test_identity_probe_requires_every_explicit_image_and_freezes_each_role(workspace, database, count):
    project = ProjectService(database, workspace.projects_root).create_project(
        code="identity_probe", title="Identity probe", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=120_000, allow_unconfigured_capabilities=True)
    project_id = str(project["id"])
    profile_id = _insert_image_profile(database)
    workflow_id = _insert_published_t2i_workflow(database, capability="IMAGE_CONCEPT")
    refs = {}
    for i in range(1, count + 1):
        source = workspace.work_root / f"identity-{i}.png"
        source.write_bytes(_PNG_1X1 + bytes([i]))
        refs[f"REFERENCE_IMAGE_{i}"] = MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"]
    with database.transaction() as connection:
        bindings = {"PROMPT": {"node_id": "5", "input": "prompt"}, "NEGATIVE_PROMPT": {"node_id": "6", "input": "prompt"}}
        bindings.update({role: {"node_id": str(11 + i), "input": "image"} for i, role in enumerate(refs)})
        connection.execute("UPDATE workflow_versions SET node_bindings_json=? WHERE id=?", (json.dumps(bindings), workflow_id))
    profiles = ProfileService(database, workspace.manifest_path)
    profiles.validate_contract_version(profile_id)
    profiles.validate_compatibility(profile_id)
    service = T2IProbePlanService(database, workspace)
    missing = service.plan(project_id, profile_id, workflow_id, reference_media_version_ids=dict(list(refs.items())[:-1]))
    assert missing["status"] == "BLOCKED"
    assert f"VERIFIED_PROJECT_REFERENCE_IMAGE_REQUIRED:REFERENCE_IMAGE_{count}" in missing["blockers"]
    ready = service.plan(project_id, profile_id, workflow_id, reference_media_version_ids=refs)
    assert ready["status"] == "READY", ready
    assert f"exactly {count} human figure(s)" in ready["snapshot"]["semantic_inputs"]["PROMPT"]
    if count == 1:
        assert "exactly one person, alone" in ready["snapshot"]["semantic_inputs"]["PROMPT"]
    submitted = service.submit(project_id, profile_id, workflow_id, ready["plan_hash"], "identity-proof", reference_media_version_ids=refs)
    assert {x["role"]: x["media_version_id"] for x in submitted["job"]["input_snapshot"]["media_bindings"]} == refs
    assert "NEGATIVE_PROMPT" in submitted["job"]["input_snapshot"]["semantic_inputs"]
    if count > 1:
        swapped = {**refs, "REFERENCE_IMAGE_1": refs["REFERENCE_IMAGE_2"], "REFERENCE_IMAGE_2": refs["REFERENCE_IMAGE_1"]}
        assert service.plan(project_id, profile_id, workflow_id, reference_media_version_ids=swapped)["plan_hash"] != ready["plan_hash"]


def _run_probe_job_to_success(workspace, database, job_id: str, key: str) -> dict:
    jobs = JobService(database, workspace)
    claim = jobs.claim("t2i-worker", ["GPU_H3"])
    assert claim is not None and str(claim["job"]["id"]) == str(job_id)
    output = workspace.work_root / f"{key}.png"
    output.write_bytes(_PNG_1X1)
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", f"{key}.png")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "t2i-worker", success=True)
    return {"artifact": artifact}


def test_t2i_probe_plan_blocked_without_workflow_and_draft(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="t2i_probe_block", title="T2I probe block", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    plan = T2IProbePlanService(database).plan(str(project["id"]), None, None)
    assert plan["status"] == "BLOCKED"
    assert "PUBLISHED_T2I_WORKFLOW_REQUIRED" in plan["blockers"]
    assert "EXPLICIT_IMAGE_PROFILE_CANDIDATE_REQUIRED" in plan["blockers"]

    draft_id = _insert_image_profile(database, status="DRAFT")
    workflow_id = _insert_published_t2i_workflow(database)
    plan2 = T2IProbePlanService(database).plan(str(project["id"]), draft_id, workflow_id)
    assert plan2["status"] == "BLOCKED"
    assert "PROFILE_VALIDATION_REQUIRED" in plan2["blockers"]
    assert "PROFILE_COMPATIBILITY_REQUIRED" in plan2["blockers"]


def test_t2i_probe_full_chain_publishes_image_profile(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="t2i_probe_ok", title="T2I probe ok", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    draft_id = _insert_image_profile(database, status="DRAFT")
    workflow_id = _insert_published_t2i_workflow(database)

    validation = profiles.validate_contract_version(draft_id)
    assert validation["status"] == "PASS"
    compatibility = profiles.validate_compatibility(draft_id)
    assert compatibility["status"] == "PASS"

    service = T2IProbePlanService(database, workspace)
    plan = service.plan(str(project["id"]), draft_id, workflow_id)
    assert plan["status"] == "READY"
    assert plan["snapshot"]["semantic_inputs"]["OUTPUT_PREFIX"].startswith("local_drama/")

    submitted = service.submit(
        str(project["id"]), draft_id, workflow_id, plan["plan_hash"], "t2i-probe-job-key",
    )
    job_id = str(submitted["job"]["id"])

    # finalize requires a succeeded job first.
    with pytest.raises(DomainRuleError, match="尚未成功"):
        service.finalize(str(project["id"]), job_id)

    # Simulate worker execution producing a VERIFIED png artifact.
    _run_probe_job_to_success(workspace, database, job_id, "t2i-evidence")

    finalized = service.finalize(str(project["id"]), job_id)
    published = finalized["profile_version"]
    assert published["status"] == "PUBLISHED"
    contract = json.loads(str(published["capability_json"]))
    assert contract["published"] is True
    assert contract["evidence_media_version_id"]
    assert published["workflow_version_id"] == workflow_id or contract.get("workflow_version_id") == workflow_id

    # Idempotent replay returns the same published version.
    replay = service.finalize(str(project["id"]), job_id)
    assert replay["profile_version"]["id"] == published["id"]


def test_publish_from_evidence_rejects_video_media_for_image_profile(workspace, database) -> None:
    from local_drama.domain.errors import DomainRuleError as _DRE

    project = ProjectService(database, workspace.projects_root).create_project(
        code="t2i_media_guard", title="T2I media guard", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    draft_id = _insert_image_profile(database, status="DRAFT")
    workflow_id = _insert_published_t2i_workflow(database)
    profiles.validate_contract_version(draft_id)
    profiles.validate_compatibility(draft_id)

    # A VIDEO evidence must not satisfy an IMAGE profile publish.
    jobs = JobService(database, workspace)
    with database.connect() as connection:
        profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (draft_id,)).fetchone()
    snapshot = {
        "purpose": "I2V_PROFILE_EVIDENCE_PROBE",
        "workflow_version_id": workflow_id,
        "execution_snapshot": {
            "profile_version_id": draft_id,
            "workflow_version_id": workflow_id,
            "profile_execution_fingerprint": ProfileService._execution_fingerprint(profile),
        },
        "semantic_inputs": {},
        "media_bindings": [],
    }
    job = jobs.create_job(
        str(project["id"]), "PROFILE_EVIDENCE_PROBE", "EXECUTION_PROFILE_VERSION", draft_id, "CPU",
        snapshot, "wrong-kind-job", execution_profile_version_id=draft_id, max_attempts=1,
    )
    assert job is not None
    claim = jobs.claim("kind-worker", ["CPU"])
    assert claim is not None
    (workspace.work_root / "wrong-kind.mp4").write_bytes(b"fake video bytes")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", "wrong-kind.mp4")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "kind-worker", success=True)
    media = MediaService(database, workspace).promote_job_artifact(str(artifact["id"]), media_kind="VIDEO")

    with pytest.raises(_DRE) as error:
        profiles.publish_from_evidence(draft_id, str(media["media_version_id"]), workflow_id)
    assert error.value.code == "PROFILE_EVIDENCE_INVALID"
