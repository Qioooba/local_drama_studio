from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.parameters import (
    ParameterContract,
    ParameterOverride,
    ParameterResolutionService,
    ParameterSource,
    ProfileParameterPolicy,
)


def _contract() -> ParameterContract:
    return ParameterContract(
        capability="VIDEO_I2V",
        schema={
            "type": "object",
            "required": ["steps", "fps"],
            "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "fps": {"type": "integer", "enum": [24, 30], "default": 24},
                "seed": {"type": "integer"},
            },
        },
        ui_schema={
            "properties": {
                "steps": {"scopes": ["PROJECT", "SHOT", "RUN"]},
                "fps": {"scopes": ["PROJECT", "SHOT", "RUN"]},
                "seed": {"scopes": ["RUN"], "cross_profile_safe": True},
            }
        },
    )


def _policy() -> ProfileParameterPolicy:
    return ProfileParameterPolicy(
        profile_version_id="profile-v1",
        defaults={"steps": 28},
        locked_values={"fps": 24},
        allowed_override_fields=frozenset({"steps", "fps", "seed"}),
    )


def test_parameter_resolution_applies_precedence_and_records_provenance() -> None:
    resolved = ParameterResolutionService().resolve(
        _contract(),
        _policy(),
        (
            ParameterOverride("PROJECT", {"steps": 32}, "profile-v1"),
            ParameterOverride("SHOT", {"steps": 36}, "profile-v1"),
            ParameterOverride("RUN", {"seed": 7}, "profile-v1"),
        ),
    )

    assert resolved["steps"].value == 36
    assert resolved["steps"].source is ParameterSource.SHOT_OVERRIDE
    assert resolved["fps"].value == 24
    assert resolved["fps"].source is ParameterSource.PROFILE_LOCK
    assert resolved["fps"].locked is True
    assert resolved["seed"].source is ParameterSource.RUN_OVERRIDE


@pytest.mark.parametrize(
    ("override", "code"),
    [
        (ParameterOverride("RUN", {"fps": 30}, "profile-v1"), "MP_PARAMETER_LOCKED"),
        (ParameterOverride("RUN", {"steps": 30}, "other-profile"), "MP_PARAMETER_PROFILE_MISMATCH"),
        (ParameterOverride("PROJECT", {"seed": 1}, "profile-v1"), "MP_PARAMETER_SCOPE_FORBIDDEN"),
    ],
)
def test_parameter_resolution_fails_closed_for_profile_scope_and_lock_rules(override, code: str) -> None:
    with pytest.raises(DomainRuleError) as raised:
        ParameterResolutionService().resolve(_contract(), _policy(), (override,))

    assert raised.value.code == code


def test_auto_override_accepts_only_cross_profile_semantic_parameters() -> None:
    resolved = ParameterResolutionService().resolve(
        _contract(), _policy(), (ParameterOverride("RUN", {"seed": 42}, None, auto_mode=True),)
    )
    assert resolved["seed"].value == 42

    with pytest.raises(DomainRuleError, match="AUTO"):
        ParameterResolutionService().resolve(
            _contract(), _policy(), (ParameterOverride("RUN", {"steps": 42}, None, auto_mode=True),)
        )


@pytest.mark.parametrize("field", ["model_path", "api_key", "baseUrl", "native_locator", "python_executable"])
def test_parameter_contract_rejects_runtime_wiring_field_names(field: str) -> None:
    with pytest.raises(DomainRuleError) as raised:
        ParameterContract(
            capability="EMBEDDING_TEXT",
            schema={"type": "object", "properties": {field: {"type": "string"}}},
            ui_schema={"properties": {field: {"scopes": ["SYSTEM"]}}},
        )
    assert raised.value.code == "MP_PARAMETER_RUNTIME_FIELD_FORBIDDEN"
