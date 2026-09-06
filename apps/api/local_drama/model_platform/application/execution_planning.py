"""Fail-closed V2 execution preview before a snapshot reaches a worker."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import (
    CapabilityAssignmentService,
    CapabilityResolution,
    CapabilityScopeContext,
)
from local_drama.model_platform.application.parameters import (
    ParameterContract,
    ParameterOverride,
    ParameterResolutionService,
    ProfileParameterPolicy,
    ResolvedParameter,
)

_FORBIDDEN_SEMANTIC_INPUT_KEYS = frozenset(
    {
        "absolute_path",
        "path",
        "base_url",
        "endpoint",
        "ollama_base_url",
        "comfy_node_id",
        "python_executable",
        "api_key",
        "secret",
        "secret_value",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionPreviewRequest:
    capability_code: str
    scope: CapabilityScopeContext
    semantic_inputs: Mapping[str, Any]
    run_overrides: Mapping[str, Any]
    expected_resolution_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPreview:
    capability_code: str
    execution_profile_version_id: str | None
    adapter_code: str | None
    adapter_version: str | None
    resolution_reason: str
    assignment_chain: tuple[Mapping[str, object], ...]
    resolved_parameters: Mapping[str, ResolvedParameter]
    network_policy: Mapping[str, Any]
    runtime_ready: bool
    blockers: tuple[str, ...]
    resolution_hash: str

    @property
    def executable(self) -> bool:
        return not self.blockers and self.execution_profile_version_id is not None and self.runtime_ready


class ExecutionPlanningService:
    """Plans V2 executions without accepting runtime-specific user input.

    Preview is deterministic for the same persisted Profile/running runtime. A
    caller must echo its resolution hash on a later submit, preventing a UI
    decision from silently changing between preview and queue handoff.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self.assignments = CapabilityAssignmentService(database)
        self.parameters = ParameterResolutionService()

    def preview(self, request: ExecutionPreviewRequest) -> ExecutionPreview:
        capability_code = request.capability_code.strip().upper()
        _validate_semantic_inputs(request.semantic_inputs)
        resolution = self.assignments.resolve(capability_code, request.scope)
        if resolution.execution_profile_version_id is None:
            return self._blocked_preview(capability_code, resolution)

        profile = self._load_published_profile(resolution.execution_profile_version_id, capability_code)
        contract = ParameterContract(
            capability=capability_code,
            schema=_object_json(profile["schema_json"], "MP_PARAMETER_CONTRACT_INVALID"),
            ui_schema=_object_json(profile["ui_schema_json"], "MP_PARAMETER_UI_SCHEMA_INVALID"),
        )
        profile_payload = _object_json(profile["payload_json"], "MP_PROFILE_PAYLOAD_INVALID")
        defaults = _mapping_field(profile_payload, "defaults")
        locked_values = _mapping_field(profile_payload, "locked_values", fallback_key="locks")
        allowed_override_fields = frozenset(str(value) for value in profile_payload.get("allowed_override_fields", ()) if str(value))
        policy = ProfileParameterPolicy(
            profile_version_id=str(profile["profile_id"]),
            defaults=defaults,
            locked_values=locked_values,
            allowed_override_fields=allowed_override_fields,
        )
        overrides: list[ParameterOverride] = []
        if resolution.assignment_overrides:
            if resolution.assignment_override_scope is None:
                raise DomainRuleError("MP_ASSIGNMENT_OVERRIDE_SCOPE_INVALID", "解析到的范围参数缺少所属作用域。")
            overrides.append(ParameterOverride(
                scope=resolution.assignment_override_scope,
                values=resolution.assignment_overrides,
                profile_version_id=str(profile["profile_id"]),
            ))
        overrides.append(ParameterOverride(
            scope="RUN",
            values=request.run_overrides,
            profile_version_id=str(profile["profile_id"]),
        ))
        resolved_parameters = self.parameters.resolve(contract, policy, tuple(overrides))
        runtime_ready = str(profile["runtime_status"]) == "ACTIVE"
        blockers = () if runtime_ready else ("RUNTIME_INSTALLATION_VERSION_NOT_ACTIVE",)
        network_policy = _network_policy(_object_json(profile["resource_policy_json"], "MP_RESOURCE_POLICY_INVALID"))
        resolution_hash = _hash(
            {
                "capability_code": capability_code,
                "execution_profile_version_id": str(profile["profile_id"]),
                "profile_payload_hash": str(profile["payload_hash"]),
                "runtime_fingerprint": str(profile["runtime_fingerprint"]),
                "adapter_binding_hash": str(profile["adapter_binding_hash"]),
                "parameter_contract_hash": str(profile["parameter_contract_hash"]),
                "assignment_chain": list(resolution.assignment_chain),
                "assignment_overrides": dict(resolution.assignment_overrides),
                "semantic_inputs": dict(request.semantic_inputs),
                "run_overrides": dict(request.run_overrides),
            }
        )
        return ExecutionPreview(
            capability_code=capability_code,
            execution_profile_version_id=str(profile["profile_id"]),
            adapter_code=str(profile["adapter_code"]),
            adapter_version=str(profile["adapter_version"]),
            resolution_reason=resolution.resolution_reason,
            assignment_chain=resolution.assignment_chain,
            resolved_parameters=resolved_parameters,
            network_policy=network_policy,
            runtime_ready=runtime_ready,
            blockers=blockers,
            resolution_hash=resolution_hash,
        )

    def assert_submit_fresh(self, preview: ExecutionPreview, expected_resolution_hash: str | None) -> None:
        if not preview.executable:
            raise DomainRuleError("MP_EXECUTION_NOT_READY", "当前能力没有可执行的已发布 Profile 或运行时。", {"blockers": list(preview.blockers)})
        if not expected_resolution_hash or expected_resolution_hash != preview.resolution_hash:
            raise DomainRuleError("MP_EXECUTION_RESOLUTION_STALE", "执行预检已变化或未确认，请刷新预检后再提交。")

    def _blocked_preview(self, capability_code: str, resolution: CapabilityResolution) -> ExecutionPreview:
        blocker = resolution.blocked_reason or "NO_PUBLISHED_PROFILE"
        resolution_hash = _hash(
            {
                "capability_code": capability_code,
                "execution_profile_version_id": None,
                "resolution_reason": resolution.resolution_reason,
                "assignment_chain": list(resolution.assignment_chain),
                "blocker": blocker,
            }
        )
        return ExecutionPreview(
            capability_code=capability_code,
            execution_profile_version_id=None,
            adapter_code=None,
            adapter_version=None,
            resolution_reason=resolution.resolution_reason,
            assignment_chain=resolution.assignment_chain,
            resolved_parameters=MappingProxyType({}),
            network_policy=MappingProxyType({"mode": "LOCAL_ONLY"}),
            runtime_ready=False,
            blockers=(blocker,),
            resolution_hash=resolution_hash,
        )

    def _load_published_profile(self, profile_id: str, capability_code: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            profile = connection.execute(
                """SELECT profile.id AS profile_id,profile.payload_json,profile.payload_hash,
                          parameter.schema_json,parameter.ui_schema_json,parameter.content_hash AS parameter_contract_hash,
                          resource.policy_json AS resource_policy_json,
                          runtime.status AS runtime_status,runtime.fingerprint AS runtime_fingerprint,
                          binding.adapter_code,('v' || binding.version_no) AS adapter_version,
                          binding.content_hash AS adapter_binding_hash
                   FROM mp_execution_profile_versions profile
                   JOIN mp_capability_definitions capability ON capability.id=profile.capability_definition_id
                   JOIN mp_profile_publications publication ON publication.execution_profile_version_id=profile.id
                   JOIN mp_parameter_contract_versions parameter ON parameter.id=profile.parameter_contract_version_id
                   JOIN mp_resource_policy_versions resource ON resource.id=profile.resource_policy_version_id
                   JOIN mp_adapter_binding_contract_versions binding
                     ON binding.id=profile.adapter_binding_contract_version_id
                   JOIN mp_runtime_installation_versions runtime ON runtime.id=profile.runtime_installation_version_id
                   WHERE profile.id=? AND capability.code=? AND publication.status='PUBLISHED'""",
                (profile_id, capability_code),
            ).fetchone()
        if profile is None:
            raise DomainRuleError("MP_EXECUTION_PROFILE_NOT_PUBLISHED", "已解析 Profile 在提交前不再是当前能力的已发布版本。")
        return cast(sqlite3.Row, profile)


def _validate_semantic_inputs(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DomainRuleError("MP_EXECUTION_INPUTS_INVALID", "semantic_inputs 必须是引用业务实体的 JSON 对象。")

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).strip().lower()
                if key in _FORBIDDEN_SEMANTIC_INPUT_KEYS:
                    raise DomainRuleError("MP_EXECUTION_RUNTIME_INPUT_FORBIDDEN", "业务执行请求不能携带路径、端点、密钥或运行时节点。", {"field": key})
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)


def _object_json(value: object, error_code: str) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError(error_code, "持久化的 V2 Profile 合同不是合法 JSON。") from error
    if not isinstance(decoded, dict):
        raise DomainRuleError(error_code, "持久化的 V2 Profile 合同必须是 JSON 对象。")
    return decoded


def _mapping_field(payload: Mapping[str, Any], key: str, *, fallback_key: str | None = None) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None and fallback_key:
        value = payload.get(fallback_key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DomainRuleError("MP_PROFILE_PARAMETER_POLICY_INVALID", f"Profile 的 {key} 必须是 JSON 对象。")
    return {str(name): item for name, item in value.items()}


def _network_policy(resource_policy: Mapping[str, Any]) -> Mapping[str, Any]:
    value = resource_policy.get("network_policy", resource_policy.get("network", {"mode": "LOCAL_ONLY"}))
    if not isinstance(value, Mapping):
        raise DomainRuleError("MP_RESOURCE_POLICY_INVALID", "资源策略的 network_policy 必须是 JSON 对象。")
    return MappingProxyType({str(key): item for key, item in value.items()})


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
