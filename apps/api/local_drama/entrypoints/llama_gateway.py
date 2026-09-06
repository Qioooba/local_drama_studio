from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import uvicorn

from local_drama.config import Settings
from local_drama.infrastructure.llama_gateway import create_llama_gateway_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-drama-llama-gateway")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["LOCAL_DRAMA_CONFIG"] = args.config
    settings = Settings.from_env()
    if not settings.llama_gateway_enabled:
        raise SystemExit("llama gateway is disabled in machine configuration")
    uvicorn.run(
        create_llama_gateway_app(settings),
        host=settings.llama_gateway_host,
        port=settings.llama_gateway_port,
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
