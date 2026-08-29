"""Host-only, redirect-free HTTPS artifact downloader for V2 import staging."""

from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from pathlib import Path
from typing import IO

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import replace_path
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.model_platform.application.trusted_download_sources import validate_trusted_https_source

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class TrustedDownloadRequest:
    source_url: str
    bundle_reference: str
    relative_path: str
    sha256: str
    size_bytes: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> urllib.request.Request | None:
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not permitted", headers, fp)


class HostTrustedDownloadExecutor:
    """Download one declared artifact into ModelRoot downloads, never a library."""

    def __init__(self, settings: Settings, *, opener: urllib.request.OpenerDirector | None = None) -> None:
        self.settings = settings
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def download(self, request: TrustedDownloadRequest) -> Path:
        if self.settings.model_root is None:
            raise DomainRuleError("MP_MODEL_ROOT_NOT_CONFIGURED", "未配置 ModelRoot，不能下载模型。")
        validate_trusted_https_source(self.settings, request.source_url)
        if not _SHA256.fullmatch(request.sha256) or request.size_bytes <= 0:
            raise DomainRuleError("MP_DOWNLOAD_EXPECTATION_INVALID", "下载任务必须有 SHA-256 和预期字节数。")
        downloads = self.settings.model_root / "downloads"
        destination = controlled_path(downloads, f"{request.bundle_reference}/{request.relative_path}", code="MP_DOWNLOAD_TARGET_INVALID")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = controlled_path(downloads, f"{request.bundle_reference}/{request.relative_path}", code="MP_DOWNLOAD_TARGET_INVALID")
        if destination.exists():
            raise DomainRuleError("MP_DOWNLOAD_TARGET_EXISTS", "下载目标已存在，拒绝覆盖。")
        partial = destination.with_name(f".partial-{destination.name}")
        digest = hashlib.sha256()
        count = 0
        try:
            with self.opener.open(request.source_url, timeout=60) as response, partial.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    count += len(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest().casefold() != request.sha256.casefold() or count != request.size_bytes:
                partial.unlink(missing_ok=True)
                self._remove_empty_parents(destination.parent, downloads)
                raise DomainRuleError("MP_DOWNLOAD_INTEGRITY_FAILED", "下载内容与已声明的哈希或大小不一致。")
            replace_path(partial, destination)
            return destination
        except urllib.error.URLError as error:
            partial.unlink(missing_ok=True)
            self._remove_empty_parents(destination.parent, downloads)
            raise DomainRuleError("MP_DOWNLOAD_TRANSPORT_FAILED", "可信模型来源不可访问。") from error

    def promote_bundle_to_staging(self, bundle_reference: str) -> Path:
        """Atomically hand a finished download to the separately audited importer."""
        if self.settings.model_root is None:
            raise DomainRuleError("MP_MODEL_ROOT_NOT_CONFIGURED", "未配置 ModelRoot，不能准备模型导入。")
        downloads = self.settings.model_root / "downloads"
        staging = self.settings.model_root / "staging"
        source = controlled_path(downloads, bundle_reference, must_exist=True, code="MP_DOWNLOAD_BUNDLE_INVALID")
        destination = controlled_path(staging, bundle_reference, code="MP_DOWNLOAD_BUNDLE_INVALID")
        if not source.is_dir() or destination.exists():
            raise DomainRuleError("MP_DOWNLOAD_BUNDLE_INVALID", "下载包不存在、不是目录或 staging 中已有同名包。")
        os.replace(source, destination)
        return destination

    @staticmethod
    def _remove_empty_parents(path: Path, root: Path) -> None:
        current = path
        while current != root:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent
