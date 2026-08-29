"""Freeze published V2 Profile resolution before a worker may execute it."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.parameters import ResolvedParameter


@dataclass(frozen=True, slots=True)
class ExecutionSnapshotDraft:
    capability_code: str
    execution_profile_version_id: str
    resolved_parameters: Mapping[str, ResolvedParameter]
    semantic_inputs: Mapping[str, Any]
    resolution: Mapping[str, Any]
    network_policy: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    id: str
    content_hash: str


class ExecutionSnapshotService:
    """A worker-facing immutable record; it never resolves a newer Profile."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, draft: ExecutionSnapshotDraft) -> ExecutionSnapshot:
        with self.database.transaction() as connection:
            return self.create_in_transaction(connection, draft)

    def create_in_transaction(self, connection: Any, draft: ExecutionSnapshotDraft) -> ExecutionSnapshot:
        """Create or reuse one snapshot using the caller's transaction.

        Submission uses this method with Job creation and linking so a failed
        handoff cannot leave an unlinked, durable execution decision behind.
        """
        capability_code = draft.capability_code.strip().upper()
        profile = connection.execute(
                """SELECT cap.id AS capability_id,cap.code AS capability_code,profile.payload_hash,profile.payload_json,
                   runtime.id AS runtime_id,runtime.fingerprint,runtime.adapter_code,runtime.adapter_version,runtime.configuration_json,
                   parameter.id AS parameter_id,parameter.content_hash AS parameter_hash,resource.policy_json
                FROM mp_execution_profile_versions profile
                JOIN mp_capability_definitions cap ON cap.id=profile.capability_definition_id
                JOIN mp_runtime_installation_versions runtime ON runtime.id=profile.runtime_installation_version_id
                JOIN mp_parameter_contract_versions parameter ON parameter.id=profile.parameter_contract_version_id
                JOIN mp_resource_policy_versions resource ON resource.id=profile.resource_policy_version_id
                JOIN mp_profile_publications publication ON publication.execution_profile_version_id=profile.id
                WHERE profile.id=? AND publication.status='PUBLISHED'""",
            (draft.execution_profile_version_id,),
        ).fetchone()
        if profile is None:
            raise DomainRuleError("MP_EXECUTION_PROFILE_NOT_PUBLISHED", "只能为已发布 V2 Profile 创建执行快照。")
        if str(profile["capability_code"]) != capability_code:
            raise DomainRuleError("MP_EXECUTION_CAPABILITY_MISMATCH", "请求能力与已发布 Profile 不一致。")
        profile_payload = _json_value(profile["payload_json"])
        model_bindings = _load_model_bindings(connection, profile_payload, str(profile["runtime_id"]))
        execution_binding = _execution_binding(profile_payload)
        values = {name: item.value for name, item in draft.resolved_parameters.items()}
        provenance = {
            name: {"source": item.source.value, "locked": item.locked}
            for name, item in draft.resolved_parameters.items()
        }
        payload = {
            "schema_version": "localdrama.execution-snapshot.v3",
            "capability_code": capability_code,
            "capability_definition_id": str(profile["capability_id"]),
            "execution_profile_version_id": draft.execution_profile_version_id,
            "profile_payload_hash": str(profile["payload_hash"]),
            "runtime_installation_version_id": str(profile["runtime_id"]),
            "runtime_fingerprint": str(profile["fingerprint"]),
            "adapter_code": str(profile["adapter_code"]),
            "adapter_version": str(profile["adapter_version"]),
            "runtime_configuration": _json_value(profile["configuration_json"]),
            "model_bindings": model_bindings,
            "execution_binding": execution_binding,
            "parameter_contract_version_id": str(profile["parameter_id"]),
            "parameter_contract_hash": str(profile["parameter_hash"]),
            "resolved_parameters": values,
            "parameter_provenance": provenance,
            "semantic_inputs": dict(draft.semantic_inputs),
            "resolution": dict(draft.resolution),
            "resource_policy": _json_value(profile["policy_json"]),
            "network_policy": dict(draft.network_policy),
        }
        content_hash = _hash(payload)
        now = datetime.now(UTC).isoformat()
        snapshot_id = str(uuid.uuid4())
        existing = connection.execute("SELECT id FROM mp_execution_snapshots WHERE content_hash=?", (content_hash,)).fetchone()
        if existing is not None:
            return ExecutionSnapshot(str(existing["id"]), content_hash)
        connection.execute(
                """INSERT INTO mp_execution_snapshots
                (id,schema_version,capability_definition_id,execution_profile_version_id,profile_payload_hash,runtime_installation_version_id,
                 runtime_fingerprint,adapter_code,adapter_version,runtime_configuration_json,model_bindings_json,execution_binding_json,parameter_contract_version_id,parameter_contract_hash,resolved_parameters_json,
                 parameter_provenance_json,semantic_inputs_json,resolution_json,resource_policy_json,network_policy_json,content_hash,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    payload["schema_version"],
                    payload["capability_definition_id"],
                    draft.execution_profile_version_id,
                    payload["profile_payload_hash"],
                    payload["runtime_installation_version_id"],
                    payload["runtime_fingerprint"],
                    payload["adapter_code"],
                    payload["adapter_version"],
                    _json(payload["runtime_configuration"]),
                    _json(payload["model_bindings"]),
                    _json(payload["execution_binding"]),
                    payload["parameter_contract_version_id"],
                    payload["parameter_contract_hash"],
                    _json(values),
                    _json(provenance),
                    _json(payload["semantic_inputs"]),
                    _json(payload["resolution"]),
                    _json(payload["resource_policy"]),
                    _json(payload["network_policy"]),
                    content_hash,
                    now,
            ),
        )
        return ExecutionSnapshot(snapshot_id, content_hash)


def _json_value(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_RESOURCE_POLICY_INVALID", "Profile 引用的资源策略不是合法 JSON。") from error
    return parsed if isinstance(parsed, dict) else {}


def _load_model_bindings(connection: Any, payload: Mapping[str, Any], runtime_version_id: str) -> list[dict[str, str]]:
    raw_ids = payload.get("runtime_model_installation_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids or any(not isinstance(item, str) or not item.strip() for item in raw_ids):
        raise DomainRuleError("MP_EXECUTION_MODEL_BINDING_REQUIRED", "已发布 Profile 缺少冻结模型安装绑定。")
    identifiers = tuple(dict.fromkeys(item.strip() for item in raw_ids))
    placeholders = ",".join("?" for _ in identifiers)
    rows = connection.execute(
        f"""SELECT installation.id,installation.native_locator,release.code AS model_release_code
            FROM mp_runtime_model_installations installation
            JOIN mp_model_releases release ON release.id=installation.release_id
            WHERE installation.id IN ({placeholders})
              AND installation.runtime_installation_version_id=?
              AND installation.install_state='READY'""",
        (*identifiers, runtime_version_id),
    ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    missing = [identifier for identifier in identifiers if identifier not in by_id]
    if missing:
        raise DomainRuleError(
            "MP_EXECUTION_MODEL_BINDING_NOT_READY",
            "Profile 绑定的模型安装已变化、缺失或不再 READY，不能创建执行快照。",
            {"runtime_model_installation_ids": missing},
        )
    return [
        {
            "runtime_model_installation_id": identifier,
            "model_release_code": str(by_id[identifier]["model_release_code"]),
            "native_locator": str(by_id[identifier]["native_locator"]),
        }
        for identifier in identifiers
    ]


def _execution_binding(profile_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the Profile's declarative execution binding into the snapshot.

    Parameters are frozen separately.  This field exists for immutable adapter
    wiring such as a Comfy workflow binding; it is never a browser-supplied
    endpoint, path, executable, or secret.
    """
    raw = profile_payload.get("execution_binding")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise DomainRuleError("MP_PROFILE_EXECUTION_BINDING_INVALID", "Profile 的 execution_binding 必须是 JSON 对象。")
    return {str(key): value for key, value in raw.items()}


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
