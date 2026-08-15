"""Control-plane for the optional, isolated ComfyUI Designer session.

The Designer is deliberately separate from production ComfyUI.  It may only
write inside ``work/comfy-lab`` and only starts when the operator explicitly
configures a local Python executable and ComfyUI root.  The default install
therefore stays truthful (blocked/not configured) instead of silently
starting a runtime or contacting a provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ComfyLabService:
    """Owns only the Designer process and its disposable sandbox."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sandbox_root = (settings.work_root / "comfy-lab").resolve()
        self.state_path = self.sandbox_root / "session.json"
        self.captures_root = self.sandbox_root / "captures"

    def _configuration(self) -> dict[str, Any]:
        python_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", "").strip()
        root_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_ROOT", "").strip()
        port_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_PORT", "8188").strip()
        try:
            port = int(port_raw)
        except ValueError:
            port = 0
        python = Path(python_raw).expanduser().resolve() if python_raw else None
        root = Path(root_raw).expanduser().resolve() if root_raw else None
        configured = bool(
            python
            and root
            and port > 0
            and python.is_file()
            and not python.is_symlink()
            and (root / "main.py").is_file()
            and not (root / "main.py").is_symlink()
        )
        return {
            "configured": configured,
            "python": str(python) if python else None,
            "root": str(root) if root else None,
            "port": port,
            "endpoint": f"http://127.0.0.1:{port}" if port > 0 else None,
        }

    def _read_state(self) -> dict[str, Any] | None:
        if not self.state_path.is_file():
            return None
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid or pid < 1:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError, ValueError):
            return False
        return True

    def _status(self) -> dict[str, Any]:
        config = self._configuration()
        state = self._read_state()
        pid = int(state.get("pid", 0)) if state and str(state.get("pid", "0")).isdigit() else None
        alive = self._pid_alive(pid)
        return {
            "status": "RUNNING" if alive else "STOPPED",
            "pid": pid if alive else None,
            "session_id": state.get("session_id") if state else None,
            "started_at": state.get("started_at") if state and alive else None,
            "endpoint": config["endpoint"],
            "launch_configured": config["configured"],
            "sandbox_root": str(self.sandbox_root),
            "formal_project_write": False,
            "local_only": True,
            "network_contacted": False,
            "stale_state": bool(state and not alive),
        }

    def status(self) -> dict[str, Any]:
        return self._status()

    def session(self) -> dict[str, Any]:
        status = self._status()
        return {
            "session": status,
            "designer": {
                "role": "WORKFLOW_DESIGNER",
                "production_isolation": True,
                "capture_target": "COMFY_LAB_SANDBOX_ONLY",
                "formal_project_write": False,
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def _client(self) -> ComfyClient:
        config = self._configuration()
        if not config["configured"] or not config["endpoint"]:
            raise DomainRuleError("COMFY_LAB_LAUNCH_NOT_CONFIGURED", "ComfyUI Designer 未配置本机 endpoint")
        return ComfyClient(str(config["endpoint"]), self.sandbox_root / "output")

    def _write_state(self, state: dict[str, Any]) -> None:
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        partial = self.state_path.with_name(f".partial-{uuid.uuid4().hex}.json")
        partial.write_text(_json(state) + "\n", encoding="utf-8")
        os.replace(partial, self.state_path)

    def start(self) -> dict[str, Any]:
        current = self._status()
        if current["status"] == "RUNNING":
            return {**current, "idempotent_replay": True, "runtime_contacted": False, "network_contacted": False}
        config = self._configuration()
        if not config["configured"]:
            raise DomainRuleError(
                "COMFY_LAB_LAUNCH_NOT_CONFIGURED",
                "ComfyUI Designer 未配置本机 Python 与安装根目录",
                {"required_env": ["LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", "LOCAL_DRAMA_COMFY_DESIGNER_ROOT"], "formal_project_write": False},
                suggested_action="配置用户自己的 ComfyUI Designer 路径后再启动",
            )
        output_root = self.sandbox_root / "output"
        input_root = self.sandbox_root / "input"
        temp_root = self.sandbox_root / "temp"
        user_root = self.sandbox_root / "user"
        for path in (output_root, input_root, temp_root, user_root, self.captures_root):
            path.mkdir(parents=True, exist_ok=True)
        log_path = self.sandbox_root / "designer.log"
        args = [
            str(config["python"]),
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            str(config["port"]),
            "--output-directory",
            str(output_root),
            "--input-directory",
            str(input_root),
            "--temp-directory",
            str(temp_root),
            "--user-directory",
            str(user_root),
            "--disable-api-nodes",
        ]
        environment = os.environ.copy()
        environment["LOCAL_DRAMA_COMFY_DESIGNER"] = "1"
        environment["LOCAL_DRAMA_COMFY_NETWORK_MODE"] = "offline"
        with log_path.open("ab") as log:
            try:
                process = subprocess.Popen(args, cwd=str(config["root"]), env=environment, stdout=log, stderr=subprocess.STDOUT, shell=False)
            except OSError as error:
                raise DomainRuleError("COMFY_LAB_START_FAILED", "ComfyUI Designer 启动失败", {"reason": type(error).__name__}) from error
        session_id = str(uuid.uuid4())
        self._write_state({"session_id": session_id, "pid": process.pid, "started_at": _now(), "command": args, "sandbox_root": str(self.sandbox_root)})
        return {**self._status(), "session_id": session_id, "status": "STARTING", "runtime_contacted": False, "network_contacted": False}

    def stop(self) -> dict[str, Any]:
        state = self._read_state()
        pid = int(state.get("pid", 0)) if state and str(state.get("pid", "0")).isdigit() else None
        if pid and self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as error:
                raise DomainRuleError("COMFY_LAB_STOP_FAILED", "ComfyUI Designer 停止失败", {"reason": type(error).__name__}) from error
        if self.state_path.exists():
            self.state_path.unlink(missing_ok=True)
        return {**self._status(), "status": "STOPPED", "runtime_contacted": False, "network_contacted": False}

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    @staticmethod
    def _reject_absolute_paths(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                ComfyLabService._reject_absolute_paths(item)
        elif isinstance(value, list):
            for item in value:
                ComfyLabService._reject_absolute_paths(item)
        elif isinstance(value, str):
            path = Path(value)
            if path.is_absolute() or value.startswith("\\\\") or ":\\" in value:
                raise DomainRuleError("COMFY_LAB_ABSOLUTE_PATH", "Designer capture 不得携带宿主绝对路径")

    def capture(self, workflow: dict[str, Any], title: str) -> dict[str, Any]:
        if not workflow:
            raise DomainRuleError("COMFY_LAB_WORKFLOW_REQUIRED", "Designer capture workflow 不能为空")
        self._reject_absolute_paths(workflow)
        capture_id = str(uuid.uuid4())
        content_hash = _hash(workflow)
        self.captures_root.mkdir(parents=True, exist_ok=True)
        target = (self.captures_root / f"{capture_id}.json").resolve()
        if not target.is_relative_to(self.captures_root.resolve()):
            raise DomainRuleError("COMFY_LAB_CAPTURE_PATH_INVALID", "Designer capture 路径越界")
        payload = {"schema_version": "localdrama.comfy-lab-capture.v1", "capture_id": capture_id, "title": title.strip() or "Designer capture", "content_hash": content_hash, "workflow": workflow, "formal_project_write": False, "local_only": True}
        target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return {"status": "CAPTURED", "capture_id": capture_id, "content_hash": content_hash, "sandbox_rel_path": target.relative_to(self.settings.work_root.resolve()).as_posix(), "formal_project_write": False, "runtime_contacted": False, "network_contacted": False}

    def test_run(self, workflow: dict[str, Any], *, execute: bool, client: ComfyClient | None = None) -> dict[str, Any]:
        if not workflow:
            raise DomainRuleError("COMFY_LAB_WORKFLOW_REQUIRED", "Designer test workflow 不能为空")
        self._reject_absolute_paths(workflow)
        status = self._status()
        plan = {"content_hash": _hash(workflow), "sandbox_root": str(self.sandbox_root), "designer_endpoint": status["endpoint"], "formal_project_write": False, "local_only": True}
        if not execute:
            return {"status": "READY" if status["status"] == "RUNNING" else "BLOCKED", "blockers": [] if status["status"] == "RUNNING" else ["COMFY_LAB_NOT_RUNNING"], "plan": plan, "would_contact_comfyui": False, "network_contacted": False}
        if status["status"] != "RUNNING":
            raise DomainRuleError("COMFY_LAB_NOT_RUNNING", "Designer 未运行，不能执行 test run", {"would_contact_comfyui": False})
        result = (client or self._client()).queue_prompt(workflow, client_id=f"local-drama-designer-{uuid.uuid4().hex}")
        return {"status": "QUEUED", "prompt_id": str(result["prompt_id"]), "plan": plan, "would_contact_comfyui": True, "network_contacted": False}
