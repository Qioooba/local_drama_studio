from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_llm import LocalLLMClient


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeTransport:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, request: Any, *, timeout: float) -> _FakeResponse:
        del timeout
        method = request.get_method()
        path = request.full_url
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append((method, path, payload))
        for prefix, response in self.responses.items():
            if path.endswith(prefix):
                return _FakeResponse(response)
        raise AssertionError(f"unexpected request path: {path}")


def _client(**overrides: Any) -> LocalLLMClient:
    params: dict[str, Any] = {
        "base_url": "http://127.0.0.1:8101",
        "model": "qwen3.8-27b",
        "provider": "LLAMA_CPP_MANAGED",
    }
    params.update(overrides)
    return LocalLLMClient(**params)


def test_managed_provider_requires_loopback_endpoint() -> None:
    with pytest.raises(DomainRuleError) as caught:
        LocalLLMClient("https://api.deepseek.com", "m", provider="LLAMA_CPP_MANAGED")
    assert caught.value.code == "LOCAL_ONLY_ENDPOINT_REQUIRED"


def test_managed_provider_rejects_api_key() -> None:
    with pytest.raises(DomainRuleError) as caught:
        _client(api_key="sk-secret")
    assert caught.value.code == "LLAMA_CPP_API_KEY_FORBIDDEN"


def test_managed_client_initialization() -> None:
    client = _client()
    assert client.provider == "LLAMA_CPP_MANAGED"
    assert client.base_url == "http://127.0.0.1:8101"
    assert client.model == "qwen3.8-27b"
    assert client.api_key is None
    assert client._endpoint_url("/v1/chat/completions") == "http://127.0.0.1:8101/v1/chat/completions"


def test_chat_json_sends_schema_and_disables_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(
        {"/v1/chat/completions": {"choices": [{"message": {"content": "{\"scenes\": [], \"confidence\": {}}"}}]}}
    )
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    client = _client()
    result = client.chat_json(
        "system prompt",
        "user prompt",
        json_schema={"type": "object", "properties": {"scenes": {"type": "array"}}},
        inference_options={"num_ctx": 8192},
    )
    assert result["scenes"] == []
    method, path, payload = transport.requests[-1]
    assert method == "POST"
    assert path.endswith("/v1/chat/completions")
    assert payload is not None
    assert payload["model"] == "qwen3.8-27b"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"]["properties"]["scenes"]["type"] == "array"
    # Mirrors the Ollama "think": false contract for structured calls.
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    # The context window is fixed at launch; per-call num_ctx is not forwarded.
    assert "num_ctx" not in json.dumps(payload)


def test_chat_json_without_schema_requests_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"/v1/chat/completions": {"choices": [{"message": {"content": "{\"ok\": true}"}}]}})
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    result = _client().chat_json("system", "user")
    assert result == {"ok": True}
    _method, _path, payload = transport.requests[-1]
    assert payload is not None
    assert payload["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("provider", ["LLAMA_CPP_MANAGED", "OLLAMA_LOOPBACK"])
def test_truncated_output_is_not_recovered_as_a_nested_json_fragment(monkeypatch, provider) -> None:
    content = '{"scenes": [{"scene_no": 1, "shots": []}], "confidence":'
    endpoint = "/api/chat" if provider == "OLLAMA_LOOPBACK" else "/v1/chat/completions"
    payload = ({"message": {"content": content}, "done_reason": "length"}
               if provider == "OLLAMA_LOOPBACK" else
               {"choices": [{"message": {"content": content}, "finish_reason": "length"}]})
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", _FakeTransport({endpoint: payload}))
    with pytest.raises(DomainRuleError) as caught:
        _client(provider=provider).chat_json("system", "user", inference_options={"max_tokens": 2048})
    assert caught.value.code == "LOCAL_LLM_OUTPUT_TRUNCATED"
    assert caught.value.details["max_tokens"] == 2048


def test_chat_json_enable_thinking_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"/v1/chat/completions": {"choices": [{"message": {"content": "{\"ok\": true}"}}]}})
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    _client().chat_json("system", "user", inference_options={"enable_thinking": True})
    _method, _path, payload = transport.requests[-1]
    assert payload is not None
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}


def test_tags_reads_managed_model_list(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"/v1/models": {"data": [{"id": "qwen3.8-27b"}]}})
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    tags = _client().tags()
    assert tags == [{"name": "qwen3.8-27b", "id": "qwen3.8-27b"}]


def test_probe_reports_four_levels_when_server_serves_model(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(
        {
            "/v1/models": {"data": [{"id": "qwen3.8-27b"}]},
            "/v1/chat/completions": {"choices": [{"message": {"content": "{\"ready\": true}"}}]},
        }
    )
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    probe = _client().probe(load_test=True)
    assert probe["status"] == "PASS"
    assert probe["probe_level_passed"] == 4
    assert probe["model_present"] is True
    assert probe["provider"] == "LLAMA_CPP_MANAGED"


def test_probe_blocked_when_managed_server_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(request: Any, *, timeout: float) -> Any:
        del request, timeout
        raise OSError("connection refused")

    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", unreachable)
    probe = _client().probe(load_test=True)
    assert probe["status"] == "BLOCKED"
    assert probe["probe_level_passed"] == 0
    assert probe["error_code"] == "LOCAL_LLM_LOOPBACK_UNAVAILABLE"


# -- Settings derivation ----------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    tmp = Path(overrides.pop("tmp_path", Path("")))
    values: dict[str, Any] = {
        "data_root": tmp / "data",
        "projects_root": tmp / "projects",
        "work_root": tmp / "work",
        "cache_root": tmp / "cache",
        "logs_root": tmp / "logs",
        "backups_root": tmp / "backups",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_derives_managed_endpoint_and_alias(workspace: Settings) -> None:
    model_path = workspace.data_root / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"gguf")
    settings = _settings(
        tmp_path=workspace.data_root.parent,
        llm_provider="LLAMA_CPP_MANAGED",
        llm_base_url="http://127.0.0.1:11434",
        llm_api_key="sk-leak",
        llama_server_port=8222,
        llama_model_path=model_path,
    )
    # Single source of truth: the endpoint comes from the launcher settings.
    assert settings.llm_base_url == "http://127.0.0.1:8222"
    assert settings.llm_api_key is None
    assert settings.llm_model == "Qwen3.8-27B-UD-Q4_K_XL"


def test_settings_from_env_parses_llama_server_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_OLLAMA_BASE_URL", "http://127.0.0.1:12434")
    monkeypatch.setenv("LOCAL_DRAMA_LLAMA_SERVER_BIN", "C:/llama/llama-server.exe")
    monkeypatch.setenv("LOCAL_DRAMA_LLAMA_SERVER_PORT", "8233")
    monkeypatch.setenv("LOCAL_DRAMA_LLAMA_MTP_ENABLED", "true")
    monkeypatch.setenv("LOCAL_DRAMA_LLAMA_MTP_DRAFT_TOKENS", "2")
    monkeypatch.setenv("LOCAL_DRAMA_LLAMA_SERVER_ARGS", "--no-webui")
    settings = Settings.from_env()
    assert settings.ollama_base_url == "http://127.0.0.1:12434"
    assert settings.llama_server_port == 8233
    assert settings.llama_mtp_enabled is True
    assert settings.llama_mtp_draft_tokens == 2
    assert settings.llama_server_args == ("--no-webui",)
