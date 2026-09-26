"""Structured output against a real local grammar engine (measured Ollama failure).

Why this file exists
--------------------
The explainer contracts are Pydantic models, so ``contract_schema_for_model`` returns a
schema full of validation keywords — including ``maxLength: 2000``.  Both local
providers forward that schema to llama.cpp's GBNF converter, and that converter rejects
some of those values outright: with Ollama and qwen3.8:27b, a string/array length bound
of exactly 2000 answers HTTP 400 ``Failed to initialize samplers: failed to parse
grammar``, while 1999 and 2001 are accepted.  The model was therefore never asked, and
every model-driven stage failed with ``LOCAL_LLM_LOOPBACK_UNAVAILABLE``.

These tests pin both halves of the fix:

* the sanitizer removes validation-only keywords recursively while keeping every
  structural one, and it is applied to all seven shipped contracts;
* the request actually sent to the runtime uses the sanitized schema, so a regression in
  either provider branch is caught without needing the model.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from local_drama.application.explainers import contracts_v2
from local_drama.infrastructure.local_llm import LocalLLMClient, grammar_safe_schema

CONTRACTS = (
    "content-extract.v2",
    "preserved-script-annotations.v1",
    "script-draft.v2",
    "storyboard.v2",
    "candidate-review.v1",
    "reference-design.v1",
    "fiction-seed.v1",
)

UNSAFE = (
    "maxLength",
    "minLength",
    "maxItems",
    "minItems",
    "uniqueItems",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: Any, *, timeout: float) -> _FakeResponse:
        del timeout
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(self.response)


def _keywords_present(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in UNSAFE:
                found.add(key)
            found |= _keywords_present(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keywords_present(item)
    return found


@pytest.mark.parametrize("contract", CONTRACTS)
def test_every_shipped_contract_is_grammar_safe_after_projection(contract: str) -> None:
    raw = contracts_v2.contract_schema_for_model(contract)
    assert _keywords_present(raw), f"{contract} 不再包含任何长度/格式约束，本测试需要更新"

    safe = grammar_safe_schema(raw)
    assert not _keywords_present(safe), f"{contract} 投影后仍含不受支持的约束"
    # Structure must survive: the decoder grammar still needs the object shape.
    assert safe["type"] == "object"
    assert sorted(safe["properties"]) == sorted(raw["properties"])
    assert safe["required"] == raw["required"]
    assert safe.get("additionalProperties") == raw.get("additionalProperties")


def test_the_sanitizer_keeps_enum_type_required_and_items() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "rows"],
        "properties": {
            "kind": {"type": "string", "enum": ["A", "B"], "maxLength": 2000},
            "rows": {
                "type": "array",
                "minItems": 1,
                "maxItems": 256,
                "items": {
                    "type": "object",
                    "required": ["reason"],
                    "properties": {"reason": {"type": "string", "maxLength": 2000, "pattern": "^.+$"}},
                },
            },
            "maybe": {"anyOf": [{"type": "object", "properties": {"a": {"type": "string", "format": "date-time"}}}, {"type": "null"}]},
        },
    }
    safe = grammar_safe_schema(schema)
    assert safe["properties"]["kind"] == {"type": "string", "enum": ["A", "B"]}
    assert safe["properties"]["rows"]["items"]["properties"]["reason"] == {"type": "string"}
    assert safe["properties"]["rows"]["items"]["required"] == ["reason"]
    assert safe["properties"]["maybe"]["anyOf"][0]["properties"]["a"] == {"type": "string"}
    assert safe["additionalProperties"] is False


def test_the_sanitizer_does_not_mutate_the_caller_schema() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 2000}}}
    grammar_safe_schema(schema)
    assert schema["properties"]["a"]["maxLength"] == 2000


def test_the_ollama_request_carries_the_grammar_safe_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"message": {"content": "{}"}, "done_reason": "stop"})
    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", transport)
    client = LocalLLMClient(
        base_url="http://127.0.0.1:11434", model="qwen3.8:27b", provider="OLLAMA_LOOPBACK"
    )
    client.chat_json(
        "system",
        "user",
        json_schema=contracts_v2.contract_schema_for_model("content-extract.v2"),
    )
    sent = transport.payloads[0]
    assert sent["think"] is False, "思考模型必须关闭 thinking，否则输出预算会被思维链吃掉"
    assert sent["format"]["type"] == "object"
    assert not _keywords_present(sent["format"])


def test_the_managed_request_carries_the_grammar_safe_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.urlopen", lambda request, timeout: _FakeResponse({})
    )
    client = LocalLLMClient(
        base_url="http://127.0.0.1:8101", model="qwen3.8-27b", provider="LLAMA_CPP_MANAGED"
    )
    sent: dict[str, Any] = {}

    def fake_open(request: Any, *, timeout: float) -> _FakeResponse:
        del timeout
        sent.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("local_drama.infrastructure.local_llm.open_local", fake_open)
    client.chat_json("s", "u", json_schema=contracts_v2.contract_schema_for_model("storyboard.v2"))
    schema = sent["response_format"]["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert not _keywords_present(schema)
    del transport
