"""The licence gate that keeps Qwen-Image-2.1 out of commercial production.

Qwen-Image-2.1 is Qwen Research Licensed while the previous 2512 / Edit-2511
chain is Apache-2.0, so publishing 2.1 as a production default must fail closed
until an authorization is actually recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.model_licensing import (
    assert_may_publish,
    commercial_use_allowed,
    is_production_environment,
    load_policy,
    policy_for,
)
from local_drama.domain.errors import DomainRuleError

POLICY = {
    "schema_version": "localdrama.model-licensing.v1",
    "models": [
        {
            "code": "qwen-image-2.1-int8-convrot",
            "license_id": "qwen-research",
            "commercial_use": False,
            "authorization": {"recorded": False},
        },
        {
            "code": "qwen-image-2512-q5-k-m",
            "license_id": "apache-2.0",
            "commercial_use": True,
            "authorization": {"recorded": True},
        },
    ],
}


def _write(tmp_path: Path, payload: dict) -> Path:
    target = tmp_path / "model-licensing.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def test_repository_policy_record_matches_the_documented_licences() -> None:
    policy = load_policy()
    qwen21 = policy_for(policy, "qwen-image-2.1-int8-convrot")
    assert qwen21 is not None
    assert qwen21["license_id"] == "qwen-research"
    assert commercial_use_allowed(policy, "qwen-image-2.1-int8-convrot") is False
    # The retained production chain stays Apache-2.0 and authorised.
    assert commercial_use_allowed(policy, "qwen-image-2512-q5-k-m") is True
    assert commercial_use_allowed(policy, "qwen-image-edit-2511-q5-k-m") is True


def test_production_environment_detection() -> None:
    assert is_production_environment("production") is True
    assert is_production_environment("PROD") is True
    assert is_production_environment("development") is False
    assert is_production_environment(None) is False


def test_unauthorised_model_is_refused_in_production(tmp_path: Path) -> None:
    path = _write(tmp_path, POLICY)
    with pytest.raises(DomainRuleError) as blocked:
        assert_may_publish("production", "qwen-image-2.1-int8-convrot", policy_path=path)
    assert blocked.value.code == "MODEL_LICENSE_COMMERCIAL_USE_UNAUTHORIZED"
    assert blocked.value.details["license_id"] == "qwen-research"


def test_unauthorised_model_is_allowed_outside_production(tmp_path: Path) -> None:
    path = _write(tmp_path, POLICY)
    result = assert_may_publish("development", "qwen-image-2.1-int8-convrot", policy_path=path)
    assert result["commercial_use_authorized"] is False
    assert result["acknowledged_research_only"] is False


def test_explicit_acknowledgement_permits_research_only_publication(tmp_path: Path) -> None:
    path = _write(tmp_path, POLICY)
    result = assert_may_publish("production", "qwen-image-2.1-int8-convrot", policy_path=path, acknowledged=True)
    # Acknowledging records scope; it never claims the licence was widened.
    assert result["commercial_use_authorized"] is False
    assert result["acknowledged_research_only"] is True


def test_authorised_model_publishes_without_acknowledgement(tmp_path: Path) -> None:
    path = _write(tmp_path, POLICY)
    result = assert_may_publish("production", "qwen-image-2512-q5-k-m", policy_path=path)
    assert result["commercial_use_authorized"] is True
    assert result["acknowledged_research_only"] is False


def test_unknown_model_fails_closed(tmp_path: Path) -> None:
    path = _write(tmp_path, POLICY)
    with pytest.raises(DomainRuleError) as blocked:
        assert_may_publish("development", "some-unvetted-model", policy_path=path)
    assert blocked.value.code == "MODEL_LICENSE_MODEL_UNKNOWN"


def test_missing_and_invalid_policy_are_reported(tmp_path: Path) -> None:
    with pytest.raises(DomainRuleError) as missing:
        load_policy(tmp_path / "absent.json")
    assert missing.value.code == "MODEL_LICENSE_POLICY_MISSING"

    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "wrong"}', encoding="utf-8")
    with pytest.raises(DomainRuleError) as invalid:
        load_policy(bad)
    assert invalid.value.code == "MODEL_LICENSE_POLICY_INVALID"


def test_commercial_use_requires_recorded_authorization(tmp_path: Path) -> None:
    """`commercial_use: true` alone is not enough; the authorization must be recorded."""

    payload = json.loads(json.dumps(POLICY))
    payload["models"][0]["commercial_use"] = True
    payload["models"][0]["authorization"] = {"recorded": False}
    assert commercial_use_allowed(payload, "qwen-image-2.1-int8-convrot") is False
