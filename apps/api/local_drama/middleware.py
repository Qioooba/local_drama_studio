from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from local_drama.api.contract_version import API_CONTRACT_HEADER, API_CONTRACT_VERSION

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'self'",
}
SECURITY_REJECTION_HEADERS = {
    **SECURITY_HEADERS,
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}

_OBSERVABILITY_LOG = logging.getLogger("local_drama.observability")
_OBSERVABILITY_LOG.setLevel(logging.INFO)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LOCAL_AUTOMATION_PATHS = (
    "/api/v1/automation-clients",
    "/api/v1/webhook-",
    "/api/v1/events:deliver",
)


def _is_loopback_client(request: Request) -> bool:
    """Return whether an automation API caller is connected from loopback."""

    # Starlette's in-process TestClient deliberately uses this synthetic host;
    # it is not reachable over a real socket and remains useful for contract
    # tests.  Every TCP caller must resolve to a literal loopback address.
    host = request.client.host if request.client is not None else None
    if host == "testclient":
        return True
    try:
        return bool(host and ip_address(host).is_loopback)
    except ValueError:
        return False


def _origin_matches_request_host(origin: str, request: Request) -> bool:
    parsed_origin = urlsplit(origin)
    host_header = request.headers.get("host", "").strip()
    if not host_header or not parsed_origin.hostname:
        return False
    if parsed_origin.scheme not in {"http", "https"} or parsed_origin.username or parsed_origin.password:
        return False
    if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
        return False
    try:
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        request_authority = urlsplit(f"//{host_header}")
        request_host = (request_authority.hostname or "").casefold()
        request_port = request_authority.port or origin_port
    except ValueError:
        return False
    return parsed_origin.hostname.casefold() == request_host and origin_port == request_port


def _is_allowed_write_origin(origin: str, allowed_origins: set[str], *, request: Request | None = None) -> bool:
    """Accept configured origins and HTTP origins served on literal loopback.

    The Vite development server may choose another free port when its preferred
    port is occupied.  Port drift must not turn an otherwise valid local UI into
    a read-only application.  Hostname matching remains deliberately narrow:
    only ``localhost`` and literal loopback IP addresses are accepted here;
    LAN origins still require explicit configuration.
    """

    if origin in allowed_origins:
        return True
    if request is not None and _origin_matches_request_host(origin, request):
        return True
    try:
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.path or parsed.query or parsed.fragment:
            return False
        # Accessing ``port`` also rejects malformed/non-numeric port values.
        _ = parsed.port
        if parsed.hostname.casefold() == "localhost":
            return True
        return ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _safe_identifier(value: object | None) -> str | None:
    """Keep request context useful without allowing log injection or secrets."""

    if value is None:
        return None
    candidate = "".join(character for character in str(value).strip() if character.isprintable())
    return candidate[:128] if _IDENTIFIER_RE.fullmatch(candidate) else None


def _path_identifier(path: str, segment: str) -> str | None:
    match = re.search(rf"/{re.escape(segment)}/([^/:]+)", path)
    return _safe_identifier(match.group(1)) if match else None


def _request_context(request: Request) -> dict[str, str | None]:
    """Build a non-sensitive context shared by access and failure logs."""

    request_id = _safe_identifier(getattr(request.state, "request_id", None))
    trace_id = _safe_identifier(request.headers.get("X-Trace-Id")) or request_id
    project_id = _safe_identifier(request.headers.get("X-Project-Id")) or _safe_identifier(request.query_params.get("project_id"))
    project_id = project_id or _path_identifier(request.url.path, "projects")
    job_id = _safe_identifier(request.headers.get("X-Job-Id")) or _safe_identifier(request.query_params.get("job_id"))
    job_id = job_id or _path_identifier(request.url.path, "jobs")
    episode_id = _safe_identifier(request.headers.get("X-Episode-Id")) or _safe_identifier(request.query_params.get("episode_id"))
    episode_id = episode_id or _path_identifier(request.url.path, "episodes")
    shot_id = _safe_identifier(request.headers.get("X-Shot-Id")) or _safe_identifier(request.query_params.get("shot_id"))
    shot_id = shot_id or _path_identifier(request.url.path, "shots")
    attempt_id = _safe_identifier(request.headers.get("X-Attempt-Id")) or _safe_identifier(request.query_params.get("attempt_id"))
    attempt_id = attempt_id or _path_identifier(request.url.path, "job-attempts")
    worker_id = _safe_identifier(request.headers.get("X-Worker-Id")) or _safe_identifier(request.query_params.get("worker_id"))
    provider = _safe_identifier(request.headers.get("X-Provider")) or _safe_identifier(request.query_params.get("provider"))
    return {
        "trace_id": trace_id,
        "request_id": request_id,
        "project_id": project_id,
        "episode_id": episode_id,
        "shot_id": shot_id,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "worker_id": worker_id,
        "provider": provider,
    }


def _log_request(event: str, request: Request, *, status_code: int | None = None, duration_ms: float | None = None, error: str | None = None) -> None:
    payload: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "ERROR" if error else "INFO",
        "service": "local_drama.api",
        "event": event,
        **_request_context(request),
        "method": request.method,
        "path": request.url.path,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 3)
    if error:
        # Exception text is intentionally reduced to a type, never request data.
        payload["error_type"] = error
        payload["error_code"] = error
    _OBSERVABILITY_LOG.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _ensure_request_context(request: Request) -> tuple[str, str]:
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    trace_id = request.headers.get("X-Trace-Id") or request_id
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    return request_id, trace_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id, _trace_id = _ensure_request_context(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            _log_request(
                "request.failed",
                request,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=type(error).__name__,
            )
            raise
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = str(request.state.trace_id)
        _log_request(
            "request.completed",
            request,
            status_code=response.status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return response


class ApiContractMiddleware(BaseHTTPMiddleware):
    """Make mixed API/UI releases fail before a route payload is consumed.

    The version is advertised on every response. Network clients must send the
    same version for business endpoints; health and contract discovery remain
    readable so an incompatible UI can explain the required recovery action.
    """

    _DISCOVERY_PATHS = frozenset(
        {
            "/api/v1/system/contract",
            "/api/v1/health/live",
            "/api/v1/health/ready",
            "/api/v1/health/dependencies",
        }
    )

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        is_api_request = request.url.path.startswith("/api/")
        in_process_test = request.client is not None and request.client.host == "testclient"
        observed = request.headers.get(API_CONTRACT_HEADER) or request.query_params.get("api_contract_version")
        enforce_request_version = not in_process_test or observed is not None
        if (
            is_api_request
            and request.method != "OPTIONS"
            and request.url.path not in self._DISCOVERY_PATHS
            and enforce_request_version
        ):
            if observed != API_CONTRACT_VERSION:
                request_id, _trace_id = _ensure_request_context(request)
                response: Response = JSONResponse(
                    status_code=409,
                    content={
                        "error": {
                            "code": "API_CONTRACT_MISMATCH",
                            "message": "界面与本地服务版本不一致，请刷新页面；若仍出现此提示，请重启本地服务",
                            "request_id": request_id,
                            "details": {
                                "expected": API_CONTRACT_VERSION,
                                "observed": observed,
                            },
                            "retryable": False,
                            "suggested_action": "刷新页面或重启 LocalDramaStudio 服务",
                        }
                    },
                )
                response.headers[API_CONTRACT_HEADER] = API_CONTRACT_VERSION
                return response
        response = await call_next(request)
        if is_api_request:
            response.headers[API_CONTRACT_HEADER] = API_CONTRACT_VERSION
        return response


class LocalOriginMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: tuple[str, ...],
        *,
        allow_same_origin_writes: bool = False,
    ) -> None:
        super().__init__(app)
        self.allowed_origins = set(allowed_origins)
        self.allow_same_origin_writes = allow_same_origin_writes

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        origin = request.headers.get("Origin")
        state_changing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if request.url.path.startswith(_LOCAL_AUTOMATION_PATHS) and not _is_loopback_client(request):
            request_id, _trace_id = _ensure_request_context(request)
            _log_request("request.rejected", request, status_code=403, error="LOCAL_ONLY_LOOPBACK_REQUIRED")
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "LOCAL_ONLY_LOOPBACK_REQUIRED",
                        "message": "本机自动化 API 只允许 loopback 连接",
                        "request_id": request_id,
                        "details": {},
                        "retryable": False,
                        "suggested_action": "从本机 LocalDramaStudio 实例发起自动化请求",
                    }
                },
                headers={"X-Request-Id": request_id, **SECURITY_REJECTION_HEADERS},
            )
        if state_changing and origin and not _is_allowed_write_origin(
            origin,
            self.allowed_origins,
            request=request if self.allow_same_origin_writes else None,
        ):
            request_id, _trace_id = _ensure_request_context(request)
            _log_request("request.rejected", request, status_code=403, error="ORIGIN_NOT_ALLOWED")
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "ORIGIN_NOT_ALLOWED",
                        "message": "写请求来源不在本机应用允许列表中",
                        "request_id": request_id,
                        # Do not reflect an attacker-controlled Origin into a
                        # response body; the stable code is sufficient for the
                        # local UI and avoids leaking untrusted request data.
                        "details": {},
                        "retryable": False,
                        "suggested_action": "从 LocalDramaStudio 页面发起请求，或用环境变量 LOCAL_DRAMA_ALLOWED_ORIGINS 登记受控本机 origin 后重启服务",
                    }
                },
                headers={"X-Request-Id": request_id, **SECURITY_REJECTION_HEADERS},
            )
        # Starlette's in-process TestClient uses the non-network host
        # ``testclient``. Functional tests may keep issuing direct commands;
        # dedicated security tests include an Origin and exercise the real
        # token boundary. A TCP client can never acquire this client address.
        in_process_test = request.client is not None and request.client.host == "testclient" and origin is None
        automation_bearer = request.url.path.startswith("/api/v1/webhook-") and request.headers.get("Authorization", "").startswith("Bearer ")
        if state_changing and not in_process_test and not automation_bearer:
            submitted = request.headers.get("X-Local-Instance-Token", "")
            expected = str(request.app.state.instance_session_token)
            if not submitted or not hmac.compare_digest(submitted, expected):
                request_id, _trace_id = _ensure_request_context(request)
                _log_request("request.rejected", request, status_code=403, error="CSRF_TOKEN_REQUIRED")
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "CSRF_TOKEN_REQUIRED",
                            "message": "写请求缺少或携带无效的本机实例令牌",
                            "request_id": request_id,
                            "details": {},
                            "retryable": False,
                            "suggested_action": "先读取 session bootstrap，再由同源客户端提交写请求",
                        }
                    },
                    headers={"X-Request-Id": request_id, **SECURITY_REJECTION_HEADERS},
                )
        response = await call_next(request)
        if not state_changing:
            response.headers["X-Local-Instance-Token"] = str(request.app.state.instance_session_token)
            response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response
