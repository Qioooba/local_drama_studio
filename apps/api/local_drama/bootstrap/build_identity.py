from __future__ import annotations

import json
from dataclasses import dataclass

from .resource_locator import ResourceLocator


@dataclass(frozen=True)
class BuildIdentity:
    version: str
    channel: str
    host_protocol: int
    worker_protocol: int
    config_schema: int
    project_package_schema: int


def load_build_identity(locator: ResourceLocator | None = None) -> BuildIdentity:
    resolved = locator or ResourceLocator.discover()
    try:
        payload = json.loads(resolved.version_path.read_text(encoding="utf-8"))
        return BuildIdentity(
            version=str(payload["version"]),
            channel=str(payload["channel"]),
            host_protocol=int(payload["host_protocol"]),
            worker_protocol=int(payload["worker_protocol"]),
            config_schema=int(payload["config_schema"]),
            project_package_schema=int(payload["project_package_schema"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"release version identity is invalid: {resolved.version_path}") from error
