"""Bounded Windows x64 LOCAL_ONLY UAT for TC-DOM-007 stale propagation.

The UAT uses a fresh Alembic-migrated SQLite database beneath an isolated
directory.  It creates a real local MP4, a shot-owned media
asset, formal approval and immutable formal selection history.  Updating the
upstream ShotRevision must preserve that history while making the approval
stale and blocking a new formal-selection preflight.  No provider, model,
ComfyUI, production database, or public network is contacted.
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
from local_drama.application.reviews import ReviewService
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
        return {"status": int(error.code), "payload": json.loads(error.read().decode("utf-8")), "error": type(error).__name__}
    except (URLError, OSError, TimeoutError) as error:
        return {"status": None, "payload": None, "error": type(error).__name__}


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(create_app(_settings(root, port)), host="127.0.0.1", port=port, log_level="warning")


def _check(code: str, passed: bool, observed: object, detail: str) -> dict[str, object]:
    return {"code": code, "status": "PASS" if passed else "FAIL", "observed": observed, "detail": detail}


def _video(settings: Settings) -> Path:
    if not settings.ffmpeg_path:
        raise RuntimeError("LOCAL_DRAMA_FFMPEG is required for this real local-media UAT")
    source = settings.work_root / "input video" / "dom007-blue.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [settings.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return source


def _checks(template: dict[str, object]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]  # type: ignore[index]


def run(*, root: Path, port: int | None = None) -> dict[str, object]:
    root = root.resolve()
    selected_port = port or _port()
    settings = _settings(root, selected_port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    projects = ProjectService(database, settings.projects_root)
    project = projects.create_project(
        code="dom007_windows_uat",
        title="TC-DOM-007 本地失效 UAT",
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
    shot = projects.create_shot(str(episode["id"]), "SHOT_001", 1_000)
    media_service = MediaService(database, settings)
    imported = media_service.import_file(
        project_id, _video(settings), owner_type="SHOT", owner_id=str(shot["id"]), stage="PROXY"
    )
    asset_id = str(imported["media_asset_id"])
    proxy = media_service.derive_version(asset_id, str(imported["media_version_id"]), "PROXY")
    formal = media_service.derive_version(asset_id, str(proxy["id"]), "FORMAL")
    reviews = ReviewService(database, settings)
    reviews.ensure_templates()
    templates = {str(item["code"]): item for item in reviews.templates()}
    formal_template = templates["formal_video"]
    machine = reviews.machine_check(str(formal["id"]))
    selection = reviews.select_version(str(formal["id"]), "FORMAL_SELECTION")
    approval = reviews.submit_review(
        str(formal["id"]), str(formal_template["id"]), "APPROVED", expected_subject_revision=2, checks=_checks(formal_template)
    )
    ready = reviews.formal_selection_preflight(project_id, [str(formal["id"])])
    revised = projects.create_shot_revision(str(shot["id"]), {"subject_action": "向前走"}, freeze=True)
    with database.connect() as connection:
        history = connection.execute(
            "SELECT id, media_version_id, selection_type, source_revision FROM selections WHERE id=?", (selection["id"],)
        ).fetchone()
        audit = connection.execute(
            "SELECT action, metadata_redacted_json FROM audit_events WHERE action='REVIEWS_MARKED_STALE' ORDER BY event_id DESC LIMIT 1"
        ).fetchone()

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root), "--port", str(selected_port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
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
        token = str(dict(bootstrap.get("payload") or {}).get("token", ""))
        context = _http(base_url, f"/api/v1/subjects/MEDIA_VERSION/{formal['id']}/review-context")
        loopback_stale = _http(
            base_url,
            "/api/v1/reviews/formal-selection:preflight",
            method="POST",
            payload={"project_id": project_id, "media_version_ids": [str(formal["id"])]},
            headers={"X-Local-Instance-Token": token},
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    context_payload = dict(context.get("payload") or {})
    loopback_payload = dict(loopback_stale.get("payload") or {})
    reviews_payload = context_payload.get("reviews") if isinstance(context_payload.get("reviews"), list) else []
    review_summary = [
        {
            "id": item.get("id"),
            "decision": item.get("decision"),
            "subject_revision": item.get("subject_revision"),
            "is_stale": item.get("is_stale"),
            "stale_reason": item.get("stale_reason"),
        }
        for item in reviews_payload
        if isinstance(item, dict)
    ]
    plan = loopback_payload.get("plan") if isinstance(loopback_payload.get("plan"), dict) else {}
    plan_items = plan.get("items") if isinstance(plan.get("items"), list) else []
    blockers = plan_items[0].get("blockers", []) if plan_items and isinstance(plan_items[0], dict) else []
    checks = [
        _check("REAL_LOCAL_MP4_AND_FORMAL_QC", machine["status"] == "PASS", {"machine_status": machine["status"], "formal_version_id": formal["id"]}, "ffmpeg-created MP4 was imported, derived, and locally QC checked"),
        _check("APPROVAL_AND_SELECTION_HISTORY_CREATED", approval["decision"] == "APPROVED" and selection["status"] == "SELECTED" and ready["status"] == "READY", {"review_id": approval["id"], "selection_id": selection["id"], "ready_status": ready["status"]}, "current formal approval enabled formal-selection preflight before the upstream change"),
        _check("UPSTREAM_SHOT_REVISION_CREATED", int(revised["revision_no"]) >= 2, {"shot_revision_id": revised["id"], "revision_no": revised["revision_no"]}, "new immutable ShotRevision is the upstream change"),
        _check("HISTORY_RETAINED", history is not None and str(history["media_version_id"]) == str(formal["id"]) and str(history["selection_type"]) == "FORMAL_SELECTION", dict(history) if history else None, "selection history is retained rather than deleted or rewritten"),
        _check("REVIEW_MARKED_STALE", bool(review_summary) and bool(review_summary[0].get("is_stale")) and review_summary[0].get("stale_reason") == "shot_revision_changed", review_summary, "loopback review context exposes the exact upstream stale reason"),
        _check("FORMAL_SELECTION_GATE_BLOCKED", loopback_stale.get("status") == 200 and plan.get("status") == "BLOCKED" and "LATEST_HUMAN_APPROVAL_REQUIRED" in blockers, {"response_status": loopback_stale.get("status"), "plan": plan}, "the current formal-selection gate cannot reuse the now-stale approval"),
        _check("STALE_AUDIT_RECORDED", audit is not None and str(audit["action"]) == "REVIEWS_MARKED_STALE", {"action": audit["action"] if audit else None}, "stale propagation has an immutable local audit event"),
        _check("LOOPBACK_ONLY", live.get("status") == 200 and base_url.startswith("http://127.0.0.1:"), {"endpoint": base_url, "health": live}, "only a temporary loopback FastAPI process was contacted"),
    ]
    return {
        "schema_version": "g10.tc-dom-007.windows-uat.v1",
        "status": "PARTIAL",
        "scope": ["TC-DOM-007", "FR-WRT-001", "FR-WRT-005"],
        "limits": [
            "bounded isolated SQLite and loopback UAT; not a production-database or release-scale certification",
            "selection records remain immutable audit history; the enforced stale state is the approval and current formal-selection gate",
            "does not exercise generation jobs, ComfyUI, provider runtimes, or model files",
        ],
        "observed_at": _now(),
        "platform": {"system": sys.platform, "platform": platform.platform(), "python": sys.version.split()[0]},
        "local_only": True,
        "network": {"loopback_endpoint": base_url, "public_network_contacted": False, "provider_contacted": False, "comfyui_contacted": False},
        "isolation": {"root_rel": root.relative_to(ROOT).as_posix() if root.is_relative_to(ROOT) else str(root), "production_database_contacted": False, "production_projects_root_contacted": False},
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
