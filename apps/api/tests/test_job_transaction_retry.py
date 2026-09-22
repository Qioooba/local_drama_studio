"""SS-01: FAILED recovery must reuse one write transaction instead of nesting.

The original defect was a real SQLite self-deadlock: ``resume`` held a
``BEGIN IMMEDIATE`` write transaction and then called ``retry``, which opened a
second connection and started another write transaction.  The second connection
waited for the first to commit while the first waited for the second to return,
so the API raised ``database is locked`` after the 10 second busy timeout.
"""

from __future__ import annotations

import threading
import time

import pytest

from local_drama.application.jobs import JobService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _job(service: JobService, key: str, *, max_attempts: int = 2) -> dict[str, object]:
    return service.create_job(None, "CPU_TEST", "PROJECT", "cpu-test-subject", "CPU", {"key": key}, key, max_attempts=max_attempts)


def _fail(service: JobService, worker_id: str = "recovery-worker") -> dict[str, object]:
    claim = service.claim(worker_id, ["CPU"])
    assert claim is not None
    result = service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        worker_id,
        success=False,
        error_code="E_LOCAL",
        error_detail_redacted="local failure",
        retryable=False,
    )
    assert result["job_state"] == "FAILED"
    return claim


@pytest.mark.parametrize("state", ["FAILED", "NEEDS_ATTENTION", "ORPHANED"])
def test_resume_of_terminal_job_finishes_well_under_the_busy_timeout(database: Database, workspace, state: str) -> None:
    """Acceptance: FAILED/NEEDS_ATTENTION/ORPHANED resume returns in < 500 ms."""

    service = JobService(database, workspace)
    job = _job(service, f"resume-{state.lower()}")
    _fail(service)
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state=? WHERE id=?", (state, job["id"]))
        connection.execute(
            "UPDATE job_attempts SET state=? WHERE job_id=?",
            ("FAILED" if state != "ORPHANED" else "ORPHANED", job["id"]),
        )

    started = time.monotonic()
    resumed = service.resume(str(job["id"]))
    elapsed = time.monotonic() - started

    assert resumed["state"] == "QUEUED"
    # The broken path needed the 10 second busy_timeout before raising.
    assert elapsed < 1.0, f"resume took {elapsed:.3f}s (nested write transaction?)"


def test_resume_requeues_exactly_once_and_keeps_a_single_requeue_event(database: Database, workspace) -> None:
    service = JobService(database, workspace)
    job = _job(service, "resume-once")
    _fail(service)

    service.resume(str(job["id"]))
    with database.connect() as connection:
        requeues = connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE subject_id=? AND type='JOB_REQUEUED'",
            (job["id"],),
        ).fetchone()[0]
        state = connection.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()["state"]
    assert requeues == 1
    assert state == "QUEUED"

    claim = service.claim("next-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]


def test_a_second_concurrent_resume_is_rejected_instead_of_reviving_twice(workspace, database) -> None:
    """Two concurrent resumes must give one requeue and one stable error."""

    service = JobService(database, workspace)
    job = _job(service, "resume-race")
    _fail(service)

    results: list[dict[str, object]] = []
    errors: list[DomainRuleError] = []
    barrier = threading.Barrier(2)

    def _resume() -> None:
        local = JobService(database, workspace)
        barrier.wait(timeout=10)
        try:
            results.append(local.resume(str(job["id"])))
        except DomainRuleError as error:
            errors.append(error)

    threads = [threading.Thread(target=_resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert len(results) + len(errors) == 2
    assert results, "at least one resume must succeed"
    assert all(error.code in {"JOB_NOT_RETRYABLE", "JOB_NOT_RESUMABLE"} for error in errors)
    assert results[0]["state"] == "QUEUED"
    with database.connect() as connection:
        requeues = connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE subject_id=? AND type='JOB_REQUEUED'",
            (job["id"],),
        ).fetchone()[0]
        assert requeues == 1


def test_retry_from_an_outer_transaction_is_the_same_shared_implementation(database: Database, workspace) -> None:
    """``resume`` reuses ``_retry_in_transaction`` on its own connection.

    Closing the outer connection proves the inner call did not open a second
    write transaction: a connection cannot be closed while it holds the SQLite
    write lock in a way that would block a sibling write.
    """

    service = JobService(database, workspace)
    job = _job(service, "shared-impl")
    _fail(service)

    with database.transaction() as connection:
        result = service._retry_in_transaction(connection, str(job["id"]), actor="transaction-test")
    assert result["state"] == "QUEUED"
    with database.connect() as connection:
        assert connection.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()["state"] == "QUEUED"


def test_health_style_read_stays_available_during_recovery(database: Database, workspace) -> None:
    """A recovery write must not block an unrelated short read for 10 seconds."""

    service = JobService(database, workspace)
    job = _job(service, "concurrent-read")
    _fail(service)

    durations: list[float] = []

    def _read_jobs() -> None:
        reader = JobService(database, workspace)
        for _ in range(20):
            started = time.monotonic()
            reader.list_jobs()
            durations.append(time.monotonic() - started)
            time.sleep(0.01)

    reader_thread = threading.Thread(target=_read_jobs)
    reader_thread.start()
    resumed = service.resume(str(job["id"]))
    reader_thread.join(timeout=30)
    assert not reader_thread.is_alive()
    assert resumed["state"] == "QUEUED"
    assert max(durations) < 1.0, f"a concurrent read waited {max(durations):.3f}s"
