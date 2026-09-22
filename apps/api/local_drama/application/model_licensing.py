"""Model licence policy: what a deployment is actually allowed to use.

Qwen-Image-2.1 ships under the Qwen Research License, whose definition of
non-commercial use is research or evaluation; commercial use needs a separate
licence.  The previous Qwen-Image-2512 / Edit-2511 chain is Apache-2.0.

That is a *deployment scope* decision, not a claim about what the GPU can run.
This module is the single place that answers "may this model be the commercial
production default on this instance", so the answer cannot drift between the
onboarding scripts, the API, and the documentation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError

SCHEMA_VERSION = "localdrama.model-licensing.v1"
DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[4] / "config" / "model-licensing.json"

# Environments in which a model becomes the production default.  Anything else
# (development, evaluation, test) is not treated as commercial production.
_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


def load_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_POLICY_PATH
    if not target.is_file():
        raise DomainRuleError(
            "MODEL_LICENSE_POLICY_MISSING",
            "缺少模型许可策略文件，无法判定商用授权状态。",
            {"path": str(target)},
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DomainRuleError("MODEL_LICENSE_POLICY_INVALID", "模型许可策略不是合法 JSON。", {"path": str(target)}) from error
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise DomainRuleError("MODEL_LICENSE_POLICY_INVALID", "模型许可策略 schema 无效。", {"path": str(target)})
    return payload


def policy_for(policy: Mapping[str, Any], model_code: str) -> dict[str, Any] | None:
    for entry in policy.get("models", []):
        if isinstance(entry, Mapping) and str(entry.get("code")) == model_code:
            return {str(key): value for key, value in entry.items()}
    return None


def commercial_use_allowed(policy: Mapping[str, Any], model_code: str) -> bool:
    """True only when the policy explicitly records commercial authorization."""

    entry = policy_for(policy, model_code)
    if entry is None:
        return False
    if not bool(entry.get("commercial_use")):
        return False
    authorization = entry.get("authorization")
    return isinstance(authorization, Mapping) and bool(authorization.get("recorded"))


def is_production_environment(environment: str | None) -> bool:
    return str(environment or "").strip().casefold() in _PRODUCTION_ENVIRONMENTS


def assert_may_publish(
    environment: str | None,
    model_code: str,
    *,
    policy_path: Path | None = None,
    acknowledged: bool = False,
) -> dict[str, Any]:
    """Fail closed when a model without commercial authorization would ship.

    ``acknowledged=True`` is an explicit operator override that must be passed
    on the command line; it never widens the model's licence, it only records
    that the operator accepts research/evaluation-only scope.
    """

    policy = load_policy(policy_path)
    entry = policy_for(policy, model_code)
    if entry is None:
        raise DomainRuleError(
            "MODEL_LICENSE_MODEL_UNKNOWN",
            "该模型没有许可记录，不能发布为生产默认。",
            {"model_code": model_code},
        )
    allowed = commercial_use_allowed(policy, model_code)
    if allowed or not is_production_environment(environment) or acknowledged:
        return {
            "model_code": model_code,
            "license_id": entry.get("license_id"),
            "commercial_use_authorized": allowed,
            "environment": environment,
            "acknowledged_research_only": bool(acknowledged and not allowed),
        }
    raise DomainRuleError(
        "MODEL_LICENSE_COMMERCIAL_USE_UNAUTHORIZED",
        "该模型未记录商用授权，不能在生产环境发布为默认 Profile。",
        {
            "model_code": model_code,
            "license_id": entry.get("license_id"),
            "license_url": entry.get("license_url"),
            "environment": environment,
        },
        suggested_action="记录授权后重试，或使用 --acknowledge-research-only 明确限定为研究/评估用途。",
    )
