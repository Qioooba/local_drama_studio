from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from urllib.request import Request, urlopen

import uvicorn

from local_drama.config import Settings
from local_drama.main import create_app


def _gateway_ready(settings: Settings) -> bool:
    host = "127.0.0.1" if settings.llama_gateway_host in {"0.0.0.0", "::", "*"} else settings.llama_gateway_host
    try:
        with urlopen(Request(f"http://{host}:{settings.llama_gateway_port}/health"), timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("gateway") is True
    except (OSError, TimeoutError, ValueError):
        return False


def _start_gateway(settings: Settings) -> subprocess.Popen[bytes] | None:
    if not settings.llama_gateway_enabled or _gateway_ready(settings):
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "local_drama.entrypoints.llama_gateway", "--config", str(settings.config_path)],
        creationflags=creationflags,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and process.poll() is None:
        if _gateway_ready(settings):
            return process
        time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
    raise RuntimeError("llama gateway did not become ready on its configured port")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-drama-api")
    parser.add_argument("--config")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["LOCAL_DRAMA_CONFIG"] = args.config
    if args.host:
        os.environ["LOCAL_DRAMA_HOST"] = args.host
    if args.port:
        os.environ["LOCAL_DRAMA_PORT"] = str(args.port)
    # Direct API launch is used by development and portable installs. Make it
    # creator-ready by pairing it with an in-process worker; Runtime Host sets
    # LOCAL_DRAMA_MANAGED_WORKER and keeps its dedicated worker topology.
    if os.environ.get("LOCAL_DRAMA_MANAGED_WORKER") != "1":
        os.environ.setdefault("LOCAL_DRAMA_EMBEDDED_WORKER", "1")
    settings = Settings.from_env()
    gateway_process = _start_gateway(settings)
    try:
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            reload=args.reload,
            access_log=False,
        )
    finally:
        if gateway_process is not None and gateway_process.poll() is None:
            gateway_process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
