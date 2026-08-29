"""Strict, immutable input/output contract for a low-cost Comfy smoke run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError

_SCHEMA_VERSION = "localdramastudio.comfy-smoke-contract.v1"
_MEDIA_KINDS = frozenset({"IMAGE", "VIDEO", "AUDIO"})


@dataclass(frozen=True, slots=True)
class ComfySmokeContract:
    """The only graph inputs a capability smoke may materialize."""

    semantic_inputs: Mapping[str, str | int | float | bool]
    timeout_seconds: int
    media_kind: str
    min_count: int
    max_count: int
    content_hash: str


def parse_comfy_smoke_contract(contract: Mapping[str, Any], node_bindings: Mapping[str, Any]) -> ComfySmokeContract:
    """Parse one intentionally small, local-only graph smoke contract.

    This parser is used at workflow registration and again before a future
    Worker run.  It does not accept paths, URLs, arbitrary JSON, or inputs not
    represented by immutable semantic node bindings.
    """

    raw = contract.get("smoke_contract") if isinstance(contract, Mapping) else None
    if not isinstance(raw, Mapping):
        raise DomainRuleError("WORKFLOW_SMOKE_CONTRACT_REQUIRED", "Comfy 能力冒烟需要工作流声明受控 smoke_contract。")
    allowed = {"schema_version", "semantic_inputs", "timeout_seconds", "expected_output"}
    unexpected = sorted(str(key) for key in raw if key not in allowed)
    if unexpected or raw.get("schema_version") != _SCHEMA_VERSION:
        raise DomainRuleError("WORKFLOW_SMOKE_CONTRACT_INVALID", "smoke_contract schema 或字段无效。", {"unexpected_fields": unexpected})
    inputs = raw.get("semantic_inputs")
    if not isinstance(inputs, Mapping) or not inputs or len(inputs) > 8:
        raise DomainRuleError("WORKFLOW_SMOKE_CONTRACT_INVALID", "smoke_contract 必须提供 1—8 个受控语义输入。")
    normalized_inputs: dict[str, str | int | float | bool] = {}
    for raw_role, value in inputs.items():
        role = str(raw_role).strip()
        if not role or role not in node_bindings:
            raise DomainRuleError("WORKFLOW_SMOKE_INPUT_UNBOUND", "smoke_contract 只能写入已有的语义 node binding。", {"role": role})
        if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not value.isfinite():
            raise DomainRuleError("WORKFLOW_SMOKE_INPUT_INVALID", "smoke 输入只允许有限的字符串、数字或布尔值。", {"role": role})
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or len(cleaned) > 512 or _looks_like_locator(cleaned):
                raise DomainRuleError("WORKFLOW_SMOKE_INPUT_INVALID", "smoke 文本不得为空、过长或包含文件/网络定位符。", {"role": role})
            normalized_inputs[role] = cleaned
        else:
            normalized_inputs[role] = value
    required_slots = contract.get("input_slots")
    if isinstance(required_slots, Mapping):
        missing = [
            str(role)
            for role, specification in required_slots.items()
            if isinstance(specification, Mapping) and specification.get("required", True) and str(role) not in normalized_inputs
        ]
        if missing:
            raise DomainRuleError("WORKFLOW_SMOKE_INPUT_REQUIRED", "smoke_contract 必须覆盖工作流合同的必需语义输入。", {"missing_roles": sorted(missing)})
    timeout_seconds = raw.get("timeout_seconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 5 <= timeout_seconds <= 300:
        raise DomainRuleError("WORKFLOW_SMOKE_TIMEOUT_INVALID", "smoke timeout_seconds 必须为 5—300 秒。")
    expected_output = raw.get("expected_output")
    if not isinstance(expected_output, Mapping) or set(expected_output) != {"media_kind", "min_count", "max_count"}:
        raise DomainRuleError("WORKFLOW_SMOKE_OUTPUT_INVALID", "smoke expected_output 必须完整声明 media_kind、min_count、max_count。")
    media_kind = str(expected_output.get("media_kind") or "").upper()
    min_count, max_count = expected_output.get("min_count"), expected_output.get("max_count")
    if media_kind not in _MEDIA_KINDS or not isinstance(min_count, int) or isinstance(min_count, bool) or not isinstance(max_count, int) or isinstance(max_count, bool) or not 1 <= min_count <= max_count <= 4:
        raise DomainRuleError("WORKFLOW_SMOKE_OUTPUT_INVALID", "smoke 输出类型或数量边界无效。")
    canonical = {
        "schema_version": _SCHEMA_VERSION,
        "semantic_inputs": normalized_inputs,
        "timeout_seconds": timeout_seconds,
        "expected_output": {"media_kind": media_kind, "min_count": min_count, "max_count": max_count},
    }
    return ComfySmokeContract(
        semantic_inputs=normalized_inputs,
        timeout_seconds=timeout_seconds,
        media_kind=media_kind,
        min_count=min_count,
        max_count=max_count,
        content_hash=hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    )


def has_comfy_smoke_contract(contract: Mapping[str, Any]) -> bool:
    return isinstance(contract, Mapping) and "smoke_contract" in contract


def _looks_like_locator(value: str) -> bool:
    lower = value.casefold()
    return (
        ":\\" in value
        or value.startswith(("/", "\\"))
        or "http://" in lower
        or "https://" in lower
        or "file://" in lower
    )
