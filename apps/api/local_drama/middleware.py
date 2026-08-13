from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


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
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin not in self.allowed_origins:
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
                headers={"X-Request-Id": request_id},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'self'"
        return response
