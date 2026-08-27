"""Bounded Windows x64 LOCAL_ONLY UAT for FR-PRJ-004 domain identity rules.

This script uses a fresh Alembic-migrated SQLite database and project root
under a Unicode/space-containing isolated directory.  It exercises the real
ProjectService plus a real loopback FastAPI process: season/episode listing,
master-scene creation and episode-range binding, shot creation/revision,
episode reorder, storyboard shot reorder, and stale revision rejection.

The evidence remains PARTIAL by design.  It is a bounded local UAT and does
not claim production database, release-scale volume, or a full browser UAT.
No ComfyUI/provider/model or public network is contacted.
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import (
    shot_studio_command_service,
)
from local_drama.infrastructure.database.sqlite import Database

from scripts.migrate import migrate

OPENER = build_opener(ProxyHandler({}))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def build_settings(root: Path, port: int) -> Settings:
    # The child names deliberately contain Unicode and spaces to exercise the
    # Windows path contract without ever touching the production roots.
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
        allowed_origins=(f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
    )


def _http(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        method=method,
        headers={"Accept": "application/json", **(headers or {})},
    )
    try:
        with OPENER.open(request, timeout=15) as response:
            raw = response.read()
            try:
                payload: object = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = None
            return {
                "status": int(response.status),
                "payload": payload,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": None,
            }
    except HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = None
        return {
            "status": int(error.code),
            "payload": payload,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": type(error).__name__,
        }
    except (URLError, OSError, TimeoutError) as error:
        return {
            "status": None,
            "payload": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": type(error).__name__,
        }


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(
        create_app(build_settings(root, port)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


def _check(name: str, passed: bool, observed: object, detail: str) -> dict[str, object]:
    return {
        "code": name,
        "status": "PASS" if passed else "FAIL",
        "observed": observed,
        "detail": detail,
    }


def run(
    *, root: Path, port: int | None = None, keep_root: bool = True
) -> dict[str, object]:
    root = root.resolve()
    port = port or _find_free_port()
    settings = build_settings(root, port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    service = ProjectService(database, settings.projects_root)
    project = service.create_project(
        code="crud_windows_uat",
        title="本地 CRUD Windows UAT",
        season_count=2,
        episode_count=3,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
        width=1920,
        height=1080,
        primary_language="zh-CN",
        subtitle_mode="NONE",
    )
    project_id = str(project["id"])
    seasons_before = service.list_seasons(project_id)
    first_season = seasons_before[0]
    second_season = seasons_before[1]
    first_episodes_before = service.list_episodes(str(first_season["id"]))
    second_episodes = service.list_episodes(str(second_season["id"]))

    scenes = [
        service.create_scene(project_id, "SCENE_001", "室内客厅", "客厅", "DAY"),
        service.create_scene(project_id, "SCENE_002", "院子", "院子", "DUSK"),
    ]
    ranges = [
        service.bind_episode_scene_range(
            str(first_episodes_before[0]["id"]),
            str(scenes[0]["id"]),
            1,
            1,
            8,
            "script:1-8",
        ),
        service.bind_episode_scene_range(
            str(first_episodes_before[1]["id"]),
            str(scenes[1]["id"]),
            1,
            9,
            16,
            "script:9-16",
        ),
    ]

    target_episode = first_episodes_before[2]
    shots_before = [
        service.create_shot(str(target_episode["id"]), f"SHOT_{index:03d}", 4_000)
        for index in range(1, 4)
    ]
    revisions_before: dict[str, str] = {}
    for index, shot in enumerate(shots_before, start=1):
        revision = shot_studio_command_service(database).save_draft_revision(
            str(shot["id"]), {"subject_action": f"动作 {index}"}
        )
        revisions_before[str(shot["id"])] = str(revision["id"])

    episode_expected_revision = int(target_episode["revision"])
    moved_episode = service.reorder_episode(
        str(target_episode["id"]), 1, expected_revision=episode_expected_revision
    )
    episodes_after_move = service.list_episodes(str(first_season["id"]))
    stale_error: str | None = None
    try:
        service.reorder_episode(
            str(target_episode["id"]), 3, expected_revision=episode_expected_revision
        )
    except DomainRuleError as error:
        stale_error = error.code

    shot_ids_before = [str(item["id"]) for item in shots_before]
    storyboard_payload = {
        "ordered_shot_ids": list(reversed(shot_ids_before)),
        "edits": [],
        "copies": [],
    }
    storyboard_plan = service.plan_storyboard_batch(
        str(target_episode["id"]), storyboard_payload
    )
    storyboard_result = service.commit_storyboard_batch(
        str(target_episode["id"]), storyboard_payload, str(storyboard_plan["plan_hash"])
    )
    shots_after = service.list_shots(str(target_episode["id"]))
    shot_ids_after = [str(item["id"]) for item in shots_after]
    revision_ids_after = {
        str(item["id"]): str(item["current_revision_id"]) for item in shots_after
    }
    ranges_after = service.list_episode_scene_ranges(
        str(first_episodes_before[0]["id"])
    )

    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--root",
            str(root),
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        live: dict[str, object] = {"status": None}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            live = _http(base_url, "/api/v1/health/live")
            if live.get("status") == 200:
                break
            time.sleep(0.1)
        bootstrap = _http(base_url, "/api/v1/session/bootstrap")
        instance_token = str(dict(bootstrap.get("payload") or {}).get("token", ""))
        season_read = _http(base_url, f"/api/v1/projects/{project_id}/seasons")
        episode_read = _http(
            base_url, f"/api/v1/projects/seasons/{first_season['id']}/episodes"
        )
        scene_read = _http(base_url, f"/api/v1/projects/{project_id}/scenes")
        shot_read = _http(
            base_url, f"/api/v1/projects/episodes/{target_episode['id']}/shots"
        )
        api_expected = int(moved_episode["revision"])
        api_reorder = _http(
            base_url,
            f"/api/v1/projects/episodes/{target_episode['id']}:reorder?{urlencode({'display_order': 2, 'expected_revision': api_expected})}",
            method="POST",
            headers={"X-Local-Instance-Token": instance_token},
        )
        concurrent_expected_revision = api_expected
        if isinstance(api_reorder.get("payload"), dict):
            concurrent_expected_revision = int(
                api_reorder["payload"]["episode"]["revision"]
            )

        api_stale = _http(
            base_url,
            f"/api/v1/projects/episodes/{target_episode['id']}:reorder?{urlencode({'display_order': 3, 'expected_revision': api_expected})}",
            method="POST",
            headers={"X-Local-Instance-Token": instance_token},
        )

        def _api_reorder(payload_display_order: int) -> dict[str, object]:
            return _http(
                base_url,
                f"/api/v1/projects/episodes/{target_episode['id']}:reorder?{urlencode({'display_order': payload_display_order, 'expected_revision': concurrent_expected_revision})}",
                method="POST",
                headers={"X-Local-Instance-Token": instance_token},
            )

        start_concurrent = threading.Event()

        def _run_concurrent_reorder(payload_display_order: int) -> dict[str, object]:
            start_concurrent.wait(timeout=10)
            return _api_reorder(payload_display_order)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(_run_concurrent_reorder, 1)
            future_b = pool.submit(_run_concurrent_reorder, 3)
            start_concurrent.set()
            concurrent_a = future_a.result(timeout=20)
            concurrent_b = future_b.result(timeout=20)

        concurrency_statuses = [concurrent_a.get("status"), concurrent_b.get("status")]
        api_concurrent_reorder_success = sum(
            1 for item in concurrency_statuses if item == 200
        )
        api_concurrent_conflict = sum(1 for item in concurrency_statuses if item == 409)
        concurrent_payloads = [
            {"display_order": 1, "response": concurrent_a},
            {"display_order": 3, "response": concurrent_b},
        ]

        api_delete_probe = _http(
            base_url,
            f"/api/v1/projects/seasons/{first_season['id']}",
            method="DELETE",
            headers={"X-Local-Instance-Token": instance_token},
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    checks = [
        _check(
            "PROJECT_CREATED_IN_ISOLATED_SQLITE",
            bool(project_id and project["code"] == "crud_windows_uat"),
            {"project_id": project_id},
            "real ProjectService transaction",
        ),
        _check(
            "SEASON_EPISODE_CRUD_READ",
            len(seasons_before) == 2 and len(second_episodes) == 3,
            {
                "seasons": len(seasons_before),
                "episodes_per_season": [
                    len(first_episodes_before),
                    len(second_episodes),
                ],
            },
            "season/episode structures are persisted and readable",
        ),
        _check(
            "MASTER_SCENE_AND_RANGE_BINDING",
            len(scenes) == 2 and len(ranges) == 2 and len(ranges_after) == 1,
            {
                "scene_ids": [item["id"] for item in scenes],
                "range_ids": [item["id"] for item in ranges],
            },
            "project scene identity is referenced by episode ranges",
        ),
        _check(
            "EPISODE_REORDER_IDENTITY_STABLE",
            moved_episode["id"] == target_episode["id"]
            and moved_episode["code"] == target_episode["code"]
            and [str(item["id"]) for item in episodes_after_move]
            == [
                str(target_episode["id"]),
                str(first_episodes_before[0]["id"]),
                str(first_episodes_before[1]["id"]),
            ],
            {
                "before": [
                    {
                        "id": item["id"],
                        "code": item["code"],
                        "order": item["display_order"],
                    }
                    for item in first_episodes_before
                ],
                "after": [
                    {
                        "id": item["id"],
                        "code": item["code"],
                        "order": item["display_order"],
                    }
                    for item in episodes_after_move
                ],
            },
            "display order changes while UUID/code stay stable",
        ),
        _check(
            "EPISODE_REORDER_EXPECTED_REVISION",
            stale_error == "REVISION_CONFLICT",
            {"error_code": stale_error},
            "stale reorder is rejected without overwrite",
        ),
        _check(
            "SHOT_REORDER_HISTORY_STABLE",
            shot_ids_after == list(reversed(shot_ids_before))
            and revision_ids_after == revisions_before
            and storyboard_result["plan_hash"] == storyboard_plan["plan_hash"],
            {
                "before_ids": shot_ids_before,
                "after_ids": shot_ids_after,
                "revision_ids_stable": revision_ids_after == revisions_before,
                "plan_hash": storyboard_result["plan_hash"],
            },
            "storyboard reorder preserves shot UUID and current revision references",
        ),
        _check(
            "LOOPBACK_API_READY",
            live.get("status") == 200,
            live,
            "real FastAPI process on 127.0.0.1",
        ),
        _check(
            "LOOPBACK_DOMAIN_READS",
            season_read.get("status") == 200
            and episode_read.get("status") == 200
            and scene_read.get("status") == 200
            and shot_read.get("status") == 200,
            {
                "seasons": season_read.get("status"),
                "episodes": episode_read.get("status"),
                "scenes": scene_read.get("status"),
                "shots": shot_read.get("status"),
            },
            "API reads expose the same isolated data",
        ),
        _check(
            "LOOPBACK_REORDER_AND_STALE_409",
            bootstrap.get("status") == 200
            and bool(instance_token)
            and api_reorder.get("status") == 200
            and api_stale.get("status") == 409
            and isinstance(api_stale.get("payload"), dict)
            and api_stale["payload"].get("error", {}).get("code")
            == "REVISION_CONFLICT",
            {"bootstrap": bootstrap, "reorder": api_reorder, "stale": api_stale},
            "API preserves optimistic concurrency contract after local session bootstrap",
        ),
        _check(
            "LOOPBACK_EPISODE_REORDER_CONCURRENCY",
            api_concurrent_reorder_success == 1 and api_concurrent_conflict >= 1,
            {"responses": concurrency_statuses, "details": concurrent_payloads},
            "Two concurrent reorder calls with same expected_revision should produce one successful write and one conflict",
        ),
        _check(
            "DESTRUCTIVE_CHILD_DELETE_PROBE",
            api_delete_probe.get("status") in (404, 405),
            {
                "status": api_delete_probe.get("status"),
                "payload": api_delete_probe.get("payload"),
            },
            "Season delete API remains unimplemented in this bounded run; FR-PRJ-004 remains PARTIAL pending delete/archive implementation",
        ),
    ]
    result: dict[str, object] = {
        "schema_version": "g10.fr-prj-004.windows-uat.v1",
        "status": "PARTIAL",
        "scope": ["FR-PRJ-004", "TC-DOM-003", "TC-DOM-004"],
        "platform": {
            "system": sys.platform,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "local_only": True,
        "loopback_endpoint": base_url,
        "isolation": {
            "root_rel": root.relative_to(ROOT).as_posix()
            if root.is_relative_to(ROOT)
            else str(root),
            "database_rel": settings.database_path.relative_to(ROOT).as_posix()
            if settings.database_path.is_relative_to(ROOT)
            else str(settings.database_path),
            "production_database_contacted": False,
            "production_projects_root_contacted": False,
            "public_network_contacted": False,
            "provider_contacted": False,
        },
        "inventory": {
            "implemented_service_paths": [
                "ProjectService.create_project (seasons/episodes)",
                "ProjectService.create_scene/list_scenes/bind_episode_scene_range",
                "ProjectService.create_shot/list_shots + ShotStudioCommandService.save_draft",
                "ProjectService.reorder_episode (normalized siblings + expected_revision)",
                "ProjectService.plan_storyboard_batch/commit_storyboard_batch",
            ],
            "api_paths": [
                "GET /projects/{project_id}/seasons",
                "GET /projects/seasons/{season_id}/episodes",
                "GET/POST /projects/{project_id}/scenes",
                "GET /projects/episodes/{episode_id}/shots",
                "POST /projects/episodes/{episode_id}:reorder",
            ],
            "not_claimed": [
                "No delete UI/API for season/episode/scene/shot was added in this bounded task; FR-PRJ-004 remains PARTIAL."
            ],
        },
        "fixture": {
            "project_id": project_id,
            "season_ids": [str(item["id"]) for item in seasons_before],
            "episode_ids": [
                str(item["id"]) for item in first_episodes_before + second_episodes
            ],
            "scene_ids": [str(item["id"]) for item in scenes],
            "shot_ids": shot_ids_before,
            "range_ids": [str(item["id"]) for item in ranges],
        },
        "checks": checks,
        "runtime_contacted": True,
        "network_contacted": True,
        "loopback_network_contacted": True,
        "public_network_contacted": False,
        "production_database_contacted": False,
        "production_mutated": False,
        "limitations": [
            "Fresh local SQLite and loopback API were exercised on Windows, but this is not a production database or browser UAT.",
            "The bounded fixture uses two seasons, six episodes, two master scenes, and three shots; release-scale volume and concurrent writers remain unverified.",
            "FR-PRJ-004 remains PARTIAL because destructive CRUD (delete/archive semantics for child entities) is not claimed by this evidence.",
        ],
        "observed_at": _now(),
    }
    if not keep_root:
        shutil.rmtree(root, ignore_errors=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT
        / "work"
        / f"project-crud-windows-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "docs"
        / "evidence"
        / "g10"
        / "fr-prj-004-windows-uat-2026-08-16.json",
    )
    parser.add_argument("--remove-root", action="store_true")
    args = parser.parse_args()
    if args.serve:
        _serve(args.root.resolve(), args.port or _find_free_port())
        return
    result = run(root=args.root, port=args.port, keep_root=not args.remove_root)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(output),
                "checks": {item["code"]: item["status"] for item in result["checks"]},
            },
            ensure_ascii=False,
        )
    )
    if any(item["status"] == "FAIL" for item in result["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
