from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from local_drama.bootstrap.config_migrations import migrate_payload


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["LOCAL_ONLY", "LAN_SERVICE"] = "LOCAL_ONLY"
    host: str = "127.0.0.1"
    port: int = Field(default=3210, ge=1, le=65535)
    allowed_origins: tuple[str, ...] = ()
    firewall_remote_address: str = "LocalSubnet"
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
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_provider: str = "LLAMA_CPP_MANAGED"
    llm_base_url: str = "http://127.0.0.1:28088"
    llm_model: str | None = None
    llama_server_bin: Path | None = None
    llama_model_path: Path | None = None
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = Field(default=8101, ge=1, le=65535)
    llama_gateway_enabled: bool = False
    llama_gateway_host: str = "127.0.0.1"
    llama_gateway_port: int = Field(default=28088, ge=1, le=65535)
    llama_idle_timeout_seconds: int = Field(default=300, ge=10, le=86400)
    llama_ctx_size: int = Field(default=8192, gt=0)
    llama_gpu_layers: int = Field(default=99, ge=0)
    llama_flash_attn: str = Field(default="auto", pattern="^(on|off|auto)$")
    llama_kv_cache_type: str = Field(default="q8_0", pattern="^(|f16|bf16|q8_0|q4_0)$")
    llama_mtp_enabled: bool = False
    llama_mtp_draft_tokens: int = Field(default=2, ge=1, le=16)
    llama_server_args: tuple[str, ...] = ()
    llama_startup_timeout_seconds: float = Field(default=180.0, gt=0)
    gpu_switch_min_free_ratio: float = Field(default=0.80, ge=0.05, le=1.0)
    model_root: Path | None = None
    model_library_roots: tuple[Path, ...] = ()
    model_download_source_hosts: tuple[str, ...] = ()
    worker_channels: tuple[str, ...] = ("CPU", "GPU_H3")
    local_ai_python: Path | None = None
    local_ai_adapter: Path | None = None
    local_ai_model_root: Path | None = None
    latentsync_python: Path | None = None
    latentsync_root: Path | None = None

    @field_validator("model_download_source_hosts")
    @classmethod
    def validate_model_download_source_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().casefold() for item in value if item.strip())
        if any("/" in item or ":" in item or "@" in item or " " in item for item in normalized):
            raise ValueError("model_download_source_hosts must contain host names only")
        if len(set(normalized)) != len(normalized):
            raise ValueError("model_download_source_hosts must not contain duplicates")
        return normalized


class MachineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[3] = 3
    instance_id: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    environment: str = "production"
    install_profile: Literal["DESKTOP", "SERVER"] = "DESKTOP"
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    frontend_dist: Path | None = None
    model_manifest: Path | None = None


_VARIABLE = re.compile(r"\$\{(?P<name>RELEASE_ROOT|INSTANCE_ROOT|MODEL_ROOT)\}")


def _expand_path(
    path: Path | None,
    *,
    release_root: Path,
    instance_root: Path,
    model_root: Path | None = None,
) -> Path | None:
    if path is None:
        return None
    replacements = {"RELEASE_ROOT": str(release_root), "INSTANCE_ROOT": str(instance_root)}
    if model_root is not None:
        replacements["MODEL_ROOT"] = str(model_root)
    missing = sorted({match.group("name") for match in _VARIABLE.finditer(str(path)) if match.group("name") not in replacements})
    if missing:
        raise ValueError(f"path references unavailable variables: {', '.join(missing)}")
    raw = _VARIABLE.sub(lambda match: replacements[match.group("name")], str(path))
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = instance_root / candidate
    return candidate.resolve()


def load_machine_config(path: Path, *, release_root: Path, instance_root: Path) -> MachineConfig | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"machine config is not valid JSON: {path}") from error
    # The installer/Host persists this migration under backup during upgrade.
    # This pure fallback keeps source and developer imports read-only while
    # honoring the exact same central migration ladder.
    config = MachineConfig.model_validate(migrate_payload(payload))
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
            "fallback_dirs": tuple(
                str(_expand_path(Path(directory), release_root=release_root, instance_root=instance_root))
                for directory in config.tools.fallback_dirs
            ),
        }
    )
    model_root = _expand_path(config.runtime.model_root, release_root=release_root, instance_root=instance_root)
    runtime_paths = {
        name: _expand_path(
            getattr(config.runtime, name),
            release_root=release_root,
            instance_root=instance_root,
            model_root=model_root,
        )
        for name in (
            "local_ai_python",
            "local_ai_adapter",
            "local_ai_model_root",
            "latentsync_python",
            "latentsync_root",
            "llama_server_bin",
            "llama_model_path",
        )
    }
    runtime = config.runtime.model_copy(
        update={
            **runtime_paths,
            "model_root": model_root,
            "model_library_roots": tuple(
                _expand_path(root, release_root=release_root, instance_root=instance_root, model_root=model_root)
                for root in config.runtime.model_library_roots
            ),
        }
    )
    return config.model_copy(
        update={
            "storage": storage,
            "tools": tools,
            "runtime": runtime,
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
        "ollama_base_url": config.runtime.ollama_base_url,
        "llm_provider": config.runtime.llm_provider,
        "llm_base_url": config.runtime.llm_base_url,
        "llm_model": config.runtime.llm_model,
        "llama_server_bin": config.runtime.llama_server_bin,
        "llama_model_path": config.runtime.llama_model_path,
        "llama_server_host": config.runtime.llama_server_host,
        "llama_server_port": config.runtime.llama_server_port,
        "llama_gateway_enabled": config.runtime.llama_gateway_enabled,
        "llama_gateway_host": config.runtime.llama_gateway_host,
        "llama_gateway_port": config.runtime.llama_gateway_port,
        "llama_idle_timeout_seconds": config.runtime.llama_idle_timeout_seconds,
        "llama_ctx_size": config.runtime.llama_ctx_size,
        "llama_gpu_layers": config.runtime.llama_gpu_layers,
        "llama_flash_attn": config.runtime.llama_flash_attn,
        "llama_kv_cache_type": config.runtime.llama_kv_cache_type,
        "llama_mtp_enabled": config.runtime.llama_mtp_enabled,
        "llama_mtp_draft_tokens": config.runtime.llama_mtp_draft_tokens,
        "llama_server_args": config.runtime.llama_server_args,
        "llama_startup_timeout_seconds": config.runtime.llama_startup_timeout_seconds,
        "gpu_switch_min_free_ratio": config.runtime.gpu_switch_min_free_ratio,
        "model_root": config.runtime.model_root,
        "model_library_roots": config.runtime.model_library_roots,
        "model_download_source_hosts": config.runtime.model_download_source_hosts,
        "worker_channels": config.runtime.worker_channels,
        "local_ai_python": config.runtime.local_ai_python,
        "local_ai_adapter": config.runtime.local_ai_adapter,
        "local_ai_model_root": config.runtime.local_ai_model_root,
        "latentsync_python": config.runtime.latentsync_python,
        "latentsync_root": config.runtime.latentsync_root,
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
