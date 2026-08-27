from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["LOCAL_ONLY", "LAN_SERVICE"] = "LOCAL_ONLY"
    host: str = "127.0.0.1"
    port: int = Field(default=3210, ge=1, le=65535)
    allowed_origins: tuple[str, ...] = ()
    trusted_lan_unauthenticated: bool = False


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_root: Path | None = None
    projects_root: Path | None = None
    work_root: Path | None = None
    cache_root: Path | None = None
    logs_root: Path | None = None
    backups_root: Path | None = None


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    fallback_dirs: tuple[str, ...] = ()


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comfy_base_url: str = "http://127.0.0.1:8188"
    llm_provider: str = "OLLAMA_LOOPBACK"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str | None = None
    model_library_roots: tuple[Path, ...] = ()
    worker_channels: tuple[str, ...] = ("CPU", "GPU_H3")


class MachineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    instance_id: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    environment: str = "production"
    install_profile: Literal["DESKTOP", "SERVER"] = "DESKTOP"
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    frontend_dist: Path | None = None
    model_manifest: Path | None = None


_VARIABLE = re.compile(r"\$\{(?P<name>RELEASE_ROOT|INSTANCE_ROOT)\}")


def _expand_path(path: Path | None, *, release_root: Path, instance_root: Path) -> Path | None:
    if path is None:
        return None
    replacements = {"RELEASE_ROOT": str(release_root), "INSTANCE_ROOT": str(instance_root)}
    raw = _VARIABLE.sub(lambda match: replacements[match.group("name")], str(path))
    return Path(raw).expanduser().resolve()


def load_machine_config(path: Path, *, release_root: Path, instance_root: Path) -> MachineConfig | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"machine config is not valid JSON: {path}") from error
    config = MachineConfig.model_validate(payload)
    storage = config.storage.model_copy(
        update={
            name: _expand_path(getattr(config.storage, name), release_root=release_root, instance_root=instance_root)
            for name in ("data_root", "projects_root", "work_root", "cache_root", "logs_root", "backups_root")
        }
    )
    tools = config.tools.model_copy(
        update={
            "ffmpeg": _expand_path(config.tools.ffmpeg, release_root=release_root, instance_root=instance_root),
            "ffprobe": _expand_path(config.tools.ffprobe, release_root=release_root, instance_root=instance_root),
        }
    )
    return config.model_copy(
        update={
            "storage": storage,
            "tools": tools,
            "frontend_dist": _expand_path(config.frontend_dist, release_root=release_root, instance_root=instance_root),
            "model_manifest": _expand_path(config.model_manifest, release_root=release_root, instance_root=instance_root),
        }
    )


def settings_values(config: MachineConfig) -> dict[str, Any]:
    values: dict[str, Any] = {
        "environment": config.environment,
        "install_profile": config.install_profile,
        "instance_id": config.instance_id,
        "network_mode": config.network.mode,
        "host": config.network.host,
        "port": config.network.port,
        "trusted_lan_unauthenticated": config.network.trusted_lan_unauthenticated,
        "comfy_base_url": config.runtime.comfy_base_url,
        "llm_provider": config.runtime.llm_provider,
        "llm_base_url": config.runtime.llm_base_url,
        "llm_model": config.runtime.llm_model,
        "model_library_roots": config.runtime.model_library_roots,
        "worker_channels": config.runtime.worker_channels,
        "tool_fallback_dirs": config.tools.fallback_dirs,
        "ffmpeg_override": config.tools.ffmpeg,
        "ffprobe_override": config.tools.ffprobe,
        "frontend_dist_root": config.frontend_dist,
        "model_manifest_override": config.model_manifest,
    }
    if config.network.allowed_origins:
        values["allowed_origins"] = config.network.allowed_origins
    for name in ("data_root", "projects_root", "work_root", "cache_root", "logs_root", "backups_root"):
        value = getattr(config.storage, name)
        if value is not None:
            values[name] = value
    return values
