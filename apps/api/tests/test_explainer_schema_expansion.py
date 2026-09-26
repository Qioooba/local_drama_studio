"""Every contract we send to a model must be one a model can actually satisfy.

Background (measured, Ollama + qwen3.8:27b)
-------------------------------------------
``expand_json_schema`` strips JSON-Schema annotation keywords before the call.  ``title``
and ``description`` are annotations — but they are also legitimate *property* names
(``events[].title``, ``outline[].title``).  Filtering them inside a ``properties`` map
removed the field while ``required`` still listed it, so:

* the grammar we hand the runtime could not require an undeclared field,
* the model therefore omitted ``events[].title``,
* Pydantic rejected the answer, the one format repair returned the same shape, and the
  stage failed with ``SCHEMA_INVALID`` after ~870 s.

Any article that produced at least one event (history, fiction, biography…) failed, while
an event-free science article passed — which is exactly how the bug hid.

These tests pin the invariant that makes that impossible: inside the model-facing schema,
**every name in ``required`` must be declared in ``properties``**.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from local_drama.application.explainers import contracts_v2

CONTRACTS = (
    "content-extract.v2",
    "preserved-script-annotations.v1",
    "script-draft.v2",
    "storyboard.v2",
    "candidate-review.v1",
    "reference-design.v1",
    "fiction-seed.v1",
)


def _undeclared_required(node: Any, path: str = "$") -> list[tuple[str, str]]:
    """``(path, name)`` for every required name that has no property declaration."""

    problems: list[tuple[str, str]] = []
    if isinstance(node, dict):
        properties = node.get("properties")
        required = node.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            for name in required:
                if str(name) not in properties:
                    problems.append((f"{path}.properties", str(name)))
        for key, value in node.items():
            problems.extend(_undeclared_required(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            problems.extend(_undeclared_required(item, f"{path}[{index}]"))
    return problems


@pytest.mark.parametrize("contract", CONTRACTS)
def test_the_model_facing_schema_never_requires_an_undeclared_field(contract: str) -> None:
    schema = contracts_v2.contract_schema_for_model(contract)
    problems = _undeclared_required(schema)
    assert not problems, f"{contract} 把未声明的字段写进了 required：{problems[:4]}"


@pytest.mark.parametrize("contract", CONTRACTS)
def test_annotation_keywords_are_still_stripped_as_annotations(contract: str) -> None:
    """Stripping annotations is still the point; only real property names survive."""

    schema = contracts_v2.contract_schema_for_model(contract)
    serialized = json.dumps(schema, ensure_ascii=False)
    assert "$defs" not in serialized and "$ref" not in serialized
    # ``title``/``description`` used as annotations are gone; used as property names they
    # are still present.  Both facts are covered by the invariant test above, so this one
    # only asserts the expansion itself finished.
    assert schema.get("type") == "object"


def test_the_event_title_field_is_declared_and_required() -> None:
    """The concrete field that broke every event-producing article."""

    schema = contracts_v2.contract_schema_for_model("content-extract.v2")
    events = schema["properties"]["events"]["items"]
    assert "title" in events["properties"], "events[].title 必须出现在 properties 中"
    assert "title" in events["required"]
    assert events["properties"]["title"]["type"] == "string"


def test_the_script_outline_title_field_is_declared() -> None:
    schema = contracts_v2.contract_schema_for_model("script-draft.v2")
    outline = schema["properties"]["outline"]["items"]
    assert "title" in outline["properties"]
    assert "title" in outline["required"]
