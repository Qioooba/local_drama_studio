"""BKT-10: an exited-but-unreaped Linux process must not look alive.

On POSIX ``os.kill(pid, 0)`` also succeeds for a zombie, so an adopted
llama-server whose parent had not yet reaped it was reported as "still running"
and stop/model-switch failed with ``LLAMA_SERVER_STOP_FAILED`` for a process
that had already released its listener and CUDA context.

This machine is Windows, so the Linux branch is exercised through the manager's
injected process-state seam.  ``test_procfs_real_zombie_is_reported_as_exited``
additionally reads a real ``Z`` entry from ``/proc`` when one exists.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.llama_server_manager import (
    LlamaServerLaunchSpec,
    LlamaServerManager,
    ProcessState,
    _procfs_process_state,
    default_process_state,
)

_FAKE_SERVER = r"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]


def arg_value(name, default=None):
    try:
        return args[args.index(name) + 1]
    except (ValueError, IndexError):
        return default


port = int(arg_value("--port", "8199"))
alias = arg_value("--alias", "test-model")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
        elif self.path == "/v1/models":
            body = json.dumps({"data": [{"id": alias}]}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _OpenPort:
    """A real loopback listener that stands in for a still-running server."""

    def __init__(self, port: int) -> None:
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", port))
        self._server.listen(5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
                connection.close()
            except OSError:
                continue

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._server.close()


def _launcher(tmp_path: Path) -> tuple[Path, Path]:
    server_py = tmp_path / "fake_server.py"
    server_py.write_text(_FAKE_SERVER, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "llama-server.bat"
        launcher.write_text(f'@"{sys.executable}" -u "{server_py}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "llama-server.sh"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{server_py}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher, server_py


def _spawn_orphan(server_py: Path, port: int, *, alias: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-u", str(server_py), "-m", "model.gguf", "--host", "127.0.0.1", "--port", str(port), "--alias", alias],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _wait_listening(port: int, *, process: subprocess.Popen[bytes], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            if process.poll() is not None:
                pytest.fail("fake server exited before it started listening")
            time.sleep(0.05)
    pytest.fail("fake server never started listening")


def _spec(launcher: Path, port: int, *, alias: str = "test-model") -> LlamaServerLaunchSpec:
    return LlamaServerLaunchSpec(executable=launcher, model_path=Path("qwen3.8-27b.gguf"), alias=alias, host="127.0.0.1", port=port)


def _manager(
    tmp_path: Path,
    *,
    process_state,
    terminate=None,
    grace: float = 5.0,
    port_free_wait: float = 0.4,
) -> LlamaServerManager:
    # ``terminate_pid=None`` keeps the real platform terminator, which is what
    # the owned-Popen branch must keep exercising.
    return LlamaServerManager(
        log_dir=tmp_path / "logs",
        startup_timeout_seconds=20.0,
        poll_seconds=0.02,
        terminate_grace_seconds=grace,
        post_exit_settle_seconds=0.0,
        port_free_wait_seconds=port_free_wait,
        process_state=process_state,
        terminate_pid=terminate,
    )


def _adopt(tmp_path: Path, port: int, pid: int, spec: LlamaServerLaunchSpec) -> LlamaServerManager:
    """Model a restarted manager that owns a PID recorded in its PID file."""

    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "llama_server.pid").write_text(str(pid), encoding="utf-8")
    manager = LlamaServerManager(
        log_dir=log_dir,
        poll_seconds=0.02,
        terminate_grace_seconds=0.4,
        post_exit_settle_seconds=0.0,
        port_free_wait_seconds=0.4,
        process_state=lambda _pid: ProcessState.EXITED,
        terminate_pid=lambda _pid: True,
    )
    manager._adopted_pid = pid
    manager._active_spec = spec
    return manager


# --- platform process-state classification ------------------------------------


@pytest.mark.parametrize("platform", ["linux"])
def test_procfs_reader_treats_zombie_and_dead_states_as_exited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    assert platform == "linux"

    def _fake_stat(text: str):
        def _read(self: Path, encoding: str = "utf-8", errors: str = "strict") -> str:
            del self, encoding, errors
            return text

        return _read

    # State is the 3rd field, after the command name which may contain ')'.
    monkeypatch.setattr(Path, "read_text", _fake_stat("1234 (llama server (x)) Z 1 1 1 1"))
    assert _procfs_process_state(1234) == ProcessState.EXITED
    monkeypatch.setattr(Path, "read_text", _fake_stat("1234 (llama-server) X 1 1 1 1"))
    assert _procfs_process_state(1234) == ProcessState.EXITED
    monkeypatch.setattr(Path, "read_text", _fake_stat("1234 (llama-server) R 1 1 1 1"))
    assert _procfs_process_state(1234) == ProcessState.RUNNING
    monkeypatch.setattr(Path, "read_text", _fake_stat("1234 (llama-server) S 1 1 1 1"))
    assert _procfs_process_state(1234) == ProcessState.RUNNING

    def _missing(self: Path, encoding: str = "utf-8", errors: str = "strict") -> str:
        del self, encoding, errors
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _missing)
    assert _procfs_process_state(1234) == ProcessState.MISSING


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="requires a Linux /proc filesystem")
def test_procfs_real_zombie_is_reported_as_exited() -> None:
    """A real unreaped child must read as EXITED, not RUNNING."""

    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child exits immediately
        os._exit(0)
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _procfs_process_state(pid) == ProcessState.EXITED:
                break
            time.sleep(0.02)
        assert _procfs_process_state(pid) == ProcessState.EXITED
        # The plain existence probe still cannot tell the difference, which is
        # exactly the defect this classification exists for.
        os.kill(pid, 0)
    finally:
        os.waitpid(pid, 0)


def test_default_process_state_matches_the_platform_probe() -> None:
    assert default_process_state(os.getpid()) == ProcessState.RUNNING
    assert default_process_state(-1) == ProcessState.MISSING
    assert default_process_state(2**31 - 1) == ProcessState.MISSING


# --- BKT-10 scenarios ---------------------------------------------------------


def test_adopted_pid_stop_when_the_parent_has_not_reaped_yet(tmp_path: Path) -> None:
    """Scenario 1: zombie PID, port already closed -> stop must succeed."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    spec = _spec(launcher, port)
    manager = _adopt(tmp_path, port, pid=43210, spec=spec)

    started = time.monotonic()
    assert manager.stop() is True
    assert time.monotonic() - started < 1.0
    assert not (tmp_path / "logs" / "llama_server.pid").exists()


def test_adopted_pid_stop_after_prompt_reaping(tmp_path: Path) -> None:
    """Scenario 2: the parent reaped normally -> stop must also succeed."""

    launcher, server_py = _launcher(tmp_path)
    port = _free_port()
    spec = _spec(launcher, port)
    orphan = _spawn_orphan(server_py, port, alias="test-model")
    try:
        _wait_listening(port, process=orphan)
        orphan.terminate()
        orphan.wait(timeout=10)
        manager = _adopt(tmp_path, port, pid=orphan.pid, spec=spec)
        assert manager.stop() is True
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_mismatched_alias_reclamation_without_reaping_starts_the_new_service(tmp_path: Path) -> None:
    """Scenario 3: wrong alias + unreaped zombie -> the expected server starts."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    # The stale PID is already gone (zombie or reaped); the port is free.
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "llama_server.pid").write_text("43211", encoding="utf-8")
    box: dict[str, subprocess.Popen[bytes]] = {}

    def _state(pid: int) -> ProcessState:
        # The stale PID reads as already exited; the newly spawned child keeps
        # the real platform state so the ordinary stop path is exercised.
        child = box.get("child")
        if child is not None and pid == child.pid:
            return default_process_state(pid)
        return ProcessState.EXITED

    manager = _manager(tmp_path, process_state=_state)
    try:
        assert manager.start(_spec(launcher, port, alias="test-model")) == f"http://127.0.0.1:{port}"
        assert manager.is_running() is True
        assert manager._process is not None
        box["child"] = manager._process
        assert manager.stop() is True
    finally:
        child = box.get("child")
        if child is not None and child.poll() is None:
            child.kill()


@pytest.mark.parametrize("stale_reader", [ProcessState.MISSING, ProcessState.EXITED])
def test_mismatched_alias_reclamation_after_prompt_reaping_starts_the_new_service(tmp_path: Path, stale_reader: ProcessState) -> None:
    """Scenario 4: reaped stale PID -> the expected server still starts."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "llama_server.pid").write_text("43212", encoding="utf-8")
    box: dict[str, subprocess.Popen[bytes]] = {}

    def _state(pid: int) -> ProcessState:
        child = box.get("child")
        if child is not None and pid == child.pid:
            return default_process_state(pid)
        return stale_reader

    manager = _manager(tmp_path, process_state=_state)
    try:
        assert manager.start(_spec(launcher, port, alias="test-model")) == f"http://127.0.0.1:{port}"
        assert manager._process is not None
        box["child"] = manager._process
        assert manager.stop() is True
    finally:
        child = box.get("child")
        if child is not None and child.poll() is None:
            child.kill()


def test_a_live_adopted_process_that_refuses_to_die_still_times_out(tmp_path: Path) -> None:
    """Acceptance: a genuinely live process that ignores SIGTERM must error."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    holder = _OpenPort(port)
    try:
        spec = _spec(launcher, port)
        manager = LlamaServerManager(
            log_dir=tmp_path / "logs",
            poll_seconds=0.02,
            terminate_grace_seconds=0.3,
            post_exit_settle_seconds=0.0,
            port_free_wait_seconds=0.3,
            # RUNNING forever: the injected terminator is a no-op.
            process_state=lambda _pid: ProcessState.RUNNING,
            terminate_pid=lambda _pid: True,
        )
        manager._adopted_pid = 43213
        manager._active_spec = spec

        with pytest.raises(DomainRuleError) as caught:
            manager.stop()
        assert caught.value.code == "LLAMA_SERVER_STOP_FAILED"
        assert caught.value.details["pid"] == 43213
    finally:
        holder.close()


def test_a_live_child_popen_branch_is_unchanged(tmp_path: Path) -> None:
    """The ordinary owned-Popen branch keeps its existing semantics."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    manager = _manager(tmp_path, process_state=default_process_state)
    assert manager.start(_spec(launcher, port)) == f"http://127.0.0.1:{port}"
    assert manager.is_running() is True
    assert manager.stop() is True
    assert manager.is_running() is False
    assert manager.stop() is False


def test_zombie_adoption_never_reports_a_healthy_managed_server(tmp_path: Path) -> None:
    """An unreaped PID must not be adopted as a live server."""

    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "llama_server.pid").write_text("43214", encoding="utf-8")
    manager = _manager(tmp_path, process_state=lambda _pid: ProcessState.EXITED, terminate=lambda _pid: True)

    assert manager.has_owned_process_record() is False
    assert not (log_dir / "llama_server.pid").exists()
    assert manager.adopt_owned_process(_spec(launcher, port)) is False
