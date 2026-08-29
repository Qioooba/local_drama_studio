from __future__ import annotations

import argparse
import copy
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database

ROOT = Path(__file__).resolve().parents[1]
IMAGE_PROFILE_CODE = "local-suite-image-concept"
VIDEO_PROFILE_CODE = "local-suite-video-i2v"
IMAGE_WORKFLOW_CODE = "qwen-image-2512-production"
VIDEO_WORKFLOW_CODE = "minimax-h3-fl2va-production"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _published_workflow(database: Database, code: str) -> dict[str, Any]:
    service = WorkflowService(database, Settings.from_env())
    with database.connect() as connection:
        row = connection.execute(
            """SELECT wv.id FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
            WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1""",
            (code,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Published source workflow is missing: {code}")
    return service.get_version(str(row["id"]))


def _existing_workflow(database: Database, code: str, graph: dict[str, Any]) -> dict[str, Any] | None:
    service = WorkflowService(database, Settings.from_env())
    # WorkflowService content hashes use the canonical graph rather than the
    # structural hash. Compare canonical content directly to keep this script
    # idempotent without depending on a private hash helper.
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT wv.id,wv.content_json FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
            WHERE w.code=? ORDER BY wv.version_no DESC""",
            (code,),
        ).fetchall()
    for row in rows:
        if _json(json.loads(str(row["content_json"]))) == _json(graph):
            return service.get_version(str(row["id"]))
    return None


def _publish_workflow(
    database: Database,
    settings: Settings,
    *,
    code: str,
    title: str,
    graph: dict[str, Any],
    capability: str,
    output_kind: str,
    bindings: dict[str, dict[str, str]],
) -> dict[str, Any]:
    service = WorkflowService(database, settings)
    version = _existing_workflow(database, code, graph)
    if version is None:
        version = service.register_package(
            code,
            title,
            graph,
            {
                "schema_version": "localdrama.workflow-contract.v2",
                "capability": capability,
                "input_slots": {},
                "output": {"media_kind": output_kind},
                "verification": "NEW_MODEL_PRODUCTION_ROUTE",
            },
            bindings,
            {
                "transport": "LOOPBACK_HTTP",
                "base_url": settings.comfy_base_url,
                "network_policy": "LOOPBACK_ONLY",
            },
        )
    if version["status"] != "PUBLISHED":
        validation = service.validate_against_comfy(
            str(version["id"]),
            ComfyClient(settings.comfy_base_url, settings.comfy_output_root),
        )
        if validation["status"] != "PASS":
            raise RuntimeError(f"Workflow validation failed for {code}: {validation}")
        version = service.publish(str(version["id"]), str(validation["validation_id"]), actor="new-model-switch")
    return version


def _production_workflows(database: Database, settings: Settings) -> dict[str, dict[str, Any]]:
    image_source = _published_workflow(database, "qwen-image-2512-q5-smoke")
    image_graph = copy.deepcopy(image_source["workflow"])
    image_graph["7"]["inputs"].update(width=480, height=832)
    image_graph["8"]["inputs"].update(steps=20, seed=260829, cfg=1.0, denoise=1.0)
    image_graph["10"]["inputs"]["filename_prefix"] = "local_drama/qwen_image_2512"
    image_bindings = {
        "PROMPT": {"node_id": "5", "input": "text"},
        "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
        "SEED": {"node_id": "8", "input": "seed"},
        "STEPS": {"node_id": "8", "input": "steps"},
        "CFG": {"node_id": "8", "input": "cfg"},
        "DENOISE": {"node_id": "8", "input": "denoise"},
        "SAMPLER_NAME": {"node_id": "8", "input": "sampler_name"},
        "SCHEDULER": {"node_id": "8", "input": "scheduler"},
        "WIDTH": {"node_id": "7", "input": "width"},
        "HEIGHT": {"node_id": "7", "input": "height"},
    }

    video_source = _published_workflow(database, "h3-i2v")
    video_graph = copy.deepcopy(video_source["workflow"])
    video_graph["1"]["inputs"]["unet_name"] = "MiniMax-H3\\minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    video_graph["2"]["inputs"]["clip_name"] = "MiniMax-H3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    video_graph["3"]["inputs"]["vae_name"] = "MiniMax-H3\\minimax_h3_video_vae_fp16.safetensors"
    video_graph["4"]["inputs"]["vae_name"] = "MiniMax-H3\\minimax_h3_audio_vae_fp32.safetensors"
    video_graph["16"]["inputs"]["filename_prefix"] = "local_drama/minimax_h3_fl2va"
    video_bindings = {
        "FIRST_FRAME": {"node_id": "5", "input": "image"},
        "PROMPT": {"node_id": "7", "input": "prompt"},
        "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"},
        "SEED": {"node_id": "8", "input": "noise_seed"},
        "FRAME_COUNT": {"node_id": "7", "input": "length"},
        "STEPS": {"node_id": "10", "input": "steps"},
        "DENOISE": {"node_id": "10", "input": "denoise"},
        "SCHEDULER": {"node_id": "10", "input": "scheduler"},
        "SAMPLER_NAME": {"node_id": "9", "input": "sampler_name"},
        "FPS": {"node_id": "15", "input": "fps"},
    }

    return {
        "image": _publish_workflow(
            database,
            settings,
            code=IMAGE_WORKFLOW_CODE,
            title="Qwen Image 2512 Q5_K_M 生产工作流",
            graph=image_graph,
            capability="IMAGE_CONCEPT",
            output_kind="IMAGE",
            bindings=image_bindings,
        ),
        "video": _publish_workflow(
            database,
            settings,
            code=VIDEO_WORKFLOW_CODE,
            title="MiniMax H3 FL2VA 新模型生产工作流",
            graph=video_graph,
            capability="VIDEO_I2V",
            output_kind="VIDEO",
            bindings=video_bindings,
        ),
    }


def _link_candidates(database: Database, workflows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    linked: dict[str, dict[str, Any]] = {}
    with database.transaction() as connection:
        for kind, code in (("image", IMAGE_PROFILE_CODE), ("video", VIDEO_PROFILE_CODE)):
            row = connection.execute(
                """SELECT epv.* FROM execution_profile_versions epv
                JOIN execution_profiles ep ON ep.id=epv.execution_profile_id
                WHERE ep.code=? AND epv.status='CANDIDATE_UNVERIFIED'
                ORDER BY epv.version_no DESC LIMIT 1""",
                (code,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Candidate profile is missing: {code}")
            workflow_id = str(workflows[kind]["id"])
            bundle = json.loads(str(row["model_bundle_json"] or "{}"))
            bundle.update(
                {
                    "workflow_version_id": workflow_id,
                    "route_status": "production_workflow_verified",
                    "activation_requested_by_user": True,
                }
            )
            connection.execute(
                """UPDATE execution_profile_versions SET workflow_version_id=?,model_bundle_json=?,
                updated_at=?,revision=revision+1 WHERE id=?""",
                (workflow_id, _json(bundle), _now(), row["id"]),
            )
            linked[kind] = {"profile_version_id": str(row["id"]), "workflow_version_id": workflow_id}
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('new-model-switch','operator','NEW_MODEL_PRODUCTION_WORKFLOWS_LINKED','model_suite',?, ?, ?)""",
            (
                "local-suite-20260829",
                "将新模型候选 Profile 链接到生产参数工作流",
                _json(linked),
            ),
        )
    return linked


def _profile_fingerprint(database: Database, profile_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Profile not found: {profile_id}")
    return ProfileService._execution_fingerprint(row)


def _run_comfy_job(
    database: Database,
    settings: Settings,
    *,
    project_id: str,
    profile_id: str,
    workflow_id: str,
    purpose: str,
    semantic_inputs: dict[str, Any],
    media_bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot = {
        "purpose": purpose,
        "workflow_version_id": workflow_id,
        "execution_snapshot": {
            "profile_version_id": profile_id,
            "workflow_version_id": workflow_id,
            "profile_execution_fingerprint": _profile_fingerprint(database, profile_id),
        },
        "semantic_inputs": semantic_inputs,
        "media_bindings": media_bindings,
        "network_policy": "LOOPBACK_ONLY",
    }
    job = JobService(database, settings).create_job(
        project_id,
        "PROFILE_EVIDENCE_PROBE",
        "EXECUTION_PROFILE_VERSION",
        profile_id,
        "GPU_H3",
        snapshot,
        f"new-model-switch:{profile_id}:{workflow_id}",
        execution_profile_version_id=profile_id,
        priority=1,
        max_attempts=1,
    )
    if job["state"] == "SUCCEEDED":
        return job
    runner = ComfyGenerationService(database, settings)
    submitted = runner.submit_next("new-model-switch")
    if submitted is None or str(submitted["job"]["id"]) != str(job["id"]):
        raise RuntimeError("The new-model evidence job was not the next GPU job")
    attempt_id = str(submitted["attempt"]["id"])
    while True:
        outcome = runner.poll_attempt(attempt_id, "new-model-switch")
        status = str(outcome["status"])
        print(_json({"job_id": job["id"], "status": status, "prompt_id": outcome.get("prompt_id")} ), flush=True)
        if status == "SUCCEEDED":
            return JobService(database, settings).get_job(str(job["id"]))
        if status in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Comfy evidence job failed: {outcome}")
        time.sleep(2)


def _artifact_id(job: dict[str, Any]) -> str:
    for attempt in reversed(job.get("attempts", [])):
        for artifact in reversed(attempt.get("artifacts", [])):
            if artifact.get("kind") == "COMFY_OUTPUT" and artifact.get("status") == "VERIFIED":
                return str(artifact["id"])
    raise RuntimeError(f"Job has no verified Comfy artifact: {job['id']}")


def _publish_profiles(
    database: Database,
    settings: Settings,
    linked: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    with database.connect() as connection:
        project = connection.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
        keyframe = connection.execute(
            """SELECT ma.project_id,mv.id AS media_version_id FROM media_assets ma
            JOIN media_versions mv ON mv.id=ma.approved_version_id
            WHERE ma.owner_type='SHOT' AND ma.purpose='KEYFRAME' AND ma.media_kind='IMAGE'
            AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
            ORDER BY mv.created_at DESC LIMIT 1"""
        ).fetchone()
    if project is None or keyframe is None:
        raise RuntimeError("A project and an approved keyframe are required for evidence publication")

    image_job = _run_comfy_job(
        database,
        settings,
        project_id=str(project["id"]),
        profile_id=linked["image"]["profile_version_id"],
        workflow_id=linked["image"]["workflow_version_id"],
        purpose="T2I_PROFILE_EVIDENCE_PROBE",
        semantic_inputs={
            "PROMPT": "cinematic vertical drama keyframe, natural lighting, detailed environment",
            "OUTPUT_PREFIX": "local_drama/new_model_evidence/qwen_image_2512",
            "SEED": 260829,
            "STEPS": 4,
            "CFG": 1.0,
            "DENOISE": 1.0,
            "SAMPLER_NAME": "euler",
            "SCHEDULER": "simple",
            "WIDTH": 480,
            "HEIGHT": 832,
        },
        media_bindings=[],
    )
    image_media = MediaService(database, settings).promote_job_artifact(
        _artifact_id(image_job), purpose="PROFILE_EVIDENCE", media_kind="IMAGE", stage="KEYFRAME", actor="new-model-switch"
    )
    image_profile = ProfileService(database, settings.manifest_path).publish_from_evidence(
        linked["image"]["profile_version_id"],
        str(image_media["media_version_id"]),
        linked["image"]["workflow_version_id"],
        actor="new-model-switch",
    )

    video_job = _run_comfy_job(
        database,
        settings,
        project_id=str(keyframe["project_id"]),
        profile_id=linked["video"]["profile_version_id"],
        workflow_id=linked["video"]["workflow_version_id"],
        purpose="I2V_PROFILE_EVIDENCE_PROBE",
        semantic_inputs={
            "PROMPT": "subtle natural breathing, stable identity and lighting",
            "OUTPUT_PREFIX": "local_drama/new_model_evidence/minimax_h3_fl2va",
            "SEED": 260829,
            "FRAME_COUNT": 5,
            "STEPS": 1,
            "DENOISE": 1.0,
            "SCHEDULER": "simple",
            "SAMPLER_NAME": "euler",
            "FPS": 24.0,
        },
        media_bindings=[{"role": "FIRST_FRAME", "media_version_id": str(keyframe["media_version_id"]), "ordinal": 0}],
    )
    video_media = MediaService(database, settings).promote_job_artifact(
        _artifact_id(video_job), purpose="PROFILE_EVIDENCE", media_kind="VIDEO", stage="PROXY", actor="new-model-switch"
    )
    video_profile = ProfileService(database, settings.manifest_path).publish_from_evidence(
        linked["video"]["profile_version_id"],
        str(video_media["media_version_id"]),
        linked["video"]["workflow_version_id"],
        actor="new-model-switch",
    )
    return {"image": dict(image_profile), "video": dict(video_profile)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "publish"))
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "new-model-activation-current.json")
    args = parser.parse_args()
    settings = Settings.from_env()
    database = Database(settings.database_path)
    workflows = _production_workflows(database, settings)
    linked = _link_candidates(database, workflows)
    result: dict[str, Any] = {"workflows": {key: value["id"] for key, value in workflows.items()}, "linked": linked}
    if args.command == "publish":
        result["published"] = _publish_profiles(database, settings, linked)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
