"""Strict loopback ComfyUI client with prompt/history/queue/interrupt/collect contracts."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from local_drama.domain.errors import DomainRuleError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", output_root: Path | None = None, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_REQUIRED", "ComfyUI client 只允许 loopback endpoint")
        self.base_url = base_url.rstrip("/")
        self.output_root = output_root.resolve() if output_root else None
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        self._assert_access_allowed(path)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=body, method=method, headers={"Content-Type": "application/json"} if body else {})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise DomainRuleError("COMFY_LOOPBACK_UNAVAILABLE", "ComfyUI loopback 请求失败", {"reason": type(error).__name__, "path": path}) from error
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as error:
            raise DomainRuleError("COMFY_INVALID_RESPONSE", "ComfyUI 返回不是有效 JSON", {"path": path}) from error

    @staticmethod
    def _assert_access_allowed(path: str) -> None:
        if os.environ.get("LOCAL_DRAMA_COMFY_ACCESS", "enabled").casefold() != "enabled":
            raise DomainRuleError(
                "COMFY_ACCESS_DISABLED",
                "当前进程已禁止访问 ComfyUI",
                {"path": path},
            )

    def system_stats(self) -> dict[str, Any]:
        return dict(self._request("GET", "/system_stats"))

    def object_info(self, node_type: str | None = None) -> dict[str, Any]:
        suffix = f"/{node_type}" if node_type else ""
        return dict(self._request("GET", f"/object_info{suffix}"))

    def queue_prompt(self, workflow: dict[str, Any], *, client_id: str | None = None, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
        if not workflow:
            raise DomainRuleError("COMFY_WORKFLOW_REQUIRED", "Comfy workflow 不能为空")
        payload = {"prompt": workflow, "client_id": client_id or str(uuid.uuid4())}
        if extra_data:
            payload["extra_data"] = extra_data
        result = self._request("POST", "/prompt", payload)
        if result.get("error") or not result.get("prompt_id"):
            raise DomainRuleError("COMFY_PROMPT_REJECTED", "ComfyUI 拒绝 workflow", {"error": result.get("error"), "node_errors": result.get("node_errors")})
        return dict(result)

    def queue(self) -> dict[str, Any]:
        return dict(self._request("GET", "/queue"))

    def history(self, prompt_id: str) -> dict[str, Any]:
        if not prompt_id:
            raise DomainRuleError("COMFY_PROMPT_ID_REQUIRED", "Comfy prompt_id 不能为空")
        return dict(self._request("GET", f"/history/{prompt_id}"))

    def wait_history(self, prompt_id: str, timeout_seconds: float = 30.0, poll_seconds: float = 0.5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = self.history(prompt_id)
            item = result.get(prompt_id)
            if item:
                status = item.get("status", {})
                status_str = status.get("status_str")
                if status_str in {"success", "error", "failure"}:
                    return {"prompt_id": prompt_id, "history": item, "status": status_str}
            time.sleep(poll_seconds)
        raise DomainRuleError("COMFY_HISTORY_TIMEOUT", "ComfyUI history 等待超时", {"prompt_id": prompt_id})

    def interrupt(self) -> dict[str, Any]:
        result = self._request("POST", "/interrupt", {})
        return dict(result)

    def collect_outputs(self, history_item: dict[str, Any]) -> list[Path]:
        if self.output_root is None:
            raise DomainRuleError("COMFY_OUTPUT_ROOT_REQUIRED", "收集 Comfy 输出需要显式配置本地 output_root")
        outputs: list[Path] = []
        for node_output in history_item.get("outputs", {}).values():
            for media_group in node_output.values():
                if not isinstance(media_group, list):
                    continue
                for item in media_group:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    relative = Path(str(item["subfolder"])) / str(item["filename"]) if item.get("subfolder") else Path(str(item["filename"]))
                    path = (self.output_root / relative).resolve()
                    if not path.is_relative_to(self.output_root) or not path.is_file() or path.is_symlink():
                        raise DomainRuleError("COMFY_OUTPUT_INVALID", "Comfy 输出不存在、越界或为 symlink", {"filename": str(relative)})
                    outputs.append(path)
        if not outputs:
            raise DomainRuleError("COMFY_OUTPUT_EMPTY", "Comfy history 没有可收集输出")
        return outputs

    def websocket_events(self, prompt_id: str, client_id: str, timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
        self._assert_access_allowed("/ws")
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise DomainRuleError("COMFY_WEBSOCKET_UNAVAILABLE", "本地 Python 环境缺少 websockets") from error
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        url = f"{scheme}://{parsed.netloc}/ws?clientId={client_id}"
        events: list[dict[str, Any]] = []
        try:
            with connect(url, open_timeout=self.timeout_seconds, close_timeout=self.timeout_seconds) as websocket:
                while True:
                    message = websocket.recv(timeout=timeout_seconds)
                    if isinstance(message, bytes):
                        continue
                    item = json.loads(message)
                    if isinstance(item, dict):
                        events.append(item)
                        if item.get("type") == "executing" and item.get("data", {}).get("prompt_id") == prompt_id and item.get("data", {}).get("node") is None:
                            break
        except Exception as error:
            raise DomainRuleError("COMFY_WEBSOCKET_FAILED", "ComfyUI WebSocket 监听失败", {"reason": type(error).__name__}) from error
        return events
