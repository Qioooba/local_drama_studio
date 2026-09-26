"""An override written in a different letter case must reach the declared parameter.

Measured failure this fixes
---------------------------
The explainer freezes a per-candidate seed through ``run_overrides={"seed": ...}`` while
the ComfyUI workflow binding declares the same knob as ``SEED``.  Contract validation is
name-exact, so the submit was refused with ``MP_PARAMETER_UNKNOWN {"fields": ["seed"]}``
even though the read-only plan had already passed — the product could plan an image but
never generate one.

The rule these tests pin: a case-variant of a **declared** field resolves to that field,
and anything else is still unknown.  The profile's own allow-list stays authoritative.
"""

from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.parameters import (
    ParameterContract,
    ParameterOverride,
    ParameterResolutionService,
    ProfileParameterPolicy,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "PROMPT": {"type": "string"},
        "SEED": {"type": "integer"},
        "STEPS": {"type": "integer"},
    },
    "additionalProperties": False,
}

PROFILE = "profile-version-1"


def _contract() -> ParameterContract:
    return ParameterContract("IMAGE_CONCEPT", SCHEMA, {"properties": {}})


def _policy(allowed: tuple[str, ...]) -> ProfileParameterPolicy:
    return ProfileParameterPolicy(
        profile_version_id=PROFILE,
        defaults={},
        locked_values={},
        allowed_override_fields=frozenset(allowed),
    )


def test_a_lowercase_override_resolves_to_the_declared_field() -> None:
    service = ParameterResolutionService()
    resolved = service.resolve(
        _contract(),
        _policy(("SEED",)),
        (ParameterOverride("RUN", {"seed": 4242}, PROFILE),),
    )
    assert resolved["SEED"].value == 4242


def test_an_unknown_field_is_still_refused() -> None:
    service = ParameterResolutionService()
    with pytest.raises(DomainRuleError) as error:
        service.resolve(
            _contract(),
            _policy(("SEED",)),
            (ParameterOverride("RUN", {"definitely_not_a_field": 1}, PROFILE),),
        )
    assert error.value.code == "MP_PARAMETER_UNKNOWN"


def test_the_profile_allow_list_is_still_authoritative() -> None:
    service = ParameterResolutionService()
    with pytest.raises(DomainRuleError) as error:
        service.resolve(
            _contract(),
            _policy(()),
            (ParameterOverride("RUN", {"seed": 1}, PROFILE),),
        )
    assert error.value.code == "MP_PARAMETER_OVERRIDE_FORBIDDEN"


def test_a_differently_cased_override_does_not_bypass_the_allow_list() -> None:
    """``Seed`` is a variant of ``SEED``, so an empty allow-list still refuses it."""

    service = ParameterResolutionService()
    with pytest.raises(DomainRuleError) as error:
        service.resolve(
            _contract(),
            _policy(()),
            (ParameterOverride("RUN", {"Seed": 1}, PROFILE),),
        )
    assert error.value.code == "MP_PARAMETER_OVERRIDE_FORBIDDEN"
