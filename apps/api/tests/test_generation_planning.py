import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.generation_planning import effective_configuration_snapshot, frozen_generation_contract


def _plan(**changes):
    values = {
        "variant_type": "BASE",
        "parent_variant_id": None,
        "branch_reason": "test",
        "prompt_revision_id": None,
        "profile_version_id": "profile-1",
        "parameter_set": {"PROMPT": "rain", "SEED": 7, "tier": "STANDARD"},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "provider_random_nonce": None,
        "bindings": [],
    }
    values.update(changes)
    return VariantPlan(**values)


def test_frozen_generation_contract_rejects_missing_semantic_binding() -> None:
    profile = {"workflow_version_id": "wf-1", "model_bundle_json": "{}", "input_contract_json": "{}", "parameter_schema_json": "{}", "resource_policy_json": "{}"}
    workflow = {"node_bindings_json": '{"PROMPT": {}}', "content_json": "{}", "contract_json": '{"production_tier": "STANDARD"}'}
    with pytest.raises(DomainRuleError) as error:
        frozen_generation_contract(profile, workflow, _plan())
    assert error.value.code == "WORKFLOW_SEMANTIC_BINDING_REQUIRED"


def test_effective_configuration_snapshot_keeps_only_replay_authority() -> None:
    snapshot = effective_configuration_snapshot({"fingerprint": "abc", "profile": {"override_schema": {"schema_version": "v2"}}, "secret": "omit"})
    assert snapshot["override_schema_version"] == "v2"
    assert "secret" not in snapshot
