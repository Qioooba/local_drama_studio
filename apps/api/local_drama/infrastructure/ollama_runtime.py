"""Lifecycle-only Ollama adapter used by the GPU runtime coordinator."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import parse_runtime_endpoint
from local_drama.infrastructure.local_http import open_local


class OllamaRuntimeClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_AMBIGUOUS", "Ollama Runtime endpoint 不能携带凭据或 query")
        if parse_runtime_endpoint(
            base_url,
            allow_private_network=False,
            schemes=frozenset({"http"}),
        ) is None:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_REQUIRED", "显存协调只管理本机 loopback Ollama Runtime")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with open_local(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise DomainRuleError(
                "OLLAMA_RUNTIME_UNAVAILABLE",
                "本机 Ollama Runtime 无法完成显存生命周期操作",
                {"reason": type(error).__name__, "path": path},
            ) from error
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise DomainRuleError("OLLAMA_RUNTIME_INVALID_RESPONSE", "Ollama Runtime 返回无效 JSON") from error
        return value if isinstance(value, dict) else {}

    def running_models(self) -> list[str]:
        value = self._request("GET", "/api/ps")
        return [
            str(item.get("name") or item.get("model") or "").strip()
            for item in value.get("models", [])
            if isinstance(item, dict) and str(item.get("name") or item.get("model") or "").strip()
        ]

    def unload_all(self) -> list[str]:
        models = self.running_models()
        for model in models:
            self._request(
                "POST",
                "/api/generate",
                {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            )
        return models

    def wait_until_unloaded(self, *, timeout_seconds: float = 30.0, poll_seconds: float = 0.25) -> None:
        deadline = time.monotonic() + timeout_seconds
        while self.running_models():
            if time.monotonic() >= deadline:
                raise DomainRuleError(
                    "OLLAMA_MODELS_NOT_UNLOADED",
                    "Ollama 已收到卸载命令，但模型仍驻留显存",
                )
            time.sleep(poll_seconds)
