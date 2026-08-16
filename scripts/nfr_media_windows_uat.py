"""Bounded Windows x64 LOCAL_ONLY UAT for the media Range/first-frame contract.

The run creates a fresh migrated SQLite database and project root, registers a
user-selected local video through the real ``MediaService``, serves the real
FastAPI application on a literal loopback port, and exercises the HTTP media
routes with a proxy-free opener.  It never uses the production database or
project tree.  The resulting evidence is intentionally ``PARTIAL``: this is a
real Windows/local observation, not a release-scale codec or endurance claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
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

LOOPBACK_OPENER = build_opener(ProxyHandler({}))
DEFAULT_SOURCE = (
    ROOT
    / "work"
    / "comfy-production"
    / "output"
    / "family_redfruit_series_001"
    / "uat"
    / "SHOT_001_seed_20260816_00001_.mp4"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def build_settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=root / "work" / "comfy-production" / "output",
        comfy_input_root=root / "work" / "comfy-production" / "input",
        port=port,
        allowed_origins=(f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _http_request(base_url: str, path: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> dict[str, object]:
    started = time.perf_counter()
    request = Request(f"{base_url.rstrip('/')}{path}", method=method, headers=headers or {})
    try:
        with LOOPBACK_OPENER.open(request, timeout=15) as response:
            body = response.read()
            return {
                "status": int(response.status),
                "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest() if body else None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": None,
            }
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        return {
            "status": None,
            "headers": {},
            "bytes": 0,
            "sha256": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": type(error).__name__,
        }


def _check_range_result(result: dict[str, object], *, expected_status: int, expected_bytes: int, total_bytes: int) -> bool:
    headers = dict(result.get("headers", {}))
    return (
        result.get("status") == expected_status
        and result.get("bytes") == expected_bytes
        and headers.get("accept-ranges") == "bytes"
        and headers.get("content-range", "").endswith(f"/{total_bytes}")
        and not result.get("error")
    )


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return float(quantiles(values, n=20, method="inclusive")[-1])


def _probe(path: Path, ffprobe: str | None) -> dict[str, object]:
    if not ffprobe or not Path(ffprobe).is_file():
        return {"status": "BLOCKED", "reason": "ffprobe_not_found"}
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return {"status": "FAIL", "reason": "ffprobe_failed"}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "FAIL", "reason": "ffprobe_json_invalid"}
    format_info = dict(payload.get("format", {}))
    stream_info = [
        {
            "codec_type": item.get("codec_type"),
            "codec_name": item.get("codec_name"),
            "width": item.get("width"),
            "height": item.get("height"),
            "duration": item.get("duration"),
            "r_frame_rate": item.get("r_frame_rate"),
            "sample_rate": item.get("sample_rate"),
            "channels": item.get("channels"),
        }
        for item in payload.get("streams", [])
        if isinstance(item, dict)
    ]
    return {
        "status": "PASS",
        "format": {
            "format_name": format_info.get("format_name"),
            "duration": format_info.get("duration"),
            "size": format_info.get("size"),
            "probe_score": format_info.get("probe_score"),
        },
        "streams": stream_info,
    }


def _serve(root: Path, port: int) -> None:
    import uvicorn
    from local_drama.main import create_app

    uvicorn.run(create_app(build_settings(root, port)), host="127.0.0.1", port=port, log_level="warning")


def run(*, root: Path, source: Path, port: int | None = None, keep_root: bool = True) -> dict[str, object]:
    root = root.resolve()
    source = source.resolve()
    port = port or _find_free_port()
    settings = build_settings(root, port)
    settings.ensure_roots()
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"NFR_MEDIA_SOURCE_MISSING: {source}")
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="nfr_media_windows_uat",
        title="NFR media Windows UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    media = MediaService(database, settings).import_file(project_id, source, purpose="NFR_UAT")
    media_version_id = str(media["media_version_id"])
    total_bytes = int(media["byte_size"])
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root), "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
    )
    try:
        ready = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = _http_request(base_url, "/api/v1/health/live")
            if ready.get("status") == 200:
                break
            time.sleep(0.1)
        content_path = f"/api/v1/media-versions/{media_version_id}/content"
        thumbnail_path = f"/api/v1/media-versions/{media_version_id}/thumbnail?size=small&frame=first"
        head = _http_request(base_url, content_path, method="HEAD")
        first_range = _http_request(base_url, content_path, headers={"Range": "bytes=0-65535"})
        suffix = _http_request(base_url, content_path, headers={"Range": "bytes=-65536"})
        first_frame_cold = _http_request(base_url, thumbnail_path)
        first_frame_warm = _http_request(base_url, thumbnail_path)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_http_request, base_url, content_path, headers={"Range": "bytes=0-65535"}) for _ in range(4)]
            concurrent = [future.result() for future in futures]
        range_ok = _check_range_result(first_range, expected_status=206, expected_bytes=65536, total_bytes=total_bytes)
        suffix_ok = _check_range_result(suffix, expected_status=206, expected_bytes=min(65536, total_bytes), total_bytes=total_bytes)
        thumbnail_ok = (
            first_frame_cold.get("status") == 200
            and first_frame_warm.get("status") == 200
            and str(dict(first_frame_cold.get("headers", {})).get("content-type", "")).startswith("image/webp")
            and first_frame_cold.get("bytes", 0) > 0
            and first_frame_warm.get("bytes", 0) > 0
        )
        concurrent_ok = all(_check_range_result(item, expected_status=206, expected_bytes=65536, total_bytes=total_bytes) for item in concurrent)
        result: dict[str, object] = {
            "schema_version": "g10.nfr-media-001.windows-uat.v1",
            "status": "PARTIAL",
            "scope": ["NFR-MEDIA-001"],
            "platform": {"system": sys.platform, "platform": platform.platform(), "python": sys.version.split()[0]},
            "local_only": True,
            "loopback_endpoint": base_url,
            "fixture": {
                "project_id": project_id,
                "media_version_id": media_version_id,
                "source_path_rel": source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source),
                "source_sha256": _sha256(source),
                "source_bytes": source.stat().st_size,
                "registered_bytes": total_bytes,
                "probe": _probe(source, settings.ffprobe_path),
            },
            "checks": [
                {"code": "LOOPBACK_API_READY", "status": "PASS" if ready and ready.get("status") == 200 else "FAIL", "observed": ready},
                {"code": "HEAD_RANGE_CONTRACT", "status": "PASS" if head.get("status") == 200 and dict(head.get("headers", {})).get("accept-ranges") == "bytes" else "FAIL", "observed": head},
                {"code": "SINGLE_RANGE_64K", "status": "PASS" if range_ok else "FAIL", "observed": first_range},
                {"code": "SUFFIX_RANGE_64K", "status": "PASS" if suffix_ok else "FAIL", "observed": suffix},
                {"code": "FIRST_FRAME_THUMBNAIL", "status": "PASS" if thumbnail_ok else "FAIL", "cold": first_frame_cold, "warm": first_frame_warm},
                {"code": "FOUR_WAY_RANGE_LOAD", "status": "PASS" if concurrent_ok else "FAIL", "workers": 4, "p95_ms": round(_p95([float(item["elapsed_ms"]) for item in concurrent]), 3), "observed": concurrent},
            ],
            "performance": {
                "first_frame_target_ms": 2000,
                "first_frame_cold_ms": first_frame_cold.get("elapsed_ms"),
                "first_frame_warm_ms": first_frame_warm.get("elapsed_ms"),
                "first_frame_target_passed": bool(thumbnail_ok and float(first_frame_cold.get("elapsed_ms", 999999)) < 2000),
                "four_way_range_p95_ms": round(_p95([float(item["elapsed_ms"]) for item in concurrent]), 3),
                "read_chunk_bound_bytes": 1024 * 1024,
                "whole_file_read": False,
            },
            "runtime_contacted": True,
            "network_contacted": False,
            "loopback_network_contacted": True,
            "public_network_contacted": False,
            "production_database_contacted": False,
            "production_mutated": False,
            "limitations": [
                "Real Windows x64 loopback API and actual local H3 MP4 were exercised, but this is a bounded short run rather than a sustained codec/load benchmark.",
                "The four-way load uses bounded Range reads, not four full browser playback sessions.",
                "NFR-MEDIA-001 remains PARTIAL until representative codecs, cold-cache behavior and release-scale Windows/browser UAT are signed off.",
            ],
            "observed_at": _now(),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if not keep_root:
            import shutil

            shutil.rmtree(root, ignore_errors=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"nfr-media-windows-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "nfr-media-windows-uat-2026-08-16.json")
    parser.add_argument("--remove-root", action="store_true")
    args = parser.parse_args()
    if args.serve:
        _serve(args.root.resolve(), args.port or _find_free_port())
        return
    result = run(root=args.root, source=args.source, port=args.port, keep_root=not args.remove_root)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "checks": {item["code"]: item["status"] for item in result["checks"]}}, ensure_ascii=False))
    if any(item["status"] == "FAIL" for item in result["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
