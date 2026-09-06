from __future__ import annotations

import json
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
    llama_launch_spec_from_settings,
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
behavior = arg_value("--behavior", "serve")

if behavior == "crash":
    sys.exit(2)
if behavior == "hang":
    time.sleep(300)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            status = 503 if behavior == "loading" else 200
        elif self.path == "/v1/models":
            body = json.dumps({"data": [{"id": alias}]}).encode()
            status = 200
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(status)
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


def _launcher(tmp_path: Path) -> tuple[Path, Path]:
    """Return (launcher the manager spawns, server python script).

    Orphan tests spawn the python script directly: a .bat launcher inserts a
    cmd.exe intermediary whose child survives TerminateProcess, so the pid in
    the pidfile must own the listening socket itself.
    """

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


def _spawn_orphan(server_py: Path, port: int, *, alias: str = "test-model") -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable, "-u", str(server_py),
            "-m", "model.gguf",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--alias", alias,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _spec(launcher: Path, port: int, *, alias: str = "test-model", extra: tuple[str, ...] = ()) -> LlamaServerLaunchSpec:
    return LlamaServerLaunchSpec(
        executable=launcher,
        model_path=Path("qwen3.8-27b.gguf"),
        alias=alias,
        host="127.0.0.1",
        port=port,
        extra_args=extra,
    )


def _manager(tmp_path: Path, *, startup_timeout: float = 15.0, port_free_wait: float = 2.0) -> LlamaServerManager:
    return LlamaServerManager(
        log_dir=tmp_path / "logs",
        startup_timeout_seconds=startup_timeout,
        poll_seconds=0.05,
        post_exit_settle_seconds=0.0,
        port_free_wait_seconds=port_free_wait,
    )


def _pidfile(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "llama_server.pid"


def _assert_port_closed(port: int, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
            time.sleep(0.1)
        except OSError:
            return
    pytest.fail(f"managed process tree still owns port {port} after stop")


def test_launch_spec_command_is_deterministic(tmp_path: Path) -> None:
    spec = LlamaServerLaunchSpec(
        executable=Path("llama-server.exe"),
        model_path=Path("model.gguf"),
        alias="qwen3.8-27b",
        mtp_enabled=True,
        mtp_draft_tokens=2,
    )
    assert spec.command() == [
        "llama-server.exe",
        "-m", "model.gguf",
        "--host", "127.0.0.1",
        "--port", "8101",
        "-c", "8192",
        "-ngl", "99",
        "--alias", "qwen3.8-27b",
        "--flash-attn", "auto",
        "--jinja",
        "-ctk", "q8_0",
        "-ctv", "q8_0",
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "2",
    ]


def test_launch_spec_rejects_extra_args_that_override_owned_settings() -> None:
    with pytest.raises(ValueError, match="managed llama-server arguments"):
        LlamaServerLaunchSpec(
            executable=Path("llama-server.exe"),
            model_path=Path("model.gguf"),
            alias="qwen3.8-27b",
            extra_args=("--port=9999",),
        )


def test_wildcard_bind_uses_loopback_client_url(tmp_path: Path) -> None:
    spec = LlamaServerLaunchSpec(
        executable=Path("llama-server.exe"),
        model_path=Path("model.gguf"),
        alias="qwen3.8-27b",
        host="0.0.0.0",
        port=8080,
    )
    manager = _manager(tmp_path)

    assert spec.command()[spec.command().index("--host") + 1] == "0.0.0.0"
    assert manager.base_url_or_raise(spec) == "http://127.0.0.1:8080"


def test_start_waits_for_health_and_reuses_same_spec(tmp_path: Path) -> None:
    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    manager = _manager(tmp_path)
    base_url = manager.start(_spec(launcher, port))
    assert base_url == f"http://127.0.0.1:{port}"
    assert manager.is_running() is True
    first_pid = _pidfile(tmp_path).read_text(encoding="utf-8")

    # Same spec again keeps the process alive instead of paying a reload.
    assert manager.start(_spec(launcher, port)) == base_url
    assert _pidfile(tmp_path).read_text(encoding="utf-8") == first_pid

    assert manager.stop() is True
    assert manager.is_running() is False
    assert not _pidfile(tmp_path).exists()
    _assert_port_closed(port)
    # stop() is idempotent
    assert manager.stop() is False


def test_start_crashed_process_raises_with_log_hint(tmp_path: Path) -> None:
    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    manager = _manager(tmp_path)
    with pytest.raises(DomainRuleError) as caught:
        manager.start(_spec(launcher, port, extra=("--behavior", "crash")))
    assert caught.value.code == "LLAMA_SERVER_CRASHED"
    assert caught.value.details["returncode"] == 2
    assert not _pidfile(tmp_path).exists()
    assert manager.is_running() is False


def test_start_timeout_stops_the_child(tmp_path: Path) -> None:
    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    manager = _manager(tmp_path, startup_timeout=2.0)
    with pytest.raises(DomainRuleError) as caught:
        manager.start(_spec(launcher, port, extra=("--behavior", "hang")))
    assert caught.value.code == "LLAMA_SERVER_TIMEOUT"
    assert manager.is_running() is False
    assert not _pidfile(tmp_path).exists()
    _assert_port_closed(port)


def test_start_keeps_waiting_while_health_returns_503(tmp_path: Path) -> None:
    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    manager = _manager(tmp_path, startup_timeout=0.3)
    with pytest.raises(DomainRuleError) as caught:
        manager.start(_spec(launcher, port, extra=("--behavior", "loading")))
    assert caught.value.code == "LLAMA_SERVER_TIMEOUT"
    assert manager.is_running() is False


def test_start_refuses_when_port_is_owned_by_another_process(tmp_path: Path) -> None:
    launcher, _server_py = _launcher(tmp_path)
    port = _free_port()
    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen(5)
    # A real llama-server accepts connections; a listener that never accepts
    # lets its backlog fill up and probes would be dropped instead of
    # answered, which misreports the port as free.
    stop_accepting = threading.Event()

    def _accept_loop() -> None:
        server.settimeout(0.2)
        while not stop_accepting.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                continue

    accept_thread = threading.Thread(target=_accept_loop, daemon=True)
    accept_thread.start()
    try:
        manager = _manager(tmp_path, port_free_wait=0.2)
        with pytest.raises(DomainRuleError) as caught:
            manager.start(_spec(launcher, port))
        assert caught.value.code == "LLAMA_SERVER_PORT_IN_USE"
        assert manager.is_running() is False
    finally:
        stop_accepting.set()
        accept_thread.join(timeout=1)
        server.close()


def test_manager_adopts_orphan_recorded_in_pidfile(tmp_path: Path) -> None:
    launcher, server_py = _launcher(tmp_path)
    port = _free_port()
    orphan = _spawn_orphan(server_py, port)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline or orphan.poll() is not None:
                    pytest.fail("fake orphan server never came up")
                time.sleep(0.05)
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _pidfile(tmp_path).write_text(str(orphan.pid), encoding="utf-8")

        # A fresh manager instance (as after a worker restart) adopts the
        # healthy orphan instead of failing on the busy port.
        manager = _manager(tmp_path)
        assert manager.start(_spec(launcher, port)) == f"http://127.0.0.1:{port}"
        assert manager.is_running() is True
        assert orphan.poll() is None

        assert manager.stop() is True
        deadline = time.monotonic() + 5
        while orphan.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert orphan.poll() is not None
    finally:
        if orphan.poll() is None:
            orphan.terminate()


def test_manager_recycles_orphan_serving_wrong_alias(tmp_path: Path) -> None:
    launcher, server_py = _launcher(tmp_path)
    port = _free_port()
    orphan = _spawn_orphan(server_py, port, alias="other-model")
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline or orphan.poll() is not None:
                    pytest.fail("fake orphan server never came up")
                time.sleep(0.05)
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _pidfile(tmp_path).write_text(str(orphan.pid), encoding="utf-8")

        manager = _manager(tmp_path)
        # The orphan serves a different model alias, so the manager must
        # recycle it and spawn the expected one.
        assert manager.start(_spec(launcher, port, alias="test-model")) == f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 5
        while orphan.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert orphan.poll() is not None
        assert manager.is_running() is True

        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["data"][0]["id"] == "test-model"
        manager.stop()
    finally:
        if orphan.poll() is None:
            orphan.terminate()


def test_launch_spec_from_settings_validates_configuration(workspace, tmp_path: Path) -> None:
    with pytest.raises(DomainRuleError) as caught:
        llama_launch_spec_from_settings(workspace)
    assert caught.value.code == "LLAMA_SERVER_BIN_MISSING"

    configured = workspace.model_copy(
        update={"llama_server_bin": tmp_path / "missing.exe", "llama_model_path": tmp_path / "model.gguf"}
    )
    with pytest.raises(DomainRuleError) as caught:
        llama_launch_spec_from_settings(configured)
    assert caught.value.code == "LLAMA_SERVER_BIN_MISSING"

    bin_path = tmp_path / "llama-server.exe"
    bin_path.write_bytes(b"")
    configured = workspace.model_copy(update={"llama_server_bin": bin_path})
    with pytest.raises(DomainRuleError) as caught:
        llama_launch_spec_from_settings(configured)
    assert caught.value.code == "LLAMA_SERVER_MODEL_MISSING"

    model_path = tmp_path / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    model_path.write_bytes(b"")
    spec = llama_launch_spec_from_settings(
        workspace.model_copy(update={"llama_server_bin": bin_path, "llama_model_path": model_path, "llm_model": "qwen3.8-27b"})
    )
    assert spec.alias == "qwen3.8-27b"
    assert spec.port == workspace.llama_server_port
    assert "--jinja" in spec.command()


def test_launch_spec_resolves_profile_model_under_configured_root(workspace, tmp_path: Path) -> None:
    bin_path = tmp_path / "llama-server.exe"
    bin_path.write_bytes(b"")
    model_root = tmp_path / "models"
    model_root.mkdir()
    configured_model = model_root / "default.gguf"
    configured_model.write_bytes(b"")
    alternate = model_root / "nested" / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    alternate.parent.mkdir()
    alternate.write_bytes(b"")
    settings = workspace.model_copy(
        update={
            "llama_server_bin": bin_path,
            "llama_model_path": configured_model,
            "llama_mtp_enabled": True,
            "llama_mtp_draft_tokens": 3,
        }
    )

    spec = llama_launch_spec_from_settings(
        settings,
        model_locator="nested/Qwen3.8-27B-UD-Q4_K_XL.gguf",
    )

    assert spec.model_path == alternate
    assert spec.alias == "Qwen3.8-27B-UD-Q4_K_XL"
    assert spec.command()[-4:] == ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]


def test_launch_spec_rejects_profile_model_outside_configured_root(workspace, tmp_path: Path) -> None:
    bin_path = tmp_path / "llama-server.exe"
    bin_path.write_bytes(b"")
    model_root = tmp_path / "models"
    model_root.mkdir()
    configured_model = model_root / "default.gguf"
    configured_model.write_bytes(b"")
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"")
    settings = workspace.model_copy(
        update={"llama_server_bin": bin_path, "llama_model_path": configured_model}
    )

    with pytest.raises(DomainRuleError) as caught:
        llama_launch_spec_from_settings(settings, model_locator="../outside.gguf")

    assert caught.value.code == "LLAMA_SERVER_MODEL_LOCATOR_INVALID"
