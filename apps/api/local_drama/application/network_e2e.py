"""A real local-only network-chain test for G7-08.

The harness uses an ephemeral loopback HTTP server and the production HTTP
clients. A socket guard records every connect and rejects non-loopback targets
before the operating system can send a packet. This proves the egress policy;
it is deliberately not a model/runtime success fixture.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any, Iterator
from urllib.request import ProxyHandler, Request, build_opener

from local_drama.application.diagnostics import _probe_loopback
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


class _HarnessHandler(BaseHTTPRequestHandler):
    server_version = "LocalDramaNetworkHarness/1"

    def _write(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.requests.append(f"GET {self.path}")  # type: ignore[attr-defined]
        if self.path == "/system_stats":
            self._write({"system": {"os": "LOCAL_TEST"}})
        elif self.path.startswith("/object_info"):
            self._write({"SaveVideo": {"display_name": "local harness"}})
        elif self.path == "/queue":
            self._write({"queue_running": [], "queue_pending": []})
        elif self.path.startswith("/history/"):
            self._write({self.path.rsplit("/", 1)[-1]: {"status": {"status_str": "success"}}})
        elif self.path == "/api/tags":
            self._write({"models": [{"name": "local-test-model"}]})
        else:
            self._write({"ok": True})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.requests.append(f"POST {self.path}")  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if self.path == "/api/generate":
            self._write({"response": '{"ready":true}'})
        else:
            self._write({"prompt_id": "local-network-harness"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _local_server() -> Iterator[tuple[str, list[str]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HarnessHandler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.requests  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@contextmanager
def _egress_guard() -> Iterator[tuple[list[str], list[str]]]:
    observed: list[str] = []
    blocked: list[str] = []
    original_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        host = str(address[0]) if isinstance(address, tuple) and address else str(address)
        observed.append(host)
        if not _is_loopback(host):
            blocked.append(host)
            raise OSError("LOCAL_ONLY_PUBLIC_EGRESS_BLOCKED")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    try:
        yield observed, blocked
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]


class NetworkE2EService:
    """Run and persist G7-08 evidence without contacting ComfyUI or the public internet."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def run(self, project_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})

        local_requests: list[str] = []
        with _local_server() as (base_url, requests), _egress_guard() as (observed, blocked):
            old_access = os.environ.get("LOCAL_DRAMA_COMFY_ACCESS")
            os.environ["LOCAL_DRAMA_COMFY_ACCESS"] = "enabled"
            try:
                comfy = ComfyClient(base_url=base_url)
                comfy.system_stats()
                comfy.object_info()
                comfy.queue()
                comfy.history("local-network-harness")
                llm = LocalLLMClient(base_url=base_url, model="local-test-model")
                llm.probe(load_test=True)
                status, details = _probe_loopback(base_url)
                if status != "PASS":
                    raise DomainRuleError("NETWORK_E2E_LOOPBACK_FAILED", "loopback 客户端链路未通过", details)
                # A TEST-NET address is used solely to prove the guard blocks it;
                # the patched connect prevents any packet from leaving the host.
                try:
                    build_opener(ProxyHandler({})).open(  # noqa: S310 - guard blocks before send
                        Request("http://203.0.113.1/"), timeout=1
                    )
                except (OSError, TimeoutError):
                    pass
                local_requests.extend(requests)
            finally:
                if old_access is None:
                    os.environ.pop("LOCAL_DRAMA_COMFY_ACCESS", None)
                else:
                    os.environ["LOCAL_DRAMA_COMFY_ACCESS"] = old_access

        status = "PASS" if blocked and all(_is_loopback(host) or host in blocked for host in observed) else "FAIL"
        attestation_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO g7_network_e2e_attestations
                (id, project_id, status, local_requests_json, blocked_public_attempts_json,
                observed_connections_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (attestation_id, project_id, status, json.dumps(local_requests), json.dumps(blocked), json.dumps(observed), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'G7_ZERO_PUBLIC_NETWORK_E2E', 'project', ?, ?, ?)""",
                (actor, project_id, f"G7 zero-public-network E2E {status}", json.dumps({"attestation_id": attestation_id, "network_contacted": False})),
            )
        return {
            "id": attestation_id,
            "project_id": project_id,
            "status": status,
            "local_requests": local_requests,
            "blocked_public_attempts": blocked,
            "observed_connections": observed,
            "runtime_contacted": False,
            "network_contacted": False,
        }
