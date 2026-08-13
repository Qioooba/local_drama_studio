from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    suggested_action: str | None = None


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        suggested_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable
        self.suggested_action = suggested_action


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):
        raise exc
    request_id = getattr(request.state, "request_id", None)
    body = ErrorBody(
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details,
        retryable=exc.retryable,
        suggested_action=exc.suggested_action,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": body.model_dump()},
        headers={"X-Request-Id": request_id or ""},
    )
