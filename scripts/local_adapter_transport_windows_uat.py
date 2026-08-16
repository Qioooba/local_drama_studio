"""Run a bounded Windows LOCAL_ONLY transport hardening UAT.

The UAT deliberately starts only ephemeral loopback HTTP servers.  It drives
the production Comfy and local-LLM clients through their real urllib opener
while hostile proxy environment variables are present.  A process-local
socket guard records connects and blocks a non-loopback address before the OS
can send a packet.  This is transport-boundary evidence, not a model/runtime
generation claim.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from threading import Lock, Thread
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.local_llm import LocalLLMClient


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


class _TransportHarness(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []
    lock: ClassVar[Lock] = Lock()

    def _record(self) -> None:
        with type(self).lock:
            type(self).requests.append(f"{self.command} {self.path}")

    def _write_json(self, value: dict[str, Any]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect_to_public(self) -> None:
        self.send_response(302)
        self.send_header("Location", "http://203.0.113.77/blocked-redirect")
        self.end_headers()

    def do_GET(self) -> None:
        self._record()
        if self.path.startswith("/redirect/"):
            self._redirect_to_public()
        elif self.path == "/system_stats":
            self._write_json({"system": {"os": "LOCAL_TRANSPORT_UAT"}})
        elif self.path == "/api/tags":
            self._write_json({"models": [{"name": "local-transport-uat-model"}]})
        else:
            self._write_json({"ok": True})

    def do_POST(self) -> None:
        self._record()
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if self.path == "/prompt":
            # The values are intentionally sensitive-shaped.  They must not
            # escape ComfyClient's stable provider rejection contract.
            self._write_json({"error": "C:/local/secret-input.mp4", "node_errors": {"token": "do-not-disclose"}})
        elif self.path.startswith("/redirect/"):
            self._redirect_to_public()
        else:
            self._write_json({"ok": True})

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _loopback_harness() -> Iterator[tuple[str, list[str]]]:
    with _TransportHarness.lock:
        _TransportHarness.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportHarness)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _TransportHarness.requests
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


@contextmanager
def _hostile_proxy_environment() -> Iterator[None]:
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    original = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = "http://203.0.113.66:3128"
        os.environ["LOCAL_DRAMA_COMFY_ACCESS"] = "enabled"
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _check(code: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), **details}


def run_uat(sandbox_root: Path) -> dict[str, Any]:
    """Exercise real local transports without starting a user-owned runtime."""

    sandbox_root.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    with _loopback_harness() as (base_url, server_requests), _egress_guard() as (observed, blocked), _hostile_proxy_environment():
        comfy_system = ComfyClient(base_url).system_stats()
        llm_tags = LocalLLMClient(base_url, "local-transport-uat-model").tags()

        redirect_comfy_code: str | None = None
        try:
            ComfyClient(f"{base_url}/redirect").system_stats()
        except DomainRuleError as error:
            redirect_comfy_code = error.code

        redirect_llm_code: str | None = None
        try:
            LocalLLMClient(f"{base_url}/redirect", "local-transport-uat-model").tags()
        except DomainRuleError as error:
            redirect_llm_code = error.code

        provider_details: dict[str, object] = {}
        try:
            ComfyClient(base_url).queue_prompt({"1": {"class_type": "Safe", "inputs": {}}})
        except DomainRuleError as error:
            provider_details = error.details

        public_endpoint_codes: list[str] = []
        for factory in (
            lambda: ComfyClient("http://203.0.113.77:8188"),
            lambda: LocalLLMClient("http://203.0.113.77:11434", "local-transport-uat-model"),
            lambda: ComfyClient("http://user:pass@127.0.0.1:8188?token=forbidden"),
        ):
            try:
                factory()
            except DomainRuleError as error:
                public_endpoint_codes.append(error.code)

        request_snapshot = list(server_requests)
        details_serialized = json.dumps(provider_details, ensure_ascii=False)
        checks.extend(
            [
                _check(
                    "WINDOWS_HOST",
                    platform.system().casefold() == "windows",
                    platform=platform.platform(),
                ),
                _check(
                    "REAL_LOOPBACK_COMFY_AND_LLM_TRANSPORT",
                    comfy_system.get("system", {}).get("os") == "LOCAL_TRANSPORT_UAT"
                    and llm_tags == [{"name": "local-transport-uat-model"}],
                    server_requests=[item for item in request_snapshot if not item.startswith("POST /prompt")],
                ),
                _check(
                    "HOSTILE_PROXY_ENV_IGNORED",
                    "127.0.0.1" in observed and not blocked,
                    observed_connections=observed,
                    blocked_connections=blocked,
                ),
                _check(
                    "REDIRECT_TO_PUBLIC_REJECTED_BEFORE_HOP",
                    redirect_comfy_code == "COMFY_LOOPBACK_UNAVAILABLE"
                    and redirect_llm_code == "LOCAL_LLM_LOOPBACK_UNAVAILABLE"
                    and not any(host == "203.0.113.77" for host in observed),
                    comfy_code=redirect_comfy_code,
                    llm_code=redirect_llm_code,
                    public_redirect_connected=False,
                ),
                _check(
                    "PUBLIC_AND_AMBIGUOUS_ENDPOINTS_REJECTED_PRE_CONNECT",
                    public_endpoint_codes == [
                        "LOCAL_ONLY_ENDPOINT_REQUIRED",
                        "LOCAL_ONLY_ENDPOINT_REQUIRED",
                        "LOCAL_ONLY_ENDPOINT_AMBIGUOUS",
                    ]
                    and all(_is_loopback(host) for host in observed),
                    error_codes=public_endpoint_codes,
                ),
                _check(
                    "PROVIDER_CREDENTIALS_AND_PATH_REDACTED",
                    provider_details == {"provider_response": "rejected"}
                    and "secret" not in details_serialized.casefold()
                    and "disclose" not in details_serialized.casefold(),
                    error_code="COMFY_PROMPT_REJECTED",
                    redacted_details=provider_details,
                ),
            ]
        )

    failed_checks = [str(check["code"]) for check in checks if not check["passed"]]
    status = "FAIL" if failed_checks else "PARTIAL"
    return {
        "schema_version": "g10.nfr-sec-003-fr-prv-002-local-adapter-windows-uat.v1",
        "status": status,
        "scope": "isolated Windows x64 LOCAL_ONLY real urllib transport against ephemeral loopback harnesses",
        "requirements": ["NFR-SEC-003", "FR-PRV-002"],
        "checks": checks,
        "failed_checks": failed_checks,
        "runtime_contacted": False,
        "loopback_network_contacted": True,
        "public_network_contacted": False,
        "production_runtime_contacted": False,
        "production_database_contacted": False,
        "mutated": False,
        "limitations": [
            "The loopback harness exercises production transports but is not a user-owned ComfyUI or Ollama runtime smoke.",
            "No packet-level release-host capture, browser three-viewport UAT, or final release sign-off is claimed.",
        ],
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sandbox-root",
        type=Path,
        default=ROOT / "work" / f"local-adapter-transport-uat-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "evidence" / "g10" / "nfr-sec-003-prv-002-local-adapter-transport-2026-08-16.json",
    )
    args = parser.parse_args()
    result = run_uat(args.sandbox_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    if result["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
