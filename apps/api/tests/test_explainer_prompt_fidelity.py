"""The shipped prompt templates must never drop a rule from the design docs.

The seven ``prompts/*.prompt.md`` files are the design-state wording of every model
call.  The implementation kept them verbatim, and an audit against a real local model
showed where the wording is *under*-specified (entity typing, claim↔entity linkage,
``importance``), so the templates may grow clarifying sentences — but a rule the design
states must never disappear, because that would silently widen what the model may do.

This test compares line by line: every non-empty line of the design's System/User block
must appear in the shipped template.  Additions are allowed and listed in
``docs/解说工厂/实施交付记录.md``; deletions fail here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from local_drama.application.explainers import contracts_v2 as contracts

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPTS = REPO_ROOT / "docs" / "解说工厂" / "解说工厂-改造设计完整包" / "解说工厂改造设计" / "prompts"

FILES = {
    "content-extract.v2": "01-content-extract.prompt.md",
    "preserved-script-annotations.v1": "02-preserved-script-annotations.prompt.md",
    "script-draft.v2": "03-script-draft.prompt.md",
    "storyboard.v2": "04-storyboard.prompt.md",
    "candidate-review.v1": "05-candidate-review.prompt.md",
    "reference-design.v1": "06-reference-design.prompt.md",
    "fiction-seed.v1": "07-fiction-seed.prompt.md",
}


def _blocks(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    found = re.findall(r"\*\*(System|User)\*\*\s*\n+```text\n(.*?)```", text, flags=re.DOTALL)
    result: dict[str, str] = {}
    for name, body in found:
        result[name] = body.strip()
    return result


def _lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


@pytest.mark.parametrize("contract", sorted(FILES))
def test_the_shipped_template_keeps_every_design_rule(contract: str) -> None:
    design = _blocks(PROMPTS / FILES[contract])
    system, user = contracts.PROMPT_TEMPLATES_V2[contract]
    for label, ours in (("System", system), ("User", user)):
        design_lines = _lines(design[label])
        ours_set = set(_lines(ours))
        missing = [line for line in design_lines if line not in ours_set]
        assert not missing, (
            f"{contract} 的 {label} 提示词丢失了设计文档中的规则：{missing[:2]}"
        )


@pytest.mark.parametrize("contract", sorted(FILES))
def test_the_shipped_template_does_not_add_a_second_persona(contract: str) -> None:
    """A clarification may add rules, never a second role or a new output contract."""

    system, _user = contracts.PROMPT_TEMPLATES_V2[contract]
    assert system.count("你是") == 1
    assert contracts.PROMPT_TEMPLATES_V2[contract][0].strip().startswith("你是")
