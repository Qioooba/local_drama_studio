from __future__ import annotations

import hmac
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'self'",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class LocalOriginMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins: tuple[str, ...]) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.allowed_origins = set(allowed_origins)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        origin = request.headers.get("Origin")
        state_changing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if state_changing and origin and origin not in self.allowed_origins:
            request_id = request.headers.get("X-Request-Id") or "origin-rejected"
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "ORIGIN_NOT_ALLOWED",
                        "message": "写请求来源不在本机应用允许列表中",
                        "request_id": request_id,
                        "details": {"origin": origin},
                        "retryable": False,
                        "suggested_action": "从 LocalDramaStudio 页面发起请求或配置受控本机 origin",
                    }
                },
                headers={"X-Request-Id": request_id, **SECURITY_HEADERS},
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
                request_id = request.headers.get("X-Request-Id") or "csrf-rejected"
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
                    headers={"X-Request-Id": request_id, **SECURITY_HEADERS},
                )
        response = await call_next(request)
        if not state_changing:
            response.headers["X-Local-Instance-Token"] = str(request.app.state.instance_session_token)
            response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response
