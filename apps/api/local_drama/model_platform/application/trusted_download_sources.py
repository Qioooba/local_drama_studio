"""Machine-owned policy for future HTTPS model downloads."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


@dataclass(frozen=True, slots=True)
class TrustedDownloadSource:
    url: str
    host: str


def validate_trusted_https_source(settings: Settings, url: str) -> TrustedDownloadSource:
    """Accept only a configured exact HTTPS host; no implicit Internet access."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError as error:
        raise DomainRuleError("MP_DOWNLOAD_SOURCE_INVALID", "模型下载来源不是合法 HTTPS URL。") from error
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password:
        raise DomainRuleError("MP_DOWNLOAD_SOURCE_INVALID", "模型下载来源必须是无凭据的 HTTPS URL。")
    if parsed.port not in {None, 443} or parsed.query or parsed.fragment:
        raise DomainRuleError("MP_DOWNLOAD_SOURCE_INVALID", "模型下载来源不能包含自定义端口、查询参数或片段。")
    if host not in set(settings.model_download_source_hosts):
        raise DomainRuleError(
            "MP_DOWNLOAD_SOURCE_UNTRUSTED",
            "该下载主机未列入 Windows 服务配置的可信模型来源；在线下载保持关闭。",
        )
    return TrustedDownloadSource(url=url.strip(), host=host)
