"""Strict loopback-only outbox webhook delivery.

Delivery is intentionally bounded and explicit.  It reads persisted outbox
events, POSTs only to a validated loopback endpoint, and marks an event
delivered only after a 2xx response.  No retries, worker claims, public
network targets, or ComfyUI/runtime calls are hidden in this service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


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
        with self.database.connect() as connection:
            if project_id and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            clauses = ["delivered_at IS NULL", "event_id>?"]
            params: list[Any] = [after_event_id]
            if project_id:
                clauses.append("project_id=?")
                params.append(project_id)
            params.append(limit)
            rows = connection.execute(
                f"SELECT * FROM outbox_events WHERE {' AND '.join(clauses)} ORDER BY event_id LIMIT ?", params
            ).fetchall()
        events = [
            {
                "event_id": int(row["event_id"]),
                "type": str(row["type"]),
                "project_id": row["project_id"],
                "subject_type": str(row["subject_type"]),
                "subject_id": str(row["subject_id"]),
                "payload": json.loads(row["payload_json"]),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]
        if not events:
            return self._result(endpoint_url, project_id, [], [], "NO_EVENTS")

        opener = build_opener(ProxyHandler({}))
        delivered: list[int] = []
        failed: list[dict[str, Any]] = []
        for event in events:
            body = json.dumps({"event": event}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request = Request(
                endpoint_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "X-Local-Drama-Event-Id": str(event["event_id"])},
            )
            try:
                with opener.open(request, timeout=3) as response:  # noqa: S310 - endpoint was validated as loopback
                    if not 200 <= int(response.status) < 300:
                        raise DomainRuleError("WEBHOOK_DELIVERY_FAILED", "loopback webhook 返回非 2xx", {"status": response.status})
                    response.read(16 * 1024)
            except DomainRuleError:
                raise
            except HTTPError as error:
                failed.append({"event_id": event["event_id"], "reason": f"HTTP_{error.code}"})
                break
            except (OSError, TimeoutError, URLError) as error:
                failed.append({"event_id": event["event_id"], "reason": type(error).__name__})
                break
            delivered.append(int(event["event_id"]))

        if delivered:
            now = _now()
            with self.database.transaction() as connection:
                placeholders = ",".join("?" for _ in delivered)
                connection.execute(
                    f"UPDATE outbox_events SET delivered_at=? WHERE delivered_at IS NULL AND event_id IN ({placeholders})",
                    [now, *delivered],
                )
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'OUTBOX_WEBHOOK_DELIVERED', 'outbox', ?, ?, ?)",
                    (actor, str(delivered[-1]), "loopback webhook 批量投递", json.dumps({"endpoint": endpoint_url, "event_ids": delivered})),
                )
        status = "DELIVERED" if not failed else "PARTIAL"
        return self._result(endpoint_url, project_id, delivered, failed, status)

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
            "mutated": bool(delivered),
        }
