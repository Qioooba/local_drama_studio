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
            # invisible to a competing dispatcher.
            stale = connection.execute(
                "SELECT id,event_id,endpoint_url FROM outbox_delivery_attempts "
                "WHERE endpoint_url=? AND status='IN_FLIGHT' AND lease_until_at<=?",
                (endpoint_url, now),
            ).fetchall()
            for row in stale:
                connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='RETRYING',lease_until_at=NULL,next_attempt_at=?,updated_at=?,revision=revision+1 WHERE id=? AND status='IN_FLIGHT'",
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

            clauses = ["e.delivered_at IS NULL", "e.event_id>?", "(a.id IS NULL OR a.status IN ('PENDING','RETRYING'))"]
            params: list[Any] = [after_event_id]
            if project_id:
                clauses.append("e.project_id=?")
                params.append(project_id)
            event_rows = connection.execute(
                f"SELECT e.* FROM outbox_events e LEFT JOIN outbox_delivery_attempts a ON a.event_id=e.event_id AND a.endpoint_url=? WHERE {' AND '.join(clauses)} "
                "AND (a.id IS NULL OR a.next_attempt_at IS NULL OR a.next_attempt_at<=?) ORDER BY e.event_id LIMIT ?",
                [endpoint_url, *params, now, limit],
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
                attempt_no = int(row["attempt_count"]) + 1
                updated = connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='IN_FLIGHT',attempt_count=?,lease_until_at=?,next_attempt_at=NULL,updated_at=?,revision=revision+1 WHERE id=? AND status IN ('PENDING','RETRYING')",
                    (attempt_no, lease, now, row["delivery_id"]),
                ).rowcount
                if updated:
                    claimed.append(
                        {
                            "delivery_id": str(row["delivery_id"]),
                            "attempt": attempt_no,
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

        opener = build_opener(ProxyHandler({}), _NoRedirect())
        delivered: list[int] = []
        failed: list[dict[str, Any]] = []
        for item in events:
            event = item["event"]
            body = json.dumps({"event": event}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            response_status: int | None = None
            request = Request(
                endpoint_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "X-Local-Drama-Event-Id": str(event["event_id"])},
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
            delivered.append(int(event["event_id"]))
            now = _now()
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE outbox_delivery_attempts SET status='DELIVERED',lease_until_at=NULL,next_attempt_at=NULL,last_error=NULL,last_response_status=?,updated_at=?,delivered_at=?,revision=revision+1 WHERE id=? AND status='IN_FLIGHT'",
                    (response_status, now, now, item["delivery_id"]),
                )
                connection.execute(
                    "UPDATE outbox_events SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL",
                    (now, event["event_id"]),
                )
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'OUTBOX_WEBHOOK_DELIVERED', 'outbox', ?, ?, ?)",
                    (actor, str(event["event_id"]), "loopback webhook 投递成功", json.dumps({"endpoint": endpoint_url, "attempt": item["attempt"], "event_id": event["event_id"]}, ensure_ascii=False)),
                )

        status = "DELIVERED" if not failed else "PARTIAL"
        return self._result(endpoint_url, project_id, delivered, failed, status)

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
                "UPDATE outbox_delivery_attempts SET status=?,lease_until_at=NULL,next_attempt_at=?,last_error=?,last_response_status=?,updated_at=?,revision=revision+1 WHERE id=? AND status='IN_FLIGHT'",
                (status, next_at, reason[:200], response_status, now, item["delivery_id"]),
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
