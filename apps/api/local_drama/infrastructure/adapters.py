"""Static LOCAL_ONLY adapter contracts.

The registry is intentionally a read-only description layer.  It validates
transport and endpoint boundaries without opening sockets, starting processes,
loading models, or probing any provider.  Runtime clients remain responsible
for their own real execution and evidence.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
HTTP_TRANSPORTS = {"LOOPBACK_HTTP", "LOCAL_LLM_LOOPBACK"}
LOCAL_EXECUTABLE_TRANSPORTS = {"LOCAL_CLI", "LOCAL_PROCESS", "FFMPEG_LOCAL"}


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.casefold() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_adapter_target(transport: str, *, base_url: str | None = None, executable_ref: str | None = None) -> None:
    """Reject remote or ambiguous adapter targets before any runtime call."""

    if transport == "REMOTE_HTTP_SERVICE" or transport.startswith("REMOTE_"):
        raise DomainRuleError("REMOTE_PROVIDER_DISABLED_IN_LOCAL_RELEASE", "LOCAL_ONLY 禁止 REMOTE transport")
    if transport in HTTP_TRANSPORTS:
        if not base_url:
            raise DomainRuleError("LOOPBACK_ENDPOINT_REQUIRED", "loopback adapter 必须声明 base_url")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not _is_loopback_host(parsed.hostname):
            raise DomainRuleError("LOOPBACK_ONLY", "本地 adapter base_url 只能指向 loopback")
        if parsed.username or parsed.password:
            raise DomainRuleError("LOOPBACK_CREDENTIALS_FORBIDDEN", "loopback adapter URL 不得携带凭据")
        if parsed.query or parsed.fragment:
            raise DomainRuleError("LOOPBACK_ENDPOINT_AMBIGUOUS", "loopback adapter URL 不得携带 query 或 fragment")
        return
    if transport in LOCAL_EXECUTABLE_TRANSPORTS:
        if not executable_ref or not executable_ref.strip():
            raise DomainRuleError("LOCAL_EXECUTABLE_REQUIRED", "本地进程 adapter 必须声明 executable_ref")
        value = executable_ref.strip()
        if "://" in value or value.startswith(("\\\\", "//")):
            raise DomainRuleError("LOCAL_EXECUTABLE_REQUIRED", "executable_ref 必须是本机路径或可执行文件名")
        return
    raise DomainRuleError("ADAPTER_TRANSPORT_UNSUPPORTED", "未注册的 adapter transport", {"transport": transport})


@dataclass(frozen=True)
class AdapterContract:
    """A non-invasive adapter declaration and its static validation result."""

    code: str
    title: str
    kind: str
    transport: str
    base_url: str | None
    executable_ref: str | None
    capabilities: tuple[str, ...]
    status: str
    blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "kind": self.kind,
            "transport": self.transport,
            "base_url": self.base_url,
            "executable_ref": self.executable_ref,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "blockers": list(self.blockers),
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }


def _contract(
    *,
    code: str,
    title: str,
    kind: str,
    transport: str,
    capabilities: tuple[str, ...],
    base_url: str | None = None,
    executable_ref: str | None = None,
) -> AdapterContract:
    try:
        validate_adapter_target(transport, base_url=base_url, executable_ref=executable_ref)
    except DomainRuleError as error:
        return AdapterContract(code, title, kind, transport, base_url, executable_ref, capabilities, "BLOCKED", (error.code,))
    if executable_ref and Path(executable_ref).is_absolute() and not Path(executable_ref).is_file():
        return AdapterContract(code, title, kind, transport, base_url, executable_ref, capabilities, "BLOCKED", ("LOCAL_EXECUTABLE_NOT_FOUND",))
    return AdapterContract(code, title, kind, transport, base_url, executable_ref, capabilities, "DECLARED", ())


class AdapterContractRegistry:
    """Build the four G7-03 local adapter contracts without touching runtimes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def inspect(self) -> dict[str, Any]:
        ffmpeg = self.settings.ffmpeg_path
        ffprobe = self.settings.ffprobe_path
        contracts = (
            _contract(
                code="comfyui-loopback",
                title="ComfyUI H3 loopback adapter",
                kind="COMFY",
                transport="LOOPBACK_HTTP",
                base_url=self.settings.comfy_base_url,
                capabilities=("system_stats", "object_info", "queue", "prompt", "history", "interrupt", "collect"),
            ),
            _contract(
                code="local-llm-loopback",
                title="Local LLM loopback adapter",
                kind="LOCAL_LLM_LOOPBACK",
                transport="LOCAL_LLM_LOOPBACK",
                base_url=self.settings.llm_base_url,
                capabilities=("model_list", "chat_json", "deterministic_temperature_zero"),
            ),
            _contract(
                code="local-cli",
                title="Local CLI adapter",
                kind="CLI",
                transport="LOCAL_CLI",
                executable_ref="local-cli",
                capabilities=("version", "stdin_stdout", "isolated_workspace"),
            ),
            _contract(
                code="ffmpeg-local",
                title="FFmpeg/FFprobe local media adapter",
                kind="FFMPEG",
                transport="FFMPEG_LOCAL",
                executable_ref=ffmpeg or ffprobe,
                capabilities=("ffmpeg", "ffprobe", "thumbnail", "proxy", "render", "probe"),
            ),
        )
        return {
            "mode": "LOCAL_ONLY",
            "contracts": [item.as_dict() for item in contracts],
            "remote_transport_allowed": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
