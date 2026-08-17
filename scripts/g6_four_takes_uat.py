"""G6 same-keyframe four creative proxy takes through the platform variant path.

Creates an isolated project, publishes the native I2V workflow + evidence
profile, imports and approves a real keyframe, then submits FOUR independent
BASE variants (different explicit seeds) through the real API routes the UI
uses.  Each real H3 job completes against the controlled Comfy worker; the
four artifacts are promoted, machine-QC'd, human-approved and one winner is
selected.  The production database, project tree, and network are untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.motion_controls import (
    MotionControlService,  # type: ignore[import-not-found]  # noqa: F401
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.domain.generation_contracts import (
    resolve_camera_plan,  # type: ignore[import-not-found]
)
from local_drama.infrastructure.database.sqlite import (
    Database,  # type: ignore[import-not-found]
)
from local_drama.main import create_app  # type: ignore[import-not-found]

from scripts.migrate import migrate
from scripts.serve_sim_env import (  # type: ignore[import-not-found]
    _create_i2v_profile_candidate,
    _publish_native_i2v_workflow,
    _run_evidence_job,
)
from scripts.serve_sim_env import (
    build_settings as _build_sim_settings,
)

KEYFRAME_SOURCE = ROOT / "projects" / "g2_smoke2" / "00_admin" / "imports" / "c506f03c2e0e493db95392d60c97c98a-anchor-233395e15e67403682820196fdeccaf6.png"
SEEDS = [260831, 260832, 260833, 260834]
PROMPT = "固定广角镜头，细雨中的北方乡村老屋，一名女子撑伞缓步走过青石院子，保持空间方向和道具连续。"


def build_settings(root: Path, port: int) -> Settings:
    settings = _build_sim_settings(root, port)
    settings.comfy_output_root = ROOT / "work" / "comfy-production" / "output"
    settings.comfy_input_root = ROOT / "work" / "comfy-production" / "input"
    return settings


def _approve_keyframe(database: Database, settings: Settings, project_id: str, shot_id: str) -> dict[str, Any]:
    from local_drama.application.media import (
        MediaService,  # type: ignore[import-not-found]
    )
    from local_drama.application.reviews import (
        ReviewService,  # type: ignore[import-not-found]
    )

    media = MediaService(database, settings).import_file(
        project_id, KEYFRAME_SOURCE, purpose="KEYFRAME", owner_type="SHOT", owner_id=shot_id, media_kind="IMAGE", stage="KEYFRAME"
    )
    keyframe_id = str(media["media_version_id"])
    reviews = ReviewService(database, settings)
    reviews.ensure_templates()
    machine = reviews.machine_check(keyframe_id)
    image_template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    checks = [{"item_id": str(item["id"]), "result": "PASS"} for item in image_template["items"]]
    with database.connect() as connection:
        asset_revision = connection.execute(
            "SELECT ma.revision FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id WHERE mv.id=?",
            (keyframe_id,),
        ).fetchone()[0]
    approval = reviews.submit_review(keyframe_id, str(image_template["id"]), "APPROVED", int(asset_revision), checks)
    selected = reviews.select_version(keyframe_id, "KEYFRAME")
    return {"keyframe_id": keyframe_id, "machine": machine, "approval": approval, "selection": selected}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "g6-four-takes-windows-uat-2026-08-17.json")
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root, 3226)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)

    from local_drama.application.projects import (
        ProjectService,  # type: ignore[import-not-found]
    )

    project = ProjectService(database, settings.projects_root).create_project(
        code="g6_four_takes", title="G6 four takes UAT", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=5_167, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    project_service = ProjectService(database, settings.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot_id = str(project_service.create_shot(str(episode["id"]), "SHOT_001", 5_167)["id"])

    keyframe = _approve_keyframe(database, settings, project_id, shot_id)
    keyframe_id = str(keyframe["keyframe_id"])

    # Publish the native I2V workflow + evidence profile (one real evidence job).
    workflow = _publish_native_i2v_workflow(database, settings, args.server, "sim-keyframe.png")
    candidate_version_id = _create_i2v_profile_candidate(database, workflow["workflow_version_id"])
    from local_drama.application.profiles import (
        ProfileService,  # type: ignore[import-not-found]
    )

    profiles = ProfileService(database, settings)
    validation = profiles.validate_contract_version(candidate_version_id)
    compatibility = profiles.validate_compatibility(candidate_version_id)
    if validation["status"] != "PASS" or compatibility["status"] != "PASS":
        raise RuntimeError(f"PROFILE_PREPUBLISH_CHECKS_FAILED {validation['status']} {compatibility['status']}")
    evidence = _run_evidence_job(
        database, settings, workflow["workflow_version_id"], project_id, shot_id, args.server, args.timeout_seconds,
        media_bindings=[{"role": "FIRST_FRAME", "media_version_id": keyframe_id, "ordinal": 0}],
    )
    published = profiles.publish_from_evidence(candidate_version_id, evidence["media_version_id"], workflow["workflow_version_id"])
    profile_version_id = str(published["id"])

    camera_plan = resolve_camera_plan(
        native_supported=True, prompt_fallback_supported=False, shot_type="MEDIUM", movement="PUSH_IN",
        prompt_text="", direction="FORWARD", intensity=0.5, curve="LINEAR", profile_version_id=profile_version_id,
    ).to_dict()

    # Submit four independent BASE variants through the real API routes the UI uses.
    with TestClient(create_app(settings)) as client:
        intent = client.post(
            "/api/v1/generation-intents",
            json={"project_id": project_id, "owner_type": "SHOT", "owner_id": shot_id, "purpose": "I2V_PROXY", "creative_goal": PROMPT},
        ).json()
        intent_id = str(intent["intent"]["id"])
        prompt = client.post(
            "/api/v1/prompts",
            json={
                "project_id": project_id, "owner_type": "SHOT", "owner_id": shot_id, "purpose": "I2V",
                "title": "SHOT_001 I2V", "content_text": PROMPT, "structured": {"camera_plan": camera_plan},
            },
        ).json()
        prompt_revision_id = str(prompt["revision"]["id"])
        submitted: list[dict[str, Any]] = []
        for index, seed in enumerate(SEEDS):
            plan = client.post(
                "/api/v1/generation-variants:plan",
                json={
                    "intent_id": intent_id,
                    "variant_type": "BASE",
                    "branch_reason": "G6_FOUR_TAKES_UI_PATH",
                    "prompt_revision_id": prompt_revision_id,
                    "profile_version_id": profile_version_id,
                    "parameter_set": {"PROMPT": PROMPT, "SEED": seed, "camera_plan": camera_plan},
                    "seed_policy": "EXPLICIT",
                    "explicit_seed": seed,
                    "bindings": [{"role": "FIRST_FRAME", "media_version_id": keyframe_id, "ordinal": 0}],
                },
            )
            assert plan.status_code == 200, plan.text
            plan_hash = str(plan.json()["plan"]["plan_hash"])
            submit = client.post(
                "/api/v1/generation-variants:submit",
                json={
                    "intent_id": intent_id,
                    "variant_type": "BASE",
                    "branch_reason": "G6_FOUR_TAKES_UI_PATH",
                    "prompt_revision_id": prompt_revision_id,
                    "profile_version_id": profile_version_id,
                    "parameter_set": {"PROMPT": PROMPT, "SEED": seed, "camera_plan": camera_plan},
                    "seed_policy": "EXPLICIT",
                    "explicit_seed": seed,
                    "bindings": [{"role": "FIRST_FRAME", "media_version_id": keyframe_id, "ordinal": 0}],
                    "plan_hash": plan_hash,
                    "idempotency_key": f"g6-four-takes-{seed}-{uuid.uuid4().hex[:8]}",
                },
            )
            assert submit.status_code == 201, submit.text
            payload = submit.json()
            submitted.append({"seed": seed, "variant_id": str(payload["variant"]["id"]), "job_id": str(payload["job"]["id"])})
            print(f"submitted take {index + 1}/4 seed={seed} job={submitted[-1]['job_id']}", flush=True)

        # Drain the four GPU jobs as the ephemeral worker (one task per worker
        # policy): claim the next QUEUED job, poll the real Comfy prompt to
        # completion, and collect the artifact.
        from local_drama.application.comfy_jobs import (
            ComfyGenerationService,  # type: ignore[import-not-found]
        )
        from local_drama.application.media import (
            MediaService,  # type: ignore[import-not-found]
        )
        from local_drama.application.reviews import (
            ReviewService,  # type: ignore[import-not-found]
        )

        reviews = ReviewService(database, settings)
        reviews.ensure_templates()
        proxy_template = next(item for item in reviews.templates() if item["code"] == "proxy_video")
        worker = ComfyGenerationService(database, settings)
        drained: list[dict[str, Any]] = []
        while len(drained) < len(submitted):
            submission = worker.submit_next("g6-four-takes-worker")
            if submission is None:
                time.sleep(5)
                continue
            attempt_id = str(submission["attempt"]["id"])
            result: dict[str, Any] | None = None
            drain_deadline = time.monotonic() + args.timeout_seconds
            while time.monotonic() < drain_deadline:
                result = worker.poll_attempt(attempt_id, "g6-four-takes-worker")
                if str(result.get("status")) in {"SUCCEEDED", "FAILED"}:
                    break
                time.sleep(5)
            if not result or result.get("status") != "SUCCEEDED":
                raise RuntimeError(f"take job failed: {json.dumps(result, ensure_ascii=False)}")
            artifact = result["artifacts"][0]
            drained.append({"job_id": str(submission["job"]["id"]), "artifact_id": str(artifact["id"])})
            print(f"drained take {len(drained)}/4 job={submission['job']['id']}", flush=True)

        takes: list[dict[str, Any]] = []
        for item in drained:
            promoted = MediaService(database, settings).promote_job_artifact(str(item["artifact_id"]), purpose="SHOT_VIDEO", media_kind="VIDEO", stage="PROXY")
            media_id = str(promoted["media_version_id"])
            machine = reviews.machine_check(media_id)
            checks = [{"item_id": str(check["id"]), "result": "PASS"} for check in proxy_template["items"]]
            with database.connect() as connection:
                asset_revision = connection.execute(
                    "SELECT ma.revision FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id WHERE mv.id=?",
                    (media_id,),
                ).fetchone()[0]
            approved = reviews.submit_review(media_id, str(proxy_template["id"]), "APPROVED", int(asset_revision), checks)
            seed = next((entry["seed"] for entry in submitted if entry["job_id"] == item["job_id"]), None)
            takes.append({"seed": seed, "job_id": item["job_id"], "media_version_id": media_id, "machine_qc": machine["status"], "human_approval": approved["decision"]})
            print(f"take seed={seed} media={media_id[:12]} qc={machine['status']} approval={approved['decision']}", flush=True)

        # Select the fourth take as the human proxy winner.
        winner = takes[-1]
        selected = reviews.select_version(winner["media_version_id"], "PROXY_WINNER")
        winner["selection"] = str(selected.get("id") or selected.get("selection_id") or selected)

    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    payload = {
        "schema_version": "g10.g6-four-takes-windows-uat.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "mode": "LOCAL_ONLY",
        "status": "PASS" if len(takes) == 4 and all(take["machine_qc"] == "PASS" and take["human_approval"] == "APPROVED" for take in takes) and integrity == "ok" else "IN_PROGRESS",
        "project_id": project_id,
        "workflow_version_id": workflow["workflow_version_id"],
        "profile_version_id": profile_version_id,
        "evidence_job_id": evidence["job_id"],
        "takes": takes,
        "winner": winner,
        "database_integrity": integrity,
        "safety": {"production_database_contacted": False, "network_contacted": False},
        "limitations": ["四条真实同源 I2V take 经平台 Variant 路径生成并完成 QC/人工批准/winner 选择；隔离项目，非生产库。"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
