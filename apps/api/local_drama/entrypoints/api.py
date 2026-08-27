from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import uvicorn

from local_drama.config import Settings
from local_drama.main import create_app


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
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
