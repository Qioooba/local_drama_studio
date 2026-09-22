"""SS-10: the outbox retry budget counts real HTTP attempts only.

The defect counted ``attempt_count + 1`` when a batch was *claimed*.  A failure
on the first event then ``break``-ed the loop, leaving the later events
``IN_FLIGHT`` with a pre-consumed attempt, so their first real POST already
looked like attempt 6 and went straight to ``DEAD_LETTER``.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.outbox_delivery import MAX_ATTEMPTS, OutboxDeliveryService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError

_PAST = "2000-01-01T00:00:00+00:00"


class _CountingHandler(BaseHTTPRequestHandler):
    """Records every real POST by event id; always fails with the set status."""

    status = 500
    received_event_ids: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received_event_ids.append(self.headers.get("X-Local-Drama-Event-Id", ""))
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _project(database, workspace) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="outbox_attempt_budget",
        title="Outbox attempt budget",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _release_backoff(database, endpoint: str) -> None:
    """Make every scheduled retry immediately eligible again."""

    with database.transaction() as connection:
        connection.execute(
            "UPDATE outbox_delivery_attempts SET next_attempt_at=?, lease_until_at=NULL WHERE endpoint_url=?",
            (_PAST, endpoint),
        )


def _attempt_rows(database, endpoint: str) -> list[dict[str, object]]:
    with database.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT event_id,status,attempt_count,last_error FROM outbox_delivery_attempts WHERE endpoint_url=? ORDER BY event_id",
                (endpoint,),
            ).fetchall()
        ]


def _event_ids(database, endpoint: str) -> list[int]:
    with database.connect() as connection:
        return [
            int(row["event_id"])
            for row in connection.execute(
                "SELECT event_id FROM outbox_delivery_attempts WHERE endpoint_url=? ORDER BY event_id",
                (endpoint,),
            ).fetchall()
        ]


def test_first_event_failure_does_not_consume_the_rest_of_the_batch(workspace, database) -> None:
    project_id = _project(database, workspace)
    jobs = JobService(database, workspace)
    for index in range(4):
        jobs.create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, f"outbox-budget-{index}")

    _CountingHandler.status = 500
    _CountingHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        delivery = OutboxDeliveryService(database)
        # Batch of two Job events: the first keeps failing until dead-lettered.
        # The loop stops on the batch that kills it, so the second event has
        # been claimed five times but never sent.
        for _ in range(MAX_ATTEMPTS):
            result = delivery.deliver(endpoint, project_id=project_id, after_event_id=1, limit=2)
            assert result["status"] == "PARTIAL"
            _release_backoff(database, endpoint)

        rows = {int(row["event_id"]): row for row in _attempt_rows(database, endpoint)}
        first_id, second_id = _event_ids(database, endpoint)[:2]
        first, second = rows[first_id], rows[second_id]
        assert first["status"] == "DEAD_LETTER"
        assert first["attempt_count"] == MAX_ATTEMPTS
        # The second event was claimed in every batch but never actually sent.
        assert second["status"] == "RETRYING"
        assert second["attempt_count"] == 0
        assert second["last_error"] is None
        posts = _CountingHandler.received_event_ids
        assert posts.count(str(first_id)) == MAX_ATTEMPTS
        assert posts.count(str(second_id)) == 0

        # Its first real POST must be attempt 1, not attempt 6.
        result = delivery.deliver(endpoint, project_id=project_id, after_event_id=1, limit=3)
        assert result["status"] == "PARTIAL"
        assert result["failed"][0]["event_id"] == second_id
        assert result["failed"][0]["attempt"] == 1

        rows = {int(row["event_id"]): row for row in _attempt_rows(database, endpoint)}
        assert rows[second_id]["attempt_count"] == 1
        assert rows[second_id]["status"] == "RETRYING"
        assert rows[second_id]["last_error"] == "HTTP_500"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_each_event_really_posts_at_most_max_attempts_times(workspace, database) -> None:
    """A single genuinely-failing event is dead-lettered on its fifth real POST."""

    project_id = _project(database, workspace)
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "outbox-single")
    _CountingHandler.status = 500
    _CountingHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        delivery = OutboxDeliveryService(database)
        for expected_attempt in range(1, MAX_ATTEMPTS + 1):
            result = delivery.deliver(endpoint, project_id=project_id, limit=1)
            assert result["failed"][0]["attempt"] == expected_attempt
            _release_backoff(database, endpoint)
        target_id = _event_ids(database, endpoint)[0]
        assert _CountingHandler.received_event_ids.count(str(target_id)) == MAX_ATTEMPTS
        rows = {int(row["event_id"]): row for row in _attempt_rows(database, endpoint)}
        assert rows[target_id]["status"] == "DEAD_LETTER"
        assert rows[target_id]["attempt_count"] == MAX_ATTEMPTS
        # The dead-lettered event is never posted a sixth time, whatever else the
        # project's own audit events do.
        delivery.deliver(endpoint, project_id=project_id, limit=10)
        assert _CountingHandler.received_event_ids.count(str(target_id)) == MAX_ATTEMPTS
        assert {int(row["event_id"]): row for row in _attempt_rows(database, endpoint)}[target_id]["status"] == "DEAD_LETTER"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_a_crash_after_the_attempt_was_counted_keeps_the_budget_honest(workspace, database) -> None:
    """An ``ATTEMPTING`` claim is reclaimed, and its counted attempt is kept."""

    project_id = _project(database, workspace)
    JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "outbox-crash")
    _CountingHandler.status = 500
    _CountingHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        delivery = OutboxDeliveryService(database)
        assert delivery.deliver(endpoint, project_id=project_id, limit=1)["status"] == "PARTIAL"
        with database.connect() as connection:
            row = connection.execute("SELECT id,attempt_count FROM outbox_delivery_attempts WHERE endpoint_url=?", (endpoint,)).fetchone()
        assert row["attempt_count"] == 1

        # Simulate a process that counted the attempt and then died before the
        # failure/acknowledgement transaction: lease it as ATTEMPTING with an
        # expired lease, exactly like the report's crash window.
        with database.transaction() as connection:
            connection.execute(
                "UPDATE outbox_delivery_attempts SET status='ATTEMPTING',attempt_count=?,lease_until_at=?,next_attempt_at=NULL WHERE id=?",
                (MAX_ATTEMPTS, _PAST, row["id"]),
            )

        reclaimed = delivery.deliver(endpoint, project_id=project_id, limit=1)
        assert reclaimed["failed"][0]["attempt"] == MAX_ATTEMPTS + 1
        assert reclaimed["status"] == "PARTIAL"
        with database.connect() as connection:
            actions = [
                item[0]
                for item in connection.execute(
                    "SELECT action FROM audit_events WHERE subject_type='outbox_delivery_attempt' ORDER BY event_id"
                ).fetchall()
            ]
            stored = connection.execute("SELECT status,attempt_count FROM outbox_delivery_attempts WHERE id=?", (row["id"],)).fetchone()
        assert "OUTBOX_DELIVERY_RECLAIMED" in actions
        # The reclaimed event was already counted, so the next real send is
        # attempt 6 and dead-letters instead of silently restarting the budget.
        assert tuple(stored) == ("DEAD_LETTER", MAX_ATTEMPTS + 1)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_concurrent_dispatchers_never_send_the_same_event_twice(workspace, database) -> None:
    project_id = _project(database, workspace)
    job = JobService(database, workspace).create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, "outbox-concurrent")
    with database.connect() as connection:
        # Exactly one queued event belongs to this Job.
        job_event_id = int(
            connection.execute(
                "SELECT event_id FROM outbox_events WHERE type='JOB_QUEUED' AND subject_id=? ORDER BY event_id DESC LIMIT 1",
                (job["id"],),
            ).fetchone()["event_id"]
        )
        before = int(connection.execute("SELECT COALESCE(MAX(event_id), 0) FROM outbox_events").fetchone()[0])
    assert job_event_id == before

    _CountingHandler.status = 204
    _CountingHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        results: list[dict[str, object]] = []
        lock = threading.Lock()

        def _deliver() -> None:
            result = OutboxDeliveryService(database).deliver(endpoint, project_id=project_id, after_event_id=job_event_id - 1, limit=1)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=_deliver) for _ in range(4)]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(timeout=30)
            assert not worker.is_alive()

        delivered = sum(int(result["delivered_count"]) for result in results)
        assert delivered == 1
        assert _CountingHandler.received_event_ids.count(str(job_event_id)) == 1
        with database.connect() as connection:
            stored = connection.execute(
                "SELECT status,attempt_count FROM outbox_delivery_attempts WHERE event_id=? AND endpoint_url=?",
                (job_event_id, endpoint),
            ).fetchone()
        assert tuple(stored) == ("DELIVERED", 1)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_unsent_claims_are_released_to_retrying_not_left_in_flight(workspace, database) -> None:
    """Every claim that never reached the wire must be immediately reclaimable."""

    project_id = _project(database, workspace)
    jobs = JobService(database, workspace)
    for index in range(3):
        jobs.create_job(project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, f"outbox-release-{index}")
    _CountingHandler.status = 503
    _CountingHandler.received_event_ids = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/events"
    try:
        result = OutboxDeliveryService(database).deliver(endpoint, project_id=project_id, limit=3)
        assert result["status"] == "PARTIAL"
        rows = _attempt_rows(database, endpoint)
        assert [row["status"] for row in rows] == ["RETRYING", "RETRYING", "RETRYING"]
        assert [row["attempt_count"] for row in rows] == [1, 0, 0]
        with database.connect() as connection:
            in_flight = connection.execute(
                "SELECT COUNT(*) FROM outbox_delivery_attempts WHERE endpoint_url=? AND status IN ('IN_FLIGHT','ATTEMPTING')",
                (endpoint,),
            ).fetchone()[0]
        assert in_flight == 0
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_public_endpoint_is_still_refused_without_mutation(workspace, database) -> None:
    with pytest.raises(DomainRuleError, match="loopback"):
        OutboxDeliveryService(database).deliver("https://example.com/hook")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox_delivery_attempts").fetchone()[0] == 0
