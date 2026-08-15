"""Scoped, bounded, loopback-only automation clients and webhooks.

The service deliberately keeps automation on the same command boundary as the
UI: clients have explicit scopes, tokens are only returned at creation, every
callback is loopback validated and HMAC signed, retries are finite, and dead
letters require an explicit retry command.  No provider/runtime or public
network transport is reachable from this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

ALLOWED_SCOPES = frozenset({"read", "plan", "submit", "review", "delivery"})
MAX_ATTEMPTS = 5
MAX_BATCH_SIZE = 100
BASE_RETRY_SECONDS = 5
MAX_RETRY_SECONDS = 300
# A claimed delivery is only held in this state while the explicit command is
# making its bounded loopback request.  A stale claim is released on the next
# command so a killed process cannot strand an event forever.
CLAIM_TIMEOUT_SECONDS = 300


class _NoRedirect(HTTPRedirectHandler):
    """Never follow a webhook redirect to a potentially non-loopback target."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_loopback_url(endpoint_url: str) -> bool:
    try:
        parsed = urlsplit(endpoint_url)
        hostname = parsed.hostname
        # Only literal loopback IPs are accepted.  Hostnames (including
        # ``localhost``) are rejected to avoid hosts-file/DNS rebinding and to
        # keep the LOCAL_ONLY boundary auditable and deterministic.
        host_ip = ip_address(hostname) if hostname else None
        _ = parsed.port  # force malformed-port validation
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not hostname:
        return False
    if parsed.fragment or host_ip is None:
        return False
    return host_ip.is_loopback


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise DomainRuleError("AUTOMATION_TOKEN_REQUIRED", "自动化 API 需要 Authorization Bearer token")
    token = authorization[7:].strip()
    if not token:
        raise DomainRuleError("AUTOMATION_TOKEN_REQUIRED", "自动化 token 不能为空")
    return token


class AutomationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_client(
        self,
        *,
        code: str,
        title: str,
        project_id: str | None,
        scopes: list[str],
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized_code = code.strip()
        normalized_title = title.strip()
        normalized_scopes = sorted(set(scopes))
        if not normalized_code or not normalized_title:
            raise DomainRuleError("AUTOMATION_CLIENT_INVALID", "自动化 client 的 code/title 不能为空")
        if not normalized_scopes or not set(normalized_scopes).issubset(ALLOWED_SCOPES):
            raise DomainRuleError("AUTOMATION_SCOPE_INVALID", "自动化 scope 必须来自 read/plan/submit/review/delivery")
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "自动化 client 必须提供有效 Idempotency-Key")
        client_id = str(uuid.uuid4())
        token = f"ldsa_{secrets.token_urlsafe(32)}"
        now = _now()
        scope = f"automation-client:{project_id or 'global'}"
        payload_hash = _hash(_json({"code": normalized_code, "title": normalized_title, "project_id": project_id, "scopes": normalized_scopes}))
        with self.database.transaction() as connection:
            prior = connection.execute("SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (scope, idempotency_key)).fetchone()
            if prior:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 Idempotency-Key 不能复用不同自动化 client 参数")
                replay = cast(dict[str, Any], json.loads(str(prior["response_json"])))
                replay["idempotent_replay"] = True
                return replay
            if project_id and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            if connection.execute("SELECT 1 FROM automation_clients WHERE code=?", (normalized_code,)).fetchone():
                raise DomainRuleError("AUTOMATION_CLIENT_CODE_EXISTS", "自动化 client code 已存在", {"code": normalized_code})
            connection.execute(
                """INSERT INTO automation_clients
                (id,project_id,code,title,token_hash,scopes_json,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?, 'ACTIVE',?,?,?,1,'v1')""",
                (client_id, project_id, normalized_code, normalized_title, _hash(token), _json(normalized_scopes), now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'operator','AUTOMATION_CLIENT_CREATED','automation_client',?,?,?)",
                (actor, client_id, "创建本机自动化 client", _json({"code": normalized_code, "project_id": project_id, "scopes": normalized_scopes, "token_returned_once": True})),
            )
            result = {
            "id": client_id,
            "project_id": project_id,
            "code": normalized_code,
            "title": normalized_title,
            "scopes": normalized_scopes,
            "status": "ACTIVE",
            "token": token,
            "token_returned_once": True,
            "loopback_only": True,
            "network_contacted": False,
            "idempotent_replay": False,
            }
            persisted = {**result, "token": None, "token_returned_once": False, "idempotent_replay": True}
            connection.execute("INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)", (scope, idempotency_key, payload_hash, _json(persisted)))
        return result

    def _client(self, token: str, required_scope: str | None = None) -> Any:
        token_hash = _hash(token)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM automation_clients WHERE token_hash=? AND status='ACTIVE'", (token_hash,)).fetchone()
            if row is None or not hmac.compare_digest(str(row["token_hash"]), token_hash):
                raise DomainRuleError("AUTOMATION_TOKEN_INVALID", "自动化 token 无效或已撤销")
            scopes = set(json.loads(str(row["scopes_json"])))
            if required_scope and required_scope not in scopes:
                raise DomainRuleError("AUTOMATION_SCOPE_FORBIDDEN", "自动化 token 没有所需 scope", {"required_scope": required_scope})
            connection.execute("UPDATE automation_clients SET last_used_at=?,updated_at=?,revision=revision+1 WHERE id=?", (_now(), _now(), row["id"]))
            return {**dict(row), "scopes": scopes}

    def _check_project_scope(self, client: Any, project_id: str | None) -> None:
        bound = client["project_id"]
        if bound and project_id and str(bound) != project_id:
            raise DomainRuleError("AUTOMATION_PROJECT_FORBIDDEN", "自动化 client 不能访问其他项目", {"project_id": project_id})
        if bound and project_id is None:
            raise DomainRuleError("AUTOMATION_PROJECT_REQUIRED", "项目绑定的自动化 client 必须显式提供 project_id")

    @staticmethod
    def _subscription_view(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "automation_client_id": str(row["automation_client_id"]),
            "project_id": row["project_id"],
            "endpoint_url": str(row["endpoint_url"]),
            "event_types": json.loads(str(row["event_types_json"])),
            "status": str(row["status"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "loopback_only": True,
        }

    def create_subscription(
        self,
        token: str,
        *,
        endpoint_url: str,
        project_id: str | None,
        event_types: list[str],
        idempotency_key: str,
        actor: str = "automation-client",
    ) -> dict[str, Any]:
        client = self._client(token, "delivery")
        self._check_project_scope(client, project_id)
        if not _is_loopback_url(endpoint_url):
            # Rejections are auditable without persisting the potentially
            # sensitive endpoint itself.  A hash keeps the operator trace
            # useful while ensuring a public URL or credentials cannot leak
            # into the audit ledger.
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'automation','WEBHOOK_SUBSCRIPTION_REJECTED','automation_client',?,?,?)",
                    (
                        actor,
                        str(client["id"]),
                        "拒绝非 loopback webhook 订阅",
                        _json({"reason": "LOOPBACK_ONLY", "endpoint_hash": _hash(endpoint_url)}),
                    ),
                )
            raise DomainRuleError("LOOPBACK_ONLY", "webhook endpoint 只能指向 loopback，禁止公网出站")
        if project_id:
            with self.database.connect() as connection:
                if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                    raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        normalized_events = sorted(set(item.strip() for item in event_types if item.strip()))
        if len(normalized_events) > 50:
            raise DomainRuleError("WEBHOOK_EVENT_FILTER_INVALID", "webhook event_types 最多 50 个")
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "webhook 订阅必须提供有效 Idempotency-Key")
        subscription_id = str(uuid.uuid4())
        secret = f"whsec_{secrets.token_urlsafe(32)}"
        now = _now()
        scope = f"webhook-subscription:{client['id']}"
        payload_hash = _hash(_json({"endpoint_url": endpoint_url, "project_id": project_id, "event_types": normalized_events}))
        with self.database.transaction() as connection:
            prior = connection.execute("SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (scope, idempotency_key)).fetchone()
            if prior:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 Idempotency-Key 不能复用不同 webhook 参数")
                replay = cast(dict[str, Any], json.loads(str(prior["response_json"])))
                replay["idempotent_replay"] = True
                return replay
            connection.execute(
                """INSERT INTO webhook_subscriptions
                (id,automation_client_id,project_id,endpoint_url,event_types_json,signing_secret,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?, 'ACTIVE',?,?,?,1,'v1')""",
                (subscription_id, client["id"], project_id, endpoint_url, _json(normalized_events), secret, now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'automation','WEBHOOK_SUBSCRIPTION_CREATED','webhook_subscription',?,?,?)",
                (actor, subscription_id, "创建 loopback webhook 订阅", _json({"project_id": project_id, "event_types": normalized_events, "secret_returned_once": True})),
            )
            result = {
            "id": subscription_id,
            "automation_client_id": str(client["id"]),
            "project_id": project_id,
            "endpoint_url": endpoint_url,
            "event_types": normalized_events,
            "status": "ACTIVE",
            "signing_secret": secret,
            "secret_returned_once": True,
            "loopback_only": True,
            "idempotent_replay": False,
            }
            persisted = {**result, "signing_secret": None, "secret_returned_once": False, "idempotent_replay": True}
            connection.execute("INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)", (scope, idempotency_key, payload_hash, _json(persisted)))
        return result

    def list_subscriptions(self, token: str) -> dict[str, Any]:
        client = self._client(token, "delivery")
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM webhook_subscriptions WHERE automation_client_id=? ORDER BY created_at DESC", (client["id"],)).fetchall()
        return {"items": [self._subscription_view(row) for row in rows], "loopback_only": True, "network_contacted": False}

    def _ensure_pending(self, connection: Any, client: Any, project_id: str | None, subscription_id: str | None, after_event_id: int, limit: int) -> None:
        clauses = ["s.automation_client_id=?", "s.status='ACTIVE'"]
        params: list[Any] = [client["id"]]
        if subscription_id:
            clauses.append("s.id=?")
            params.append(subscription_id)
        if project_id:
            clauses.append("(s.project_id IS NULL OR s.project_id=?)")
            params.append(project_id)
        subscriptions = connection.execute(f"SELECT s.* FROM webhook_subscriptions s WHERE {' AND '.join(clauses)}", params).fetchall()
        events = connection.execute("SELECT * FROM outbox_events WHERE event_id>? ORDER BY event_id LIMIT ?", (after_event_id, limit)).fetchall()
        for subscription in subscriptions:
            selected_types = set(json.loads(str(subscription["event_types_json"])))
            for event in events:
                if subscription["project_id"] and event["project_id"] != subscription["project_id"]:
                    continue
                if selected_types and event["type"] not in selected_types:
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO webhook_deliveries
                    (id,subscription_id,event_id,status,attempt_count,next_attempt_at,created_at,updated_at,revision,schema_version)
                    VALUES (?,?,?,'PENDING',0,?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), subscription["id"], event["event_id"], _now(), _now(), _now()),
                )

    @staticmethod
    def _event(row: Any) -> dict[str, Any]:
        return {
            "event_id": int(row["event_id"]),
            "type": str(row["type"]),
            "project_id": row["project_id"],
            "subject_type": str(row["subject_type"]),
            "subject_id": str(row["subject_id"]),
            "payload": json.loads(str(row["payload_json"])),
            "occurred_at": row["occurred_at"],
        }

    def _attempt(self, row: Any) -> tuple[bool, str | None, int | None, str | None]:
        # Re-validate the persisted target before every network call.  This
        # protects the egress boundary even if a database is manually edited
        # or an older migration contained a hostname target.
        endpoint_url = str(row["endpoint_url"])
        if not _is_loopback_url(endpoint_url):
            return False, "LOOPBACK_ENDPOINT_REVALIDATION_FAILED", None, None
        event = self._event(row)
        body = json.dumps({"delivery_id": str(row["id"]), "event": event}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = "sha256=" + hmac.new(str(row["signing_secret"]).encode("utf-8"), body, hashlib.sha256).hexdigest()
        request = Request(
            endpoint_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Local-Drama-Event-Id": str(event["event_id"]),
                "X-Local-Drama-Delivery-Id": str(row["id"]),
                "X-Local-Drama-Signature": signature,
                "X-Local-Drama-Attempt": str(int(row["attempt_count"]) + 1),
            },
        )
        try:
            # An HTTP redirect is not a safe local transport primitive: the
            # redirected Location could point off-host.  Disable redirects
            # and use an empty proxy configuration so the request stays
            # explicitly loopback-only.
            with build_opener(_NoRedirect(), ProxyHandler({})).open(request, timeout=3) as response:  # noqa: S310 - endpoint is revalidated above
                response.read(16 * 1024)
                status = int(response.status)
                if 200 <= status < 300:
                    return True, None, status, signature
                return False, f"HTTP_{status}", status, signature
        except HTTPError as error:
            return False, f"HTTP_{error.code}", int(error.code), signature
        except (OSError, TimeoutError, URLError) as error:
            return False, type(error).__name__, None, signature

    def deliver(
        self,
        token: str,
        *,
        subscription_id: str | None = None,
        project_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 20,
        actor: str = "automation-client",
    ) -> dict[str, Any]:
        if after_event_id < 0 or limit < 1 or limit > MAX_BATCH_SIZE:
            raise DomainRuleError("WEBHOOK_DELIVERY_PAGE_INVALID", "webhook after_event_id/limit 超出范围")
        client = self._client(token, "delivery")
        self._check_project_scope(client, project_id)
        now = _now()
        with self.database.transaction() as connection:
            if project_id and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            if subscription_id and connection.execute("SELECT 1 FROM webhook_subscriptions WHERE id=? AND automation_client_id=? AND status='ACTIVE'", (subscription_id, client["id"])).fetchone() is None:
                raise DomainRuleError("WEBHOOK_SUBSCRIPTION_NOT_FOUND", "webhook 订阅不存在或不可用", {"subscription_id": subscription_id})
            # Recover a claim left by a killed process.  Claims are only used
            # to serialize explicit commands; they never become an implicit
            # background worker or an unbounded retry loop.
            stale_before = (datetime.now(UTC) - timedelta(seconds=CLAIM_TIMEOUT_SECONDS)).isoformat()
            connection.execute(
                "UPDATE webhook_deliveries SET status='RETRYING',next_attempt_at=?,updated_at=?,revision=revision+1 WHERE status='SENDING' AND updated_at<?",
                (now, now, stale_before),
            )
            self._ensure_pending(connection, client, project_id, subscription_id, after_event_id, limit)
            # Newly materialized deliveries receive their timestamp inside
            # _ensure_pending; refresh the comparison cursor so they are
            # eligible in the same explicit command.
            now = _now()
            clauses = ["s.automation_client_id=?", "s.status='ACTIVE'", "d.status IN ('PENDING','RETRYING')", "(d.next_attempt_at IS NULL OR d.next_attempt_at<=?)"]
            params: list[Any] = [client["id"], now]
            if subscription_id:
                clauses.append("d.subscription_id=?")
                params.append(subscription_id)
            if project_id:
                clauses.append("(s.project_id IS NULL OR e.project_id=?)")
                params.append(project_id)
            params.append(limit)
            candidate_rows = connection.execute(
                f"SELECT d.*,s.endpoint_url,s.signing_secret,e.event_id,e.type,e.project_id,e.subject_type,e.subject_id,e.payload_json,e.occurred_at FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id JOIN outbox_events e ON e.event_id=d.event_id WHERE {' AND '.join(clauses)} ORDER BY d.created_at,d.id LIMIT ?",
                params,
            ).fetchall()
            # Claim rows while holding the SQLite write transaction.  A
            # second explicit command racing this one sees zero rowcount and
            # therefore cannot POST the same delivery twice.
            rows: list[Any] = []
            for row in candidate_rows:
                claimed = connection.execute(
                    "UPDATE webhook_deliveries SET status='SENDING',updated_at=?,revision=revision+1 WHERE id=? AND status IN ('PENDING','RETRYING')",
                    (now, row["id"]),
                )
                if claimed.rowcount == 1:
                    rows.append(row)
        if not rows:
            return self._result("NO_EVENTS", [], [], [], client["id"])
        delivered: list[str] = []
        failed: list[dict[str, Any]] = []
        dead_letters: list[str] = []
        for row in rows:
            succeeded, error, response_status, signature = self._attempt(row)
            attempt_no = int(row["attempt_count"]) + 1
            updated = _now()
            if succeeded:
                with self.database.transaction() as connection:
                    connection.execute("UPDATE webhook_deliveries SET status='DELIVERED',attempt_count=?,next_attempt_at=NULL,last_error=NULL,last_response_status=?,signature=?,updated_at=?,delivered_at=?,revision=revision+1 WHERE id=? AND status='SENDING'", (attempt_no, response_status, signature, updated, updated, row["id"]))
                    connection.execute("INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'automation','WEBHOOK_DELIVERY_SUCCEEDED','webhook_delivery',?,?,?)", (actor, str(row["id"]), "loopback webhook 投递成功", _json({"event_id": row["event_id"], "attempt": attempt_no, "response_status": response_status})))
                delivered.append(str(row["id"]))
            else:
                dead = attempt_no >= MAX_ATTEMPTS
                next_at = None if dead else (datetime.now(UTC) + timedelta(seconds=min(BASE_RETRY_SECONDS * (2 ** (attempt_no - 1)), MAX_RETRY_SECONDS))).isoformat()
                with self.database.transaction() as connection:
                    connection.execute("UPDATE webhook_deliveries SET status=?,attempt_count=?,next_attempt_at=?,last_error=?,last_response_status=?,signature=?,updated_at=?,revision=revision+1 WHERE id=? AND status='SENDING'", ("DEAD_LETTER" if dead else "RETRYING", attempt_no, next_at, error, response_status, signature, updated, row["id"]))
                    connection.execute("INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'automation',?,?,?,?,?)", (actor, "WEBHOOK_DELIVERY_DEAD_LETTER" if dead else "WEBHOOK_DELIVERY_RETRY_SCHEDULED", "webhook_delivery", str(row["id"]), "loopback webhook 投递失败", _json({"event_id": row["event_id"], "attempt": attempt_no, "next_attempt_at": next_at, "error": error})))
                failed.append({"delivery_id": str(row["id"]), "event_id": int(row["event_id"]), "reason": error, "attempt": attempt_no, "next_attempt_at": next_at})
                if dead:
                    dead_letters.append(str(row["id"]))
        status = "DELIVERED" if len(delivered) == len(rows) else ("DEAD_LETTER" if dead_letters and not delivered else "RETRYING")
        return self._result(status, delivered, failed, dead_letters, client["id"])

    @staticmethod
    def _result(status: str, delivered: list[str], failed: list[dict[str, Any]], dead_letters: list[str], client_id: str) -> dict[str, Any]:
        return {
            "status": status,
            "client_id": str(client_id),
            "delivered_delivery_ids": delivered,
            "delivered_count": len(delivered),
            "failed": failed,
            "dead_letter_delivery_ids": dead_letters,
            "max_attempts": MAX_ATTEMPTS,
            "max_batch_size": MAX_BATCH_SIZE,
            "retry_backoff_seconds": {"base": BASE_RETRY_SECONDS, "max": MAX_RETRY_SECONDS},
            "bounded": True,
            "loopback_only": True,
            "remote_transport_allowed": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": bool(delivered or failed),
        }

    def list_deliveries(self, token: str, *, subscription_id: str | None = None, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        if limit < 1 or limit > MAX_BATCH_SIZE:
            raise DomainRuleError("WEBHOOK_DELIVERY_PAGE_INVALID", "webhook delivery limit 超出范围")
        client = self._client(token, "delivery")
        clauses = ["s.automation_client_id=?"]
        params: list[Any] = [client["id"]]
        if subscription_id:
            clauses.append("d.subscription_id=?")
            params.append(subscription_id)
        if status:
            normalized = status.upper()
            if normalized not in {"PENDING", "SENDING", "RETRYING", "DELIVERED", "DEAD_LETTER"}:
                raise DomainRuleError("WEBHOOK_DELIVERY_STATUS_INVALID", "delivery status 无效")
            clauses.append("d.status=?")
            params.append(normalized)
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT d.id,d.subscription_id,d.event_id,d.status,d.attempt_count,d.next_attempt_at,d.last_error,d.last_response_status,d.created_at,d.updated_at,d.delivered_at FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id WHERE {' AND '.join(clauses)} ORDER BY d.created_at DESC,d.id DESC LIMIT ?", params).fetchall()
        return {"items": [dict(row) for row in rows], "bounded": True, "max_batch_size": MAX_BATCH_SIZE, "network_contacted": False}

    def retry_delivery(self, token: str, delivery_id: str, *, actor: str = "automation-client") -> dict[str, Any]:
        client = self._client(token, "delivery")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT d.*,s.automation_client_id,s.id AS subscription_id,s.project_id AS subscription_project_id FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id WHERE d.id=? AND s.automation_client_id=?", (delivery_id, client["id"])).fetchone()
            if row is None:
                raise DomainRuleError("WEBHOOK_DELIVERY_NOT_FOUND", "webhook delivery 不存在", {"delivery_id": delivery_id})
            if row["status"] != "DEAD_LETTER":
                raise DomainRuleError("WEBHOOK_DELIVERY_NOT_DEAD", "只有 DEAD_LETTER delivery 可以显式重试", {"status": row["status"]})
            now = _now()
            connection.execute("UPDATE webhook_deliveries SET status='PENDING',attempt_count=0,next_attempt_at=?,last_error=NULL,last_response_status=NULL,signature=NULL,updated_at=?,revision=revision+1 WHERE id=?", (now, now, delivery_id))
            connection.execute("INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json) VALUES (?,'automation','WEBHOOK_DELIVERY_RETRY_REQUESTED','webhook_delivery',?,?,?)", (actor, delivery_id, "显式重试 dead-letter webhook", _json({"attempt_count_reset": True})))
        return self.deliver(token, subscription_id=str(row["subscription_id"]), project_id=row["subscription_project_id"], limit=1, actor=actor)
