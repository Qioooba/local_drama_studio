"""SS-02 and SS-03: one state-decision authority for every recovery path.

The report's fault matrix is the acceptance criterion: cancellation, pause,
deletion and late completions must all converge on the documented terminal
states, and a soft-deleted Job must never become claimable again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from local_drama.application.jobs import (
    CANCEL_REQUESTED,
    CANCELLED,
    DELETED,
    NEEDS_ATTENTION,
    OUTCOME_ATTENTION,
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE_FAILURE,
    OUTCOME_SUCCEEDED,
    OUTCOME_UNKNOWN,
    PAUSED,
    QUEUED,
    JobService,
    JobStateFacts,
    decide_job_state,
)
from local_drama.application.worker_sessions import WorkerSessionService
from local_drama.domain.errors import DomainRuleError


def _job(service: JobService, key: str, *, max_attempts: int = 3, channel: str = "CPU") -> dict[str, object]:
    return service.create_job(None, "CPU_TEST", "PROJECT", f"subject-{key}", channel, {"key": key}, key, max_attempts=max_attempts)


def _expire_lease(database, attempt_id: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET lease_expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), attempt_id),
        )


def _state(database, job_id: str) -> str:
    with database.connect() as connection:
        return str(connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()["state"])


def _attempt_row(database, attempt_id: str) -> dict[str, object]:
    with database.connect() as connection:
        return dict(connection.execute("SELECT * FROM job_attempts WHERE id=?", (attempt_id,)).fetchone())


# --- the shared decision table ------------------------------------------------


def test_decision_table_covers_cancel_pause_delete_and_budget() -> None:
    deleted = decide_job_state(JobStateFacts(state="RUNNING", deleted=True), OUTCOME_SUCCEEDED)
    assert deleted.job_state == DELETED
    assert deleted.next_run_at is None

    cancelled = decide_job_state(JobStateFacts(state=CANCEL_REQUESTED, cancel_requested=True), OUTCOME_SUCCEEDED)
    assert cancelled.job_state == CANCELLED

    paused = decide_job_state(JobStateFacts(state=PAUSED), OUTCOME_SUCCEEDED)
    assert paused.job_state == PAUSED

    resume_pending = decide_job_state(JobStateFacts(state=PAUSED, resume_requested=True, attempt_no=1, max_attempts=3), OUTCOME_UNKNOWN)
    assert resume_pending.job_state == QUEUED

    accepted = decide_job_state(JobStateFacts(state="RUNNING", provider_accepted=True, attempt_no=1, max_attempts=3), OUTCOME_UNKNOWN)
    assert accepted.job_state == NEEDS_ATTENTION

    retryable = decide_job_state(JobStateFacts(state="RUNNING", attempt_no=1, max_attempts=3), OUTCOME_RETRYABLE_FAILURE)
    assert retryable.job_state == QUEUED

    exhausted = decide_job_state(JobStateFacts(state="RUNNING", attempt_no=3, max_attempts=3), OUTCOME_RETRYABLE_FAILURE)
    assert exhausted.job_state == "FAILED"

    quarantined = decide_job_state(JobStateFacts(state="RUNNING", attempt_no=3, max_attempts=3), OUTCOME_UNKNOWN)
    assert quarantined.job_state == NEEDS_ATTENTION

    attention = decide_job_state(JobStateFacts(state="RUNNING", attempt_no=1, max_attempts=3), OUTCOME_ATTENTION)
    assert attention.job_state == NEEDS_ATTENTION

    terminal = decide_job_state(JobStateFacts(state="RUNNING", attempt_no=1, max_attempts=3), OUTCOME_FAILED)
    assert terminal.job_state == "FAILED"


def test_pause_resume_then_second_attempt_really_succeeds(workspace, database) -> None:
    """PR-02: pause → resume → old attempt settles → the next success is a success.

    The pause wrote ``cancel_requested_at`` as the old attempt's cooperative stop
    flag, and nothing consumed it when that attempt finally settled.  The second
    attempt therefore read a stale flag and its ``success=True`` completion was
    decided as ``CANCELLED`` — and the new artifact gate refused publication even
    earlier, because it checks the same timestamp.
    """

    service = JobService(database, workspace)
    job = _job(service, "pause-resume", max_attempts=3)
    first = service.claim("pause-resume-worker", ["CPU"])
    assert first is not None
    assert service.pause(str(job["id"]))["state"] == PAUSED
    assert service.resume(str(job["id"]))["state"] == PAUSED  # pending: old attempt is live

    # The paused attempt settles; the resume intent makes the Job claimable at once
    # and the stop flag it was watching is consumed in the same transaction.
    settled = service.complete(str(first["attempt"]["id"]), str(first["attempt"]["lease_token"]), "pause-resume-worker", success=False, retryable=True)
    assert settled["job_state"] == QUEUED
    assert settled["attempt_state"] == CANCELLED
    with database.connect() as connection:
        row = connection.execute(
            "SELECT cancel_requested_at FROM jobs WHERE id=?", (str(job["id"]),)
        ).fetchone()
    assert row["cancel_requested_at"] is None

    second = service.claim("pause-resume-worker", ["CPU"])
    assert second is not None, "the resumed Job must be claimable"
    done = service.complete(
        str(second["attempt"]["id"]), str(second["attempt"]["lease_token"]), "pause-resume-worker", success=True
    )
    assert done["attempt_state"] == "SUCCEEDED"
    assert done["job_state"] == "SUCCEEDED"
    assert _state(database, str(job["id"])) == "SUCCEEDED"


def test_pause_resume_then_lease_expiry_also_clears_the_stop_flag(workspace, database) -> None:
    """The lease-expiry recovery path settles the old attempt too (PR-02)."""

    service = JobService(database, workspace)
    job = _job(service, "pause-expiry", max_attempts=3)
    first = service.claim("pause-expiry-worker", ["CPU"])
    assert first is not None
    service.pause(str(job["id"]))
    service.resume(str(job["id"]))
    _expire_lease(database, str(first["attempt"]["id"]))

    reconciled = JobService(database, workspace).reconcile()
    assert reconciled["reconciled"] == 1
    assert _state(database, str(job["id"])) == QUEUED
    with database.connect() as connection:
        row = connection.execute(
            "SELECT cancel_requested_at FROM jobs WHERE id=?", (str(job["id"]),)
        ).fetchone()
    assert row["cancel_requested_at"] is None

    second = service.claim("pause-expiry-worker", ["CPU"])
    assert second is not None
    done = service.complete(
        str(second["attempt"]["id"]), str(second["attempt"]["lease_token"]), "pause-expiry-worker", success=True
    )
    assert done["job_state"] == "SUCCEEDED"


def test_cancel_after_resume_still_wins(workspace, database) -> None:
    """Cancellation must keep outranking a resume in every race window."""

    service = JobService(database, workspace)
    job = _job(service, "resume-then-cancel", max_attempts=3)
    first = service.claim("resume-then-cancel-worker", ["CPU"])
    assert first is not None
    service.pause(str(job["id"]))
    service.resume(str(job["id"]))
    service.cancel(str(job["id"]))

    # The old attempt settles: the explicit cancel still wins, and its flag is not
    # consumed by a resume that the cancel superseded.
    settled = service.complete(
        str(first["attempt"]["id"]), str(first["attempt"]["lease_token"]), "resume-then-cancel-worker",
        success=False, retryable=True,
    )
    assert settled["job_state"] == CANCELLED
    assert service.claim("resume-then-cancel-worker", ["CPU"]) is None


def test_decision_table_marks_only_the_resume_settle_as_flag_consuming() -> None:
    resume = decide_job_state(
        JobStateFacts(state=PAUSED, resume_requested=True, attempt_no=1, max_attempts=3), OUTCOME_UNKNOWN
    )
    assert resume.consume_stop_flags is True
    ordinary = decide_job_state(
        JobStateFacts(state="RUNNING", attempt_no=1, max_attempts=3), OUTCOME_RETRYABLE_FAILURE
    )
    assert ordinary.consume_stop_flags is False
    assert ordinary.job_state == QUEUED
    paused = decide_job_state(JobStateFacts(state=PAUSED), OUTCOME_UNKNOWN)
    assert paused.consume_stop_flags is False


# --- SS-02: the report's fault matrix ----------------------------------------


def test_cancel_then_expired_lease_stays_cancelled_and_is_never_claimed_again(workspace, database) -> None:
    """Report scenario: claim -> cancel -> lease expiry -> reconcile."""

    service = JobService(database, workspace)
    job = _job(service, "cancel-lease")
    claim = service.claim("cancel-lease-worker", ["CPU"])
    assert claim is not None
    assert service.cancel(str(job["id"]))["state"] == CANCEL_REQUESTED

    _expire_lease(database, str(claim["attempt"]["id"]))
    reconciled = JobService(database, workspace).reconcile()

    assert _state(database, str(job["id"])) == CANCELLED
    assert reconciled["reconciled"] == 1
    assert service.claim("replacement-worker", ["CPU"]) is None
    assert _attempt_row(database, str(claim["attempt"]["id"]))["state"] == "ORPHANED"


def _active_session(sessions: WorkerSessionService, workspace, worker_id: str) -> dict[str, object]:
    session = sessions.start_session(
        worker_id,
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["CPU"],
    )
    sessions.heartbeat(str(session["id"]))
    return session


def test_cancel_then_worker_session_stop_stays_cancelled(workspace, database) -> None:
    """Report scenario: claim(bound session) -> cancel -> session stop -> reconcile."""

    service = JobService(database, workspace)
    sessions = WorkerSessionService(database, workspace)
    session = _active_session(sessions, workspace, "session-cancel-worker")
    job = _job(service, "cancel-session")
    claim = service.claim("session-cancel-worker", ["CPU"], worker_session_id=str(session["id"]))
    assert claim is not None
    assert service.cancel(str(job["id"]))["state"] == CANCEL_REQUESTED

    sessions.stop(str(session["id"]))
    sessions.reconcile()

    assert _state(database, str(job["id"])) == CANCELLED
    assert service.claim("replacement-worker", ["CPU"]) is None


def test_pause_then_worker_session_stop_stays_paused(workspace, database) -> None:
    """Report scenario: claim(bound session) -> pause -> session stop -> reconcile."""

    service = JobService(database, workspace)
    sessions = WorkerSessionService(database, workspace)
    session = _active_session(sessions, workspace, "session-pause-worker")
    job = _job(service, "pause-session")
    claim = service.claim("session-pause-worker", ["CPU"], worker_session_id=str(session["id"]))
    assert claim is not None
    assert service.pause(str(job["id"]))["state"] == PAUSED

    sessions.stop(str(session["id"]))
    sessions.reconcile()

    assert _state(database, str(job["id"])) == PAUSED
    # A pause with no recovery intent must not be silently turned back into work.
    assert service.claim("replacement-worker", ["CPU"]) is None


def test_uncancelled_crash_still_recovers_to_a_second_attempt(workspace, database) -> None:
    """The positive SIGKILL-equivalent path must keep working."""

    service = JobService(database, workspace)
    job = _job(service, "plain-crash", max_attempts=3)
    claim = service.claim("crashed-worker", ["CPU"])
    assert claim is not None

    _expire_lease(database, str(claim["attempt"]["id"]))
    JobService(database, workspace).reconcile()

    assert _state(database, str(job["id"])) == QUEUED
    replacement = service.claim("replacement-worker", ["CPU"])
    assert replacement is not None and replacement["attempt"]["attempt_no"] == 2


def test_cancel_beats_a_late_success_acknowledgement(workspace, database) -> None:
    service = JobService(database, workspace)
    job = _job(service, "cancel-late-success")
    claim = service.claim("late-worker", ["CPU"])
    assert claim is not None
    service.cancel(str(job["id"]))

    result = service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "late-worker",
        success=True,
    )
    assert result["job_state"] == CANCELLED
    assert result["attempt_state"] == CANCELLED
    assert _state(database, str(job["id"])) == CANCELLED


def test_pause_survives_a_late_success_acknowledgement(workspace, database) -> None:
    service = JobService(database, workspace)
    job = _job(service, "pause-late-success")
    claim = service.claim("late-worker", ["CPU"])
    assert claim is not None
    service.pause(str(job["id"]))

    result = service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "late-worker",
        success=True,
    )
    assert result["job_state"] == PAUSED
    assert service.claim("replacement-worker", ["CPU"]) is None


# --- SS-03: a deleted Job is terminal everywhere -----------------------------


def test_deleted_failed_job_cannot_be_retried_or_claimed(workspace, database) -> None:
    """Report scenario: FAILED -> delete -> retry -> claim."""

    service = JobService(database, workspace)
    job = _job(service, "deleted-retry")
    claim = service.claim("delete-worker", ["CPU"])
    assert claim is not None
    service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "delete-worker",
        success=False,
        error_code="E_LOCAL",
        retryable=False,
    )
    assert service.delete(str(job["id"]))["deleted"] is True

    with pytest.raises(DomainRuleError) as retry_error:
        service.retry(str(job["id"]))
    assert retry_error.value.code == "JOB_NOT_FOUND"
    with pytest.raises(DomainRuleError) as resume_error:
        service.resume(str(job["id"]))
    assert resume_error.value.code == "JOB_NOT_FOUND"

    assert service.claim("other-worker", ["CPU"]) is None
    assert service.list_jobs() == []


def test_paused_job_with_an_unsettled_attempt_cannot_be_deleted(workspace, database) -> None:
    """Report scenario: pause -> resume pending -> delete -> old attempt complete."""

    service = JobService(database, workspace)
    job = _job(service, "paused-delete")
    claim = service.claim("pause-worker", ["CPU"])
    assert claim is not None
    assert service.pause(str(job["id"]))["state"] == PAUSED
    assert service.resume(str(job["id"]))["state"] == PAUSED  # pending resume

    with pytest.raises(DomainRuleError) as delete_error:
        service.delete(str(job["id"]))
    assert delete_error.value.code == "JOB_DELETE_ACTIVE_FORBIDDEN"

    # After the old attempt settles the resume intent is live again, so the Job
    # is legitimately claimable and deleting it is still refused.
    service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "pause-worker",
        success=False,
        error_code="JOB_PAUSED",
    )
    assert _state(database, str(job["id"])) == QUEUED
    with pytest.raises(DomainRuleError) as still_active:
        service.delete(str(job["id"]))
    assert still_active.value.code == "JOB_DELETE_ACTIVE_FORBIDDEN"

    # Cancelling first is the documented way out, and the deleted Job stays
    # invisible afterwards.
    service.cancel(str(job["id"]))
    assert service.delete(str(job["id"]))["deleted"] is True
    assert service.claim("other-worker", ["CPU"]) is None


def test_a_deleted_job_is_not_revived_by_reconcile_or_dependency_recovery(workspace, database) -> None:
    service = JobService(database, workspace)
    upstream = service.create_job(
        None,
        "CPU_TEST",
        "PROJECT",
        "upstream-subject",
        "CPU",
        {"key": "deleted-upstream"},
        "deleted-upstream",
    )
    downstream = service.create_job(
        None,
        "CPU_TEST",
        "PROJECT",
        "downstream-subject",
        "CPU",
        {"key": "deleted-downstream"},
        "deleted-downstream",
        depends_on_job_ids=[str(upstream["id"])],
    )
    claim = service.claim("delete-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == upstream["id"]
    service.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "delete-worker",
        success=False,
        error_code="E_LOCAL",
        retryable=False,
    )
    # Deleting the upstream job must not unblock (and must not resurrect) it.
    assert service.delete(str(upstream["id"]))["deleted"] is True

    recovery = service.requeue_recovered_dependencies()
    service.reconcile()

    assert recovery["requeued"] == 0
    with database.connect() as connection:
        upstream_row = connection.execute("SELECT state, deleted_at FROM jobs WHERE id=?", (upstream["id"],)).fetchone()
        downstream_row = connection.execute("SELECT state FROM jobs WHERE id=?", (downstream["id"],)).fetchone()
    assert upstream_row["state"] == "FAILED"
    assert upstream_row["deleted_at"] is not None
    assert downstream_row["state"] == NEEDS_ATTENTION
    assert service.claim("other-worker", ["CPU"]) is None


def test_deleted_job_never_accepts_provider_or_progress_writes(workspace, database) -> None:
    service = JobService(database, workspace)
    job = _job(service, "deleted-writes")
    claim = service.claim("delete-worker", ["CPU"])
    assert claim is not None
    attempt_id = str(claim["attempt"]["id"])
    token = str(claim["attempt"]["lease_token"])

    # Force the deleted marker on an attempt that is still unsettled.  Normal
    # flows refuse to delete in this state, so this models the race the guard
    # exists for: the Job is already hidden but a worker still holds a lease.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET deleted_at=?, next_run_at=NULL WHERE id=?",
            (datetime.now(UTC).isoformat(), job["id"]),
        )

    for call in (
        lambda: service.heartbeat(attempt_id, token, "delete-worker"),
        lambda: service.attach_provider(attempt_id, token, "delete-worker", "provider-job-1"),
        lambda: service.mark_provider_acceptance_unknown(attempt_id, token, "delete-worker"),
    ):
        with pytest.raises(DomainRuleError) as error:
            call()
        assert error.value.code == "JOB_NOT_FOUND"

    # The attempt may still settle and release its resource lock, but the Job
    # must stay deleted instead of becoming claimable again.
    result = service.complete(attempt_id, token, "delete-worker", success=True)
    assert result["job_state"] == DELETED
    with database.connect() as connection:
        assert connection.execute("SELECT deleted_at FROM jobs WHERE id=?", (job["id"],)).fetchone()["deleted_at"] is not None
        lease = connection.execute("SELECT released_at FROM job_resource_leases WHERE attempt_id=?", (attempt_id,)).fetchone()
    assert lease is not None and lease["released_at"] is not None
    assert service.claim("other-worker", ["CPU"]) is None

