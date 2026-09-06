from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.local_llm import LocalLLMClient


class _RedirectHandler(BaseHTTPRequestHandler):
    redirect_target = ""
    requests = 0
    lock = Lock()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        with type(self).lock:
            type(self).requests += 1
        self.send_response(302)
        self.send_header("Location", self.redirect_target)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self.do_GET()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _SinkHandler(BaseHTTPRequestHandler):
    requests = 0
    lock = Lock()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        with type(self).lock:
            type(self).requests += 1
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self.do_GET()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _ComfyRejectHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        body = json.dumps({
            "error": {"type": "prompt_outputs_failed_validation", "message": "secret provider detail"},
            "node_errors": {
                "17": {"class_type": "CheckpointLoaderSimple", "errors": [{"type": "value_not_in_list", "message": "secret path"}]},
            },
        }).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _server(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    return server


@pytest.fixture()
def redirect_servers():
    redirect = _server(_RedirectHandler)
    sink = _server(_SinkHandler)
    _RedirectHandler.redirect_target = f"http://127.0.0.1:{sink.server_port}/private"
    _RedirectHandler.requests = 0
    _SinkHandler.requests = 0
    redirect_thread = Thread(target=redirect.serve_forever, daemon=True)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    redirect_thread.start()
    sink_thread.start()
    yield redirect, sink
    redirect.shutdown()
    sink.shutdown()
    redirect.server_close()
    sink.server_close()


def test_local_clients_do_not_follow_redirects_or_use_provider_payload_details(redirect_servers, monkeypatch) -> None:
    redirect, _sink = redirect_servers
    # The safe API harness disables Comfy access globally.  This test is
    # specifically exercising the allowed loopback transport after the
    # access guard, so opt into that bounded local path explicitly.
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_ACCESS", "enabled")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    with pytest.raises(DomainRuleError) as comfy_error:
        ComfyClient(f"http://127.0.0.1:{redirect.server_port}").system_stats()
    assert comfy_error.value.code == "COMFY_LOOPBACK_UNAVAILABLE"
    assert _RedirectHandler.requests == 1
    assert _SinkHandler.requests == 0

    with pytest.raises(DomainRuleError) as llm_error:
        LocalLLMClient(f"http://127.0.0.1:{redirect.server_port}", "local-model").tags()
    assert llm_error.value.code == "LOCAL_LLM_LOOPBACK_UNAVAILABLE"
    assert _SinkHandler.requests == 0

    client = ComfyClient()
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"error": "/secret/path", "node_errors": {"token": "secret"}})
    with pytest.raises(DomainRuleError) as reject_error:
        client.queue_prompt({"1": {"class_type": "Safe", "inputs": {}}})
    assert reject_error.value.details == {"provider_response": "rejected", "node_errors": []}
    assert "secret" not in json.dumps(reject_error.value.details)


def test_comfy_prompt_http_validation_error_is_not_reported_as_runtime_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_ACCESS", "enabled")
    server = _server(_ComfyRejectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(DomainRuleError) as caught:
            ComfyClient(f"http://127.0.0.1:{server.server_port}").queue_prompt({"17": {"class_type": "Safe", "inputs": {}}})
    finally:
        server.shutdown()
        server.server_close()

    assert caught.value.code == "COMFY_PROMPT_REJECTED"
    assert caught.value.details == {
        "provider_response": "rejected",
        "http_status": 400,
        "error_type": "prompt_outputs_failed_validation",
        "node_errors": [{"node_id": "17", "class_type": "CheckpointLoaderSimple", "error_types": ["value_not_in_list"]}],
    }
    assert "secret" not in json.dumps(caught.value.details)


@pytest.mark.parametrize(
    "client_factory",
    [
        lambda: ComfyClient("http://user:pass@127.0.0.1:8188"),
        lambda: ComfyClient("http://127.0.0.1:8188/?token=secret"),
        lambda: LocalLLMClient("http://user:pass@127.0.0.1:11434", "local-model"),
        lambda: LocalLLMClient("http://127.0.0.1:11434/?token=secret", "local-model"),
    ],
)
def test_local_clients_reject_credential_or_query_bearing_endpoints(client_factory) -> None:
    with pytest.raises(DomainRuleError, match="凭据、query") as error:
        client_factory()
    assert error.value.code == "LOCAL_ONLY_ENDPOINT_AMBIGUOUS"
