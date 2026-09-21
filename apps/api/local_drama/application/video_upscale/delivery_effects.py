from __future__ import annotations

from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError


def inherited_applied_effects(input_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve effects already baked into an immutable render.

    Super-resolution snapshots carry the exact source descriptor.  The
    top-level copy is retained so older readers do not need to understand the
    whole source schema.
    """

    value = input_snapshot.get("applied_effects")
    if not isinstance(value, Mapping):
        source = input_snapshot.get("source_descriptor")
        value = source.get("applied_effects") if isinstance(source, Mapping) else None
    effects = dict(value) if isinstance(value, Mapping) else {}
    if "subtitle_burned" not in effects and "subtitle_burned_in" in input_snapshot:
        effects["subtitle_burned"] = bool(input_snapshot.get("subtitle_burned_in"))
    effects.setdefault("subtitle_burned", False)
    effects.setdefault("watermark_profile_snapshot", None)
    return effects


def resolve_delivery_effect_application(
    *,
    input_snapshot: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    watermark_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Reject destructive effect changes and identify effects to reuse.

    Burned subtitles and watermarks are pixels and cannot be removed or
    replaced during packaging.  An identical requested watermark is reused
    rather than rendered for a second time.
    """

    inherited = inherited_applied_effects(input_snapshot)
    subtitle_mode = str(target_spec.get("subtitles") or "NONE").upper()
    wants_burned_subtitle = subtitle_mode in {"BURN_IN", "BOTH"}
    inherited_subtitle = bool(inherited.get("subtitle_burned"))
    if inherited_subtitle and not wants_burned_subtitle:
        raise DomainRuleError(
            "DELIVERY_BURNED_SUBTITLE_CONFLICT",
            "源画面已烧录字幕，当前目标要求去除烧录字幕；请选择干净的 COMPOSE 源重新超分",
            {"source_effect": "SUBTITLE_BURNED", "target_subtitles": subtitle_mode},
        )

    inherited_watermark = inherited.get("watermark_profile_snapshot")
    inherited_watermark = dict(inherited_watermark) if isinstance(inherited_watermark, Mapping) else None
    requested_watermark = dict(watermark_snapshot) if isinstance(watermark_snapshot, Mapping) else None
    if inherited_watermark is not None:
        inherited_id = str(inherited_watermark.get("id") or "")
        requested_id = str((requested_watermark or {}).get("id") or "")
        if not requested_id:
            raise DomainRuleError(
                "DELIVERY_BURNED_WATERMARK_CONFLICT",
                "源画面已烧录水印，当前目标要求去除水印；请选择干净的 COMPOSE 源重新超分",
                {"source_watermark_profile_id": inherited_id or None},
            )
        if not inherited_id or inherited_id != requested_id:
            raise DomainRuleError(
                "DELIVERY_BURNED_WATERMARK_CONFLICT",
                "源画面已烧录不同水印，不能在交付阶段替换；请选择干净的 COMPOSE 源重新超分",
                {
                    "source_watermark_profile_id": inherited_id or None,
                    "target_watermark_profile_id": requested_id,
                },
            )

    return {
        "inherited": inherited,
        "subtitle": {
            "already_burned": inherited_subtitle,
            "target_requires_burned": wants_burned_subtitle,
            "action": "REUSE" if inherited_subtitle and wants_burned_subtitle else "NONE",
        },
        "watermark": {
            "inherited_profile": inherited_watermark,
            "requested_profile": requested_watermark,
            "action": "REUSE" if inherited_watermark is not None else ("APPLY" if requested_watermark is not None else "NONE"),
        },
    }
