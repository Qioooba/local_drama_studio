from __future__ import annotations

import pytest

from local_drama.application import diagnostics
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import (
    VariantInput,
    validate_delivery_source,
    validate_local_transport,
    validate_review_approval,
)
from local_drama.infrastructure.comfy import ComfyClient


def test_review_machine_pass_does_not_satisfy_human_approval() -> None:
    with pytest.raises(DomainRuleError) as error:
        validate_review_approval([{"item_id": "identity", "result": "PASS"}], {"identity", "motion"})
    assert error.value.code == "REVIEW_REQUIRED_CHECK_FAILED"


def test_delivery_requires_approved_render_target_and_integrity() -> None:
    with pytest.raises(DomainRuleError) as error:
        validate_delivery_source(render_approved=False, target_selected=True, integrity_ok=True)
    assert error.value.code == "DELIVERY_SOURCE_NOT_APPROVED"


def test_remote_transport_is_rejected_even_if_url_is_supplied() -> None:
    with pytest.raises(DomainRuleError) as error:
        validate_local_transport("REMOTE_HTTP_SERVICE", "https://example.invalid")
    assert error.value.code == "REMOTE_PROVIDER_DISABLED_IN_LOCAL_RELEASE"


def test_variant_plan_separates_creative_branch_and_operational_replay() -> None:
    base = VariantPlan(
        variant_type="BASE",
        parent_variant_id=None,
        branch_reason="initial",
        prompt_revision_id="prompt-1",
        profile_version_id="profile-1",
        parameter_set={"frames": 81},
        seed_policy="EXPLICIT",
        explicit_seed=10,
        bindings=(VariantInput("FIRST_FRAME", "media-1"),),
    )
    base.validate(variant_id="variant-1", ancestors=set(), allowed_roles={"FIRST_FRAME"})
    replay = VariantPlan(
        variant_type="EXACT_REPLAY",
        parent_variant_id="variant-1",
        branch_reason="diagnostic replay",
        prompt_revision_id="prompt-1",
        profile_version_id="profile-1",
        parameter_set={"frames": 81},
        seed_policy="EXPLICIT",
        explicit_seed=10,
        bindings=(VariantInput("FIRST_FRAME", "media-1"),),
    )
    replay.validate(variant_id="variant-2", ancestors={"variant-1"}, allowed_roles={"FIRST_FRAME"})
    with pytest.raises(DomainRuleError) as error:
        base.validate(variant_id="variant-1", ancestors={"variant-1"}, allowed_roles={"FIRST_FRAME"})
    assert error.value.code == "VARIANT_LINEAGE_CYCLE"


def test_variant_plan_rejects_unknown_type_and_duplicate_semantic_slot() -> None:
    invalid = VariantPlan(
        variant_type="RETRY",
        parent_variant_id=None,
        branch_reason="ambiguous retry",
        prompt_revision_id=None,
        profile_version_id="profile-1",
        parameter_set={},
        seed_policy="RANDOM",
        explicit_seed=None,
        bindings=(),
    )
    with pytest.raises(DomainRuleError) as error:
        invalid.validate(variant_id="variant-1", ancestors=set(), allowed_roles=set())
    assert error.value.code == "INVALID_VARIANT_TYPE"

    duplicate = VariantPlan(
        variant_type="BASE",
        parent_variant_id=None,
        branch_reason="duplicate slot",
        prompt_revision_id=None,
        profile_version_id="profile-1",
        parameter_set={},
        seed_policy="RANDOM",
        explicit_seed=None,
        bindings=(VariantInput("FIRST_FRAME", "media-1"), VariantInput("FIRST_FRAME", "media-2")),
    )
    with pytest.raises(DomainRuleError) as error:
        duplicate.validate(variant_id="variant-1", ancestors=set(), allowed_roles={"FIRST_FRAME"})
    assert error.value.code == "DUPLICATE_INPUT_BINDING"


def test_comfy_access_guard_blocks_before_network(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_ACCESS", "disabled")
    with pytest.raises(DomainRuleError) as error:
        ComfyClient().system_stats()
    assert error.value.code == "COMFY_ACCESS_DISABLED"


def test_diagnostics_comfy_guard_skips_urlopen(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_ACCESS", "disabled")

    def unexpected_open_local(*args, **kwargs):
        raise AssertionError("HTTP transport must not run while ComfyUI access is disabled")

    monkeypatch.setattr(diagnostics, "open_local", unexpected_open_local)
    status, observed = diagnostics._probe_loopback("http://127.0.0.1:8188")
    assert status == "BLOCKED"
    assert observed == {"reason": "access_disabled"}
