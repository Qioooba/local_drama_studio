from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.adapters import AdapterContractRegistry, validate_adapter_target
from local_drama.main import create_app


def test_registry_is_static_and_declares_all_local_adapters(workspace) -> None:
    calls: list[object] = []
    original = socket.socket.connect

    def fail_connect(_sock: socket.socket, address: object) -> None:
        calls.append(address)
        raise AssertionError("adapter contract inspection must not open a socket")

    socket.socket.connect = fail_connect  # type: ignore[assignment]
    try:
        registry = AdapterContractRegistry(workspace).inspect()
    finally:
        socket.socket.connect = original  # type: ignore[assignment]
    assert calls == []
    assert registry["remote_transport_allowed"] is False
    assert {item["kind"] for item in registry["contracts"]} == {"COMFY", "OPENAI_COMPATIBLE_LLM", "CLI", "FFMPEG"}
    assert all(item["network_contacted"] is False for item in registry["contracts"])


@pytest.mark.parametrize(
    ("transport", "base_url", "executable_ref", "code"),
    [
        ("REMOTE_HTTP_SERVICE", "https://api.example.com", None, "REMOTE_PROVIDER_DISABLED_IN_LOCAL_RELEASE"),
        ("LOOPBACK_HTTP", "https://api.example.com", None, "LOOPBACK_ONLY"),
        ("OPENAI_COMPATIBLE_LOOPBACK", "http://10.0.0.1:11434", None, "LOOPBACK_ONLY"),
        ("LOCAL_CLI", None, "https://download.example.com/tool", "LOCAL_EXECUTABLE_REQUIRED"),
    ],
)
def test_adapter_target_rejects_remote_or_ambiguous_targets(transport, base_url, executable_ref, code) -> None:
    with pytest.raises(DomainRuleError) as raised:
        validate_adapter_target(transport, base_url=base_url, executable_ref=executable_ref)
    assert raised.value.code == code


def test_adapter_contract_route_is_read_only(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/adapters/contracts")
    assert response.status_code == 200
    payload = response.json()["registry"]
    assert payload["mode"] == "LOCAL_ONLY"
    assert payload["remote_transport_allowed"] is False
    assert payload["mutated"] is False
