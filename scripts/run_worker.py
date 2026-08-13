"""Run one LOCAL_ONLY media worker until its queue is idle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.worker import LocalMediaWorker
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--max-jobs", type=int, default=100)
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.ensure_roots()
    results = LocalMediaWorker(
        Database(settings.database_path), settings
    ).run_until_idle(args.worker_id, args.max_jobs)
    print(
        json.dumps(
            {
                "worker_id": args.worker_id,
                "processed": len(results),
                "results": results,
            },
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
