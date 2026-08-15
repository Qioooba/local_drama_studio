from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation import AutomationService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


class _WebhookHandler(BaseHTTPRequestHandler):
    received: list[tuple[dict[str, str], dict[str, object]]] = []
    status = 204

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received.append((dict(self.headers), json.loads(body)))
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _project(database, workspace) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="automation_webhooks",
        title="Automation webhooks",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def test_scoped_loopback_webhook_signs_and_delivers_once(database, workspace) -> None:
    project_id = _project(database, workspace)
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "automation-1")
    service = AutomationService(database)
    client = service.create_client(code="automation-test", title="Automation test", project_id=project_id, scopes=["read", "delivery"], idempotency_key="automation-client-1")
    replayed_client = service.create_client(code="automation-test", title="Automation test", project_id=project_id, scopes=["read", "delivery"], idempotency_key="automation-client-1")
    assert replayed_client["id"] == client["id"] and replayed_client["token"] is None and replayed_client["idempotent_replay"] is True
    server, thread = _server()
    try:
        subscription = service.create_subscription(
            client["token"], endpoint_url=f"http://127.0.0.1:{server.server_port}/events", project_id=project_id, event_types=[], idempotency_key="automation-hook-1"
        )
        replayed_subscription = service.create_subscription(
            client["token"], endpoint_url=f"http://127.0.0.1:{server.server_port}/events", project_id=project_id, event_types=[], idempotency_key="automation-hook-1"
        )
        assert replayed_subscription["id"] == subscription["id"] and replayed_subscription["signing_secret"] is None
        _WebhookHandler.received = []
        result = service.deliver(client["token"], subscription_id=subscription["id"], project_id=project_id, limit=100)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert result["status"] == "DELIVERED"
    assert result["delivered_count"] >= 2
    assert result["loopback_only"] is True
    headers, payload = _WebhookHandler.received[0]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    expected = "sha256=" + hmac.new(subscription["signing_secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert headers["X-Local-Drama-Signature"] == expected
    assert "token" not in json.dumps(subscription)
    listed = service.list_deliveries(client["token"], subscription_id=subscription["id"])
    assert listed["items"] and all(item["status"] == "DELIVERED" for item in listed["items"])
    assert service.deliver(client["token"], subscription_id=subscription["id"], project_id=project_id)["status"] == "NO_EVENTS"


def test_webhook_retries_are_bounded_and_dead_letter_can_be_explicitly_retried(database, workspace) -> None:
    project_id = _project(database, workspace)
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "automation-2")
    service = AutomationService(database)
    client = service.create_client(code="automation-retry", title="Automation retry", project_id=project_id, scopes=["delivery"], idempotency_key="automation-client-2")
    server, thread = _server()
    _WebhookHandler.status = 503
    try:
        subscription = service.create_subscription(client["token"], endpoint_url=f"http://127.0.0.1:{server.server_port}/events", project_id=project_id, event_types=[], idempotency_key="automation-hook-2")
        for _ in range(5):
            with database.transaction() as connection:
                connection.execute("UPDATE webhook_deliveries SET next_attempt_at=? WHERE status='RETRYING'", ("1970-01-01T00:00:00+00:00",))
            service.deliver(client["token"], subscription_id=subscription["id"], project_id=project_id, limit=1)
        dead = service.list_deliveries(client["token"], subscription_id=subscription["id"], status="DEAD_LETTER")
        assert dead["items"]
        _WebhookHandler.status = 204
        retried = service.retry_delivery(client["token"], str(dead["items"][0]["id"]))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        _WebhookHandler.status = 204
    assert retried["status"] == "DELIVERED"


def test_automation_scope_and_loopback_rules_are_enforced(database, workspace) -> None:
    service = AutomationService(database)
    with pytest.raises(DomainRuleError, match="scope"):
        service.create_client(code="bad-scope", title="Bad", project_id=None, scopes=["remote"], idempotency_key="automation-client-3")
    client = service.create_client(code="read-only", title="Read only", project_id=None, scopes=["read"], idempotency_key="automation-client-4")
    with pytest.raises(DomainRuleError, match="scope"):
        service.create_subscription(client["token"], endpoint_url="http://127.0.0.1:1234/hook", project_id=None, event_types=[], idempotency_key="automation-hook-3")
    delivery_client = service.create_client(code="delivery", title="Delivery", project_id=None, scopes=["delivery"], idempotency_key="automation-client-5")
    with pytest.raises(DomainRuleError, match="loopback"):
        service.create_subscription(delivery_client["token"], endpoint_url="https://example.com/hook", project_id=None, event_types=[], idempotency_key="automation-hook-4")


def test_automation_routes_require_idempotency_and_allow_scoped_bearer(database, workspace) -> None:
    project_id = _project(database, workspace)
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            "/api/v1/automation-clients",
            headers={"Idempotency-Key": "route-client-1"},
            json={"code": "route-automation", "title": "Route automation", "project_id": project_id, "scopes": ["delivery"]},
        )
        assert created.status_code == 201
        token = created.json()["client"]["token"]
        replay = client.post(
            "/api/v1/automation-clients",
            headers={"Idempotency-Key": "route-client-1"},
            json={"code": "route-automation", "title": "Route automation", "project_id": project_id, "scopes": ["delivery"]},
        )
        assert replay.status_code == 201 and replay.json()["client"]["token"] is None
        subscription = client.post(
            "/api/v1/webhook-subscriptions",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "route-hook-1"},
            json={"endpoint_url": "http://127.0.0.1:9876/events", "project_id": project_id},
        )
        assert subscription.status_code == 201
        assert client.post("/api/v1/webhook-deliveries:deliver", json={}).status_code == 403
