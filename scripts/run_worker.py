"""Run or inspect one durable LOCAL_ONLY worker supervisor session."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
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
    parser.add_argument("--watch", action="store_true", help="常驻轮询本机队列，直到收到停止信号")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="常驻模式空队列轮询间隔")
    parser.add_argument("--stop-file", help="常驻模式的本机停止信号文件")
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
    stop_requested = threading.Event()
    stop_file = Path(args.stop_file).resolve() if args.stop_file else None
    if args.watch and stop_file is None:
        parser.error("--watch requires --stop-file")
    if stop_file is not None:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.unlink(missing_ok=True)

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    if args.watch:
        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)
    result = WorkerSupervisor(database, settings).run_until_idle(
        args.worker_id,
        channels=[item.strip().upper() for item in args.channels.split(",") if item.strip()],
        max_jobs=None if args.watch else args.max_jobs,
        max_restarts=args.max_restarts,
        worker_version=args.worker_version,
        api_version=args.api_version,
        idle_poll_seconds=args.poll_seconds if args.watch else None,
        should_stop=(lambda: stop_requested.is_set() or bool(stop_file and stop_file.exists())) if args.watch else None,
    )
    if stop_file is not None:
        stop_file.unlink(missing_ok=True)
    # This is the persisted supervisor state, not an inference from a browser
    # tab.  INCOMPATIBLE exits non-zero so launch scripts fail closed.
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 2 if result["status"] == "INCOMPATIBLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
