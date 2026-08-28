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
from urllib.parse import urlparse

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class WorkflowRuntimeService:
    """Versioned Workflow App Contract and supervised production runtime control-plane."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], *, verify_files: bool) -> dict[str, Any]:
        endpoint = str(manifest.get("endpoint") or "").rstrip("/")
        parsed = urlparse(endpoint)
        blockers: list[dict[str, str]] = []
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            blockers.append({"code": "RUNTIME_ENDPOINT_NOT_LOOPBACK", "message": "生产 ComfyUI endpoint 必须为 loopback"})
        python_raw = str(manifest.get("python_path") or "").strip()
        root_raw = str(manifest.get("root_path") or "").strip()
        mode = str(manifest.get("mode") or "MANAGED").upper()
        if mode not in {"MANAGED", "EXTERNAL"}:
            blockers.append({"code": "RUNTIME_MODE_INVALID", "message": "mode 仅支持 MANAGED 或 EXTERNAL"})
        if mode == "MANAGED":
            if not python_raw or not root_raw:
                blockers.append({"code": "RUNTIME_LAUNCH_PATH_REQUIRED", "message": "托管运行时必须声明 python_path 与 root_path"})
            elif verify_files:
                python = Path(python_raw).expanduser().resolve()
                root = Path(root_raw).expanduser().resolve()
                if not python.is_file() or python.is_symlink():
                    blockers.append({"code": "RUNTIME_PYTHON_INVALID", "message": "Python 可执行文件不存在或为符号链接"})
                if not (root / "main.py").is_file() or (root / "main.py").is_symlink():
                    blockers.append({"code": "RUNTIME_ROOT_INVALID", "message": "ComfyUI 根目录缺少 main.py"})
        if not isinstance(manifest.get("custom_nodes", []), list):
            blockers.append({"code": "RUNTIME_CUSTOM_NODES_INVALID", "message": "custom_nodes 必须为版本清单数组"})
        if not isinstance(manifest.get("models", []), list):
            blockers.append({"code": "RUNTIME_MODELS_INVALID", "message": "models 必须为模型指纹数组"})
        normalized = {
            "schema_version": "localdrama.runtime-environment.v1",
            "mode": mode,
            "endpoint": endpoint,
            "python_path": python_raw or None,
            "root_path": root_raw or None,
            "launch_args": list(manifest.get("launch_args") or []),
            "custom_nodes": list(manifest.get("custom_nodes") or []),
            "models": list(manifest.get("models") or []),
            "python_packages": dict(manifest.get("python_packages") or {}),
            "comfy_commit": manifest.get("comfy_commit"),
            "gpu": dict(manifest.get("gpu") or {}),
        }
        return {"status": "BLOCKED" if blockers else "PASS", "blockers": blockers, "manifest": normalized, "fingerprint": _hash(normalized)}

    def create_environment(self, code: str, title: str, manifest: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        validation = self._validate_manifest(manifest, verify_files=False)
        now, environment_id, version_id = _now(), str(uuid.uuid4()), str(uuid.uuid4())
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """INSERT INTO runtime_environments
                    (id,code,title,status,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,'ACTIVE',?,?,?,1,'v1')""",
                    (environment_id, code, title, now, now, actor),
                )
            except Exception as error:
                if "UNIQUE" in str(error).upper():
                    raise DomainRuleError("RUNTIME_ENVIRONMENT_CODE_CONFLICT", "运行环境代码已存在") from error
                raise
            connection.execute(
                """INSERT INTO runtime_environment_versions
                (id,runtime_environment_id,version_no,manifest_json,environment_fingerprint,status,validation_json,published_at,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,?,'DRAFT',?,NULL,?,?,?,1,'v1')""",
                (version_id, environment_id, _json(validation["manifest"]), validation["fingerprint"], _json(validation), now, now, actor),
            )
        return self.get_environment(environment_id)

    def add_environment_version(self, environment_id: str, manifest: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        validation = self._validate_manifest(manifest, verify_files=False)
        now, version_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            environment = connection.execute("SELECT id FROM runtime_environments WHERE id=?", (environment_id,)).fetchone()
            if environment is None:
                raise DomainRuleError("RUNTIME_ENVIRONMENT_NOT_FOUND", "运行环境不存在")
            version_no = int(connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM runtime_environment_versions WHERE runtime_environment_id=?", (environment_id,)).fetchone()[0])
            connection.execute(
                """INSERT INTO runtime_environment_versions
                (id,runtime_environment_id,version_no,manifest_json,environment_fingerprint,status,validation_json,published_at,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,'DRAFT',?,NULL,?,?,?,1,'v1')""",
                (version_id, environment_id, version_no, _json(validation["manifest"]), validation["fingerprint"], _json(validation), now, now, actor),
            )
        return self.get_environment_version(version_id)

    def list_environments(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT e.*,(SELECT COUNT(*) FROM runtime_environment_versions v WHERE v.runtime_environment_id=e.id) version_count
                FROM runtime_environments e WHERE e.status='ACTIVE' ORDER BY e.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_environment(self, environment_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            environment = connection.execute("SELECT * FROM runtime_environments WHERE id=?", (environment_id,)).fetchone()
            versions = connection.execute("SELECT id FROM runtime_environment_versions WHERE runtime_environment_id=? ORDER BY version_no DESC", (environment_id,)).fetchall()
        if environment is None:
            raise DomainRuleError("RUNTIME_ENVIRONMENT_NOT_FOUND", "运行环境不存在")
        return {"environment": dict(environment), "versions": [self.get_environment_version(str(row["id"])) for row in versions]}

    def get_environment_version(self, version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM runtime_environment_versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise DomainRuleError("RUNTIME_ENVIRONMENT_VERSION_NOT_FOUND", "运行环境版本不存在")
        item = dict(row)
        item["manifest"] = json.loads(item.pop("manifest_json"))
        item["validation"] = json.loads(item.pop("validation_json") or "{}")
        return item

    def validate_environment(self, version_id: str, actor: str = "local-user") -> dict[str, Any]:
        version = self.get_environment_version(version_id)
        validation = self._validate_manifest(version["manifest"], verify_files=True)
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE runtime_environment_versions SET validation_json=?,updated_at=?,created_by=?,revision=revision+1 WHERE id=?",
                (_json(validation), now, actor, version_id),
            )
        return validation

    def publish_environment(self, version_id: str, actor: str = "local-user") -> dict[str, Any]:
        validation = self.validate_environment(version_id, actor)
        if validation["status"] != "PASS":
            raise DomainRuleError("RUNTIME_ENVIRONMENT_VALIDATION_FAILED", "运行环境未通过校验", {"blockers": validation["blockers"]})
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE runtime_environment_versions SET status='PUBLISHED',published_at=?,updated_at=?,revision=revision+1 WHERE id=?", (now, now, version_id))
        return self.get_environment_version(version_id)

    @staticmethod
    def _validate_contract(workflow: dict[str, Any], capability: str, contract: dict[str, Any], bindings: dict[str, Any], semantic_phases: list[dict[str, Any]]) -> dict[str, Any]:
        blockers: list[dict[str, str]] = []
        inputs = contract.get("inputs")
        outputs = contract.get("outputs")
        if not capability.strip():
            blockers.append({"code": "WORKFLOW_CAPABILITY_REQUIRED", "message": "工作流能力不能为空"})
        if not isinstance(inputs, dict) or not inputs:
            blockers.append({"code": "WORKFLOW_INPUT_CONTRACT_REQUIRED", "message": "应用契约必须声明语义输入"})
        if not isinstance(outputs, dict) or not outputs:
            blockers.append({"code": "WORKFLOW_OUTPUT_CONTRACT_REQUIRED", "message": "应用契约必须声明输出"})
        for role, binding in bindings.items():
            if not isinstance(binding, dict):
                blockers.append({"code": "WORKFLOW_BINDING_INVALID", "message": f"{role} 的绑定必须是对象"})
                continue
            node_id = str(binding.get("node_id") or "")
            input_name = str(binding.get("input") or "")
            if node_id not in workflow or not input_name:
                blockers.append({"code": "WORKFLOW_BINDING_TARGET_MISSING", "message": f"{role} 未绑定到有效节点输入"})
        for phase in semantic_phases:
            if not isinstance(phase, dict) or not phase.get("name") or not isinstance(phase.get("node_ids"), list):
                blockers.append({"code": "WORKFLOW_PHASE_INVALID", "message": "语义阶段必须声明 name 与 node_ids"})
                continue
            unknown = [str(item) for item in phase["node_ids"] if str(item) not in workflow]
            if unknown:
                blockers.append({"code": "WORKFLOW_PHASE_NODE_MISSING", "message": f"语义阶段引用不存在节点：{','.join(unknown)}"})
        return {"status": "BLOCKED" if blockers else "PASS", "blockers": blockers}

    def create_contract(self, workflow_version_id: str, capability: str, contract: dict[str, Any], bindings: dict[str, Any], semantic_phases: list[dict[str, Any]], actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            workflow_row = connection.execute("SELECT content_json FROM workflow_versions WHERE id=?", (workflow_version_id,)).fetchone()
            version_no = int(connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM workflow_app_contract_versions WHERE workflow_version_id=?", (workflow_version_id,)).fetchone()[0])
        if workflow_row is None:
            raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "WorkflowVersion 不存在")
        workflow = json.loads(str(workflow_row["content_json"] or "{}"))
        validation = self._validate_contract(workflow, capability, contract, bindings, semantic_phases)
        content = {"capability": capability, "contract": contract, "bindings": bindings, "semantic_phases": semantic_phases}
        now, contract_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_app_contract_versions
                (id,workflow_version_id,version_no,capability,contract_json,bindings_json,semantic_phases_json,content_hash,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'v1')""",
                (contract_id, workflow_version_id, version_no, capability, _json(contract), _json(bindings), _json(semantic_phases), _hash(content), "DRAFT" if validation["status"] == "PASS" else "INVALID", now, now, actor),
            )
        return {**self.get_contract(contract_id), "validation": validation}

    def get_contract(self, contract_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM workflow_app_contract_versions WHERE id=?", (contract_id,)).fetchone()
        if row is None:
            raise DomainRuleError("WORKFLOW_APP_CONTRACT_NOT_FOUND", "Workflow App Contract 不存在")
        item = dict(row)
        item["contract"] = json.loads(item.pop("contract_json"))
        item["bindings"] = json.loads(item.pop("bindings_json"))
        item["semantic_phases"] = json.loads(item.pop("semantic_phases_json") or "[]")
        return item

    def publish_contract(self, contract_id: str) -> dict[str, Any]:
        item = self.get_contract(contract_id)
        with self.database.connect() as connection:
            workflow_row = connection.execute("SELECT content_json,status FROM workflow_versions WHERE id=?", (item["workflow_version_id"],)).fetchone()
        workflow = json.loads(str(workflow_row["content_json"] or "{}"))
        validation = self._validate_contract(workflow, item["capability"], item["contract"], item["bindings"], item["semantic_phases"])
        if validation["status"] != "PASS" or str(workflow_row["status"]) != "PUBLISHED":
            raise DomainRuleError("WORKFLOW_APP_CONTRACT_VALIDATION_FAILED", "应用契约或底层工作流未通过发布条件", {"blockers": validation["blockers"], "workflow_status": workflow_row["status"]})
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE workflow_app_contract_versions SET status='PUBLISHED',updated_at=?,revision=revision+1 WHERE id=?", (now, contract_id))
        return self.get_contract(contract_id)

    def bind(self, workflow_version_id: str, contract_version_id: str, runtime_environment_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        contract = self.get_contract(contract_version_id)
        environment = self.get_environment_version(runtime_environment_version_id)
        if contract["workflow_version_id"] != workflow_version_id or contract["status"] != "PUBLISHED" or environment["status"] != "PUBLISHED":
            raise DomainRuleError("WORKFLOW_RUNTIME_BINDING_INVALID", "只能绑定同一工作流的已发布应用契约和已发布运行环境")
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_runtime_bindings
                (workflow_version_id,contract_version_id,runtime_environment_version_id,created_at,created_by,schema_version)
                VALUES (?,?,?,?,?,'v1')
                ON CONFLICT(workflow_version_id) DO UPDATE SET contract_version_id=excluded.contract_version_id,
                runtime_environment_version_id=excluded.runtime_environment_version_id,created_at=excluded.created_at,created_by=excluded.created_by""",
                (workflow_version_id, contract_version_id, runtime_environment_version_id, now, actor),
            )
        return {"workflow_version_id": workflow_version_id, "contract_version_id": contract_version_id, "runtime_environment_version_id": runtime_environment_version_id, "bound_at": now}

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid or pid < 1:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError, ValueError):
            return False
        return True

    def runtime_status(self, version_id: str) -> dict[str, Any]:
        version = self.get_environment_version(version_id)
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM runtime_instances WHERE runtime_environment_version_id=? ORDER BY created_at DESC LIMIT 1", (version_id,)).fetchone()
        instance = dict(row) if row else None
        alive = self._pid_alive(int(instance["process_id"]) if instance and instance.get("process_id") else None)
        reachable, health = False, {}
        try:
            health = ComfyClient(version["manifest"]["endpoint"], allow_private_network=False, timeout_seconds=2).system_stats()
            reachable = True
        except DomainRuleError as error:
            health = {"error_code": error.code}
        observed = "RUNNING" if reachable else "STARTING" if alive else "STOPPED"
        if instance:
            now = _now()
            with self.database.transaction() as connection:
                connection.execute("UPDATE runtime_instances SET state=?,health_json=?,last_heartbeat_at=?,updated_at=?,revision=revision+1 WHERE id=?", (observed, _json(health), now, now, instance["id"]))
            instance.update({"state": observed, "health": health, "last_heartbeat_at": now})
        return {"runtime_environment_version": version, "instance": instance, "observed_state": observed, "reachable": reachable, "health": health}

    def start_runtime(self, version_id: str, instance_kind: str, actor: str = "local-user") -> dict[str, Any]:
        current = self.runtime_status(version_id)
        if current["observed_state"] in {"RUNNING", "STARTING"} and current["instance"]:
            return {**current, "idempotent_replay": True}
        version = current["runtime_environment_version"]
        if version["status"] != "PUBLISHED":
            raise DomainRuleError("RUNTIME_ENVIRONMENT_NOT_PUBLISHED", "只能启动已发布的运行环境版本")
        manifest = version["manifest"]
        kind = instance_kind.upper()
        now, instance_id = _now(), str(uuid.uuid4())
        pid: int | None = None
        if kind == "MANAGED":
            validation = self._validate_manifest(manifest, verify_files=True)
            if validation["status"] != "PASS" or manifest.get("mode") != "MANAGED":
                raise DomainRuleError("RUNTIME_ENVIRONMENT_VALIDATION_FAILED", "托管运行时配置无效", {"blockers": validation["blockers"]})
            parsed = urlparse(str(manifest["endpoint"]))
            port = int(parsed.port or 8188)
            work_root = (self.settings.work_root / "comfy-production").resolve()
            for child in ("input", "output", "temp", "user"):
                (work_root / child).mkdir(parents=True, exist_ok=True)
            args = [
                str(manifest["python_path"]), "main.py", "--listen", "127.0.0.1", "--port", str(port),
                "--input-directory", str(work_root / "input"), "--output-directory", str(work_root / "output"),
                "--temp-directory", str(work_root / "temp"), "--user-directory", str(work_root / "user"),
                *[str(item) for item in manifest.get("launch_args", [])],
            ]
            log_path = self.settings.logs_root / "comfy-production.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment["LOCAL_DRAMA_COMFY_RUNTIME"] = "PRODUCTION"
            with log_path.open("ab") as log:
                try:
                    process = subprocess.Popen(args, cwd=str(manifest["root_path"]), env=environment, stdout=log, stderr=subprocess.STDOUT, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError as error:
                    raise DomainRuleError("COMFY_RUNTIME_START_FAILED", "生产 ComfyUI 启动失败", {"reason": type(error).__name__}) from error
            pid = process.pid
        elif kind != "EXTERNAL":
            raise DomainRuleError("RUNTIME_INSTANCE_KIND_INVALID", "运行实例类型无效")
        port = urlparse(str(manifest["endpoint"])).port or 0
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO runtime_instances
                (id,runtime_environment_version_id,instance_kind,state,port,process_id,owned_attempt_id,observed_fingerprint,health_json,last_heartbeat_at,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,'STARTING',?,?,NULL,?,'{}',NULL,?,?,?,1,'v1')""",
                (instance_id, version_id, kind, port, pid, version["environment_fingerprint"], now, now, actor),
            )
        return self.runtime_status(version_id)

    def stop_runtime(self, version_id: str, expected_instance_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM runtime_instances WHERE id=? AND runtime_environment_version_id=?", (expected_instance_id, version_id)).fetchone()
        if row is None:
            raise DomainRuleError("RUNTIME_INSTANCE_MISMATCH", "运行实例已变化，请刷新后重试")
        instance = dict(row)
        if instance["instance_kind"] == "EXTERNAL":
            raise DomainRuleError("EXTERNAL_RUNTIME_NOT_OWNED", "外部运行时不受本应用进程管理")
        pid = int(instance["process_id"]) if instance.get("process_id") else None
        if pid and self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as error:
                raise DomainRuleError("COMFY_RUNTIME_STOP_FAILED", "生产 ComfyUI 停止失败", {"reason": type(error).__name__}) from error
        now = _now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE runtime_instances SET state='STOPPED',updated_at=?,last_heartbeat_at=?,revision=revision+1 WHERE id=?", (now, now, expected_instance_id))
        return {"instance_id": expected_instance_id, "observed_state": "STOPPED", "stopped": True}
