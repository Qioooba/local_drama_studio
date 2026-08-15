from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.outbox_delivery import OutboxDeliveryService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


class _Handler(BaseHTTPRequestHandler):
    received: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _FlakyHandler(BaseHTTPRequestHandler):
    status = 503
    received_event_ids: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.received_event_ids.append(self.headers.get("X-Local-Drama-Event-Id", ""))
        self.send_response(self.status)
        if self.status in {301, 302, 307, 308}:
            self.send_header("Location", "https://example.com/public-hook")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_outbox_delivery_is_bounded_loopback_only_and_idempotent(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="outbox_delivery", title="Outbox delivery", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "outbox-1")
    _Handler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = OutboxDeliveryService(database).deliver(f"http://127.0.0.1:{server.server_port}/events", project_id=project_id, limit=100)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert result["status"] == "DELIVERED"
    assert result["delivered_count"] >= 2
    assert result["mutated"] is True
    assert any(item["event"]["type"] == "JOB_QUEUED" for item in _Handler.received)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE delivered_at IS NOT NULL").fetchone()[0] >= 2
    empty = OutboxDeliveryService(database).deliver(f"http://127.0.0.1:{server.server_port}/events", project_id=project_id)
    assert empty["status"] == "NO_EVENTS"


def test_outbox_delivery_rejects_public_endpoint_without_mutation(workspace, database) -> None:
    with pytest.raises(DomainRuleError, match="loopback"):
        OutboxDeliveryService(database).deliver("https://example.com/hook")


def test_outbox_delivery_persists_backoff_reclaims_stale_claim_and_deduplicates(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="outbox_reliability", title="Outbox reliability", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "outbox-reliability-1")
    _FlakyHandler.status = 302
    _FlakyHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        first = OutboxDeliveryService(database).deliver(endpoint, project_id=project_id, limit=1)
        assert first["status"] == "PARTIAL"
        assert first["failed"][0]["attempt"] == 1
        assert first["failed"][0]["reason"] == "WEBHOOK_REDIRECT_BLOCKED"
        with database.connect() as connection:
            attempt = connection.execute(
                "SELECT * FROM outbox_delivery_attempts WHERE endpoint_url=? ORDER BY event_id LIMIT 1", (endpoint,)
            ).fetchone()
            assert attempt["status"] == "RETRYING"
            assert attempt["attempt_count"] == 1
            assert attempt["next_attempt_at"] is not None
            # Simulate an API crash after the POST but before the acknowledgement
            # commit.  A fresh service instance must reclaim the expired claim.
            connection.execute(
                "UPDATE outbox_delivery_attempts SET status='IN_FLIGHT',lease_until_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (attempt["id"],),
            )
        _FlakyHandler.status = 204
        second = OutboxDeliveryService(database).deliver(endpoint, project_id=project_id, limit=1)
        assert second["status"] == "DELIVERED"
        assert second["delivered_count"] == 1
        third = OutboxDeliveryService(database).deliver(endpoint, project_id=project_id, after_event_id=999999, limit=1)
        assert third["status"] == "NO_EVENTS"
        assert len(_FlakyHandler.received_event_ids) == 2
        assert _FlakyHandler.received_event_ids[0] == _FlakyHandler.received_event_ids[1]
        with database.connect() as connection:
            row = connection.execute("SELECT status,attempt_count,delivered_at FROM outbox_delivery_attempts WHERE id=?", (attempt["id"],)).fetchone()
            assert tuple(row) == ("DELIVERED", 2, row["delivered_at"])
            actions = [item[0] for item in connection.execute("SELECT action FROM audit_events WHERE subject_type='outbox_delivery_attempt' ORDER BY event_id").fetchall()]
            assert "OUTBOX_DELIVERY_RETRY_SCHEDULED" in actions
            assert "OUTBOX_DELIVERY_RECLAIMED" in actions
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
