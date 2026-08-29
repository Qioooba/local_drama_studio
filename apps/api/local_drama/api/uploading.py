from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import Request

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path, safe_filename


@asynccontextmanager
async def receive_bounded_upload(
    request: Request,
    *,
    work_group: str,
    allowed_suffixes: frozenset[str],
    maximum_bytes: int,
    default_filename: str,
    error_prefix: str,
    type_error_code: str | None = None,
    type_error_message: str = "上传文件格式不受支持",
    too_large_message: str | None = None,
    empty_message: str = "请选择非空文件",
) -> AsyncIterator[tuple[Path, str, int]]:
    """Stream one browser file to a private temporary file with shared guards.

    Client paths are never accepted.  Callers receive only a sanitized basename
    and must promote/copy the temporary file into an application-owned library.
    """
    filename = unquote(request.headers.get("x-file-name", default_filename))
    upload_name = safe_filename(filename, default=default_filename)
    if Path(upload_name).suffix.lower() not in allowed_suffixes:
        raise DomainRuleError(
            type_error_code or f"{error_prefix}_TYPE_INVALID",
            type_error_message,
            {"allowed_suffixes": sorted(allowed_suffixes)},
        )
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > maximum_bytes:
                raise DomainRuleError(
                    f"{error_prefix}_TOO_LARGE",
                    too_large_message or f"上传文件不能超过 {maximum_bytes // (1024 * 1024)} MB",
                )
        except ValueError as error:
            raise DomainRuleError(f"{error_prefix}_LENGTH_INVALID", "上传文件长度无效") from error
    work_root = request.app.state.settings.work_root.resolve()
    temporary_directory = controlled_path(work_root, Path(work_group) / uuid.uuid4().hex)
    temporary_directory.mkdir(parents=True, exist_ok=False)
    temporary = controlled_path(temporary_directory, upload_name)
    try:
        received_bytes = 0
        with temporary.open("xb") as destination:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received_bytes += len(chunk)
                if received_bytes > maximum_bytes:
                    raise DomainRuleError(
                        f"{error_prefix}_TOO_LARGE",
                        too_large_message or f"上传文件不能超过 {maximum_bytes // (1024 * 1024)} MB",
                    )
                destination.write(chunk)
        if received_bytes == 0:
            raise DomainRuleError(f"{error_prefix}_EMPTY", empty_message)
        yield temporary, upload_name, received_bytes
    finally:
        temporary.unlink(missing_ok=True)
        try:
            temporary_directory.rmdir()
        except OSError:
            pass
