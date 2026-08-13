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
