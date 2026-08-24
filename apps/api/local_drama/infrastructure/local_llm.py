"""Strict local and OpenAI-compatible LLM client.

Defaults to loopback-only Ollama client; supports explicitly configured
OpenAI-compatible remote providers (e.g. DeepSeek) with Bearer token authentication,
4-level connection probing, and multimodal vision inputs.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_http import open_local

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LocalLLMClient:
    def __init__(
        self,
        base_url: str,
        model: str | None,
        timeout_seconds: float = 30.0,
        provider: str = "OLLAMA_LOOPBACK",
        api_key: str | None = None,
    ) -> None:
        normalized_provider = (provider or "OLLAMA_LOOPBACK").strip().upper()
        if normalized_provider not in {"OLLAMA_LOOPBACK", "OPENAI_COMPAT"}:
            raise DomainRuleError("LLM_PROVIDER_UNSUPPORTED", f"不支持的 LLM Provider: {provider}")

        parsed = urlparse(base_url)
        if normalized_provider == "OLLAMA_LOOPBACK":
            if parsed.scheme != "http" or (parsed.hostname or "").casefold() not in LOOPBACK_HOSTS:
                raise DomainRuleError("LOCAL_ONLY_ENDPOINT_REQUIRED", "本地 LLM 只允许 http loopback endpoint")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise DomainRuleError("LOCAL_ONLY_ENDPOINT_AMBIGUOUS", "本地 LLM endpoint 不得携带凭据、query 或 fragment")
        else:
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise DomainRuleError("LLM_ENDPOINT_INVALID", "OpenAI 兼容 LLM 只允许 http 或 https endpoint")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise DomainRuleError("LOCAL_ONLY_ENDPOINT_AMBIGUOUS", "LLM endpoint 不得在 URL 中携带凭据、query 或 fragment")

        if not model or not model.strip():
            raise DomainRuleError("LOCAL_LLM_MODEL_REQUIRED", "LLM 必须显式配置模型名")

        self.provider = normalized_provider
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key.strip() if api_key and api_key.strip() else None

    def _endpoint_url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1") and path.startswith("/v1/"):
            return f"{base[:-3]}{path}"
        return f"{base}{path}"

    def _request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = self._endpoint_url(path)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers: dict[str, str] = {"Content-Type": "application/json"} if body else {}
        if self.provider == "OPENAI_COMPAT" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = Request(url, data=body, method="POST" if body else "GET", headers=headers)
        timeout = timeout_seconds or self.timeout_seconds
        try:
            if self.provider == "OLLAMA_LOOPBACK":
                with open_local(request, timeout=timeout) as response:
                    raw = response.read()
            else:
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise DomainRuleError("LLM_AUTH_FAILED", "LLM 认证失败，请检查 API Key", {"status_code": error.code}) from error
            error_code = "LOCAL_LLM_LOOPBACK_UNAVAILABLE" if self.provider == "OLLAMA_LOOPBACK" else "LLM_PROVIDER_UNAVAILABLE"
            raise DomainRuleError(error_code, "LLM 请求失败", {"status_code": error.code, "reason": type(error).__name__}) from error
        except (URLError, TimeoutError, OSError) as error:
            error_code = "LOCAL_LLM_LOOPBACK_UNAVAILABLE" if self.provider == "OLLAMA_LOOPBACK" else "LLM_PROVIDER_UNAVAILABLE"
            raise DomainRuleError(error_code, "LLM 网络连接失败", {"reason": type(error).__name__}) from error

        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as error:
            raise DomainRuleError("LOCAL_LLM_INVALID_RESPONSE", "LLM 返回不是有效 JSON") from error
        if not isinstance(value, dict):
            raise DomainRuleError("LOCAL_LLM_INVALID_RESPONSE", "LLM 返回结构无效")
        return value

    def tags(self) -> list[dict[str, Any]]:
        if self.provider == "OLLAMA_LOOPBACK":
            value = self._request("/api/tags")
            models = value.get("models", [])
            return [item for item in models if isinstance(item, dict)]
        else:
            try:
                value = self._request("/v1/models")
                data = value.get("data", [])
                if isinstance(data, list):
                    return [{"name": item.get("id") or item.get("name"), "id": item.get("id")} for item in data if isinstance(item, dict)]
                return []
            except DomainRuleError as error:
                if error.code == "LLM_AUTH_FAILED":
                    raise
                return []

    def probe(self, *, load_test: bool = False) -> dict[str, Any]:
        """Test connection in 4 distinct levels:

        Level 1: Network reachable
        Level 2: Auth successful
        Level 3: Model available
        Level 4: Minimal sample inference successful
        """
        probe_levels: dict[str, dict[str, Any]] = {
            "level_1_network": {"passed": False, "detail": "未测试"},
            "level_2_auth": {"passed": False, "detail": "未测试"},
            "level_3_model": {"passed": False, "detail": "未测试"},
            "level_4_inference": {"passed": False, "detail": "未测试"},
        }
        available_models: list[str] = []
        model_present = False
        error_code: str | None = None

        if self.provider == "OLLAMA_LOOPBACK":
            try:
                models = self.tags()
                probe_levels["level_1_network"] = {"passed": True, "detail": "本地 Ollama 进程可达"}
                probe_levels["level_2_auth"] = {"passed": True, "detail": "本地免密鉴权通过"}
                names = {str(item.get("name")) for item in models}
                available_models = sorted(names)
                model_present = self.model in names or any(self.model == n.split(":")[0] for n in names)
                probe_levels["level_3_model"] = {
                    "passed": model_present,
                    "detail": "本地模型存在" if model_present else f"未在 Ollama 发现模型 {self.model}",
                }
            except DomainRuleError as error:
                error_code = error.code
                probe_levels["level_1_network"] = {"passed": False, "detail": f"Ollama 无法连接: {error.message}"}

            if probe_levels["level_1_network"]["passed"] and model_present and load_test:
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
                    load_test_passed = bool(response.get("response"))
                    probe_levels["level_4_inference"] = {
                        "passed": load_test_passed,
                        "detail": "样例推理执行成功" if load_test_passed else "模型未返回有效生成内容",
                    }
                    if not load_test_passed:
                        error_code = "LOCAL_LLM_EMPTY_RESPONSE"
                except DomainRuleError as error:
                    error_code = error.code
                    probe_levels["level_4_inference"] = {"passed": False, "detail": f"样例推理失败: {error.message}"}
            elif probe_levels["level_1_network"]["passed"] and model_present and not load_test:
                probe_levels["level_4_inference"] = {"passed": True, "detail": "候选同步跳过负载推理"}
        else:
            # OPENAI_COMPAT
            try:
                models = self.tags()
                probe_levels["level_1_network"] = {"passed": True, "detail": "Endpoint 网络连接成功"}
                probe_levels["level_2_auth"] = {"passed": True, "detail": "API Key 鉴权有效"}
                names = {str(item.get("name") or item.get("id")) for item in models if (item.get("name") or item.get("id"))}
                available_models = sorted(names)
                model_present = (self.model in names) if names else True
                probe_levels["level_3_model"] = {
                    "passed": True,
                    "detail": f"模型 {self.model} 已配置" + ("并在模型列表确认" if names and self.model in names else ""),
                }
            except DomainRuleError as error:
                error_code = error.code
                if error.code == "LLM_AUTH_FAILED":
                    probe_levels["level_1_network"] = {"passed": True, "detail": "Endpoint 网络连接成功"}
                    probe_levels["level_2_auth"] = {"passed": False, "detail": "API Key 认证失败（401/403）"}
                else:
                    probe_levels["level_1_network"] = {"passed": False, "detail": f"网络不可达: {error.message}"}

            if probe_levels["level_2_auth"]["passed"]:
                if load_test or not names:
                    try:
                        resp = self._request(
                            "/v1/chat/completions",
                            {
                                "model": self.model,
                                "messages": [
                                    {"role": "system", "content": "You are a test assistant. Output strictly JSON."},
                                    {"role": "user", "content": 'Return exactly JSON: {"ready":true}'},
                                ],
                                "temperature": 0,
                                "stream": False,
                            },
                        )
                        choices = resp.get("choices", [])
                        valid_resp = bool(choices and isinstance(choices, list) and choices[0].get("message", {}).get("content"))
                        probe_levels["level_4_inference"] = {
                            "passed": valid_resp,
                            "detail": "最小样例推理执行成功" if valid_resp else "模型未返回有效 choices 内容",
                        }
                        if valid_resp:
                            model_present = True
                            probe_levels["level_3_model"] = {"passed": True, "detail": f"模型 {self.model} 推理可用"}
                        else:
                            error_code = "LOCAL_LLM_EMPTY_RESPONSE"
                    except DomainRuleError as error:
                        error_code = error.code
                        probe_levels["level_4_inference"] = {"passed": False, "detail": f"样例推理失败: {error.message}"}
                else:
                    probe_levels["level_4_inference"] = {"passed": True, "detail": "候选同步跳过负载推理"}

        passed_level = 0
        for lvl_name in ("level_1_network", "level_2_auth", "level_3_model", "level_4_inference"):
            if probe_levels[lvl_name]["passed"]:
                passed_level += 1
            else:
                break

        is_pass = passed_level == 4 and (model_present or bool(probe_levels["level_4_inference"]["passed"]))
        result: dict[str, Any] = {
            "status": "PASS" if is_pass else "BLOCKED",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_levels": probe_levels,
            "probe_level_passed": passed_level,
            "available_models": available_models,
            "model_present": model_present,
            "load_test": load_test,
        }
        if error_code and not is_pass:
            result["error_code"] = error_code
        return result

    def chat_json(
        self,
        system: str,
        user: str,
        images: list[str] | None = None,
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.provider == "OLLAMA_LOOPBACK":
            msg_payload: dict[str, Any] = {
                "model": self.model,
                "stream": False,
                # Ollama's JSON mode prevents reasoning-oriented local models
                # from surrounding the requested object with prose.  The
                # response is still validated against the domain contract by
                # the application service; this only makes transport output
                # reliably parseable.
                "format": json_schema or "json",
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
            if images:
                msg_payload["messages"][1]["images"] = images
            response = self._request("/api/chat", msg_payload, timeout_seconds=600)
            message = response.get("message")
            content = message.get("content") if isinstance(message, dict) else None
        else:
            # OPENAI_COMPAT
            user_content: Any = user
            if images:
                formatted_images = []
                for img in images:
                    if not img:
                        continue
                    url = img if (img.startswith("http://") or img.startswith("https://") or img.startswith("data:")) else f"data:image/jpeg;base64,{img}"
                    formatted_images.append({"type": "image_url", "image_url": {"url": url}})
                user_content = [{"type": "text", "text": user}, *formatted_images]

            response = self._request(
                "/v1/chat/completions",
                {
                    "model": self.model,
                    "stream": False,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                },
                timeout_seconds=600,
            )
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices:
                raise DomainRuleError("LOCAL_LLM_EMPTY_RESPONSE", "OpenAI 兼容 LLM 没有返回 choices 内容")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None

        if not isinstance(content, str) or not content.strip():
            raise DomainRuleError("LOCAL_LLM_EMPTY_RESPONSE", "LLM 没有返回结构化内容")
        result = self._parse_json_content(content)
        if isinstance(result, list):
            return {"scenes": result, "_normalization": {"source_top_level": "array", "content_preserved": True}}
        if not isinstance(result, dict):
            raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "LLM JSON 顶层必须是对象或 scenes 数组")
        return result

    @staticmethod
    def _parse_json_content(content: str) -> Any:
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
        fenced_values = [item.strip() for item in re.findall(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)]
        sources = [*fenced_values, cleaned]
        values: list[Any] = []
        decoder = json.JSONDecoder()
        for candidate in sources:
            try:
                values.append(json.loads(candidate))
            except json.JSONDecodeError:
                pass
            # Reasoning models sometimes emit a small JSON example before the
            # actual payload.  Collect every decodable candidate instead of
            # returning the first brace-delimited value.
            for index, character in enumerate(candidate):
                if character not in "{[":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                values.append(value)

        required = {"scenes", "confidence", "questions", "source_passages"}

        def unwrap(value: Any) -> Any:
            if not isinstance(value, dict) or required.issubset(value):
                return value
            for key in ("result", "data", "output", "breakdown", "script_breakdown"):
                nested = value.get(key)
                if isinstance(nested, (dict, list)):
                    return unwrap(nested)
            # A domain object may legitimately contain one list-valued field
            # (for example {"scenes": []}). Only unwrap named transport
            # envelopes; otherwise preserve the object's real shape.
            return value

        normalized = [unwrap(value) for value in values]
        if normalized:
            def score(value: Any) -> int:
                if isinstance(value, dict):
                    return 100 * len(required.intersection(value)) + len(value)
                if isinstance(value, list):
                    return 10 + len(value)
                return 0

            return max(normalized, key=score)
        raise DomainRuleError("LOCAL_LLM_OUTPUT_INVALID", "LLM 返回内容不是可提取的 JSON 对象")
