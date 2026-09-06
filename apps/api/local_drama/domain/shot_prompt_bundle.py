"""Deterministic, auditable prompt bundles for shot generation.

The page may edit the positive and negative portions of a prompt, but the
shot facts (``base_prompt``) are always compiled by the application from the
current DirectorIntent.  A bundle is then compiled against the published
workflow's semantic bindings so the actual executable prompt is explicit:
workflows with a negative input receive it separately; workflows without one
receive an ``Avoid:`` section in ``PROMPT``.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import DomainRuleError

PROMPT_BUNDLE_SCHEMA_VERSION = "localdrama.prompt-bundle.v1"
PROMPT_PROVENANCE_AI = "AI_GENERATED"
PROMPT_PROVENANCE_PAGE = "PAGE_USER_EDIT"
PROMPT_COMPILER_WORKFLOW_NEGATIVE = "WORKFLOW_NEGATIVE_BINDING"
PROMPT_COMPILER_AVOID_FALLBACK = "PROMPT_AVOID_FALLBACK"
PROMPT_FRAME_REFRAME_NONE = "NONE"
PROMPT_FRAME_REFRAME_SINGLE_MOMENT = "SINGLE_MOMENT"
PROMPT_FRAME_ROLES = frozenset({"FIRST_FRAME", "END_FRAME"})

# These constraints are intentionally visible in the editable page field and
# retained in every execution snapshot.  They address the observed Qwen
# contact-sheet/triptych failure mode without pretending that a model can be
# forced to obey a hidden field that its workflow does not bind.
DEFAULT_SHOT_NEGATIVE_PROMPT = (
    "multi-panel, triptych, contact sheet, storyboard, split-screen, collage, "
    "multi-panel layout, text layout, captions, subtitles, watermark"
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _negative_prompt(value: object) -> str:
    text = _text(value)
    return text or DEFAULT_SHOT_NEGATIVE_PROMPT


def normalize_frame_reframe_mode(value: object) -> str:
    """Normalize the page-visible frame prompt policy.

    The default remains ``NONE`` for old callers and stored requests.  The
    director page explicitly opts into ``SINGLE_MOMENT`` for first/end-frame
    redraws so a legacy client cannot silently change the meaning of an old
    command.
    """

    mode = _text(value) or PROMPT_FRAME_REFRAME_NONE
    if mode not in {PROMPT_FRAME_REFRAME_NONE, PROMPT_FRAME_REFRAME_SINGLE_MOMENT}:
        raise DomainRuleError("SHOT_PROMPT_FRAME_REFRAME_INVALID", "首尾帧画面重构策略无效")
    return mode


def compile_shot_prompt_bundle(
    raw: Mapping[str, Any] | None = None,
    *,
    base_prompt: str | None = None,
    effective_base_prompt: str | None = None,
    frame_role: str | None = None,
    frame_reframe_mode: str | None = None,
    workflow_bindings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical prompt bundle for a shot generation command.

    ``base_prompt`` is the original AI/shot fact and is never replaced.  A
    frame planner may supply ``effective_base_prompt`` for a role-specific
    single-image rewrite; this is the only text sent to the executable
    workflow while both values remain auditable in the returned bundle.
    """

    source = dict(raw or {})
    selected_base = _text(base_prompt) or _text(source.get("base_prompt"))
    if not selected_base:
        raise DomainRuleError(
            "SHOT_PROMPT_BASE_REQUIRED",
            "生成请求缺少 AI 基础提示词，不能提交",
        )
    selected_effective_base = (
        _text(effective_base_prompt)
        or _text(source.get("effective_base_prompt"))
        or selected_base
    )
    normalized_role = _text(frame_role) or _text(source.get("frame_role"))
    if normalized_role and normalized_role not in PROMPT_FRAME_ROLES:
        raise DomainRuleError("SHOT_PROMPT_FRAME_ROLE_INVALID", "首尾帧角色无效")
    reframe_mode = normalize_frame_reframe_mode(
        frame_reframe_mode if frame_reframe_mode is not None else source.get("frame_reframe_mode")
    )
    positive_override = _text(source.get("positive_override"))
    negative_prompt = _negative_prompt(source.get("negative_prompt"))
    provenance = _text(source.get("provenance")) or PROMPT_PROVENANCE_AI
    if provenance not in {PROMPT_PROVENANCE_AI, PROMPT_PROVENANCE_PAGE}:
        raise DomainRuleError("SHOT_PROMPT_PROVENANCE_INVALID", "提示词来源标记无效")
    positive = selected_effective_base
    if positive_override:
        positive = f"{positive}\n\n{positive_override}"
    has_negative_binding = bool(workflow_bindings and "NEGATIVE_PROMPT" in workflow_bindings)
    if has_negative_binding:
        compiler_mode = PROMPT_COMPILER_WORKFLOW_NEGATIVE
        final_prompt = positive
    else:
        compiler_mode = PROMPT_COMPILER_AVOID_FALLBACK
        final_prompt = f"{positive}\n\nAvoid: {negative_prompt}"
    return {
        "schema_version": PROMPT_BUNDLE_SCHEMA_VERSION,
        "base_prompt": selected_base,
        "effective_base_prompt": selected_effective_base,
        "frame_role": normalized_role or None,
        "frame_reframe_mode": reframe_mode,
        "positive_override": positive_override,
        "negative_prompt": negative_prompt,
        "provenance": provenance,
        "compiler_mode": compiler_mode,
        "final_prompt": final_prompt,
    }


def apply_prompt_bundle_to_parameters(
    parameter_set: Mapping[str, Any],
    bundle: Mapping[str, Any],
    workflow_bindings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Set executable prompt inputs while preserving unrelated parameters."""

    final_prompt = _text(bundle.get("final_prompt"))
    if not final_prompt:
        raise DomainRuleError("SHOT_PROMPT_FINAL_REQUIRED", "最终提示词为空，不能提交")
    result = dict(parameter_set)
    result["PROMPT"] = final_prompt
    if workflow_bindings and "NEGATIVE_PROMPT" in workflow_bindings:
        result["NEGATIVE_PROMPT"] = _negative_prompt(bundle.get("negative_prompt"))
    else:
        # An unbound semantic key would fail the published workflow contract;
        # the compiled Avoid section is the only executable negative channel.
        result.pop("NEGATIVE_PROMPT", None)
    return result
