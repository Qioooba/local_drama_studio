"""Versioned workflow packages and semantic input-slot compilation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.manifest import load_manifest

TRUSTED_COMFY_BUILTINS = {"CreateVideo", "LoadImage", "SaveImage", "SaveVideo"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class WorkflowService:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings or Settings()
        self.package_root = (settings.workflow_packages_root if settings else database.path.parent.parent / "work" / "workflow_packages").resolve()

    @staticmethod
    def _validate_code(code: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", code):
            raise DomainRuleError("WORKFLOW_CODE_INVALID", "workflow code 只能包含字母、数字、点、下划线和连字符")

    def _write_package(self, code: str, version_no: int, payload: dict[str, Any]) -> str:
        relative = Path("workflow_packages") / code / f"v{version_no}.json"
        target = (self.package_root.parent / relative).resolve()
        if not target.is_relative_to(self.package_root.parent.resolve()):
            raise DomainRuleError("WORKFLOW_PACKAGE_PATH_INVALID", "workflow package 路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".partial-{uuid.uuid4().hex}.json")
        try:
            partial.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(partial, target)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise
        return relative.as_posix()

    def _validate_node_supply_chain(self, workflow: dict[str, Any]) -> dict[str, Any]:
        manifest = load_manifest(self.settings.manifest_path)
        nodes = manifest.data.get("nodes", {})
        trusted_custom = {str(item) for item in nodes.get("class_mappings", [])}
        plugin_init_sha = str(nodes.get("plugin_init_sha256", ""))
        mapping_sha = str(nodes.get("node_mapping_sha256", ""))
        required = {
            str(node.get("class_type"))
            for node in workflow.values()
            if isinstance(node, dict) and node.get("class_type")
        }
        untrusted = sorted(required - TRUSTED_COMFY_BUILTINS - trusted_custom)
        hashes_valid = all(len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value) for value in (plugin_init_sha, mapping_sha))
        if untrusted or not hashes_valid:
            raise DomainRuleError(
                "WORKFLOW_NODE_SUPPLY_CHAIN_UNTRUSTED",
                "workflow 引用了未进入可信清单的节点，或自定义节点 hash 清单无效",
                {"untrusted_nodes": untrusted, "manifest_sha256": manifest.sha256},
            )
        return {
            "required_nodes": sorted(required),
            "trusted_custom_nodes": sorted(required & trusted_custom),
            "trusted_builtin_nodes": sorted(required & TRUSTED_COMFY_BUILTINS),
            "plugin_init_sha256": plugin_init_sha.lower(),
            "node_mapping_sha256": mapping_sha.lower(),
            "manifest_sha256": manifest.sha256,
        }

    @staticmethod
    def _validate_bindings(workflow: dict[str, Any], contract: dict[str, Any], node_bindings: dict[str, Any]) -> None:
        """Validate semantic bindings before a package becomes durable.

        The browser only submits semantic slot names.  Keeping this check at
        registration time prevents a typo in a node id/input from surviving
        into a validation attestation and makes the immutable package useful
        for offline inspection as well.
        """

        if not isinstance(node_bindings, dict):
            raise DomainRuleError("WORKFLOW_BINDINGS_INVALID", "workflow node bindings 必须是对象")
        for role, binding in node_bindings.items():
            if not isinstance(role, str) or not role.strip() or not isinstance(binding, dict):
                raise DomainRuleError("WORKFLOW_BINDINGS_INVALID", "workflow semantic binding 必须包含 role 和对象映射", {"role": role})
            node_id = str(binding.get("node_id", ""))
            input_name = str(binding.get("input", ""))
            node = workflow.get(node_id)
            if not node_id or not input_name or not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                raise DomainRuleError("WORKFLOW_BINDING_INVALID", "workflow semantic binding 指向不存在节点或输入", {"role": role, "node_id": node_id, "input": input_name})
            if input_name not in node["inputs"]:
                raise DomainRuleError("WORKFLOW_BINDING_INPUT_MISSING", "workflow semantic binding 指向节点未声明的输入", {"role": role, "node_id": node_id, "input": input_name})

        input_slots = contract.get("input_slots") if isinstance(contract, dict) else None
        if isinstance(input_slots, dict):
            missing = sorted(str(slot) for slot, spec in input_slots.items() if isinstance(spec, dict) and spec.get("required", True) and str(slot) not in node_bindings)
            if missing:
                raise DomainRuleError("WORKFLOW_REQUIRED_BINDING_MISSING", "workflow contract 声明的必需输入没有 semantic binding", {"missing_slots": missing})

    def register_package(
        self,
        code: str,
        title: str,
        workflow: dict[str, Any],
        contract: dict[str, Any],
        node_bindings: dict[str, Any],
        runtime_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_code(code)
        if not workflow or not isinstance(workflow, dict):
            raise DomainRuleError("WORKFLOW_REQUIRED", "workflow package content 不能为空")
        supply_chain = self._validate_node_supply_chain(workflow)
        self._validate_bindings(workflow, contract, node_bindings)
        if any(
            isinstance(value, str) and (value.startswith("\\") or ":\\" in value or value.startswith("/"))
            for node in workflow.values()
            if isinstance(node, dict)
            for value in node.get("inputs", {}).values()
        ):
            raise DomainRuleError("WORKFLOW_ABSOLUTE_PATH", "workflow 不得携带绝对路径")
        now = _now()
        content_hash = _hash(workflow)
        with self.database.transaction() as connection:
            workflow_row = connection.execute("SELECT * FROM workflows WHERE code=?", (code,)).fetchone()
            if workflow_row is None:
                workflow_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, 'local-user', 1, 'v2')",
                    (workflow_id, code, title, now, now),
                )
                version_no = 1
            else:
                workflow_id = str(workflow_row["id"])
                version_no = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM workflow_versions WHERE workflow_id=?", (workflow_id,)
                    ).fetchone()["next_no"]
                )
            version_id = str(uuid.uuid4())
            package_rel_path = self._write_package(
                code,
                version_no,
                {
                    "schema_version": "localdrama.workflow-package.v2",
                    "code": code,
                    "title": title,
                    "version_no": version_no,
                    "content_hash": content_hash,
                    "workflow": workflow,
                    "contract": contract,
                    "node_bindings": node_bindings,
                    "runtime_contract": runtime_contract or {},
                    "node_supply_chain": supply_chain,
                },
            )
            connection.execute(
                """INSERT INTO workflow_versions
                (id, workflow_id, version_no, content_hash, status, contract_json, content_json, package_rel_path, node_bindings_json, runtime_contract_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, 'local-user', 1, 'v2')""",
                (
                    version_id,
                    workflow_id,
                    version_no,
                    content_hash,
                    _json(contract),
                    _json(workflow),
                    package_rel_path,
                    _json(node_bindings),
                    _json(runtime_contract or {}),
                    now,
                    now,
                ),
            )
        return self.get_version(version_id)

    def get_version(self, version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT w.code, w.title, wv.* FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id WHERE wv.id=?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "WorkflowVersion 不存在")
        result = dict(row)
        result["workflow"] = json.loads(result.pop("content_json"))
        result["contract"] = json.loads(result.pop("contract_json"))
        result["node_bindings"] = json.loads(result.pop("node_bindings_json"))
        result["runtime_contract"] = json.loads(result.pop("runtime_contract_json"))
        return result

    def list_versions(self) -> list[dict[str, Any]]:
        """Return immutable workflow history without contacting a runtime."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT w.code, w.title, wv.id, wv.workflow_id, wv.version_no,
                wv.content_hash, wv.status, wv.contract_json, wv.package_rel_path,
                wv.published_at, wv.created_at, wv.updated_at, wv.revision
                FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
                ORDER BY w.code, wv.version_no DESC"""
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["contract"] = json.loads(item.pop("contract_json") or "{}")
            items.append(item)
        return items

    def compile_semantic_inputs(self, version_id: str, semantic_inputs: dict[str, Any]) -> dict[str, Any]:
        version = self.get_version(version_id)
        workflow = copy.deepcopy(version["workflow"])
        bindings = version["node_bindings"]
        for role, value in semantic_inputs.items():
            binding = bindings.get(role)
            if not isinstance(binding, dict) or not binding.get("node_id") or not binding.get("input"):
                raise DomainRuleError("WORKFLOW_SLOT_UNSUPPORTED", "workflow 未声明该 semantic input slot", {"role": role})
            node = workflow.get(str(binding["node_id"]))
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                raise DomainRuleError("WORKFLOW_BINDING_INVALID", "workflow semantic binding 指向不存在节点", {"role": role})
            node["inputs"][str(binding["input"])] = value
        compiled_hash = _hash({"workflow_version_id": version_id, "workflow": workflow, "semantic_inputs": semantic_inputs})
        return {
            "workflow_version_id": version_id,
            "workflow": workflow,
            "semantic_inputs": semantic_inputs,
            "compiled_hash": compiled_hash,
            "content_hash": version["content_hash"],
        }

    def validate_against_comfy(self, version_id: str, client: ComfyClient) -> dict[str, Any]:
        version = self.get_version(version_id)
        supply_chain = self._validate_node_supply_chain(version["workflow"])
        object_info = client.object_info()
        available = set(object_info)
        required = {str(node.get("class_type")) for node in version["workflow"].values() if isinstance(node, dict) and node.get("class_type")}
        missing = sorted(required - available)
        runtime_layout: dict[str, Any] | None = None
        if "H3" in str(version["contract"].get("capability", "")).upper():
            from local_drama.application.h3_workflows import H3WorkflowFactory

            runtime_layout = H3WorkflowFactory(self.settings).runtime_layout()
        node_status = "PASS" if not missing else "BLOCKED"
        status = node_status if runtime_layout is None else "PASS" if node_status == "PASS" and runtime_layout.get("status") == "PASS" else "BLOCKED"
        evidence = {
            "workflow_version_id": version_id,
            "status": status,
            "required_nodes": sorted(required),
            "missing_nodes": missing,
            "comfy_url": client.base_url,
            "runtime_layout": runtime_layout,
            "node_supply_chain": supply_chain,
        }

        validation_id = str(uuid.uuid4())
        evidence_hash = _hash(evidence)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_validation_attestations
                (id, workflow_version_id, workflow_content_hash, status, evidence_json, evidence_hash, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'local-system')""",
                (validation_id, version_id, version["content_hash"], status, _json(evidence), evidence_hash, _now()),
            )
        return {**evidence, "validation_id": validation_id, "evidence_hash": evidence_hash}

    def _verified_attestation(self, version_id: str, validation_id: str) -> dict[str, Any]:
        version = self.get_version(version_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_validation_attestations WHERE id=? AND workflow_version_id=?",
                (validation_id, version_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError("WORKFLOW_VALIDATION_ATTESTATION_REQUIRED", "发布只能使用服务端本机验证产生的 attestation")
        evidence = json.loads(str(row["evidence_json"]))
        if row["status"] != "PASS" or evidence.get("status") != "PASS":
            raise DomainRuleError("WORKFLOW_VALIDATION_REQUIRED", "workflow 必须先通过本机 Comfy 节点验证")
        if row["workflow_content_hash"] != version["content_hash"] or row["evidence_hash"] != _hash(evidence):
            raise DomainRuleError("WORKFLOW_VALIDATION_STALE", "workflow 验证证据与当前不可变内容不匹配")
        return {**evidence, "validation_id": validation_id, "evidence_hash": str(row["evidence_hash"])}

    def publish(self, version_id: str, validation_id: str, actor: str = "local-user") -> dict[str, Any]:
        version = self.get_version(version_id)
        validation = self._verified_attestation(version_id, validation_id)
        is_h3 = "H3" in str(version["contract"].get("capability", "")).upper()
        if is_h3 and validation.get("runtime_layout", {}).get("status") != "PASS":
            raise DomainRuleError("WORKFLOW_RUNTIME_LAYOUT_REQUIRED", "H3 workflow 必须先通过 manifest/runtime sidecar layout 验证")
        if validation.get("status") != "PASS":
            raise DomainRuleError("WORKFLOW_VALIDATION_REQUIRED", "workflow 必须先通过本机 Comfy 节点验证")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT workflow_id FROM workflow_versions WHERE id=?", (version_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "WorkflowVersion 不存在")
            connection.execute(
                "UPDATE workflow_versions SET status='RETIRED', updated_at=?, revision=revision+1 WHERE workflow_id=? AND status='PUBLISHED'",
                (now, row["workflow_id"]),
            )
            connection.execute(
                "UPDATE workflow_versions SET status='PUBLISHED', published_at=?, updated_at=?, revision=revision+1 WHERE id=?",
                (now, now, version_id),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'WORKFLOW_PUBLISHED', 'workflow_version', ?, '发布本地 workflow', ?)",
                (actor, version_id, _json(validation)),
            )
        return self.get_version(version_id)

    def revoke(self, version_id: str, reason: str, actor: str = "local-user") -> dict[str, Any]:
        if not reason.strip():
            raise DomainRuleError("WORKFLOW_REVOKE_REASON_REQUIRED", "撤销 workflow 必须记录原因")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT workflow_id, status FROM workflow_versions WHERE id=?", (version_id,)).fetchone()
            if row is None:
                raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "WorkflowVersion 不存在")
            connection.execute("UPDATE workflow_versions SET status='RETIRED', updated_at=?, revision=revision+1 WHERE id=?", (now, version_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'WORKFLOW_REVOKED', 'workflow_version', ?, '撤销未通过 runtime gate 的 workflow', ?)",
                (actor, version_id, _json({"reason": reason})),
            )
        return self.get_version(version_id)

    def rollback(self, version_id: str, validation_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Re-activate an immutable prior version only after an explicit local validation."""
        validation = self._verified_attestation(version_id, validation_id)
        result = self.publish(version_id, validation_id, actor)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'WORKFLOW_ROLLED_BACK', 'workflow_version', ?, '回滚到已验证的本地 workflow 版本', ?)",
                (actor, version_id, _json(validation)),
            )
        return result
