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
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.filesystem.atomic import replace_path


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
        self.configuration_path = self.sandbox_root / "launch-config.json"
        self.captures_root = self.sandbox_root / "captures"

    def _read_configuration_file(self) -> dict[str, Any]:
        if not self.configuration_path.is_file() or self.configuration_path.is_symlink():
            return {}
        try:
            value = json.loads(self.configuration_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _configuration(self) -> dict[str, Any]:
        saved = self._read_configuration_file()
        attached_endpoint = str(saved.get("attached_endpoint", "")).strip()
        attached = str(saved.get("mode", "")).upper() == "ATTACHED_LOOPBACK" and attached_endpoint.startswith("http://127.0.0.1:")
        python_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", "").strip() or str(saved.get("python_path", "")).strip()
        root_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_ROOT", "").strip() or str(saved.get("root_path", "")).strip()
        port_raw = os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_PORT", "").strip() or str(saved.get("port", 8188)).strip()
        try:
            port = int(port_raw)
        except ValueError:
            port = 0
        python = Path(python_raw).expanduser().resolve() if python_raw else None
        root = Path(root_raw).expanduser().resolve() if root_raw else None
        configured = attached or bool(
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
            "endpoint": attached_endpoint if attached else (f"http://127.0.0.1:{port}" if port > 0 else None),
            "attached": attached,
            "source": "ENVIRONMENT" if os.environ.get("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", "").strip() else "SAVED" if saved else "NONE",
        }

    @staticmethod
    def _valid_candidate(python: Path, root: Path) -> bool:
        return bool(
            python.is_file()
            and not python.is_symlink()
            and (root / "main.py").is_file()
            and not (root / "main.py").is_symlink()
        )

    def _candidate_roots(self) -> list[Path]:
        configured = self._configuration()
        roots: list[Path] = []
        for raw in (
            configured.get("root"),
            os.environ.get("COMFYUI_ROOT"),
            self.settings.workspace_root / "ComfyUI",
            self.settings.workspace_root.parent / "ComfyUI",
            Path("C:/ComfyUI"),
            Path("D:/ComfyUI"),
            Path("E:/ComfyUI"),
            Path("F:/ComfyUI"),
        ):
            if not raw:
                continue
            path = Path(str(raw)).expanduser().resolve()
            if path not in roots:
                roots.append(path)
        return roots

    def discover(self, *, apply: bool = False) -> dict[str, Any]:
        attached_endpoint: str | None = None
        if self.settings.config_path is not None:
            try:
                ComfyClient(str(self.settings.comfy_base_url), timeout_seconds=2.0).system_stats()
                attached_endpoint = str(self.settings.comfy_base_url)
            except (DomainRuleError, OSError):
                attached_endpoint = None
        candidates: list[dict[str, Any]] = []
        for root in self._candidate_roots():
            python_paths = [
                root / "python_embeded" / "python.exe",
                root / ".venv" / "Scripts" / "python.exe",
                root / "venv" / "Scripts" / "python.exe",
                Path(sys.executable).resolve(),
            ]
            for python in python_paths:
                if self._valid_candidate(python, root):
                    item = {"python_path": str(python), "root_path": str(root), "port": 8188}
                    if item not in candidates:
                        candidates.append(item)
        applied = False
        if apply:
            if candidates:
                self.configure(candidates[0]["python_path"], candidates[0]["root_path"], candidates[0]["port"])
                applied = True
            elif attached_endpoint:
                payload = {
                    "schema_version": "localdrama.comfy-lab-launch.v1",
                    "mode": "ATTACHED_LOOPBACK",
                    "attached_endpoint": attached_endpoint,
                    "port": int(attached_endpoint.rsplit(":", 1)[-1]),
                    "updated_at": _now(),
                }
                self.sandbox_root.mkdir(parents=True, exist_ok=True)
                partial = self.configuration_path.with_name(f".partial-{uuid.uuid4().hex}.json")
                partial.write_text(_json(payload) + "\n", encoding="utf-8")
                replace_path(partial, self.configuration_path)
                applied = True
            else:
                raise DomainRuleError(
                    "COMFY_LAB_INSTALLATION_NOT_FOUND",
                    "没有在有限的常见位置发现可启动的 ComfyUI",
                    {"searched_roots": [str(path) for path in self._candidate_roots()]},
                    suggested_action="在高级配置中选择 ComfyUI 根目录和 Python，或把 ComfyUI 放到常见目录",
                )
        return {
            "status": "CONFIGURED" if applied or self._configuration()["configured"] else "FOUND" if candidates else "NOT_FOUND",
            "candidates": candidates,
            "attached_endpoint": attached_endpoint,
            "applied": applied,
            "configuration": self._configuration(),
            "searched_roots": [str(path) for path in self._candidate_roots()],
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def configure(self, python_path: str, root_path: str, port: int) -> dict[str, Any]:
        python = Path(python_path).expanduser().resolve()
        root = Path(root_path).expanduser().resolve()
        if port < 1024 or port > 65535:
            raise DomainRuleError("COMFY_LAB_PORT_INVALID", "Designer 端口必须在 1024 到 65535 之间")
        if not self._valid_candidate(python, root):
            raise DomainRuleError(
                "COMFY_LAB_CONFIGURATION_INVALID",
                "所选路径不是可启动的 ComfyUI 安装",
                {"python_exists": python.is_file(), "main_exists": (root / "main.py").is_file()},
                suggested_action="选择包含 main.py 的 ComfyUI 根目录及其可用 Python",
            )
        payload = {"schema_version": "localdrama.comfy-lab-launch.v1", "python_path": str(python), "root_path": str(root), "port": port, "updated_at": _now()}
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        partial = self.configuration_path.with_name(f".partial-{uuid.uuid4().hex}.json")
        partial.write_text(_json(payload) + "\n", encoding="utf-8")
        replace_path(partial, self.configuration_path)
        return {**self._configuration(), "persisted": True, "runtime_contacted": False, "network_contacted": False}

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
        attached_running = False
        if config.get("attached") and config.get("endpoint"):
            try:
                ComfyClient(str(config["endpoint"]), timeout_seconds=2.0).system_stats()
                attached_running = True
            except (DomainRuleError, OSError):
                attached_running = False
        state = self._read_state()
        pid = int(state.get("pid", 0)) if state and str(state.get("pid", "0")).isdigit() else None
        alive = self._pid_alive(pid)
        return {
            "status": "RUNNING" if alive or attached_running else "STOPPED",
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
            "attached": bool(config.get("attached")),
            "lifecycle_owned": not bool(config.get("attached")),
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
        return ComfyClient(
            str(config["endpoint"]),
            self.settings.comfy_output_root if config.get("attached") else self.sandbox_root / "output",
            allow_private_network=self.settings.allows_private_network,
        )

    def _write_state(self, state: dict[str, Any]) -> None:
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        partial = self.state_path.with_name(f".partial-{uuid.uuid4().hex}.json")
        partial.write_text(_json(state) + "\n", encoding="utf-8")
        replace_path(partial, self.state_path)

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
        if self._configuration().get("attached"):
            return {**self._status(), "runtime_contacted": False, "network_contacted": False}
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
        if self._configuration().get("attached"):
            return {**self._status(), "runtime_contacted": False, "network_contacted": False}
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

    def _capture_path(self, capture_id: str) -> Path:
        try:
            uuid.UUID(str(capture_id))
        except ValueError as error:
            raise DomainRuleError("COMFY_LAB_CAPTURE_ID_INVALID", "Designer capture id 无效") from error
        target = (self.captures_root / f"{capture_id}.json").resolve()
        if not target.is_relative_to(self.captures_root.resolve()):
            raise DomainRuleError("COMFY_LAB_CAPTURE_PATH_INVALID", "Designer capture 路径越界")
        return target

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        target = self._capture_path(capture_id)
        if not target.is_file() or target.is_symlink():
            raise DomainRuleError("COMFY_LAB_CAPTURE_NOT_FOUND", "Designer capture 不存在", {"capture_id": capture_id})
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainRuleError("COMFY_LAB_CAPTURE_INVALID", "Designer capture 文件无效", {"capture_id": capture_id}) from error
        workflow = payload.get("workflow") if isinstance(payload, dict) else None
        declared_hash = payload.get("content_hash") if isinstance(payload, dict) else None
        actual_hash = _hash(workflow) if isinstance(workflow, dict) and workflow else None
        if actual_hash is None or declared_hash != actual_hash:
            raise DomainRuleError(
                "COMFY_LAB_CAPTURE_HASH_MISMATCH",
                "Designer capture 内容与冻结哈希不一致，必须重新捕获并实测",
                {"capture_id": capture_id},
            )
        test_path = target.with_suffix(".test.json")
        test_evidence = None
        if test_path.is_file() and not test_path.is_symlink():
            try:
                test_evidence = json.loads(test_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                test_evidence = None
        return {**payload, "test_evidence": test_evidence, "sandbox_rel_path": target.relative_to(self.settings.work_root.resolve()).as_posix()}

    def list_captures(self) -> list[dict[str, Any]]:
        if not self.captures_root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.captures_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.name.endswith(".test.json") or path.is_symlink():
                continue
            try:
                capture = self.get_capture(path.stem)
            except DomainRuleError:
                continue
            items.append({key: capture.get(key) for key in ("capture_id", "title", "content_hash", "sandbox_rel_path", "test_evidence")})
        return items

    def promotable_capture(self, capture_id: str) -> dict[str, Any]:
        capture = self.get_capture(capture_id)
        evidence = capture.get("test_evidence")
        if not isinstance(evidence, dict) or evidence.get("status") != "PASS" or evidence.get("content_hash") != capture.get("content_hash"):
            raise DomainRuleError("COMFY_LAB_EXECUTION_TEST_REQUIRED", "提升为正式候选前必须对同一 capture 完成 PASS 执行测试", {"capture_id": capture_id})
        return capture

    def test_run(self, workflow: dict[str, Any] | None, *, capture_id: str | None = None, execute: bool, client: ComfyClient | None = None) -> dict[str, Any]:
        capture = self.get_capture(capture_id) if capture_id else None
        if capture is not None:
            workflow = capture.get("workflow")
        if not workflow:
            raise DomainRuleError("COMFY_LAB_WORKFLOW_REQUIRED", "Designer test workflow 不能为空")
        self._reject_absolute_paths(workflow)
        status = self._status()
        plan = {"content_hash": _hash(workflow), "sandbox_root": str(self.sandbox_root), "designer_endpoint": status["endpoint"], "formal_project_write": False, "local_only": True}
        if not execute:
            return {"status": "READY" if status["status"] == "RUNNING" else "BLOCKED", "blockers": [] if status["status"] == "RUNNING" else ["COMFY_LAB_NOT_RUNNING"], "plan": plan, "would_contact_comfyui": False, "network_contacted": False}
        if status["status"] != "RUNNING":
            raise DomainRuleError("COMFY_LAB_NOT_RUNNING", "Designer 未运行，不能执行 test run", {"would_contact_comfyui": False})
        runtime = client or self._client()
        result = runtime.queue_prompt(workflow, client_id=f"local-drama-designer-{uuid.uuid4().hex}")
        prompt_id = str(result["prompt_id"])
        history = runtime.wait_history(prompt_id, timeout_seconds=240.0)
        passed = history.get("status") == "success"
        evidence = {
            "status": "PASS" if passed else "BLOCKED",
            "capture_id": capture_id,
            "content_hash": _hash(workflow),
            "prompt_id": prompt_id,
            "runtime_status": history.get("status"),
            "tested_at": _now(),
            "designer_endpoint": status["endpoint"],
            "formal_project_write": False,
            "network_contacted": False,
        }
        if capture_id:
            test_path = self._capture_path(capture_id).with_suffix(".test.json")
            partial = test_path.with_name(f".partial-{uuid.uuid4().hex}.test.json")
            partial.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            replace_path(partial, test_path)
        return {**evidence, "plan": plan, "would_contact_comfyui": True}
