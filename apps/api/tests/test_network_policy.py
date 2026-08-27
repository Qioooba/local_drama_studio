import pytest

from local_drama.domain.network_policy import (
    EndpointScope,
    endpoint_scope,
    parse_runtime_endpoint,
    validate_bind_host,
)


@pytest.mark.parametrize("host", ["10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.8.9", "fd00::2"])
def test_lan_mode_accepts_only_explicit_private_runtime_addresses(host: str) -> None:
    assert endpoint_scope(host) is EndpointScope.PRIVATE_NETWORK
    assert parse_runtime_endpoint(f"http://[{host}]:8188" if ":" in host else f"http://{host}:8188", allow_private_network=True)


@pytest.mark.parametrize("host", ["172.15.0.1", "172.32.0.1", "8.8.8.8", "comfy.internal"])
def test_runtime_policy_does_not_guess_private_dns_or_adjacent_ranges(host: str) -> None:
    assert parse_runtime_endpoint(f"http://{host}:8188", allow_private_network=True) is None


def test_local_only_bind_cannot_be_widened_accidentally() -> None:
    with pytest.raises(ValueError):
        validate_bind_host("0.0.0.0", "LOCAL_ONLY")
