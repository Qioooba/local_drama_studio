"""Managed ``llama-server`` process lifecycle for the single-GPU runtime switch.

The manager owns one child process per lease window.  Process exit is the
authoritative CUDA release boundary, mirroring the one-shot PyTorch workers:
``stop()`` returns only after the OS reports the process gone, so the next
runtime can claim VRAM without a polling oracle.

A pidfile next to the log lets a restarted manager adopt or terminate a
server left behind by a previous manager instance (for example after a worker
restart), which keeps orphaned 20 GB weights from silently poisoning every
later switch.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.infrastructure.local_http import open_local

_STILL_ACTIVE = 259
_MANAGED_ARGUMENTS = frozenset(
    {
        "-m",
        "--model",
        "--host",
        "--port",
        "-c",
        "--ctx-size",
        "-ngl",
        "--gpu-layers",
        "--alias",
        "--flash-attn",
        "-fa",
        "--jinja",
        "--no-jinja",
        "-ctk",
        "--cache-type-k",
        "-ctv",
        "--cache-type-v",
        "-t",
        "--threads",
        "--spec-type",
        "--spec-draft-n-max",
    }
)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # A configured executable may be a launcher (for example a .bat or a
        # bundled shim). TerminateProcess only kills that wrapper and can
        # strand the actual model server with its listener and CUDA context.
        # taskkill /T applies the owned-process boundary to the whole tree.
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            completed = subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        # ``taskkill`` can report that the wrapper PID disappeared while it
        # was walking the tree even though the requested tree termination has
        # completed.  Process existence, not taskkill's diagnostic exit code,
        # is the lifecycle authority used everywhere else in this manager.
        return completed.returncode == 0 or not _pid_is_alive(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _port_accepts_connections(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _client_host(bind_host: str) -> str:
    """Return a routable local address for a wildcard listener."""

    return "127.0.0.1" if bind_host in {"0.0.0.0", "::", "*"} else bind_host


@dataclass(frozen=True, slots=True)
class LlamaServerLaunchSpec:
    """Everything needed to build one deterministic llama-server command line."""

    executable: Path
    model_path: Path
    alias: str
    host: str = "127.0.0.1"
    port: int = 8101
    ctx_size: int = 8192
    gpu_layers: int = 99
    # llama.cpp builds since mid-2025 take on/off/auto; empty omits the flag
    # for operators pinning an older build via extra_args.
    flash_attn: str = "auto"
    jinja: bool = True
    kv_cache_type: str | None = "q8_0"
    threads: int | None = None
    mtp_enabled: bool = False
    mtp_draft_tokens: int = 2
    # Optional, non-lifecycle flags (for example --no-webui or --mmproj)
    # travel verbatim. Model, endpoint, context, KV and MTP stay typed above.
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mtp_draft_tokens < 1:
            raise ValueError("mtp_draft_tokens must be positive")
        conflicts = sorted(
            {
                argument.split("=", 1)[0]
                for argument in self.extra_args
                if argument.split("=", 1)[0] in _MANAGED_ARGUMENTS
            }
        )
        if conflicts:
            raise ValueError("extra_args override managed llama-server arguments: " + ", ".join(conflicts))

    def command(self) -> list[str]:
        argv = [
            str(self.executable),
            "-m", str(self.model_path),
            "--host", self.host,
            "--port", str(self.port),
            "-c", str(self.ctx_size),
            "-ngl", str(self.gpu_layers),
            "--alias", self.alias,
        ]
        if self.flash_attn:
            argv.extend(["--flash-attn", self.flash_attn])
        if self.jinja:
            argv.append("--jinja")
        if self.kv_cache_type:
            # Quantized KV cache requires flash attention in llama.cpp; the
            # default fa=on keeps -ctk/-ctv valid.
            argv.extend(["-ctk", self.kv_cache_type, "-ctv", self.kv_cache_type])
        if self.threads:
            argv.extend(["-t", str(self.threads)])
        if self.mtp_enabled:
            argv.extend(["--spec-type", "draft-mtp", "--spec-draft-n-max", str(self.mtp_draft_tokens)])
        argv.extend(self.extra_args)
        return argv


def llama_launch_spec_from_settings(
    settings: Settings,
    *,
    model_locator: str | None = None,
) -> LlamaServerLaunchSpec:
    """Build one launch spec from machine policy and a frozen model locator.

    A V2 Profile stores its GGUF locator relative to the configured model
    directory. Legacy calls omit it and use ``llama_model_path`` directly.
    """

    if settings.llama_server_bin is None:
        raise DomainRuleError(
            "LLAMA_SERVER_BIN_MISSING",
            "托管 llama.cpp Runtime 未配置可执行文件路径（LOCAL_DRAMA_LLAMA_SERVER_BIN）",
            suggested_action="在机器配置或环境变量中指向 llama-server.exe",
        )
    executable = Path(settings.llama_server_bin)
    if not executable.is_file():
        raise DomainRuleError(
            "LLAMA_SERVER_BIN_MISSING",
            "llama-server 可执行文件不存在",
            {"path": str(executable)},
        )
    if settings.llama_model_path is None:
        raise DomainRuleError(
            "LLAMA_SERVER_MODEL_MISSING",
            "托管 llama.cpp Runtime 未配置 GGUF 模型路径（LOCAL_DRAMA_LLAMA_MODEL_PATH）",
            suggested_action="在机器配置或环境变量中指向 Qwen3.8-27B GGUF 文件",
        )
    configured_model_path = Path(settings.llama_model_path)
    if model_locator is not None:
        if not model_locator.strip():
            raise DomainRuleError("LLAMA_SERVER_MODEL_LOCATOR_INVALID", "冻结的 GGUF 模型定位符不能为空")
        model_path = controlled_path(
            configured_model_path.parent,
            model_locator,
            must_exist=True,
            require_file=True,
            code="LLAMA_SERVER_MODEL_LOCATOR_INVALID",
        )
        alias = Path(model_locator).stem
    else:
        model_path = configured_model_path
        alias = (settings.llm_model or "").strip() or model_path.stem
    if not model_path.is_file():
        raise DomainRuleError(
            "LLAMA_SERVER_MODEL_MISSING",
            "GGUF 模型文件不存在",
            {"path": str(model_path)},
        )
    return LlamaServerLaunchSpec(
        executable=executable,
        model_path=model_path,
        alias=alias,
        host=settings.llama_server_host,
        port=settings.llama_server_port,
        ctx_size=settings.llama_ctx_size,
        gpu_layers=settings.llama_gpu_layers,
        flash_attn=settings.llama_flash_attn,
        jinja=True,
        kv_cache_type=(settings.llama_kv_cache_type or "").strip() or None,
        mtp_enabled=settings.llama_mtp_enabled,
        mtp_draft_tokens=settings.llama_mtp_draft_tokens,
        extra_args=tuple(settings.llama_server_args),
    )


class LlamaServerManager:
    """Start, adopt, health-probe and terminate one llama-server process."""

    def __init__(
        self,
        *,
        log_dir: Path,
        startup_timeout_seconds: float = 180.0,
        poll_seconds: float = 0.5,
        terminate_grace_seconds: float = 5.0,
        post_exit_settle_seconds: float = 0.5,
        port_free_wait_seconds: float = 2.0,
        sleep: Any = time.sleep,
    ) -> None:
        # A ~17 GB Q4 GGUF cold load takes tens of seconds from disk; the
        # generous default avoids killing a healthy startup on slow storage.
        self.startup_timeout_seconds = startup_timeout_seconds
        self.poll_seconds = poll_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.post_exit_settle_seconds = post_exit_settle_seconds
        self.port_free_wait_seconds = port_free_wait_seconds
        self._sleep = sleep
        self._log_dir = Path(log_dir)
        self._log_path = self._log_dir / "llama_server.log"
        self._pid_path = self._log_dir / "llama_server.pid"
        self._process: subprocess.Popen[bytes] | None = None
        self._adopted_pid: int | None = None
        self._active_spec: LlamaServerLaunchSpec | None = None
        self._log_handle: Any = None

    @property
    def active_spec(self) -> LlamaServerLaunchSpec | None:
        return self._active_spec

    def base_url(self) -> str | None:
        if self._active_spec is None:
            return None
        return f"http://{_client_host(self._active_spec.host)}:{self._active_spec.port}"

    def is_running(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        if self._adopted_pid is not None:
            return _pid_is_alive(self._adopted_pid)
        return False

    def start(self, spec: LlamaServerLaunchSpec, *, timeout_seconds: float | None = None) -> str:
        """Ensure a server matching ``spec`` is healthy; return its base URL."""

        if self.is_running() and self._active_spec == spec:
            return self.base_url_or_raise(spec)
        if self.is_running():
            self.stop()
        if self._adopt_orphan(spec):
            return self.base_url_or_raise(spec)
        self._wait_port_free(spec)
        self._spawn(spec)
        self._wait_until_ready(spec, timeout_seconds=timeout_seconds or self.startup_timeout_seconds)
        return self.base_url_or_raise(spec)

    def adopt_owned_process(self, spec: LlamaServerLaunchSpec) -> bool:
        """Adopt a healthy server recorded by this manager's PID file.

        Eviction happens before another runtime is activated, so a newly
        created worker must be able to recover ownership without first
        starting a llama.cpp lease.  Processes without our PID file remain
        deliberately unmanaged.
        """

        if self.is_running():
            return self._active_spec == spec
        return self._adopt_orphan(spec)

    def has_owned_process_record(self) -> bool:
        """Return whether the PID file names a currently live process."""

        try:
            pid = int(self._pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        if _pid_is_alive(pid):
            return True
        self._remove_pidfile()
        return False

    def stop(self) -> bool:
        """Terminate the server and wait for OS-level process exit."""

        active_spec = self._active_spec
        process, self._process = self._process, None
        adopted_pid, self._adopted_pid = self._adopted_pid, None
        stopped = False
        try:
            if process is not None:
                if process.poll() is None:
                    if sys.platform == "win32":
                        if not _terminate_pid(process.pid):
                            raise DomainRuleError(
                                "LLAMA_SERVER_STOP_FAILED",
                                "无法终止 llama-server 进程树",
                                {"pid": process.pid},
                            )
                        try:
                            process.wait(timeout=self.terminate_grace_seconds)
                        except subprocess.TimeoutExpired as error:
                            raise DomainRuleError(
                                "LLAMA_SERVER_STOP_FAILED",
                                "llama-server 进程树未在时限内退出",
                                {"pid": process.pid},
                            ) from error
                    else:
                        process.terminate()
                        try:
                            process.wait(timeout=self.terminate_grace_seconds)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=self.terminate_grace_seconds)
                stopped = True
            elif adopted_pid is not None and _pid_is_alive(adopted_pid):
                if not _terminate_pid(adopted_pid):
                    raise DomainRuleError(
                        "LLAMA_SERVER_STOP_FAILED",
                        "无法终止遗留的 llama-server 进程",
                        {"pid": adopted_pid},
                    )
                self._wait_pid_exit(adopted_pid)
                stopped = True
            if stopped and active_spec is not None:
                self._wait_port_release(active_spec)
            return stopped
        finally:
            self._active_spec = None
            self._close_log()
            self._remove_pidfile()
            if stopped:
                # TerminateProcess returns before the driver reclaims CUDA
                # memory; a short settle keeps the next runtime from racing
                # a still-live 20 GB allocation.
                self._sleep(self.post_exit_settle_seconds)

    # -- internals ---------------------------------------------------------

    def base_url_or_raise(self, spec: LlamaServerLaunchSpec) -> str:
        return f"http://{_client_host(spec.host)}:{spec.port}"

    def _spawn(self, spec: LlamaServerLaunchSpec) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log_handle = open(self._log_path, "a", encoding="utf-8", errors="replace")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                spec.command(),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as error:
            self._close_log()
            raise DomainRuleError(
                "LLAMA_SERVER_START_FAILED",
                "llama-server 进程启动失败",
                {"reason": type(error).__name__},
            ) from error
        self._pid_path.write_text(str(self._process.pid), encoding="utf-8")

    def _adopt_orphan(self, spec: LlamaServerLaunchSpec) -> bool:
        """Adopt or terminate a server left by a previous manager instance."""

        try:
            pid = int(self._pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            self._remove_pidfile()
            return False
        if not _pid_is_alive(pid):
            self._remove_pidfile()
            return False
        base_url = f"http://{_client_host(spec.host)}:{spec.port}"
        if self._health(base_url) is True and self._serves_alias(base_url, spec.alias):
            self._adopted_pid = pid
            self._active_spec = spec
            return True
        # Alive but not our expected server: recycle it so the port and VRAM
        # come back under this manager's control.
        _terminate_pid(pid)
        self._wait_pid_exit(pid)
        self._remove_pidfile()
        self._sleep(self.post_exit_settle_seconds)
        return False

    def _wait_pid_exit(self, pid: int) -> None:
        deadline = time.monotonic() + self.terminate_grace_seconds
        while _pid_is_alive(pid):
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "LLAMA_SERVER_STOP_FAILED",
                    "llama-server 进程未在时限内退出",
                    {"pid": pid},
                )
            self._sleep(self.poll_seconds)

    def _wait_port_release(self, spec: LlamaServerLaunchSpec) -> None:
        """Wait until the stopped server no longer owns its listening port."""

        deadline = time.monotonic() + self.terminate_grace_seconds
        while _port_accepts_connections(_client_host(spec.host), spec.port):
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "LLAMA_SERVER_STOP_FAILED",
                    "llama-server 进程已退出，但监听端口仍未释放",
                    {"pid": None, "host": spec.host, "port": spec.port},
                )
            self._sleep(self.poll_seconds)

    def _wait_port_free(self, spec: LlamaServerLaunchSpec) -> None:
        # A just-terminated child can leave its listener closing for a short
        # moment; only a port that stays occupied after the window is treated
        # as a foreign process.
        deadline = time.monotonic() + self.port_free_wait_seconds
        while _port_accepts_connections(_client_host(spec.host), spec.port):
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "LLAMA_SERVER_PORT_IN_USE",
                    "llama-server 端口已被其他进程占用，托管 Runtime 无法启动",
                    {"host": spec.host, "port": spec.port},
                    suggested_action="释放端口或修改 LOCAL_DRAMA_LLAMA_SERVER_PORT",
                )
            self._sleep(self.poll_seconds)

    def _wait_until_ready(self, spec: LlamaServerLaunchSpec, *, timeout_seconds: float) -> None:
        base_url = f"http://{_client_host(spec.host)}:{spec.port}"
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self._process is not None and self._process.poll() is not None:
                self._close_log()
                self._remove_pidfile()
                raise DomainRuleError(
                    "LLAMA_SERVER_CRASHED",
                    "llama-server 启动过程中退出",
                    {"returncode": self._process.returncode, "log_path": str(self._log_path)},
                    suggested_action="查看 llama_server.log 中的启动错误（常见：量化包与本机 llama.cpp 构建不匹配）",
                )
            if self._health(base_url) is True:
                self._active_spec = spec
                return
            if time.monotonic() >= deadline:
                self.stop()
                raise DomainRuleError(
                    "LLAMA_SERVER_TIMEOUT",
                    "llama-server 未在时限内就绪（大 GGUF 冷加载较慢，可调大 LOCAL_DRAMA_LLAMA_STARTUP_TIMEOUT_SECONDS）",
                    {"timeout_seconds": timeout_seconds, "log_path": str(self._log_path)},
                )
            self._sleep(self.poll_seconds)

    def _health(self, base_url: str) -> bool | None:
        """``True`` ready, ``False`` reachable-but-loading, ``None`` unreachable."""

        try:
            request = Request(f"{base_url}/health", method="GET")
            with open_local(request, timeout=2.0) as response:
                return response.status == 200
        except HTTPError as error:
            # llama-server answers 503 while the model is still loading.
            if error.code == 503:
                return False
            return None
        except (OSError, TimeoutError):
            return None

    def _serves_alias(self, base_url: str, alias: str) -> bool:
        try:
            request = Request(f"{base_url}/v1/models", method="GET")
            with open_local(request, timeout=2.0) as response:
                import json

                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return False
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return False
        return any(isinstance(item, dict) and str(item.get("id") or "") == alias for item in data)

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def _remove_pidfile(self) -> None:
        try:
            self._pid_path.unlink()
        except OSError:
            pass
