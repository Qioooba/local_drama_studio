"""Prepare and serve the post-launch full-workflow simulation environment.

Copies the production SQLite (online backup) and the selected project tree
into an isolated root, publishes a native comfy_extras H3 I2V workflow,
creates and evidence-publishes an I2V capability profile (driving one real
H3 job through the controlled loopback worker), then serves the real FastAPI
app on a loopback port for the click-through browser simulation.  The
production database and project tree are never touched; the worker writes
into the repo's controlled work/comfy-production sandbox.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.comfy_jobs import (
    ComfyGenerationService,  # type: ignore[import-not-found]
)
from local_drama.application.h3_workflows import (
    H3WorkflowFactory,  # type: ignore[import-not-found]
)
from local_drama.application.jobs import JobService  # type: ignore[import-not-found]
from local_drama.application.media import MediaService  # type: ignore[import-not-found]
from local_drama.application.profiles import (
    ProfileService,  # type: ignore[import-not-found]
)
from local_drama.application.projects import (
    ProjectService,  # type: ignore[import-not-found]
)
from local_drama.application.reviews import (
    ReviewService,  # type: ignore[import-not-found]
)
from local_drama.application.workflows import (
    WorkflowService,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.domain.generation_contracts import (
    resolve_camera_plan,  # type: ignore[import-not-found]
)
from local_drama.infrastructure.comfy import (
    ComfyClient,  # type: ignore[import-not-found]
)
from local_drama.infrastructure.database.sqlite import (
    Database,  # type: ignore[import-not-found]
)
from local_drama.main import create_app  # type: ignore[import-not-found]

from scripts.migrate import migrate

PRODUCTION_DB = ROOT / "data" / "local_drama.sqlite3"
PRODUCTION_PROJECTS = ROOT / "projects"
PROJECT_CODE = "g2_smoke2"


def build_settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=ROOT / "work" / "comfy-production" / "output",
        comfy_input_root=ROOT / "work" / "comfy-production" / "input",
        port=port,
    )


def _backup_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)


def _snapshot_project(source_db: Path, source_projects: Path, target_projects: Path) -> tuple[str, str]:
    with sqlite3.connect(f"file:{source_db.resolve().as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT id, root_rel FROM projects WHERE code=? ORDER BY updated_at DESC LIMIT 1", (PROJECT_CODE,)).fetchone()
        if row is None:
            raise RuntimeError(f"SIM_PROJECT_MISSING: {PROJECT_CODE}")
        project_id, root_rel = str(row[0]), str(row[1])
    source_project = (source_projects / root_rel).resolve()
    if not source_project.is_dir():
        raise RuntimeError(f"SIM_PROJECT_ROOT_MISSING: {source_project}")
    target_project = (target_projects / root_rel).resolve()
    target_project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_project, target_project, dirs_exist_ok=True)
    return project_id, root_rel


def _publish_native_i2v_workflow(database: Database, settings: Settings, server: str, keyframe_rel: str) -> dict[str, Any]:
    factory = H3WorkflowFactory(settings)
    prompt = "固定广角镜头，细雨中的北方乡村老屋，保持空间方向和道具连续，克制的单一动作。"
    workflow = factory.build_fl2va(
        prompt,
        first_frame=keyframe_rel,
        seed=20260817,
        duration_seconds=5.0,
        aspect_ratio="auto",
        filename_prefix="sim_env/SHOT_001",
        sigma_points=10,
    )
    workflows = WorkflowService(database, settings)
    version = workflows.register_package(
        "sim_native_i2v",
        "Simulation native I2V",
        workflow,
        {"capability": "H3_FL2VA_I2V_CANDIDATE", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}},
        {"FIRST_FRAME": {"node_id": "5", "input": "image"}},
        {"local_only": True, "network_policy": "LOOPBACK_ONLY"},
    )
    client = ComfyClient(server, settings.comfy_output_root)
    validation = workflows.validate_against_comfy(str(version["id"]), client)
    if validation["status"] != "PASS":
        raise RuntimeError(f"SIM_WORKFLOW_VALIDATION_FAILED: {json.dumps(validation, ensure_ascii=False)[:1000]}")
    published = workflows.publish(str(version["id"]), str(validation["validation_id"]))
    return {"workflow_version_id": str(version["id"]), "published_status": published["status"]}


def _create_i2v_profile_candidate(database: Database, workflow_version_id: str) -> str:
    profile_id = f"sim-i2v-{uuid.uuid4().hex[:8]}"
    version_id = f"{profile_id}-v1"
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES (?,?,?)", (profile_id, "sim-native-i2v", "Simulation native I2V profile"))
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id,
             model_bundle_json, input_contract_json, parameter_schema_json, output_contract_json,
             resource_policy_json, status, manifest_sha256, capability_json, worker_policy,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?,?,1,'I2V',NULL,?,?,?,?,?,?,'DRAFT',?,?,?,?,?,?,1,'v2')""",
            (
                version_id,
                profile_id,
                workflow_version_id,
                json.dumps({"model_ref": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "provider_kind": "LOCAL_COMFY", "network_allowed": False}),
                json.dumps(
                    {
                        "transport": "LOOPBACK_HTTP",
                        "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}},
                        "media_kinds": {"FIRST_FRAME": {"media_kind": "IMAGE", "required": True}},
                        "capabilities": {"seed": {"determinism": "EXPLICIT"}},
                    }
                ),
                json.dumps(
                    {
                        "seed": {"type": "integer", "determinism": "EXPLICIT"},
                        "prompt": {"type": "string"},
                        "capabilities": {
                            "camera": {"support": "NATIVE", "prompt_fallback": False},
                            "extend": {"support": "UNSUPPORTED", "required_inputs": []},
                            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
                            "reference": {"support": "UNSUPPORTED", "required_inputs": []},
                            "motion": {"support": "UNSUPPORTED", "required_inputs": []},
                        },
                    }
                ),
                json.dumps({"media_kind": "VIDEO", "container": "mp4", "codec": "h264"}),
                json.dumps({"channel": "GPU_H3", "gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"}),
                None,
                json.dumps({"provider_kind": "LOCAL_COMFY", "network_allowed": False}),
                "ONE_H3_WORKER_ONE_GPU_TASK",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                "sim-operator",
            ),
        )
    return version_id


def _run_evidence_job(
    database: Database,
    settings: Settings,
    workflow_version_id: str,
    project_id: str,
    shot_id: str,
    server: str,
    timeout_seconds: int,
    *,
    job_type: str = "I2V",
    semantic_inputs: dict[str, Any] | None = None,
    media_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    jobs = JobService(database, settings)
    job = jobs.create_job(
        project_id,
        job_type,
        "SHOT",
        shot_id,
        "GPU_H3",
        {
            "workflow_version_id": workflow_version_id,
            "semantic_inputs": semantic_inputs or {},
            "media_bindings": media_bindings or [],
        },
        f"sim-evidence-{uuid.uuid4().hex[:8]}",
    )
    service = ComfyGenerationService(database, settings)
    submission = service.submit_next("sim-evidence-worker")
    if submission is None:
        raise RuntimeError("SIM_EVIDENCE_JOB_NOT_CLAIMABLE")
    attempt_id = str(submission["attempt"]["id"])
    deadline = time.monotonic() + timeout_seconds
    result: dict[str, Any] | None = None
    poll_states: list[str] = []
    while time.monotonic() < deadline:
        result = service.poll_attempt(attempt_id, "sim-evidence-worker")
        state = str(result.get("status"))
        poll_states.append(state)
        if state in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(5)
    if not result or result.get("status") != "SUCCEEDED":
        raise RuntimeError(json.dumps({"poll_states": poll_states, "result": result}, ensure_ascii=False))
    artifact = result["artifacts"][0]
    promoted = MediaService(database, settings).promote_job_artifact(
        str(artifact["id"]), purpose="SHOT_VIDEO", media_kind="VIDEO", stage="FORMAL"
    )
    return {
        "job_id": str(job["id"]),
        "attempt_id": attempt_id,
        "prompt_id": str(submission["prompt_id"]),
        "artifact_id": str(artifact["id"]),
        "media_version_id": str(promoted["media_version_id"]),
        "poll_states": poll_states,
    }


def _publish_native_t2v_workflow(database: Database, settings: Settings, server: str) -> dict[str, Any]:
    factory = H3WorkflowFactory(settings)
    prompt = "细雨中的北方乡村老屋，一名女子缓步走进院子，保持空间方向和道具连续。"
    workflow = factory.build_t2va(
        prompt,
        seed=20260818,
        duration_seconds=5.0,
        aspect_ratio="9:16",
        filename_prefix="sim_env_t2v/EVIDENCE",
        sigma_points=10,
    )
    workflows = WorkflowService(database, settings)
    version = workflows.register_package(
        "sim_native_t2v",
        "Simulation native T2V",
        workflow,
        {"capability": "H3_T2VA_CANDIDATE", "local_only": True},
        {
            "PROMPT": {"node_id": "8", "input": "prompt"},
            "SEED": {"node_id": "5", "input": "noise_seed"},
            "FRAME_COUNT": {"node_id": "8", "input": "length"},
            "OUTPUT_PREFIX": {"node_id": "14", "input": "filename_prefix"},
        },
        {"local_only": True, "network_policy": "LOOPBACK_ONLY"},
    )
    client = ComfyClient(server, settings.comfy_output_root)
    validation = workflows.validate_against_comfy(str(version["id"]), client)
    if validation["status"] != "PASS":
        raise RuntimeError(f"SIM_T2V_WORKFLOW_VALIDATION_FAILED: {json.dumps(validation, ensure_ascii=False)[:1000]}")
    published = workflows.publish(str(version["id"]), str(validation["validation_id"]))
    return {"workflow_version_id": str(version["id"]), "published_status": published["status"]}


def _create_t2v_profile_candidate(database: Database, workflow_version_id: str) -> str:
    profile_id = f"sim-t2v-{uuid.uuid4().hex[:8]}"
    version_id = f"{profile_id}-v1"
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES (?,?,?)", (profile_id, "sim-native-t2v", "Simulation native T2V profile"))
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id,
             model_bundle_json, input_contract_json, parameter_schema_json, output_contract_json,
             resource_policy_json, status, manifest_sha256, capability_json, worker_policy,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?,?,1,'T2V',NULL,?,?,?,?,?,?,'DRAFT',?,?,?,?,?,?,1,'v2')""",
            (
                version_id,
                profile_id,
                workflow_version_id,
                json.dumps({"model_ref": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "provider_kind": "LOCAL_COMFY", "network_allowed": False}),
                json.dumps(
                    {
                        "transport": "LOOPBACK_HTTP",
                        "input_slots": {},
                        "capabilities": {"seed": {"determinism": "EXPLICIT"}},
                    }
                ),
                json.dumps(
                    {
                        "seed": {"type": "integer", "determinism": "EXPLICIT"},
                        "prompt": {"type": "string"},
                        "capabilities": {
                            "camera": {"support": "NATIVE", "prompt_fallback": False},
                            "extend": {"support": "UNSUPPORTED", "required_inputs": []},
                            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
                            "reference": {"support": "UNSUPPORTED", "required_inputs": []},
                            "motion": {"support": "UNSUPPORTED", "required_inputs": []},
                        },
                    }
                ),
                json.dumps({"media_kind": "VIDEO", "container": "mp4", "codec": "h264"}),
                json.dumps({"channel": "GPU_H3", "gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"}),
                None,
                json.dumps({"provider_kind": "LOCAL_COMFY", "network_allowed": False}),
                "ONE_H3_WORKER_ONE_GPU_TASK",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                "sim-operator",
            ),
        )
    return version_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="isolated writable root")
    parser.add_argument("--port", type=int, default=3225)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--timeout-seconds", type=int, default=1500)
    parser.add_argument("--serve-only", action="store_true", help="serve an already-prepared sim root without re-preparing")
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root, args.port)
    settings.ensure_roots()
    if args.serve_only:
        if not (root / "data" / "local_drama.sqlite3").is_file():
            raise RuntimeError(f"SIM_ROOT_INVALID: {root}")
        print(f"sim env serving existing root {root} port={args.port}", flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
        return
    if not PRODUCTION_DB.is_file():
        raise RuntimeError(f"SIM_PRODUCTION_DB_MISSING: {PRODUCTION_DB}")
    _backup_database(PRODUCTION_DB, settings.database_path)
    project_id, root_rel = _snapshot_project(PRODUCTION_DB, PRODUCTION_PROJECTS, settings.projects_root)
    migrate(settings.database_path)
    database = Database(settings.database_path)

    with database.connect() as connection:
        keyframe = connection.execute(
            """SELECT mv.id AS media_version_id FROM media_assets ma
            JOIN media_versions mv ON mv.id=ma.approved_version_id
            WHERE ma.project_id=? AND ma.purpose='KEYFRAME' AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
            ORDER BY mv.created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        shot = connection.execute(
            "SELECT id FROM shots WHERE episode_id IN (SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?) ORDER BY order_key,id LIMIT 1",
            (project_id,),
        ).fetchone()
    if keyframe is None or shot is None:
        raise RuntimeError(f"SIM_PREREQUISITES_MISSING keyframe={keyframe is not None} shot={shot is not None}")
    keyframe_id = str(keyframe["media_version_id"])
    shot_id = str(shot["id"])

    workflow = _publish_native_i2v_workflow(database, settings, args.server, "sim-keyframe.png")
    candidate_version_id = _create_i2v_profile_candidate(database, workflow["workflow_version_id"])
    profiles = ProfileService(database, settings)
    validation = profiles.validate_contract_version(candidate_version_id)
    if validation["status"] != "PASS":
        raise RuntimeError(f"SIM_PROFILE_VALIDATION_FAILED: {json.dumps(validation, ensure_ascii=False)[:800]}")
    compatibility = profiles.validate_compatibility(candidate_version_id)
    if compatibility["status"] != "PASS":
        raise RuntimeError(f"SIM_PROFILE_COMPATIBILITY_FAILED: {json.dumps(compatibility, ensure_ascii=False)[:800]}")
    evidence = _run_evidence_job(
        database, settings, workflow["workflow_version_id"], project_id, shot_id, args.server, args.timeout_seconds,
        media_bindings=[{"role": "FIRST_FRAME", "media_version_id": keyframe_id, "ordinal": 0}],
    )
    published = profiles.publish_from_evidence(candidate_version_id, evidence["media_version_id"], workflow["workflow_version_id"])
    published_profile_version_id = str(published["id"])

    # T2V ("one-sentence video") native chain: publish workflow + evidence profile.
    t2v_workflow = _publish_native_t2v_workflow(database, settings, args.server)
    t2v_candidate = _create_t2v_profile_candidate(database, t2v_workflow["workflow_version_id"])
    t2v_validation = profiles.validate_contract_version(t2v_candidate)
    if t2v_validation["status"] != "PASS":
        raise RuntimeError(f"SIM_T2V_PROFILE_VALIDATION_FAILED: {json.dumps(t2v_validation, ensure_ascii=False)[:800]}")
    t2v_compatibility = profiles.validate_compatibility(t2v_candidate)
    if t2v_compatibility["status"] != "PASS":
        raise RuntimeError(f"SIM_T2V_PROFILE_COMPATIBILITY_FAILED: {json.dumps(t2v_compatibility, ensure_ascii=False)[:800]}")
    t2v_evidence = _run_evidence_job(
        database, settings, t2v_workflow["workflow_version_id"], project_id, shot_id, args.server, args.timeout_seconds,
        job_type="T2V",
        semantic_inputs={
            "PROMPT": "细雨中的北方乡村老屋，一名女子缓步走进院子，保持空间方向和道具连续。",
            "SEED": 20260818,
            "FRAME_COUNT": 124,
            "OUTPUT_PREFIX": "sim_env_t2v/EVIDENCE",
        },
        media_bindings=[],
    )
    t2v_published = profiles.publish_from_evidence(t2v_candidate, t2v_evidence["media_version_id"], t2v_workflow["workflow_version_id"])
    t2v_profile_version_id = str(t2v_published["id"])

    # Give the shot a structured CameraPlan resolved against the published profile
    # so the UI generation preflight is enabled (real service round-trip).
    project_service = ProjectService(database, settings.projects_root)
    with database.connect() as connection:
        current = connection.execute(
            "SELECT fields_json FROM shot_revisions WHERE id=(SELECT current_revision_id FROM shots WHERE id=?)",
            (shot_id,),
        ).fetchone()
    fields = json.loads(str(current["fields_json"])) if current else {}
    plan = resolve_camera_plan(
        native_supported=True,
        prompt_fallback_supported=False,
        shot_type="MEDIUM",
        movement="PUSH_IN",
        prompt_text="",
        direction="FORWARD",
        intensity=0.5,
        curve="LINEAR",
        profile_version_id=published_profile_version_id,
    )
    fields["camera_plan"] = plan.to_dict()
    project_service.create_shot_revision(shot_id, fields, freeze=True)

    # The new shot revision marks the shot-owned keyframe approval stale (correct
    # propagation).  Re-approve the keyframe through the real review service so
    # the UI generation gate sees a current APPROVED keyframe again.
    reviews = ReviewService(database, settings)
    reviews.ensure_templates()
    with database.connect() as connection:
        asset_revision = connection.execute(
            "SELECT ma.revision FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id WHERE mv.id=?",
            (keyframe_id,),
        ).fetchone()[0]
    image_template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    review_checks = [{"item_id": str(item["id"]), "result": "PASS"} for item in image_template["items"]]
    reviews.submit_review(keyframe_id, str(image_template["id"]), "APPROVED", int(asset_revision), review_checks)

    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    print(
        f"sim env ready project={project_id} root_rel={root_rel} shot={shot_id} keyframe={keyframe_id} "
        f"workflow={workflow['workflow_version_id']} profile={candidate_version_id} evidence_job={evidence['job_id']} "
        f"evidence_media={evidence['media_version_id']} published={published['status']} "
        f"t2v_workflow={t2v_workflow['workflow_version_id']} t2v_profile={t2v_profile_version_id} "
        f"t2v_evidence_job={t2v_evidence['job_id']} t2v_evidence_media={t2v_evidence['media_version_id']} "
        f"t2v_published={t2v_published['status']} integrity={integrity} port={args.port}",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
