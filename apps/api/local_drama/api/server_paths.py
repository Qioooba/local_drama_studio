"""HTTP boundary rules for paths that refer to the server filesystem."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import client_is_server_loopback, path_is_within_roots


def require_server_loopback(request: Request, *, action: str) -> None:
    """Reject path-based desktop operations initiated by a remote browser."""
    settings = request.app.state.settings
    peer = request.client.host if request.client else None
    if settings.is_lan_service and not client_is_server_loopback(peer):
        raise DomainRuleError(
            "SERVER_PATH_REMOTE_CLIENT",
            f"远程浏览器不能{action}服务器任意路径",
            suggested_action="请使用浏览器上传接口，或从管理员配置的服务端资源库选择",
        )


def require_configured_model_file(request: Request, value: str | Path) -> Path:
    """Resolve a model reference and enforce the LAN server allowlist."""
    candidate = Path(value).expanduser().resolve()
    settings = request.app.state.settings
    if settings.is_lan_service:
        roots = tuple(settings.model_library_roots)
        if not roots:
            raise DomainRuleError(
                "MODEL_LIBRARY_ROOTS_NOT_CONFIGURED",
                "服务器尚未配置可登记的模型库",
                suggested_action="设置 LOCAL_DRAMA_MODEL_LIBRARY_ROOTS 后重启服务",
            )
        if not path_is_within_roots(candidate, roots, require_file=True):
            raise DomainRuleError("MODEL_LIBRARY_FILE_NOT_ALLOWED", "模型文件必须位于管理员配置的服务端模型库内")
    return candidate
