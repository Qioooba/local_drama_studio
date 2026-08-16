"""Run one LOCAL_ONLY media worker against an isolated simulation root.

The worker must never touch the production database: Settings are built
explicitly from the sim root (like the sim API server) instead of from_env.
It first drains CPU-channel jobs with LocalMediaWorker, then drains GPU_H3
jobs (real Comfy submission) through ComfyGenerationService exactly like the
platform's ephemeral one-task worker policy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from local_drama.application.comfy_jobs import (  # type: ignore[import-not-found]
    ComfyGenerationService,  # type: ignore[import-not-found]
)
from local_drama.application.media import MediaService  # type: ignore[import-not-found]
from local_drama.application.worker import (  # type: ignore[import-not-found]
    LocalMediaWorker,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.infrastructure.database.sqlite import (  # type: ignore[import-not-found]
    Database,  # type: ignore[import-not-found]
)


def build_settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=ROOT / "work" / "comfy-production" / "output",
        comfy_input_root=ROOT / "work" / "comfy-production" / "input",
    )


def _drain_gpu(database: Database, settings: Settings, worker_id: str) -> list[dict[str, object]]:
    service = ComfyGenerationService(database, settings)
    drained: list[dict[str, object]] = []
    while True:
        submission = service.submit_next(worker_id)
        if submission is None:
            break
        attempt_id = str(submission["attempt"]["id"])
        poll_states: list[str] = []
        result: dict[str, object] | None = None
        deadline = time.monotonic() + 1500
        while time.monotonic() < deadline:
            result = service.poll_attempt(attempt_id, worker_id)
            state = str(result.get("status"))
            poll_states.append(state)
            if state in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(5)
        drained.append(
            {"job_id": str(submission["job"]["id"]), "attempt_id": attempt_id, "status": str(result.get("status")) if result else "TIMEOUT", "poll_states": poll_states}
        )
        # Promote the real Comfy artifact into a reviewable MediaVersion so the
        # workbench candidate shelf and the review inbox surface the take
        # (mirrors the explicit promote-media API used by the production flow).
        if result and result.get("status") == "SUCCEEDED" and result.get("artifacts"):
            media_service = MediaService(database, settings)
            promoted = media_service.promote_job_artifact(
                str(result["artifacts"][0]["id"]), purpose="SHOT_VIDEO", media_kind="VIDEO", stage="PROXY"
            )
            drained[-1]["promoted_media_version_id"] = str(promoted["media_version_id"])
    return drained


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="isolated simulation root")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--loop", action="store_true", help="keep draining until killed")
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "data" / "local_drama.sqlite3").is_file():
        raise RuntimeError(f"SIM_ROOT_INVALID: {root}")
    settings = build_settings(root)
    settings.ensure_roots()
    database = Database(settings.database_path)
    cpu_results: list[dict[str, object]] = []
    gpu_results: list[dict[str, object]] = []
    while True:
        cpu_results = LocalMediaWorker(database, settings).run_until_idle(args.worker_id, args.max_jobs)
        gpu_results = _drain_gpu(database, settings, args.worker_id)
        if not args.loop:
            break
        time.sleep(5)
    print(
        json.dumps(
            {"worker_id": args.worker_id, "cpu_processed": len(cpu_results), "cpu_results": cpu_results, "gpu_processed": len(gpu_results), "gpu_results": gpu_results},
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
