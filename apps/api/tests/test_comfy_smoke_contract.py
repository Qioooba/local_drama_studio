from __future__ import annotations

import pytest

from local_drama.application.comfy_smoke_contract import parse_comfy_smoke_contract
from local_drama.domain.errors import DomainRuleError


def _contract() -> dict[str, object]:
    return {
        "capability": "IMAGE_CONCEPT",
        "input_slots": {"PROMPT": {"required": True}, "SEED": {"required": False}},
        "smoke_contract": {
            "schema_version": "localdramastudio.comfy-smoke-contract.v1",
            "semantic_inputs": {"PROMPT": "一只橙猫坐在窗边", "SEED": 7},
            "timeout_seconds": 90,
            "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
        },
    }


def test_comfy_smoke_contract_is_bounded_and_hashable() -> None:
    parsed = parse_comfy_smoke_contract(_contract(), {"PROMPT": {"node_id": "1", "input": "text"}, "SEED": {"node_id": "2", "input": "seed"}})
    assert parsed.semantic_inputs == {"PROMPT": "一只橙猫坐在窗边", "SEED": 7}
    assert parsed.timeout_seconds == 90
    assert parsed.media_kind == "IMAGE"
    assert len(parsed.content_hash) == 64


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value["smoke_contract"].__setitem__("semantic_inputs", {"PROMPT": "C:\\private"}), "WORKFLOW_SMOKE_INPUT_INVALID"),
        (lambda value: value["smoke_contract"].__setitem__("timeout_seconds", 301), "WORKFLOW_SMOKE_TIMEOUT_INVALID"),
        (lambda value: value["smoke_contract"].__setitem__("expected_output", {"media_kind": "IMAGE", "min_count": 0, "max_count": 1}), "WORKFLOW_SMOKE_OUTPUT_INVALID"),
    ],
)
def test_comfy_smoke_contract_rejects_unbounded_or_unsafe_facts(mutate, code: str) -> None:
    contract = _contract()
    mutate(contract)
    with pytest.raises(DomainRuleError) as error:
        parse_comfy_smoke_contract(contract, {"PROMPT": {"node_id": "1", "input": "text"}, "SEED": {"node_id": "2", "input": "seed"}})
    assert error.value.code == code
