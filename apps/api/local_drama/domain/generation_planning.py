"""Pure validation and snapshot rules for generation planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan


@dataclass(frozen=True)
class FrozenGenerationContract:
    workflow_bindings: dict[str, Any]
    workflow_content: dict[str, Any]
    workflow_contract: dict[str, Any]
    model_bundle: dict[str, Any]
    input_contract: dict[str, Any]
    parameter_schema: dict[str, Any]
    resource_policy: dict[str, Any]


def _json_object(raw: Any, *, code: str, message: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise DomainRuleError(code, message) from error
    if not isinstance(value, dict):
        raise DomainRuleError(code, message)
    return value


def frozen_generation_contract(
    profile: Mapping[str, Any],
    workflow: Mapping[str, Any],
    plan: VariantPlan,
) -> FrozenGenerationContract:
    bindings = _json_object(
        workflow["node_bindings_json"], code="WORKFLOW_BINDING_INVALID", message="Workflow semantic binding 契约无效",
    )
    content = _json_object(
        workflow["content_json"], code="WORKFLOW_BINDING_INVALID", message="Workflow content 契约无效",
    )
    contract = _json_object(
        workflow["contract_json"], code="WORKFLOW_CONTRACT_INVALID", message="Workflow contract 契约无效",
    )
    required_roles = set()
    if isinstance(plan.parameter_set.get("PROMPT"), str) and str(plan.parameter_set["PROMPT"]).strip():
        required_roles.add("PROMPT")
    if plan.seed_policy == "EXPLICIT":
        required_roles.add("SEED")
    missing_roles = sorted(required_roles - set(bindings))
    if missing_roles:
        raise DomainRuleError(
            "WORKFLOW_SEMANTIC_BINDING_REQUIRED",
            "当前 Workflow 未绑定生成请求中的关键语义输入，禁止用模板默认值执行",
            {"missing_roles": missing_roles, "workflow_version_id": profile["workflow_version_id"]},
        )
    requested_tier = str(plan.parameter_set.get("tier") or "").strip().upper()
    frozen_tier = str(contract.get("production_tier") or "").strip().upper()
    if requested_tier and contract.get("dynamic_production_tiers") is not True and frozen_tier != requested_tier:
        raise DomainRuleError(
            "WORKFLOW_TIER_CONTRACT_MISMATCH",
            "当前 Workflow 未冻结所选生产档位，禁止显示一个档位却执行另一套帧数/采样参数",
            {
                "requested_tier": requested_tier,
                "workflow_tier": frozen_tier or None,
                "workflow_version_id": profile["workflow_version_id"],
            },
        )
    if plan.seed_policy == "EXPLICIT" and "SEED" in bindings:
        semantic_seed = plan.parameter_set.get("SEED")
        if semantic_seed != plan.explicit_seed:
            raise DomainRuleError(
                "VARIANT_SEED_SNAPSHOT_MISMATCH",
                "Variant 显式 seed 必须与实际 Workflow SEED 语义输入一致",
                {"explicit_seed": plan.explicit_seed, "semantic_seed": semantic_seed},
            )
    return FrozenGenerationContract(
        workflow_bindings=bindings,
        workflow_content=content,
        workflow_contract=contract,
        model_bundle=_json_object(
            profile["model_bundle_json"], code="PROFILE_MODEL_BUNDLE_INVALID", message="Profile model bundle 快照必须是对象",
        ),
        input_contract=_json_object(
            profile["input_contract_json"], code="PROFILE_INPUT_CONTRACT_INVALID", message="Profile input contract 必须是对象",
        ),
        parameter_schema=_json_object(
            profile["parameter_schema_json"], code="PROFILE_PARAMETER_SCHEMA_INVALID", message="Profile parameter schema 必须是对象",
        ),
        resource_policy=_json_object(
            profile["resource_policy_json"], code="PROFILE_RESOURCE_POLICY_INVALID", message="Profile resource policy 必须是对象",
        ),
    )


def effective_configuration_snapshot(configuration: Mapping[str, Any]) -> dict[str, Any]:
    profile = configuration.get("profile")
    override_schema = profile.get("override_schema") if isinstance(profile, dict) else None
    return {
        "schema_version": "localdrama.effective-configuration-snapshot.v1",
        "fingerprint": configuration["fingerprint"],
        "profile_version_id": configuration.get("profile_version_id"),
        "effective_settings": configuration.get("effective_settings", {}),
        "setting_sources": configuration.get("setting_sources", {}),
        "blocking_errors": configuration.get("blocking_errors", []),
        "warnings": configuration.get("warnings", []),
        "runtime_status": configuration.get("runtime_status", "UNKNOWN"),
        "override_schema_version": override_schema.get("schema_version") if isinstance(override_schema, dict) else None,
    }
