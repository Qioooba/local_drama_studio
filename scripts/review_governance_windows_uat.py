"""Bounded Windows x64 LOCAL_ONLY UAT for FR-REV-002..004.

Uses a fresh Alembic-migrated SQLite database below an isolated path,
real FFmpeg-generated local MP4 files, and a temporary loopback FastAPI
process.  It validates append-only review-template history, the separation of
machine QC from a human decision, and visible all-or-nothing batch failures.
It deliberately does not contact a provider, ComfyUI, model file, public
network, or the production database.  Evidence is PARTIAL by design.
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
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from local_drama.application.media import MediaService
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
        # Keep this media UAT ASCII-only: Python's Windows legacy-codepage
        # capture of FFprobe JSON can otherwise fail before review governance
        # is reached.  Unicode-path coverage is recorded separately.
        data_root=root / "data sqlite",
        projects_root=root / "projects root",
        work_root=root / "work area",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=root / "work area" / "comfy-output",
        comfy_input_root=root / "work area" / "comfy-input",
        port=port,
        allowed_origins=(f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
    )


def _http(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
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
        raw = error.read()
        try:
            parsed: object = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = None
        return {"status": int(error.code), "payload": parsed, "error": type(error).__name__}
    except (URLError, OSError, TimeoutError) as error:
        return {"status": None, "payload": None, "error": type(error).__name__}


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(create_app(_settings(root, port)), host="127.0.0.1", port=port, log_level="warning")


def _check(code: str, passed: bool, observed: object, detail: str) -> dict[str, object]:
    return {"code": code, "status": "PASS" if passed else "FAIL", "observed": observed, "detail": detail}


def _video(settings: Settings, name: str, color: str) -> Path:
    if not settings.ffmpeg_path:
        raise RuntimeError("LOCAL_DRAMA_FFMPEG is required for this real local-media UAT")
    source = settings.work_root / "local input media" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [settings.ffmpeg_path, "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return source


def _checks(template: dict[str, object]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]  # type: ignore[index]


def _payload(response: dict[str, object]) -> dict[str, object]:
    return dict(response.get("payload") or {}) if isinstance(response.get("payload"), dict) else {}


def _error_code(response: dict[str, object]) -> str | None:
    error = _payload(response).get("error")
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") else None


def run(*, root: Path, port: int | None = None) -> dict[str, object]:
    root = root.resolve()
    selected_port = port or _port()
    settings = _settings(root, selected_port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="review_governance_uat",
        title="审核治理 Windows 本地 UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    media_service = MediaService(database, settings)
    historic = media_service.import_file(project_id, _video(settings, "template-history.mp4", "blue"), stage="PROXY")
    batch_a = media_service.import_file(project_id, _video(settings, "batch-a.mp4", "green"), stage="PROXY")
    batch_b = media_service.import_file(project_id, _video(settings, "batch-b.mp4", "red"), stage="PROXY")
    historic_id = str(historic["media_version_id"])
    batch_ids = [str(batch_a["media_version_id"]), str(batch_b["media_version_id"])]
    imported_content_paths = [media_service.content_path(media_id)[1] for media_id in [historic_id, *batch_ids]]

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root), "--port", str(selected_port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
    )
    base_url = f"http://127.0.0.1:{selected_port}"
    try:
        live: dict[str, object] = {"status": None}
        for _ in range(200):
            live = _http(base_url, "/api/v1/health/live")
            if live["status"] == 200:
                break
            time.sleep(0.1)
        bootstrap = _http(base_url, "/api/v1/session/bootstrap")
        token = str(_payload(bootstrap).get("token", ""))
        write_headers = {"X-Local-Instance-Token": token}

        templates_v1_response = _http(base_url, "/api/v1/review-templates")
        templates_v1 = _payload(templates_v1_response).get("items", [])
        proxy_v1 = next((item for item in templates_v1 if isinstance(item, dict) and item.get("code") == "proxy_video" and item.get("version_no") == 1), {})
        machine = _http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{historic_id}/machine-checks", method="POST", payload={}, headers=write_headers)
        context_after_machine = _http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{historic_id}/review-context")
        historic_review = _http(
            base_url,
            f"/api/v1/subjects/MEDIA_VERSION/{historic_id}/reviews",
            method="POST",
            payload={"template_version_id": proxy_v1.get("id"), "decision": "APPROVED", "expected_subject_revision": 1, "checks": _checks(proxy_v1)},
            headers=write_headers,
        )
        template_v2 = _http(
            base_url,
            "/api/v1/review-templates",
            method="POST",
            payload={
                "code": "proxy_video",
                "subject_type": "MEDIA_VERSION",
                "items": [*list(proxy_v1.get("items", [])), {"id": "human_note", "label": "人工补充说明", "required": False}],
            },
            headers=write_headers,
        )
        templates_after = _http(base_url, "/api/v1/review-templates")
        historic_context = _http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{historic_id}/review-context")

        template_v2_item = _payload(template_v2).get("template", {})
        stale_plan = _http(
            base_url,
            "/api/v1/reviews/batch:preflight",
            method="POST",
            payload={"project_id": project_id, "items": [{"media_version_id": item, "template_version_id": template_v2_item.get("id")} for item in batch_ids]},
            headers=write_headers,
        )
        select_for_stale = _http(
            base_url,
            f"/api/v1/media-versions/{batch_ids[1]}:select",
            method="POST",
            payload={"selection_type": "PROXY_WINNER"},
            headers=write_headers,
        )
        stale_commit = _http(
            base_url,
            "/api/v1/reviews/batch:commit",
            method="POST",
            payload={"plan_token": _payload(stale_plan).get("plan", {}).get("plan_token"), "decision": "APPROVED", "checks": _checks(template_v2_item)},
            headers=write_headers,
        )
        batch_contexts_after_failure = [_http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{item}/review-context") for item in batch_ids]

        fresh_plan = _http(
            base_url,
            "/api/v1/reviews/batch:preflight",
            method="POST",
            payload={"project_id": project_id, "items": [{"media_version_id": item, "template_version_id": template_v2_item.get("id")} for item in batch_ids]},
            headers=write_headers,
        )
        successful_commit = _http(
            base_url,
            "/api/v1/reviews/batch:commit",
            method="POST",
            payload={"plan_token": _payload(fresh_plan).get("plan", {}).get("plan_token"), "decision": "APPROVED", "checks": _checks(template_v2_item)},
            headers=write_headers,
        )
        batch_contexts_after_success = [_http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{item}/review-context") for item in batch_ids]
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    template_rows = _payload(templates_after).get("items", [])
    proxy_versions = [item for item in template_rows if isinstance(item, dict) and item.get("code") == "proxy_video"]
    historic_reviews = _payload(historic_context).get("reviews", [])
    machine_context = _payload(context_after_machine)
    failure_counts = [len(_payload(context).get("reviews", [])) for context in batch_contexts_after_failure]
    success_reviews = [_payload(context).get("reviews", []) for context in batch_contexts_after_success]
    batch_result = _payload(successful_commit).get("result", {})
    machine_payload = _payload(machine).get("machine_check", {})
    historic_review_payload = _payload(historic_review).get("review", {})
    checks = [
        _check("ISOLATED_REAL_LOCAL_MP4", all(path.is_file() and path.stat().st_size > 0 for path in imported_content_paths), {"media_version_ids": [historic_id, *batch_ids], "byte_sizes": [path.stat().st_size for path in imported_content_paths]}, "three FFmpeg-created local MP4 files were imported into the isolated project"),
        _check("LOOPBACK_SESSION_AND_API", live.get("status") == 200 and bootstrap.get("status") == 200 and bool(token), {"endpoint": base_url, "health": live.get("status"), "bootstrap": bootstrap.get("status")}, "only the temporary 127.0.0.1 FastAPI API was contacted"),
        _check("MACHINE_QC_SEPARATE_FROM_HUMAN", machine.get("status") == 201 and machine_payload.get("status") == "PASS" and len(machine_context.get("machine_checks", [])) == 1 and machine_context.get("reviews") == [], {"machine_run_id": machine_payload.get("id"), "review_count_after_qc": len(machine_context.get("reviews", []))}, "machine QC persists technical evidence but never manufactures a human review decision"),
        _check("HUMAN_APPROVAL_IS_EXPLICIT", historic_review.get("status") == 201 and historic_review_payload.get("decision") == "APPROVED", {"review_id": historic_review_payload.get("id"), "machine_run_id": machine_payload.get("id")}, "human APPROVED was submitted through its own explicit checklist API call"),
        _check("TEMPLATE_HISTORY_APPEND_ONLY", template_v2.get("status") == 201 and len(proxy_versions) == 2 and [item.get("version_no") for item in proxy_versions] == [1, 2] and isinstance(proxy_v1, dict) and proxy_versions[0].get("items") == proxy_v1.get("items"), {"versions": [{"id": item.get("id"), "version_no": item.get("version_no"), "item_ids": [entry.get("id") for entry in item.get("items", [])]} for item in proxy_versions]}, "v2 was appended; the v1 definition was not rewritten"),
        _check("HISTORICAL_REVIEW_EXPLAINABLE", len(historic_reviews) == 1 and isinstance(historic_reviews[0], dict) and historic_reviews[0].get("review_template_version_id") == proxy_v1.get("id") and historic_reviews[0].get("template", {}).get("version_no") == 1, {"historical_review": historic_reviews[0] if historic_reviews else None}, "review context resolves the exact v1 definition retained by the historical decision"),
        _check("BATCH_STALE_VISIBLE_AND_ATOMIC", stale_plan.get("status") == 200 and select_for_stale.get("status") == 200 and stale_commit.get("status") == 409 and _error_code(stale_commit) == "REVIEW_BATCH_STALE" and failure_counts == [0, 0], {"stale_commit_status": stale_commit.get("status"), "error_code": _error_code(stale_commit), "review_counts_after_failure": failure_counts}, "one changed batch item is explicitly reported stale and the other item receives no partial review"),
        _check("BATCH_SUCCESS_RETURNS_PER_ITEM_HISTORY", fresh_plan.get("status") == 200 and successful_commit.get("status") == 200 and isinstance(batch_result, dict) and batch_result.get("status") == "COMMITTED" and len(batch_result.get("items", [])) == 2 and all(len(items) == 1 and isinstance(items[0], dict) and items[0].get("template", {}).get("version_no") == 2 for items in success_reviews), {"plan_status": _payload(fresh_plan).get("plan", {}).get("status"), "commit_status": batch_result.get("status"), "result_count": len(batch_result.get("items", []))}, "a re-preflighted batch commits both explicit items and each history references v2"),
    ]
    return {
        "schema_version": "g10.fr-rev-002-004.windows-uat.v1",
        "status": "PARTIAL",
        "scope": ["FR-REV-002", "FR-REV-003", "FR-REV-004"],
        "observed_at": _now(),
        "platform": {"system": sys.platform, "platform": platform.platform(), "python": sys.version.split()[0]},
        "local_only": True,
        "network": {"loopback_endpoint": base_url, "public_network_contacted": False, "provider_contacted": False, "comfyui_contacted": False},
        "isolation": {"root_rel": root.relative_to(ROOT).as_posix() if root.is_relative_to(ROOT) else str(root), "production_database_contacted": False, "production_projects_root_contacted": False},
        "limits": [
            "bounded isolated SQLite and loopback API UAT; not a production-database, three-viewport, role-permission, or release-scale certification",
            "does not exercise model/provider generation, ComfyUI, remote transports, or real user production media",
            "evidence demonstrates template evolution and batch recovery contracts but does not close the overall release ledger",
        ],
        "checks": checks,
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
