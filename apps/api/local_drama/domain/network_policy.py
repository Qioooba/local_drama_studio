"""Canonical bind and runtime-endpoint trust policy.

This module is deliberately infrastructure-free so configuration, adapters,
clients, diagnostics, and health checks all evaluate the same boundary.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit


class NetworkMode(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    LAN_SERVICE = "LAN_SERVICE"


class EndpointScope(StrEnum):
    LOOPBACK = "LOOPBACK"
    PRIVATE_NETWORK = "PRIVATE_NETWORK"
    PUBLIC_NETWORK = "PUBLIC_NETWORK"
    INVALID = "INVALID"


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PRIVATE_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def normalize_network_mode(value: str | NetworkMode) -> NetworkMode:
    try:
        return NetworkMode(str(value).strip().upper())
    except ValueError:
        raise ValueError("network_mode must be LOCAL_ONLY or LAN_SERVICE") from None


def validate_bind_host(host: str, mode: str | NetworkMode) -> str:
    """Normalize and validate the interface an API process may bind."""

    network_mode = normalize_network_mode(mode)
    normalized = host.strip()
    if network_mode is NetworkMode.LOCAL_ONLY:
        folded = normalized.casefold()
        if folded not in LOOPBACK_HOSTS:
            raise ValueError("LOCAL_ONLY API host must be a literal loopback address")
        return folded
    if normalized in {"0.0.0.0", "::", "*"}:
        return normalized
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        raise ValueError("LAN_SERVICE API host must be a literal IP address or wildcard bind") from None
    return normalized


def endpoint_scope(hostname: str | None) -> EndpointScope:
    """Classify a hostname without DNS resolution or proxy involvement."""

    folded = (hostname or "").strip().casefold()
    if not folded:
        return EndpointScope.INVALID
    if folded in LOOPBACK_HOSTS:
        return EndpointScope.LOOPBACK
    try:
        address = ipaddress.ip_address(folded)
    except ValueError:
        return EndpointScope.PUBLIC_NETWORK
    if address.is_loopback:
        return EndpointScope.LOOPBACK
    if any(address in network for network in PRIVATE_LAN_NETWORKS):
        return EndpointScope.PRIVATE_NETWORK
    if address.is_unspecified:
        return EndpointScope.INVALID
    return EndpointScope.PUBLIC_NETWORK


def is_allowed_runtime_host(hostname: str | None, *, allow_private_network: bool) -> bool:
    scope = endpoint_scope(hostname)
    return scope is EndpointScope.LOOPBACK or (
        allow_private_network and scope is EndpointScope.PRIVATE_NETWORK
    )


def is_loopback_host(hostname: str | None) -> bool:
    return endpoint_scope(hostname) is EndpointScope.LOOPBACK


def parse_runtime_endpoint(
    base_url: str,
    *,
    allow_private_network: bool,
    schemes: frozenset[str] = frozenset({"http", "https"}),
) -> SplitResult | None:
    """Return a validated local/private endpoint, otherwise ``None``."""

    parsed = urlsplit(base_url)
    if parsed.scheme not in schemes or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if not is_allowed_runtime_host(parsed.hostname, allow_private_network=allow_private_network):
        return None
    return parsed


def endpoint_is_remote(base_url: str) -> bool:
    """Whether an endpoint leaves loopback, used for explicit outbound consent."""

    return endpoint_scope(urlsplit(base_url).hostname) is not EndpointScope.LOOPBACK
