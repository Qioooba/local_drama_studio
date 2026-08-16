"""Bounded Windows x64 LOCAL_ONLY UAT for TC-VAR-013/TC-VAR-004.

The UAT starts a real FastAPI loopback service on 127.0.0.1 and exercises
GenerationVariant profile-branch and provider-random resubmit workflows through the
actual submission endpoints.  It creates real media inputs and variants, persists
jobs for each submitted branch, and verifies that ProfileBranch / ProviderRandom
change paths are isolated.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from local_drama.application.generation import GenerationService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

from scripts.migrate import migrate

OPENER = build_opener(ProxyHandler({}))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "data sqlite",
        projects_root=root / "projects root",
        work_root=root / "work area",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        port=port,
        allowed_origins=(f"http://127.0.0.1:{port}",),
    )


def _http(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {}), **(headers or {})},
    )
    try:
        with OPENER.open(request, timeout=15) as response:
            return {"status": int(response.status), "payload": json.loads(response.read().decode("utf-8")), "error": None}
    except HTTPError as error:
        return {"status": int(error.code), "payload": json.loads(error.read().decode("utf-8")), "error": type(error).__name__}
    except (URLError, OSError, TimeoutError) as error:
        return {"status": None, "payload": None, "error": type(error).__name__}


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(create_app(_settings(root, port)), host="127.0.0.1", port=port, log_level="warning")


def _check(code: str, passed: bool, observed: Any, detail: str) -> dict[str, Any]:
    return {"code": code, "status": "PASS" if passed else "FAIL", "observed": observed, "detail": detail}


def _snapshot_profile_id(snapshot_json: Any) -> str | None:
    payload: dict[str, Any] | None = None
    if isinstance(snapshot_json, str):
        try:
            payload = json.loads(snapshot_json)
        except json.JSONDecodeError:
            return None
    elif isinstance(snapshot_json, dict):
        payload = snapshot_json
    if not isinstance(payload, dict):
        return None
    execution_snapshot = payload.get("execution_snapshot")
    if not isinstance(execution_snapshot, dict):
        return None
    profile_version_id = execution_snapshot.get("profile_version_id")
    return str(profile_version_id) if profile_version_id is not None else None


def _image(settings: Settings, root: Path, name: str) -> Path:
    if not settings.ffmpeg_path:
        raise RuntimeError("LOCAL_DRAMA_FFMPEG is required for this local UAT")
    source = root / "work area" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [settings.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=0.1", "-frames:v", "1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return source


def _published_profile(database: Database, settings: Settings) -> str:
    profile_service = ProfileService(database, settings.manifest_path)
    profile_service.sync_manifest()
    profiles = profile_service.list_profiles()
    if not profiles:
        raise RuntimeError("No manifest profiles available for TC-VAR UAT")
    profile_version_id = str(profiles[0]["version_id"])
    now = "2026-08-16T00:00:00Z"
    workflow_id = str(uuid.uuid4())
    workflow_version_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) "
            "VALUES (?, ?, 'TC-VAR workflow', ?, ?, 'uat', 1, 'v2')",
            (workflow_id, f"tc-var-uat-{uuid.uuid4().hex[:8]}-{profile_version_id}", now, now),
        )
        connection.execute(
            """
            INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, status, contract_json, content_json, package_rel_path,
             node_bindings_json, runtime_contract_json, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'PUBLISHED', '{}', ?, NULL, ?, '{}', ?, ?, ?, 'uat', 1, 'v2')
            """,
            (
                workflow_version_id,
                workflow_id,
                "a" * 64,
                json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": ""}}}),
                json.dumps({"FIRST_FRAME": {"node_id": "1", "input": "image", "type": "image"}}),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE execution_profile_versions
            SET status='PUBLISHED', workflow_version_id=?, input_contract_json=?, parameter_schema_json=?, revision=revision+1
            WHERE id=?
            """,
            (
                workflow_version_id,
                json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}),
                json.dumps({"seed": {"support": "OPTIONAL", "determinism": "NONDETERMINISTIC"}}),
                profile_version_id,
            ),
        )
    return profile_version_id


def _copy_profile_version(database: Database, source_version_id: str, *, status: str = "PUBLISHED") -> str:
    profile_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = "2026-08-16T00:10:00Z"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version) "
            "VALUES (?, ?, 'TC-VAR branch target', ?, ?, 'uat', 1, 'v2')",
            (profile_id, f"tc-var-branch-{profile_id}", now, now),
        )
        connection.execute(
            """
            INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id, model_bundle_json,
             input_contract_json, parameter_schema_json, output_contract_json, resource_policy_json, status,
             manifest_sha256, capability_json, worker_policy, created_at, updated_at, created_by, revision, schema_version)
            SELECT ?, ?, 1, capability, runtime_version_id, workflow_version_id, model_bundle_json,
             input_contract_json, parameter_schema_json, output_contract_json, resource_policy_json, ?, 
             manifest_sha256, capability_json, worker_policy, ?, ?, 'uat', 1, schema_version
            FROM execution_profile_versions WHERE id=?
            """,
            (version_id, profile_id, status, now, now, source_version_id),
        )
    return version_id


def run(*, root: Path, port: int | None = None) -> dict[str, Any]:
    root = root.resolve()
    selected_port = port or _port()
    settings = _settings(root, selected_port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)

    projects = ProjectService(database, settings.projects_root)
    project = projects.create_project(
        code=f"tcvar013004{uuid.uuid4().hex[:10]}",
        title="TC-VAR-013/004 Windows UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "VARSHOT", 1000)
    source_image = _image(settings, root, "tc-var013-base.png")
    media_service = MediaService(database, settings)
    base_media = media_service.import_file(
        project_id, source_image, owner_type="SHOT", owner_id=str(shot["id"]), stage="PROXY"
    )
    base_media_version_id = str(base_media["media_version_id"])

    base_profile_id = _published_profile(database, settings)
    target_profile_id = _copy_profile_version(database, base_profile_id)
    generation = GenerationService(database, settings)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V", "TC-VAR UAT intent")

    base_payload: dict[str, Any] = {
        "intent_id": str(intent["id"]),
        "variant_type": "BASE",
        "branch_reason": "baseline",
        "profile_version_id": base_profile_id,
        "parameter_set": {"frames": 81, "steps": 20},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [{"role": "FIRST_FRAME", "media_version_id": base_media_version_id, "ordinal": 0}],
    }

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root), "--port", str(selected_port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
    )
    base_url = f"http://127.0.0.1:{selected_port}"
    try:
        live: dict[str, Any] = {"status": None}
        for _ in range(200):
            live = _http(base_url, "/api/v1/health/live")
            if live["status"] == 200:
                break
            time.sleep(0.1)

        bootstrap = _http(base_url, "/api/v1/session/bootstrap")
        token = str(dict(bootstrap.get("payload", {})).get("token", ""))
        headers = {"X-Local-Instance-Token": token}

        plan = _http(base_url, "/api/v1/generation-variants:plan", method="POST", payload=base_payload, headers=headers)
        base_plan = dict(plan.get("payload", {}).get("plan", {})) if isinstance(plan.get("payload"), dict) else {}
        base_submit_payload = {**base_payload, "plan_hash": base_plan.get("plan_hash", ""), "idempotency_key": "tc-var-submit-base"}
        base_submit = _http(base_url, "/api/v1/generation-variants:submit", method="POST", payload=base_submit_payload, headers=headers)
        base_submit_payload_obj = base_submit.get("payload", {})
        base_variant = base_submit_payload_obj.get("variant", {})
        base_job = base_submit_payload_obj.get("job", {})

        profile_branch_plan_response = _http(
            base_url,
            f"/api/v1/generation-variants/{base_variant.get('id')}:derive-plan",
            method="POST",
            payload={"operation": "PROFILE_BRANCH", "profile_version_id": target_profile_id, "branch_reason": "profile branch compare"},
            headers=headers,
        )
        profile_branch_plan = dict(profile_branch_plan_response.get("payload", {}).get("plan", {})) if isinstance(
            profile_branch_plan_response.get("payload"), dict
        ) else {}
        profile_branch_submit_payload = {
            **(dict(profile_branch_plan.get("draft", {})) if isinstance(profile_branch_plan.get("draft"), dict) else {}),
            "plan_hash": profile_branch_plan.get("plan_hash", ""),
            "idempotency_key": "tc-var-submit-profile-branch",
        }
        profile_branch_submit = _http(
            base_url,
            "/api/v1/generation-variants:submit",
            method="POST",
            payload=profile_branch_submit_payload,
            headers=headers,
        )
        profile_branch_variant = profile_branch_submit.get("payload", {}).get("variant", {})
        profile_branch_job = profile_branch_submit.get("payload", {}).get("job", {})

        random_plan_one = _http(
            base_url,
            f"/api/v1/generation-variants/{base_variant.get('id')}:derive-plan",
            method="POST",
            payload={"operation": "RESUBMIT_PROVIDER_RANDOM", "branch_reason": "tc-var random one"},
            headers=headers,
        )
        random_plan_two = _http(
            base_url,
            f"/api/v1/generation-variants/{base_variant.get('id')}:derive-plan",
            method="POST",
            payload={"operation": "RESUBMIT_PROVIDER_RANDOM", "branch_reason": "tc-var random two"},
            headers=headers,
        )
        random_plan_one_obj = dict(random_plan_one.get("payload", {}).get("plan", {})) if isinstance(random_plan_one.get("payload"), dict) else {}
        random_plan_two_obj = dict(random_plan_two.get("payload", {}).get("plan", {})) if isinstance(random_plan_two.get("payload"), dict) else {}
        random_submit_one = _http(
            base_url,
            "/api/v1/generation-variants:submit",
            method="POST",
            payload={
                **(dict(random_plan_one_obj.get("draft", {})) if isinstance(random_plan_one_obj.get("draft"), dict) else {}),
                "plan_hash": random_plan_one_obj.get("plan_hash", ""),
                "idempotency_key": "tc-var-random-1",
            },
            headers=headers,
        )
        random_submit_two = _http(
            base_url,
            "/api/v1/generation-variants:submit",
            method="POST",
            payload={
                **(dict(random_plan_two_obj.get("draft", {})) if isinstance(random_plan_two_obj.get("draft"), dict) else {}),
                "plan_hash": random_plan_two_obj.get("plan_hash", ""),
                "idempotency_key": "tc-var-random-2",
            },
            headers=headers,
        )
        random_variant_one = random_submit_one.get("payload", {}).get("variant", {})
        random_job_one = random_submit_one.get("payload", {}).get("job", {})
        random_variant_two = random_submit_two.get("payload", {}).get("variant", {})
        random_job_two = random_submit_two.get("payload", {}).get("job", {})
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    with database.connect() as connection:
        base_jobs = list(
            connection.execute(
                "SELECT id, channel, state, subject_id FROM jobs WHERE subject_type='GENERATION_VARIANT' AND subject_id=?",
                (base_variant.get("id"),),
            ).fetchall()
        )
        profile_jobs = list(
            connection.execute(
                "SELECT id, channel, state, subject_id FROM jobs WHERE subject_type='GENERATION_VARIANT' AND subject_id=?",
                (profile_branch_variant.get("id"),),
            ).fetchall()
        )
        random_jobs = list(
            connection.execute(
                "SELECT j.id, j.channel, j.state, j.subject_id "
                "FROM jobs j WHERE subject_type='GENERATION_VARIANT' AND subject_id IN (?, ?)",
                (random_variant_one.get("id"), random_variant_two.get("id")),
            ).fetchall()
        )
        variants = list(
            connection.execute(
                "SELECT id, capability_profile_version_id, status, variant_type FROM generation_variants WHERE intent_id=?",
                (str(intent["id"]),),
            ).fetchall()
        )
    checks: list[dict[str, Any]] = [
        _check("LOOPBACK_ONLY", live.get("status") == 200 and base_url.startswith("http://127.0.0.1:"), {"endpoint": base_url}, "only local loopback host was used"),
        _check(
            "BASE_VARIANT_SUBMITTED",
            plan.get("status") == 200
            and base_plan.get("status") == "READY"
            and base_submit.get("status") == 201
            and base_variant.get("status") == "QUEUED"
            and base_job.get("subject_id") == base_variant.get("id"),
            {"plan": plan, "submit": base_submit},
            "base variant submit succeeds and creates a queued GPU_H3 job",
        ),
        _check(
            "BASE_AND_BRANCH_VARIANTS_PERSISTED",
            len(variants) == 4
            and len(base_jobs) == 1
            and len(profile_jobs) == 1
            and len(random_jobs) == 2
            and isinstance(random_submit_one.get("payload", {}).get("job"), dict)
            and isinstance(random_submit_two.get("payload", {}).get("job"), dict),
            {
                "variants": len(variants),
                "base_jobs": len(base_jobs),
                "profile_jobs": len(profile_jobs),
                "random_jobs": len(random_jobs),
            },
            "exactly one base and three descendant variants were persisted, with one job each",
        ),
        _check(
            "PROFILE_BRANCH_PLAN_SCOPE",
            profile_branch_plan_response.get("status") == 200
            and profile_branch_plan.get("status") == "READY"
            and profile_branch_plan.get("draft", {}).get("profile_version_id") == target_profile_id
            and profile_branch_plan.get("diff", {}).get("changed_fields") == ["profile_version_id"],
            profile_branch_plan,
            "PROFILE_BRANCH is limited to profile version field changes",
        ),
        _check(
            "PROFILE_BRANCH_SUBMIT_JOB",
            profile_branch_submit.get("status") == 201
            and profile_branch_variant.get("capability_profile_version_id") == target_profile_id
            and profile_branch_variant.get("status") == "QUEUED"
            and profile_branch_job.get("subject_id") == profile_branch_variant.get("id")
            and len(profile_jobs) == 1,
            {"branch_variant": profile_branch_variant, "branch_job": profile_branch_job},
            "PROFILE_BRANCH submit creates isolated variant and matching job",
        ),
        _check(
            "RESUBMIT_RANDOM_NONCE_UNIQUE",
            random_plan_one.get("status") == 200
            and random_plan_two.get("status") == 200
            and random_plan_one_obj.get("draft", {}).get("provider_random_nonce") != random_plan_two_obj.get("draft", {}).get("provider_random_nonce")
            and random_plan_one_obj.get("draft", {}).get("seed_policy") == "PROVIDER_RANDOM"
            and random_plan_two_obj.get("draft", {}).get("seed_policy") == "PROVIDER_RANDOM",
            {
                "plan_one_nonce": random_plan_one_obj.get("draft", {}).get("provider_random_nonce"),
                "plan_two_nonce": random_plan_two_obj.get("draft", {}).get("provider_random_nonce"),
            },
            "two RESUBMIT_PROVIDER_RANDOM plans generate independent nonces",
        ),
        _check(
            "RESUBMIT_RANDOM_SUBMIT_JOBS",
            random_submit_one.get("status") == 201
            and random_submit_two.get("status") == 201
            and random_variant_one.get("id") != random_variant_two.get("id")
            and random_job_one.get("id") != random_job_two.get("id")
            and {random_job_one.get("subject_id"), random_job_two.get("subject_id")} == {random_variant_one.get("id"), random_variant_two.get("id")}
            and len(random_jobs) == 2
            and _snapshot_profile_id(random_job_one.get("input_snapshot")) == base_profile_id
            and _snapshot_profile_id(random_job_two.get("input_snapshot")) == base_profile_id,
            {
                "random_variant_ids": [random_variant_one.get("id"), random_variant_two.get("id")],
                "random_job_ids": [random_job_one.get("id"), random_job_two.get("id")],
                "random_snapshot_profile_ids": [
                    _snapshot_profile_id(random_job_one.get("input_snapshot")),
                    _snapshot_profile_id(random_job_two.get("input_snapshot")),
                ],
            },
            "RESUBMIT_PROVIDER_RANDOM submit creates two independent jobs",
        ),
    ]

    return {
        "schema_version": "g10.tc-var-013-014.windows-uat.v1",
        "status": "PARTIAL",
        "scope": ["TC-VAR-013", "TC-VAR-004", "GENERATION_VARIANT"],
        "platform": {"system": sys.platform, "platform": platform.platform(), "python": sys.version.split()[0]},
        "local_only": True,
        "network": {
            "loopback_endpoint": base_url,
            "public_network_contacted": False,
            "provider_contacted": False,
            "comfyui_contacted": False,
        },
        "isolation": {
            "root_rel": root.relative_to(ROOT).as_posix() if root.is_relative_to(ROOT) else str(root),
            "production_database_contacted": False,
            "production_projects_root_contacted": False,
        },
        "checks": checks,
        "limits": [
            "No variant take was executed by a worker; only submission, job persistence, and branch plan isolation are validated.",
            "No external runtime/Comfy/model execution path was invoked.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    if args.serve:
        _serve(args.root, args.port or _port())
        return 0

    result = run(root=args.root, port=args.port)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(item["status"] == "PASS" for item in result["checks"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
