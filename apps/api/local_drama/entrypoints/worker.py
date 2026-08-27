from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from collections.abc import Sequence
from pathlib import Path

from local_drama.application.storage_operations import StorageOperationService
from local_drama.application.worker_sessions import WorkerSessionService, WorkerSupervisor
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.logging_setup import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-drama-worker")
    parser.add_argument("--config")
    parser.add_argument("--worker-id")
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--channels")
    parser.add_argument("--worker-version")
    parser.add_argument("--api-version")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--stop-file")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["LOCAL_DRAMA_CONFIG"] = args.config
    settings = Settings.from_env()
    settings.ensure_roots()
    configure_logging(settings, process_role="worker")
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
    channels = args.channels or ",".join(settings.worker_channels)
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
        channels=[item.strip().upper() for item in channels.split(",") if item.strip()],
        max_jobs=None if args.watch else args.max_jobs,
        max_restarts=args.max_restarts,
        worker_version=args.worker_version,
        api_version=args.api_version,
        idle_poll_seconds=args.poll_seconds if args.watch else None,
        should_stop=(lambda: stop_requested.is_set() or bool(stop_file and stop_file.exists())) if args.watch else None,
    )
    if stop_file is not None:
        stop_file.unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 2 if result["status"] == "INCOMPATIBLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
