"""Run a bounded, local-only observation of list read paths.

This is intentionally smaller than the release performance gate.  It builds the
same 60-episode/800-shot/10k-media isolated fixture as ``g10_scale_uat`` and
measures bounded pages through the real FastAPI app.  The output is evidence for
pagination/read-path coverage, not a claim that NFR-PERF-001's 10k p95 or
NFR-PERF-002's Windows/browser target has passed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.g10_scale_uat import build_fixture


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20, method="inclusive")[18] if len(values) >= 2 else values[0]


def _timed_get(client: TestClient, path: str, repeats: int = 3) -> dict[str, Any]:
    elapsed: list[float] = []
    last: Any = None
    statuses: list[int] = []
    for _ in range(repeats):
        started = time.perf_counter()
        response = client.get(path)
        elapsed.append((time.perf_counter() - started) * 1000)
        statuses.append(response.status_code)
        raw = response.json()
        # Keep evidence small: retain response shape/page metadata, never the
        # 50 media rows themselves.
        last = {
            "keys": sorted(raw.keys()),
            "item_count": len(raw.get("items", [])) if isinstance(raw.get("items"), list) else None,
            "next_cursor": raw.get("next_cursor"),
            "page": raw.get("page"),
        }
    return {"path": path, "status_codes": statuses, "p95_ms_observed": round(_p95(elapsed), 3), "max_ms_observed": round(max(elapsed), 3), "last": last}


def run(root: Path) -> dict[str, Any]:
    settings, project_id, episode_ids, _shot_ids, build_seconds = build_fixture(root)
    app = create_app(settings)
    pages: list[dict[str, Any]] = []
    with TestClient(app) as client:
        # Review inbox is the 10k-media path.  Include a deep bounded cursor to
        # exercise the existing stable cursor contract without loading all rows.
        for cursor in (0, 50, 5_000, 9_950):
            page = _timed_get(client, f"/api/v1/reviews/inbox?project_id={project_id}&cursor={cursor}&limit=50")
            payload = page["last"]
            page["cursor"] = cursor
            page["returned"] = int(payload.get("item_count") or 0)
            page["next_cursor"] = payload.get("next_cursor")
            page["bounded"] = page["returned"] <= 50
            pages.append({"kind": "review_inbox", **page})

        # Every production read is capped.  Sample all episodes so this covers
        # the 800-shot fixture without asking any endpoint for an unbounded list.
        production_pages = []
        for episode_id in episode_ids:
            page = _timed_get(client, f"/api/v1/episodes/{episode_id}/production?cursor=0&limit=10", repeats=1)
            payload = page["last"]
            production_pages.append({"episode_id": episode_id, "returned": int(payload.get("item_count") or 0), "page": payload.get("page")})
        pages.append({"kind": "production_read_model", "episode_count": len(production_pages), "bounded_pages": all(item["returned"] <= 10 for item in production_pages), "sample": production_pages[:3]})

        projects = _timed_get(client, "/api/v1/projects?cursor=0&limit=50")
        projects["kind"] = "project_list"
        projects["returned"] = int(projects["last"].get("item_count") or 0)
        projects["page"] = projects["last"].get("page")
        projects["bounded"] = projects["returned"] <= 50
        pages.append(projects)

    # Source-level lazy/network contract complements browser tests.  It is not a
    # substitute for a Playwright network trace on Windows x64.
    lazy_sources = [
        ROOT / "apps" / "web" / "src" / "features" / "reviews" / "ReviewInboxPanel.tsx",
        ROOT / "apps" / "web" / "src" / "features" / "generation" / "GenerationWorkbench.tsx",
        ROOT / "apps" / "web" / "src" / "features" / "production" / "ContinuityPanel.tsx",
    ]
    lazy_contract = []
    for path in lazy_sources:
        source = path.read_text(encoding="utf-8")
        lazy_contract.append({"path": path.relative_to(ROOT).as_posix(), "lazy_thumbnail_count": source.count('loading="lazy"'), "requests_original_content": "/content" in source})

    database = Database(settings.database_path)
    with database.connect() as connection:
        counts = {
            "episodes": int(connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]),
            "shots": int(connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0]),
            "media_versions": int(connection.execute("SELECT COUNT(*) FROM media_versions").fetchone()[0]),
        }
        migration_head = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
    return {
        "schema_version": "nfr.performance.bounded-observation.v1",
        "status": "PARTIAL",
        "benchmark_status": "OBSERVED_NOT_BENCHMARKED",
        "scope": "isolated migrated SQLite + real FastAPI TestClient read paths",
        "fixture": {**counts, "synthetic_media_integrity": "UNKNOWN", "playable_media_claimed": False},
        "migration_head": migration_head,
        "build_seconds": round(build_seconds, 3),
        "pages": pages,
        "lazy_thumbnail_contract": lazy_contract,
        "bounded_contracts_passed": all(item.get("bounded", True) and item.get("bounded_pages", True) for item in pages),
        "runtime_contacted": False,
        "network_contacted": False,
        "interpretation": "This bounded observation exercises cursor/page caps and source-level lazy thumbnail policy. It does not claim NFR-PERF-001 10k p95 <500ms, NFR-PERF-002 Windows/browser virtual-scroll UAT, cold-cache behavior, or production hardware capacity.",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"nfr-perf-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "nfr-perf-bounded-observation-2026-08-16.json")
    args = parser.parse_args()
    result = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "benchmark_status": result["benchmark_status"], "output": str(args.output)}, ensure_ascii=False))
    if not result["bounded_contracts_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
