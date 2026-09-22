"""TTS provider parameter-capability contract.

The product's canonical emotion token is the uppercase ``NEUTRAL`` (the dialogue
API schema default and the batch-submit default). A provider that declares the
parameter as *unsupported* must accept that default and reject only genuine
non-default requests — otherwise the batch endpoint fails every single line with
``TTS_PARAMETER_UNSUPPORTED`` for a setting the user never chose.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from local_drama.application.worker_handlers.tts_job import (
    SAPI_PARAMETER_CAPABILITIES,
    VOXCPM2_PARAMETER_CAPABILITIES,
    tts_parameter_capabilities,
)
from local_drama.domain.errors import DomainRuleError

PROVIDERS = [SAPI_PARAMETER_CAPABILITIES, VOXCPM2_PARAMETER_CAPABILITIES]


@pytest.mark.parametrize("capabilities", PROVIDERS, ids=lambda item: item.provider_kind)
def test_metadata_only_parameters_are_reported_and_never_applied(capabilities) -> None:
    """Emotion is recording-only provenance on both providers.

    Neither SAPI nor VoxCPM2 has an emotion input channel, and the product's TTS
    submit path only ever sends the canonical default while exposing a speech-rate
    control in the UI.  Emotion must therefore be declared as metadata-only (not
    silently treated as an audio control) and must never appear in the list of
    parameters this provider actually applies.
    """
    assert capabilities.parameters["emotion"].mode == "METADATA_ONLY"
    assert "emotion" in capabilities.metadata_only_parameters()
    assert "emotion" not in capabilities.applied_parameters()
    assert capabilities.applied_parameters() == ["speech_rate"]


@pytest.mark.parametrize("capabilities", PROVIDERS, ids=lambda item: item.provider_kind)
def test_metadata_only_parameters_are_never_rejected(capabilities) -> None:
    """A recording-only parameter must not fail a submission.

    Regression: when emotion was briefly declared ``UNSUPPORTED``, every batch
    submission that carried a non-default emotion failed with
    ``TTS_PARAMETER_UNSUPPORTED`` even though the provider ignores the value and
    the value is still recorded for provenance.
    """
    for value in ("NEUTRAL", "neutral", "ANGRY", "警觉", "激动"):
        assert capabilities.requires_rejection("emotion", value) is False, value


@pytest.mark.parametrize("capabilities", PROVIDERS, ids=lambda item: item.provider_kind)
def test_unsupported_parameters_still_reject_non_defaults(capabilities) -> None:
    """The rejection gate must stay live for genuinely unsupported parameters."""
    original = capabilities.parameters["emotion"]
    unsupported = replace(capabilities, parameters={**capabilities.parameters, "emotion": replace(original, mode="UNSUPPORTED")})
    assert unsupported.requires_rejection("emotion", "NEUTRAL") is False
    assert unsupported.requires_rejection("emotion", "neutral") is False
    assert unsupported.requires_rejection("emotion", "ANGRY") is True
    # An undeclared parameter can never be honoured.
    assert unsupported.requires_rejection("not_a_parameter", 1) is True


@pytest.mark.parametrize("capabilities", PROVIDERS, ids=lambda item: item.provider_kind)
def test_speech_rate_default_uses_numeric_equality(capabilities) -> None:
    # speech_rate is NATIVE/POST_PROCESSING on both providers, so it is never
    # rejected; the numeric default must stay a number rather than a string.
    assert capabilities.parameters["speech_rate"].default == 1.0
    assert capabilities.requires_rejection("speech_rate", 1.0) is False
    assert capabilities.requires_rejection("speech_rate", 1.5) is False
    assert capabilities.parameters["speech_rate"].mode in {"NATIVE", "POST_PROCESSING"}


@pytest.mark.parametrize("capabilities", PROVIDERS, ids=lambda item: item.provider_kind)
def test_declared_emotion_default_matches_the_product_default(capabilities) -> None:
    """The declared default must be the value the product actually sends."""
    from local_drama.api.schemas.dialogue import EpisodeTTSBatchRequest

    product_default = EpisodeTTSBatchRequest.model_fields["emotion"].default
    declared = capabilities.parameters["emotion"].default
    assert str(declared).strip().casefold() == str(product_default).strip().casefold()


def test_unknown_provider_is_reported_explicitly() -> None:
    with pytest.raises(DomainRuleError) as error:
        tts_parameter_capabilities("NOT_A_PROVIDER")
    assert error.value.code == "TTS_PROVIDER_UNSUPPORTED"
