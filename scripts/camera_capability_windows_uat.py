"""Bounded Windows LOCAL_ONLY UAT for FR-CTL-001 camera capability gates.

The run creates a fresh migrated SQLite database under a Unicode/space path,
seeds only isolated *Published Profile fixtures*, and starts the actual FastAPI
application on 127.0.0.1.  It deliberately never starts ComfyUI, opens a user
model, or contacts a public endpoint.  The resulting evidence is therefore
PARTIAL: it proves local contract and ProjectService/API gates, not model
execution or a browser release UAT.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

from scripts.migrate import migrate

OPENER = build_opener(ProxyHandler({}))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "数据 SQLite",
        projects_root=root / "项目 根 空格",
        work_root=root / "工作区",
        cache_root=root / "缓存",
        logs_root=root / "日志",
        backups_root=root / "备份",
        comfy_output_root=root / "工作区" / "comfy-output",
        comfy_input_root=root / "工作区" / "comfy-input",
        port=port,
        allowed_origins=(f"http://127.0.0.1:{port}",),
    )


def _http(base_url: str, path: str, *, method: str = "GET", body: dict[str, object] | None = None, headers: dict[str, str] | None = None) -> dict[str, object]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    started = time.perf_counter()
    request = Request(f"{base_url}{path}", data=data, headers=request_headers, method=method)
    try:
        with OPENER.open(request, timeout=15) as response:
            return {"status": int(response.status), "payload": json.loads(response.read().decode("utf-8")), "error": None, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    except HTTPError as error:
        raw = error.read()
        try:
            payload: object = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = None
        return {"status": int(error.code), "payload": payload, "error": type(error).__name__, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    except (URLError, OSError, TimeoutError) as error:
        return {"status": None, "payload": None, "error": type(error).__name__, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(create_app(_settings(root, port)), host="127.0.0.1", port=port, log_level="warning")


def _seed_published_profile(database: Database, *, code: str, camera: dict[str, object]) -> str:
    """Seed a minimal local fixture with an explicit PUBLISHED contract.

    This fixture is restricted to this disposable UAT database.  It is not
    represented as model-evidence publication and cannot be mistaken for a
    production Profile or user model run.
    """
    profile_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = _now()
    schema = {"seed": {"determinism": "EXPLICIT"}, "capabilities": {"camera": camera}}
    input_contract = {"transport": "LOCAL_PROCESS", "input_slots": {}}
    output_contract = {"media_kind": "VIDEO"}
    resource_policy = {"gpu_heavy_concurrency": 1}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO execution_profiles (id,code,title,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,'uat',1,'v2')",
            (profile_id, code, f"{code} isolated fixture", now, now),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,parameter_schema_json,
             output_contract_json,resource_policy_json,status,manifest_sha256,capability_json,worker_policy,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'I2V','{}',?,?,?,?,'PUBLISHED',NULL,'{}',NULL,?,?, 'uat',1,'v2')""",
            (version_id, profile_id, json.dumps(input_contract), json.dumps(schema), json.dumps(output_contract), json.dumps(resource_policy), now, now),
        )
    return version_id


def _plan(profile_id: str, mode: str, *, prompt_text: str = "") -> dict[str, object]:
    return {"mode": mode, "shot_type": "CLOSEUP", "movement": "PUSH_IN", "prompt_text": prompt_text, "direction": "FORWARD", "intensity": 0.55, "curve": "EASE_IN_OUT", "profile_version_id": profile_id}


def _ready_fields(plan: dict[str, object]) -> dict[str, object]:
    return {
        "shot_type": "CLOSEUP", "composition": "center", "subject_action": "turn toward camera",
        "camera_plan": plan, "target_duration_ms": 4000, "dialogue": "", "environment": "interior",
        "continuity": "same wardrobe", "creative_intent": "focus on reaction",
    }


def _error_code(response: dict[str, object]) -> str | None:
    payload = response.get("payload")
    return str(payload.get("error", {}).get("code")) if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else None


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _check(code: str, passed: bool, observed: object, detail: str) -> dict[str, object]:
    return {"code": code, "status": "PASS" if passed else "FAIL", "observed": observed, "detail": detail}


def run(*, root: Path, port: int | None = None, keep_root: bool = True) -> dict[str, object]:
    root, port = root.resolve(), port or _free_port()
    settings = _settings(root, port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    native_profile = _seed_published_profile(database, code="camera-native-uat", camera={"support": "NATIVE"})
    fallback_profile = _seed_published_profile(database, code="camera-fallback-uat", camera={"support": "PROMPT_FALLBACK", "prompt_fallback": True})
    unsupported_profile = _seed_published_profile(database, code="camera-unsupported-uat", camera={"support": "UNSUPPORTED"})

    projects = ProjectService(database, settings.projects_root)
    project = projects.create_project(code="camera_windows_uat", title="相机能力 Windows UAT", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    native_shot = projects.create_shot(str(episode["id"]), "CAM_NATIVE", 4000)
    fallback_shot = projects.create_shot(str(episode["id"]), "CAM_FALLBACK", 4000)
    unsupported_shot = projects.create_shot(str(episode["id"]), "CAM_UNSUPPORTED", 4000)
    stale_shot = projects.create_shot(str(episode["id"]), "CAM_STALE", 4000)

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root), "--port", str(port)], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        live: dict[str, object] = {"status": None}
        for _ in range(200):
            live = _http(base_url, "/api/v1/health/live")
            if live["status"] == 200:
                break
            time.sleep(0.1)
        bootstrap = _http(base_url, "/api/v1/session/bootstrap")
        token = str(_mapping(bootstrap.get("payload")).get("token", ""))
        write_headers = {"X-Local-Instance-Token": token}
        request = {"shot_type": "CLOSEUP", "movement": "PUSH_IN", "direction": "FORWARD", "intensity": 0.55, "curve": "EASE_IN_OUT"}
        native_resolution = _http(base_url, f"/api/v1/profile-versions/{native_profile}:resolve-camera-plan", method="POST", body=request, headers=write_headers)
        fallback_resolution = _http(base_url, f"/api/v1/profile-versions/{fallback_profile}:resolve-camera-plan", method="POST", body=request, headers=write_headers)
        unsupported_resolution = _http(base_url, f"/api/v1/profile-versions/{unsupported_profile}:resolve-camera-plan", method="POST", body=request, headers=write_headers)

        native_plan = _mapping(_mapping(_mapping(native_resolution.get("payload")).get("resolution")).get("camera_plan"))
        fallback_plan = _mapping(_mapping(_mapping(fallback_resolution.get("payload")).get("resolution")).get("camera_plan"))
        native_revision = _http(base_url, f"/api/v2/shots/{native_shot['id']}/draft", method="PUT", body={"fields": _ready_fields(native_plan), "freeze": True}, headers=write_headers)
        native_ready = _http(base_url, f"/api/v2/shots/{native_shot['id']}:mark-ready", method="POST", body={}, headers=write_headers)
        fallback_revision = _http(base_url, f"/api/v2/shots/{fallback_shot['id']}/draft", method="PUT", body={"fields": _ready_fields(fallback_plan), "freeze": True}, headers=write_headers)
        fallback_ready = _http(base_url, f"/api/v2/shots/{fallback_shot['id']}:mark-ready", method="POST", body={}, headers=write_headers)
        forged_revision = _http(base_url, f"/api/v2/shots/{unsupported_shot['id']}/draft", method="PUT", body={"fields": _ready_fields(_plan(unsupported_profile, "NATIVE")), "freeze": True}, headers=write_headers)

        stale_revision = _http(base_url, f"/api/v2/shots/{stale_shot['id']}/draft", method="PUT", body={"fields": _ready_fields(_plan(native_profile, "NATIVE")), "freeze": True}, headers=write_headers)
        with database.transaction() as connection:
            connection.execute("UPDATE execution_profile_versions SET parameter_schema_json=? WHERE id=?", (json.dumps({"seed": {"determinism": "EXPLICIT"}, "capabilities": {"camera": {"support": "UNSUPPORTED"}}}), native_profile))
        stale_ready = _http(base_url, f"/api/v2/shots/{stale_shot['id']}:mark-ready", method="POST", body={}, headers=write_headers)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def resolution_payload(response: dict[str, object]) -> dict[str, object]:
        payload = response.get("payload")
        return _mapping(_mapping(payload).get("resolution"))

    native = resolution_payload(native_resolution)
    fallback = resolution_payload(fallback_resolution)
    unsupported = resolution_payload(unsupported_resolution)
    native_camera = _mapping(native.get("camera_plan"))
    fallback_camera = _mapping(fallback.get("camera_plan"))
    unsupported_camera = _mapping(unsupported.get("camera_plan"))
    native_ready_shot = _mapping(_mapping(native_ready.get("payload")).get("shot"))
    fallback_ready_shot = _mapping(_mapping(fallback_ready.get("payload")).get("shot"))
    checks: list[dict[str, object]] = [
        _check("LOOPBACK_FASTAPI_READY", live.get("status") == 200 and bootstrap.get("status") == 200 and bool(token), {"live": live, "bootstrap": bootstrap}, "actual 127.0.0.1 FastAPI process with local write token"),
        _check("PUBLISHED_NATIVE_RESOLUTION", native_resolution.get("status") == 200 and native_camera.get("mode") == "NATIVE" and native.get("submission_allowed") is True, native, "explicit Published Profile NATIVE contract"),
        _check("PUBLISHED_FALLBACK_RESOLUTION", fallback_resolution.get("status") == 200 and fallback_camera.get("mode") == "PROMPT_FALLBACK" and bool(fallback_camera.get("prompt_text")) and fallback.get("submission_allowed") is True, fallback, "fallback must expose an explicit prompt"),
        _check("PUBLISHED_UNSUPPORTED_RESOLUTION", unsupported_resolution.get("status") == 200 and unsupported_camera.get("mode") == "UNSUPPORTED" and unsupported.get("submission_allowed") is False, unsupported, "unsupported is a truthful non-submittable outcome"),
        _check("RESOLUTION_LOCAL_READ_ONLY", all(item.get("runtime_contacted") is False and item.get("network_contacted") is False and item.get("mutated") is False for item in (native, fallback, unsupported)), {"native": native, "fallback": fallback, "unsupported": unsupported}, "resolving contracts neither executes nor configures a Runtime"),
        _check("NATIVE_REVISION_AND_READY", native_revision.get("status") == 201 and native_ready.get("status") == 200 and native_ready_shot.get("status") == "READY", {"revision": native_revision, "ready": native_ready}, "real API positive path"),
        _check("FALLBACK_REVISION_AND_READY", fallback_revision.get("status") == 201 and fallback_ready.get("status") == 200 and fallback_ready_shot.get("status") == "READY", {"revision": fallback_revision, "ready": fallback_ready}, "real API fallback positive path"),
        _check("FORGED_UNSUPPORTED_REVISION_REJECTED", forged_revision.get("status") == 422 and _error_code(forged_revision) == "CAMERA_PLAN_UNSUPPORTED", forged_revision, "server does not trust client supplied NATIVE mode"),
        _check("READY_RECHECKS_STALE_PROFILE", stale_revision.get("status") == 201 and stale_ready.get("status") == 422 and _error_code(stale_ready) == "CAMERA_PLAN_UNSUPPORTED", {"revision": stale_revision, "ready": stale_ready}, "Ready re-resolves after isolated fixture contract changes"),
    ]
    result: dict[str, object] = {
        "schema_version": "g10.fr-ctl-001.windows-local-uat.v1", "status": "PARTIAL", "requirement_id": "FR-CTL-001",
        "scope": ["Published Profile NATIVE/PROMPT_FALLBACK/UNSUPPORTED resolution", "ShotRevision and Production Ready positive/negative API gates", "loopback-only Windows isolated UAT"],
        "platform": {"system": sys.platform, "platform": platform.platform(), "python": sys.version.split()[0]}, "local_only": True, "loopback_endpoint": base_url,
        "isolation": {"root_rel": root.relative_to(ROOT).as_posix() if root.is_relative_to(ROOT) else str(root), "database_rel": settings.database_path.relative_to(ROOT).as_posix() if settings.database_path.is_relative_to(ROOT) else str(settings.database_path), "published_profile_fixtures": [native_profile, fallback_profile, unsupported_profile], "production_database_contacted": False, "user_model_executed": False, "provider_contacted": False, "public_network_contacted": False},
        "checks": checks, "runtime_contacted": False, "network_contacted": True, "loopback_network_contacted": True, "public_network_contacted": False, "production_mutated": False,
        "limitations": ["Profiles are disposable isolated Published fixtures, not production evidence publication or a user-model execution.", "The test changes one isolated fixture contract directly solely to prove ProjectService's stale Ready recheck; published contract immutability is covered separately.", "No browser interaction, real user model execution, or release-scale concurrency is claimed."], "observed_at": _now(),
    }
    if not keep_root:
        shutil.rmtree(root, ignore_errors=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT / "work" / f"camera-capability-windows-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "fr-ctl-001-camera-windows-uat-2026-08-16.json")
    parser.add_argument("--remove-root", action="store_true")
    args = parser.parse_args()
    if args.serve:
        _serve(args.root.resolve(), args.port or _free_port())
        return
    result = run(root=args.root, port=args.port, keep_root=not args.remove_root)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = result["checks"]
    assert isinstance(checks, list)
    check_summary = {str(item.get("code")): item.get("status") for item in checks if isinstance(item, dict)}
    print(json.dumps({"status": result["status"], "output": str(output), "checks": check_summary}, ensure_ascii=False))
    if any(item.get("status") == "FAIL" for item in checks if isinstance(item, dict)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
