"""Strict loopback-only Ollama client; never falls back to a remote provider."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_http import open_local

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LocalLLMClient:
    def __init__(self, base_url: str, model: str | None, timeout_seconds: float = 30.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or (parsed.hostname or "").casefold() not in LOOPBACK_HOSTS:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_REQUIRED", "本地 LLM 只允许 http loopback endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_AMBIGUOUS", "本地 LLM endpoint 不得携带凭据、query 或 fragment")
        if not model or not model.strip():
            raise DomainRuleError("LOCAL_LLM_MODEL_REQUIRED", "本地 LLM 必须显式配置模型名")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, payload: dict[str, Any] | None = None, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=body, method="POST" if body else "GET", headers={"Content-Type": "application/json"} if body else {})
        try:
            with open_local(request, timeout=timeout_seconds or self.timeout_seconds) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise DomainRuleError("LOCAL_LLM_LOOPBACK_UNAVAILABLE", "本地 LLM loopback 请求失败", {"reason": type(error).__name__}) from error
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as error:
            raise DomainRuleError("LOCAL_LLM_INVALID_RESPONSE", "本地 LLM 返回不是有效 JSON") from error
        if not isinstance(value, dict):
            raise DomainRuleError("LOCAL_LLM_INVALID_RESPONSE", "本地 LLM 返回结构无效")
        return value

    def tags(self) -> list[dict[str, Any]]:
        value = self._request("/api/tags")
        models = value.get("models", [])
        return [item for item in models if isinstance(item, dict)]

    def probe(self, *, load_test: bool = False) -> dict[str, Any]:
        try:
            models = self.tags()
        except DomainRuleError as error:
            return {"status": "BLOCKED", "base_url": self.base_url, "model": self.model, "error_code": error.code}
        names = {str(item.get("name")) for item in models}
        model_present = self.model in names
        result: dict[str, Any] = {
            "status": "PASS" if model_present else "BLOCKED",
            "base_url": self.base_url,
            "model": self.model,
            "available_models": sorted(names),
            "model_present": model_present,
            "load_test": load_test,
        }
        if load_test and model_present:
            try:
                response = self._request(
                    "/api/generate",
                    {
                        "model": self.model,
                        "prompt": 'Return exactly JSON: {"ready":true}',
                        "format": "json",
                        "stream": False,
                        "options": {"temperature": 0, "num_predict": 8},
                    },
                )
                result["load_test_response"] = bool(response.get("response"))
                if not result["load_test_response"]:
                    result.update({"status": "BLOCKED", "error_code": "LOCAL_LLM_EMPTY_RESPONSE"})
            except DomainRuleError as error:
                result.update({"status": "BLOCKED", "error_code": error.code})
        return result

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        response = self._request(
            "/api/chat",
            {
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout_seconds=600,
        )
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise DomainRuleError("LOCAL_LLM_EMPTY_RESPONSE", "本地 LLM 没有返回结构化内容")
        result = self._parse_json_content(content)
        if isinstance(result, list):
            return {"scenes": result, "_normalization": {"source_top_level": "array", "content_preserved": True}}
        if not isinstance(result, dict):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM JSON 顶层必须是对象或 scenes 数组")
        return result

    @staticmethod
    def _parse_json_content(content: str) -> Any:
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else cleaned
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                    return value
                except json.JSONDecodeError:
                    continue
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "本地 LLM 返回内容不是可提取的 JSON 对象")
