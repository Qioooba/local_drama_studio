"""Outbox delivery must be owned by a claim token, not by a status string (PR-06).

Reproduced interleaving on the audit snapshot:

1. dispatcher A claims events 1 and 2 and blocks on event 1's HTTP wait;
2. event 2's lease expires, dispatcher B reclaims it, enters ``ATTEMPTING`` and
   waits for the receiver's response;
3. A's event 1 fails, its ``finally`` runs ``_release_unsent([event 2])``, which
   only checked ``delivery_id`` and status — so it flipped **B's** ``ATTEMPTING``
   row to ``RETRYING``;
4. B received HTTP 204 but its acknowledgement UPDATE could no longer find an
   ``ATTEMPTING`` row, so it returned ``WEBHOOK_DELIVERY_CLAIM_LOST`` and the event
   stayed ``RETRYING``; the message the receiver already accepted is sent again.

The fix gives every claim a token; begin/ack/failure/release are compare-and-sets
on that token, and the release only touches the rows this dispatcher still owns.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.outbox_delivery import OutboxDeliveryService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

import pytest

ENDPOINT = "http://127.0.0.1:9/events"


def _seed_events(database: Database, workspace: Any, code: str) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    JobService(database, workspace).create_job(
        project_id, "LOCAL_TEST", "PROJECT", project_id, "CPU", {}, f"{code}-job"
    )
    return project_id


def _ledger_rows(database: Database) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM outbox_delivery_attempts WHERE endpoint_url=? ORDER BY event_id", (ENDPOINT,)
        ).fetchall()
    return [dict(row) for row in rows]


def _set_expired_lease(database: Database, delivery_id: str, token: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE outbox_delivery_attempts SET status='IN_FLIGHT', claim_token=?,"
            " lease_until_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (token, delivery_id),
        )


def test_a_late_failure_never_releases_another_dispatchers_active_claim(
    workspace: Any, database: Database, monkeypatch: Any
) -> None:
    """The exact interleaving: A's failure must not disturb B's live send."""

    project_id = _seed_events(database, workspace, "outbox_claim_ownership")
    service = OutboxDeliveryService(database)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO outbox_delivery_attempts (id,event_id,endpoint_url,status,attempt_count,created_at,updated_at,revision,schema_version)"
            " SELECT lower(hex(randomblob(16))), event_id, ?, 'PENDING', 0, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1, 'v1'"
            " FROM outbox_events WHERE project_id=? AND delivered_at IS NULL AND event_id NOT IN"
            " (SELECT event_id FROM outbox_delivery_attempts WHERE endpoint_url=?)",
            (ENDPOINT, project_id, ENDPOINT),
        )
    rows = _ledger_rows(database)
    assert len(rows) >= 2, rows

    # Dispatcher B owns event 2 and is already sending it.
    second = rows[1]
    b_token = "claim-of-dispatcher-B"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE outbox_delivery_attempts SET status='IN_FLIGHT', claim_token=?, lease_until_at=NULL WHERE id=?",
            (b_token, second["id"]),
        )
    released_by_a = [{"delivery_id": str(second["id"]), "attempt": 0, "claim_token": "claim-of-dispatcher-A"}]
    service._release_unsent(released_by_a, now="2026-01-01T00:00:05+00:00")  # noqa: SLF001

    after = {str(row["id"]): row for row in _ledger_rows(database)}
    assert after[str(second["id"])]["status"] == "IN_FLIGHT"
    assert after[str(second["id"])]["claim_token"] == b_token

    # B's successful acknowledgement still finds its own claim.
    began = service._begin_attempt(  # noqa: SLF001
        {"delivery_id": str(second["id"]), "claim_token": b_token}, now="2026-01-01T00:00:06+00:00"
    )
    assert began == 1
    with database.transaction() as connection:
        acked = connection.execute(
            "UPDATE outbox_delivery_attempts SET status='DELIVERED', claim_token=NULL"
            " WHERE id=? AND status='ATTEMPTING' AND claim_token=?",
            (second["id"], b_token),
        ).rowcount
    assert acked == 1
    assert {str(row["id"]): row["status"] for row in _ledger_rows(database)}[str(second["id"])] == "DELIVERED"


def test_a_late_acknowledgement_cannot_overwrite_a_new_owner(
    workspace: Any, database: Database, monkeypatch: Any
) -> None:
    """A's stale token must fail its compare-and-set after B reclaimed the row."""

    project_id = _seed_events(database, workspace, "outbox_claim_stale_ack")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO outbox_delivery_attempts (id,event_id,endpoint_url,status,attempt_count,created_at,updated_at,revision,schema_version)"
            " SELECT 'ledger-1', event_id, ?, 'PENDING', 0, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1, 'v1'"
            " FROM outbox_events WHERE project_id=? AND delivered_at IS NULL LIMIT 1",
            (ENDPOINT, project_id),
        )
    a_token = "claim-of-dispatcher-A"
    _set_expired_lease(database, "ledger-1", a_token)

    # B reclaims the expired claim.
    service = OutboxDeliveryService(database)
    claimed = service.deliver(ENDPOINT, project_id=project_id, limit=1)
    assert claimed["status"] in {"DELIVERED", "PARTIAL"}
    b_row = _ledger_rows(database)[0]

    # A's late acknowledgement (its own token) must not match.
    with database.transaction() as connection:
        stale = connection.execute(
            "UPDATE outbox_delivery_attempts SET status='DELIVERED'"
            " WHERE id=? AND status='ATTEMPTING' AND claim_token=?",
            ("ledger-1", a_token),
        ).rowcount
    assert stale == 0
    assert str(b_row["claim_token"]) != a_token


def test_begin_attempt_fails_when_the_token_was_replaced(workspace: Any, database: Database) -> None:
    project_id = _seed_events(database, workspace, "outbox_claim_token_replaced")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO outbox_delivery_attempts (id,event_id,endpoint_url,status,attempt_count,created_at,updated_at,revision,schema_version)"
            " SELECT 'ledger-2', event_id, ?, 'IN_FLIGHT', 0, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1, 'v1'"
            " FROM outbox_events WHERE project_id=? AND delivered_at IS NULL LIMIT 1",
            (ENDPOINT, project_id),
        )
        connection.execute(
            "UPDATE outbox_delivery_attempts SET claim_token='owner-B' WHERE id='ledger-2'"
        )
    service = OutboxDeliveryService(database)
    with pytest.raises(DomainRuleError) as error:
        service._begin_attempt(  # noqa: SLF001
            {"delivery_id": "ledger-2", "claim_token": "owner-A"}, now="2026-01-01T00:00:07+00:00"
        )
    assert error.value.code == "WEBHOOK_DELIVERY_CLAIM_LOST"
    # The other owner's row was not touched.
    assert _ledger_rows(database)[0]["status"] == "IN_FLIGHT"
    assert _ledger_rows(database)[0]["attempt_count"] == 0

