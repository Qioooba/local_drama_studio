"""Strict loopback-only outbox webhook delivery.

Delivery is intentionally bounded and explicit.  It reads persisted outbox
events, POSTs only to a validated loopback endpoint, and marks an event
delivered only after a 2xx response.  Endpoint-scoped claims are persisted so
an API restart can reclaim a delivery that was in flight.  Retries are finite,
exponentially backed off, and auditable; no public network targets or
ComfyUI/runtime calls are hidden in this service.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

MAX_ATTEMPTS = 5
BASE_RETRY_SECONDS = 5
MAX_RETRY_SECONDS = 300
CLAIM_LEASE_SECONDS = 30


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_loopback_url(endpoint_url: str) -> bool:
    parsed = urlsplit(endpoint_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not parsed.hostname:
        return False
    host = parsed.hostname
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirect(HTTPRedirectHandler):
    """Never let a loopback webhook hop to a public endpoint."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise DomainRuleError("WEBHOOK_REDIRECT_BLOCKED", "loopback webhook 禁止跟随重定向")


class OutboxDeliveryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def deliver(
        self,
        endpoint_url: str,
        *,
        project_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 20,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not _is_loopback_url(endpoint_url):
            raise DomainRuleError("LOOPBACK_ONLY", "webhook endpoint 只能指向 loopback，禁止公网出站")
        if after_event_id < 0 or limit < 1 or limit > 100:
            raise DomainRuleError("WEBHOOK_DELIVERY_PAGE_INVALID", "webhook after_event_id/limit 超出范围")
        now = _now()
        with self.database.transaction() as connection:
            if project_id and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            # A process can exit after the POST and before the acknowledgement
            # transaction.  Reclaim only expired claims; a live claim remains
            # invisible to a competing dispatcher.  ``ATTEMPTING`` is reclaimed
            # too: its attempt was already counted, so the retry budget stays
            # honest across a crash mid-POST.
            stale = connection.execute(
                "SELECT id,event_id,endpoint_url FROM outbox_delivery_attempts "
                "WHERE endpoint_url=? AND status IN ('IN_FLIGHT','ATTEMPTING') AND lease_until_at<=?",
                (endpoint_url, now),
            ).fetchall()
            for row in stale:
                connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='RETRYING',lease_until_at=NULL,next_attempt_at=?,updated_at=?,revision=revision+1 WHERE id=? AND status IN ('IN_FLIGHT','ATTEMPTING')",
                    (now, now, row["id"]),
                )
                connection.execute(
                    "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'operator','OUTBOX_DELIVERY_RECLAIMED','outbox_delivery_attempt',?,?,?)",
                    (
                        actor,
                        str(row["id"]),
                        "重启后回收过期 outbox 投递 claim",
                        json.dumps({"event_id": int(row["event_id"]), "endpoint": endpoint_url}, ensure_ascii=False),
                    ),
                )

            # Materialize a ledger row only for events this endpoint has no
            # ledger row for.  Re-claiming an existing row is the claim query's
            # job below, and it only accepts PENDING/RETRYING, which keeps a
            # DELIVERED or DEAD_LETTER event terminal even when an old
            # ``next_attempt_at`` value is still present.
            event_scope = " AND e.project_id=?" if project_id else ""
            event_rows = connection.execute(
                f"""SELECT e.* FROM outbox_events e WHERE e.delivered_at IS NULL AND e.event_id>?{event_scope}
                AND NOT EXISTS (SELECT 1 FROM outbox_delivery_attempts a WHERE a.event_id=e.event_id AND a.endpoint_url=?)
                ORDER BY e.event_id LIMIT ?""",
                [after_event_id, *([project_id] if project_id else []), endpoint_url, limit],
            ).fetchall()
            for row in event_rows:
                connection.execute(
                    "INSERT OR IGNORE INTO outbox_delivery_attempts (id,event_id,endpoint_url,status,attempt_count,created_at,updated_at,revision,schema_version) VALUES (?,?,?,'PENDING',0,?,?,1,'v1')",
                    (str(uuid.uuid4()), row["event_id"], endpoint_url, now, now),
                )
            rows = connection.execute(
                "SELECT a.id AS delivery_id,a.event_id,a.attempt_count,e.type,e.project_id,e.subject_type,e.subject_id,e.payload_json,e.occurred_at "
                "FROM outbox_delivery_attempts a JOIN outbox_events e ON e.event_id=a.event_id "
                "WHERE a.endpoint_url=? AND e.delivered_at IS NULL AND a.status IN ('PENDING','RETRYING') "
                "AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=?) "
                + ("AND e.project_id=? " if project_id else "")
                + "AND e.event_id>? ORDER BY e.event_id LIMIT ?",
                [endpoint_url, now, *([project_id] if project_id else []), after_event_id, limit],
            ).fetchall()
            if not rows:
                return self._result(endpoint_url, project_id, [], [], "NO_EVENTS")
            lease_until = datetime.now(UTC).timestamp() + CLAIM_LEASE_SECONDS
            lease = datetime.fromtimestamp(lease_until, UTC).isoformat()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                # Claiming reserves the event for this dispatcher only, and the
                # reservation carries a *token*: every later begin/ack/failure/
                # release must prove it still owns the row.  Status alone is not
                # ownership — after a lease expiry another dispatcher can hold the
                # same row in the same status.  The retry budget is still *not*
                # consumed here: an event that is never actually sent must keep its
                # full budget.
                claim_token = str(uuid.uuid4())
                updated = connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='IN_FLIGHT',claim_token=?,lease_until_at=?,next_attempt_at=NULL,updated_at=?,revision=revision+1 WHERE id=? AND status IN ('PENDING','RETRYING')",
                    (claim_token, lease, now, row["delivery_id"]),
                ).rowcount
                if updated:
                    claimed.append(
                        {
                            "delivery_id": str(row["delivery_id"]),
                            "attempt": int(row["attempt_count"]),
                            "claim_token": claim_token,
                            "event": {
                                "event_id": int(row["event_id"]),
                                "type": str(row["type"]),
                                "project_id": row["project_id"],
                                "subject_type": str(row["subject_type"]),
                                "subject_id": str(row["subject_id"]),
                                "payload": json.loads(row["payload_json"]),
                                "occurred_at": row["occurred_at"],
                            },
                        }
                    )
        events = claimed
        if not events:
            return self._result(endpoint_url, project_id, [], [], "NO_EVENTS")
        # Any claim this dispatcher does not turn into a real HTTP attempt is
        # released before returning, so a failure on the first event never
        # strands — or pre-consumes the budget of — the rest of the batch.
        unsent: list[dict[str, Any]] = list(events)
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        delivered: list[int] = []
        failed: list[dict[str, Any]] = []
        try:
            for item in events:
                event = item["event"]
                # The attempt is counted only now, immediately before the HTTP
                # send actually starts.  An event whose turn never comes keeps
                # its complete retry budget.
                item["attempt"] = self._begin_attempt(item, now=now)
                unsent.remove(item)
                body = json.dumps({"event": event}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                response_status: int | None = None
                request = Request(
                    endpoint_url,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Local-Drama-Event-Id": str(event["event_id"]),
                        "X-Local-Drama-Attempt": str(item["attempt"]),
                    },
                )
                try:
                    with opener.open(request, timeout=3) as response:  # noqa: S310 - endpoint was validated as loopback
                        response_status = int(response.status)
                        if not 200 <= response_status < 300:
                            raise DomainRuleError("WEBHOOK_DELIVERY_FAILED", "loopback webhook 返回非 2xx", {"status": response.status})
                        response.read(16 * 1024)
                except DomainRuleError as error:
                    failed.append({"event_id": event["event_id"], "reason": error.code, "attempt": item["attempt"]})
                    self._record_failure(item, endpoint_url, error.code, response_status, actor)
                    break
                except HTTPError as error:
                    reason = f"HTTP_{error.code}"
                    failed.append({"event_id": event["event_id"], "reason": reason, "attempt": item["attempt"]})
                    self._record_failure(item, endpoint_url, reason, int(error.code), actor)
                    break
                except (OSError, TimeoutError, URLError) as error:
                    reason = type(error).__name__
                    failed.append({"event_id": event["event_id"], "reason": reason, "attempt": item["attempt"]})
                    self._record_failure(item, endpoint_url, reason, None, actor)
                    break
                acknowledged_at = _now()
                with self.database.transaction() as connection:
                    acknowledged = bool(
                        connection.execute(
                            "UPDATE outbox_delivery_attempts SET status='DELIVERED',claim_token=NULL,lease_until_at=NULL,next_attempt_at=NULL,last_error=NULL,last_response_status=?,updated_at=?,delivered_at=?,revision=revision+1 WHERE id=? AND status='ATTEMPTING' AND claim_token=?",
                            (
                                response_status,
                                acknowledged_at,
                                acknowledged_at,
                                item["delivery_id"],
                                str(item.get("claim_token") or ""),
                            ),
                        ).rowcount
                    )
                    if not acknowledged:
                        # The claim was reclaimed while this POST was in flight:
                        # the event stays retryable and this dispatcher must not
                        # claim — or write — a success it no longer owns.
                        failed.append({"event_id": event["event_id"], "reason": "WEBHOOK_DELIVERY_CLAIM_LOST", "attempt": item["attempt"]})
                        continue
                    connection.execute(
                        "UPDATE outbox_events SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL",
                        (acknowledged_at, event["event_id"]),
                    )
                    connection.execute(
                        "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'OUTBOX_WEBHOOK_DELIVERED', 'outbox', ?, ?, ?)",
                        (
                            actor,
                            str(event["event_id"]),
                            "loopback webhook 投递成功",
                            json.dumps({"endpoint": endpoint_url, "attempt": item["attempt"], "event_id": event["event_id"]}, ensure_ascii=False),
                        ),
                    )
                delivered.append(int(event["event_id"]))
        finally:
            # Release every claimed event that never reached the wire, whatever
            # ended the loop (first-event failure, DomainRuleError, cancellation
            # or an unexpected exception).
            self._release_unsent(unsent, now=_now())

        status = "DELIVERED" if not failed else "PARTIAL"
        return self._result(endpoint_url, project_id, delivered, failed, status)

    def _begin_attempt(self, item: dict[str, Any], *, now: str) -> int:
        """Persist the real attempt number immediately before the HTTP send.

        ``IN_FLIGHT`` only means "reserved by this dispatcher".  The budget is
        consumed here, in its own committed transaction, so a crash during the
        POST cannot lose the attempt and an event that is never sent cannot
        consume one.  The compare-and-set includes the *claim token*, so a
        dispatcher whose reservation was reclaimed cannot start (or count) the
        attempt even though the status happens to read the same.
        """

        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE outbox_delivery_attempts SET status='ATTEMPTING',attempt_count=attempt_count+1,updated_at=?,revision=revision+1 WHERE id=? AND status='IN_FLIGHT' AND claim_token=?",
                (now, item["delivery_id"], str(item.get("claim_token") or "")),
            ).rowcount
            if not updated:
                # Another dispatcher reclaimed the claim; do not send it twice.
                raise DomainRuleError("WEBHOOK_DELIVERY_CLAIM_LOST", "outbox 投递 claim 已被回收")
            row = connection.execute(
                "SELECT attempt_count FROM outbox_delivery_attempts WHERE id=?",
                (item["delivery_id"],),
            ).fetchone()
        return int(row["attempt_count"])

    def _release_unsent(self, items: list[dict[str, Any]], *, now: str) -> None:
        """Give back only the claims this dispatcher still owns.

        The release used to accept ``ATTEMPTING`` as well, which meant a failure in
        one batch stole the row from a *different* dispatcher that had reclaimed it
        after a lease expiry and was already sending it.  Only this dispatcher's
        own token is released, so an active sender is never disturbed.
        """

        if not items:
            return
        with self.database.transaction() as connection:
            for item in items:
                connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='RETRYING',claim_token=NULL,lease_until_at=NULL,next_attempt_at=?,updated_at=?,revision=revision+1 WHERE id=? AND claim_token=? AND status IN ('IN_FLIGHT','ATTEMPTING')",
                    (now, now, item["delivery_id"], str(item.get("claim_token") or "")),
                )

    def _record_failure(
        self,
        item: dict[str, Any],
        endpoint_url: str,
        reason: str,
        response_status: int | None,
        actor: str,
    ) -> None:
        attempt = int(item["attempt"])
        dead = attempt >= MAX_ATTEMPTS
        now = _now()
        next_at = None if dead else (datetime.now(UTC) + timedelta(seconds=min(BASE_RETRY_SECONDS * (2 ** (attempt - 1)), MAX_RETRY_SECONDS))).isoformat()
        status = "DEAD_LETTER" if dead else "RETRYING"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE outbox_delivery_attempts SET status=?,claim_token=NULL,lease_until_at=NULL,next_attempt_at=?,last_error=?,last_response_status=?,updated_at=?,revision=revision+1 WHERE id=? AND status='ATTEMPTING' AND claim_token=?",
                (status, next_at, reason[:200], response_status, now, item["delivery_id"], str(item.get("claim_token") or "")),
            )
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'operator',?,?,?,?,?)",
                (
                    actor,
                    "OUTBOX_DELIVERY_DEAD_LETTER" if dead else "OUTBOX_DELIVERY_RETRY_SCHEDULED",
                    "outbox_delivery_attempt",
                    str(item["delivery_id"]),
                    "outbox webhook 投递失败",
                    json.dumps({"event_id": item["event"]["event_id"], "endpoint": endpoint_url, "attempt": attempt, "next_attempt_at": next_at, "error": reason[:200]}, ensure_ascii=False),
                ),
            )

    @staticmethod
    def _result(endpoint_url: str, project_id: str | None, delivered: list[int], failed: list[dict[str, Any]], status: str) -> dict[str, Any]:
        return {
            "endpoint_url": endpoint_url,
            "scope": {"project_id": project_id},
            "status": status,
            "delivered_event_ids": delivered,
            "delivered_count": len(delivered),
            "failed": failed,
            "bounded": True,
            "max_batch_size": 100,
            "remote_transport_allowed": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "max_attempts": MAX_ATTEMPTS,
            "retry_backoff_seconds": {"base": BASE_RETRY_SECONDS, "max": MAX_RETRY_SECONDS},
            "claim_lease_seconds": CLAIM_LEASE_SECONDS,
            "mutated": bool(delivered or failed),
        }
