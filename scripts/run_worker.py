"""Run or inspect one durable LOCAL_ONLY worker supervisor session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.storage_operations import StorageOperationService
from local_drama.application.worker_sessions import (
    WorkerSessionService,
    WorkerSupervisor,
)
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id")
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--channels", default="CPU", help="逗号分隔的 Job channel")
    parser.add_argument("--worker-version")
    parser.add_argument("--api-version")
    parser.add_argument("--status", action="store_true", help="只读输出已持久化 WorkerSession 状态")
    parser.add_argument("--reconcile", action="store_true", help="不启动 worker，仅恢复过期 session/Job lease 与 storage operation")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.ensure_roots()
    database = Database(settings.database_path)
    sessions = WorkerSessionService(database, settings)
    if args.status:
        print(json.dumps({"sessions": sessions.list_sessions(), "mutated": False}, ensure_ascii=False, default=str))
        return 0
    if args.reconcile and not args.worker_id:
        result = {
            "worker_sessions": sessions.reconcile(),
            "storage_operations": StorageOperationService(database, settings).reconcile(),
        }
        print(json.dumps({"reconcile": result}, ensure_ascii=False, default=str))
        return 0
    if not args.worker_id:
        parser.error("--worker-id is required unless --status or --reconcile is used")
    result = WorkerSupervisor(database, settings).run_until_idle(
        args.worker_id,
        channels=[item.strip().upper() for item in args.channels.split(",") if item.strip()],
        max_jobs=args.max_jobs,
        max_restarts=args.max_restarts,
        worker_version=args.worker_version,
        api_version=args.api_version,
    )
    # This is the persisted supervisor state, not an inference from a browser
    # tab.  INCOMPATIBLE exits non-zero so launch scripts fail closed.
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 2 if result["status"] == "INCOMPATIBLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
