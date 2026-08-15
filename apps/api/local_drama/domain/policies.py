from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import DomainRuleError
from .generation_contracts import CameraPlan

REQUIRED_SHOT_FIELDS = (
    "shot_type",
    "composition",
    "subject_action",
    "camera_plan",
    "target_duration_ms",
    "dialogue",
    "environment",
    "continuity",
    "creative_intent",
)

VALID_PROJECT_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ACTIVE"},
    "ACTIVE": {"PAUSED", "ARCHIVED"},
    "PAUSED": {"ACTIVE", "ARCHIVED"},
    "ARCHIVED": {"ACTIVE"},
}

VALID_SHOT_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"DIRECTED"},
    "DIRECTED": {"READY"},
    "READY": {"GENERATING"},
    "GENERATING": {"REVIEW", "BLOCKED"},
    "REVIEW": {"READY", "APPROVED"},
    "BLOCKED": {"READY"},
    "APPROVED": {"READY"},
}


def require_transition(graph: dict[str, set[str]], current: str, target: str, entity: str) -> None:
    if target not in graph.get(current, set()):
        raise DomainRuleError(
            "INVALID_STATE_TRANSITION",
            f"{entity} 不允许从 {current} 转为 {target}",
            {"entity": entity, "current": current, "target": target},
        )


def validate_project_code(code: str) -> None:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code):
        raise DomainRuleError("INVALID_PROJECT_CODE", "项目 code 必须是 2—64 位小写 ASCII、数字或下划线")


def validate_project_spec(*, episode_count: int, aspect_ratio: str | None, fps_num: int | None, fps_den: int | None,
                          allow_unconfigured: bool, season_count: int = 1, width: int | None = None, height: int | None = None,
                          primary_language: str | None = None, subtitle_mode: str | None = None,
                          subtitle_language: str | None = None) -> None:
    if episode_count < 1 or season_count < 1:
        raise DomainRuleError("INVALID_EPISODE_COUNT", "season_count 与 episode_count 必须大于 0")
    if episode_count * season_count > 10_000:
        raise DomainRuleError("PROJECT_STRUCTURE_TOO_LARGE", "项目季集总数不能超过 10000")
    if (width is None) != (height is None) or (width is not None and (width < 64 or height is None or height < 64)):
        raise DomainRuleError("INVALID_PRODUCTION_RESOLUTION", "制作分辨率必须同时提供有效 width 与 height")
    if subtitle_mode not in {None, "NONE", "SIDECAR", "BURN_IN", "BOTH"}:
        raise DomainRuleError("INVALID_SUBTITLE_MODE", "subtitle_mode 必须是 NONE/SIDECAR/BURN_IN/BOTH")
    if subtitle_mode not in {None, "NONE"} and not subtitle_language:
        raise DomainRuleError("SUBTITLE_LANGUAGE_REQUIRED", "启用字幕时必须显式选择字幕语言")
    if not allow_unconfigured and not aspect_ratio:
        raise DomainRuleError("PRODUCTION_SPEC_REQUIRED", "必须显式选择制作画幅或明确允许稍后配置")
    if not allow_unconfigured and (not fps_num or not fps_den or fps_num <= 0 or fps_den <= 0):
        raise DomainRuleError("PRODUCTION_SPEC_REQUIRED", "必须显式选择有理数 fps 或明确允许稍后配置")
    if not allow_unconfigured and (width is None or height is None or not primary_language or not subtitle_mode):
        raise DomainRuleError("PRODUCTION_SPEC_REQUIRED", "必须显式选择分辨率、主语言和字幕策略或明确允许稍后配置")


def missing_shot_fields(fields: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_SHOT_FIELDS:
        value = fields.get(field)
        if field == "camera_plan":
            try:
                CameraPlan.from_payload(value)
            except DomainRuleError:
                missing.append(field)
        elif value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip() and field not in {"dialogue", "environment"}:
            missing.append(field)
    return missing


def validate_shot_ready(fields: dict[str, object]) -> None:
    missing = missing_shot_fields(fields)
    if missing:
        raise DomainRuleError(
            "SHOT_NOT_PRODUCTION_READY",
            "镜头缺少生产必填字段",
            {"missing_fields": missing},
        )
    camera_plan = CameraPlan.from_payload(fields["camera_plan"])
    if camera_plan.mode == "UNSUPPORTED":
        raise DomainRuleError(
            "CAMERA_PLAN_UNSUPPORTED",
            "当前已发布 Profile 不支持该结构化运镜，不能标记为 Production Ready",
            {"profile_version_id": camera_plan.profile_version_id, "movement": camera_plan.movement},
        )


def validate_review_approval(checks: list[dict[str, str]], required_item_ids: set[str]) -> None:
    by_id = {check.get("item_id"): check.get("result") for check in checks}
    missing = sorted(required_item_ids - set(by_id))
    failed = sorted(item_id for item_id, result in by_id.items() if item_id and result != "PASS")
    if missing or failed:
        raise DomainRuleError(
            "REVIEW_REQUIRED_CHECK_FAILED",
            "存在未通过或未完成的必填检查，不能批准",
            {"missing_items": missing, "failed_items": failed},
        )


def validate_delivery_source(*, render_approved: bool, target_selected: bool, integrity_ok: bool) -> None:
    if not render_approved:
        raise DomainRuleError("DELIVERY_SOURCE_NOT_APPROVED", "交付只能引用已批准的整集渲染版本")
    if not target_selected:
        raise DomainRuleError("DELIVERY_TARGET_REQUIRED", "必须显式选择 DeliveryTargetVersion")
    if not integrity_ok:
        raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "源媒体完整性失败，不能构建交付")


def validate_local_transport(transport: str, base_url: str | None = None) -> None:
    if transport == "REMOTE_HTTP_SERVICE":
        raise DomainRuleError("REMOTE_PROVIDER_DISABLED_IN_LOCAL_RELEASE", "首版 LOCAL_ONLY 不允许 REMOTE transport")
    if transport == "LOOPBACK_HTTP" and base_url:
        from urllib.parse import urlparse

        hostname = urlparse(base_url).hostname
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise DomainRuleError("LOOPBACK_ONLY", "本地 Runtime base_url 只能指向 loopback")


@dataclass(frozen=True)
class VariantInput:
    role: str
    media_version_id: str
    ordinal: int = 0
    weight: float | None = None

    def validate(self) -> None:
        if not self.role.strip() or not self.media_version_id.strip():
            raise DomainRuleError("VARIANT_INPUT_REQUIRED", "Variant 输入必须包含语义 role 和 MediaVersion")
        if self.ordinal < 0:
            raise DomainRuleError("VARIANT_INPUT_ORDINAL_INVALID", "Variant 输入 ordinal 必须是非负整数")
        if self.weight is not None and (not math.isfinite(self.weight) or not 0.0 <= self.weight <= 1.0):
            raise DomainRuleError("VARIANT_INPUT_WEIGHT_INVALID", "Variant 输入 weight 必须是 0—1 之间的有限数")


def validate_variant_lineage(parent_id: str | None, variant_id: str, ancestors: set[str]) -> None:
    if parent_id == variant_id or variant_id in ancestors:
        raise DomainRuleError("VARIANT_LINEAGE_CYCLE", "GenerationVariant 谱系不能形成环")


def validate_binding_roles(bindings: list[VariantInput], allowed_roles: set[str]) -> None:
    unknown = sorted({binding.role for binding in bindings} - allowed_roles)
    if unknown:
        raise DomainRuleError(
            "UNSUPPORTED_INPUT_ROLE",
            "Profile 不支持一个或多个输入角色",
            {"unknown_roles": unknown},
        )
