"""Typed production contracts for camera and motion semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .errors import DomainRuleError


@dataclass(frozen=True)
class CameraPlan:
    mode: str
    shot_type: str
    movement: str
    prompt_text: str = ""
    direction: str = "UNSPECIFIED"
    intensity: float = 0.5
    curve: str = "LINEAR"
    profile_version_id: str | None = None

    def validate(self) -> None:
        if self.mode not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("CAMERA_PLAN_MODE_INVALID", "CameraPlan mode 无效")
        if not self.shot_type.strip() or not self.movement.strip():
            raise DomainRuleError("CAMERA_PLAN_REQUIRED", "CameraPlan 必须包含 shot_type 和 movement")
        if not self.direction.strip() or not self.curve.strip() or not 0.0 <= self.intensity <= 1.0:
            raise DomainRuleError("CAMERA_PLAN_SEMANTICS_INVALID", "CameraPlan 方向、强度和曲线无效")
        if self.mode == "PROMPT_FALLBACK" and not self.prompt_text.strip():
            raise DomainRuleError("CAMERA_PROMPT_REQUIRED", "CameraPlan prompt fallback 必须有显式 prompt")
        if self.mode == "UNSUPPORTED" and self.prompt_text.strip():
            raise DomainRuleError("CAMERA_UNSUPPORTED_PROMPT", "UNSUPPORTED CameraPlan 不得伪装为可执行 prompt")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> CameraPlan:
        if not isinstance(payload, dict):
            raise DomainRuleError("CAMERA_PLAN_STRUCTURED_REQUIRED", "CameraPlan 必须是结构化对象，不能是自由文本")
        try:
            plan = cls(
                mode=str(payload.get("mode", "")),
                shot_type=str(payload.get("shot_type", "")),
                movement=str(payload.get("movement", "")),
                prompt_text=str(payload.get("prompt_text", "")),
                direction=str(payload.get("direction", "")),
                intensity=float(payload.get("intensity", -1)),
                curve=str(payload.get("curve", "")),
                profile_version_id=str(payload["profile_version_id"]) if payload.get("profile_version_id") else None,
            )
        except (TypeError, ValueError) as error:
            raise DomainRuleError("CAMERA_PLAN_SEMANTICS_INVALID", "CameraPlan 字段类型无效") from error
        plan.validate()
        return plan


@dataclass(frozen=True)
class MotionMask:
    media_version_id: str
    subject_role: str
    invert: bool = False

    def validate(self) -> None:
        if not self.media_version_id.strip() or not self.subject_role.strip():
            raise DomainRuleError("MOTION_MASK_REQUIRED", "MotionMask 必须绑定媒体版本和 subject role")


@dataclass(frozen=True)
class TimedDirection:
    time_us: int
    direction: str
    strength: float

    def validate(self) -> None:
        if self.time_us < 0:
            raise DomainRuleError("TIMED_DIRECTION_TIME_INVALID", "TimedDirection 时间必须是非负整数微秒")
        if not self.direction.strip() or not 0.0 <= self.strength <= 1.0:
            raise DomainRuleError("TIMED_DIRECTION_VALUE_INVALID", "TimedDirection direction/strength 无效")


@dataclass(frozen=True)
class PerformanceBinding:
    actor_id: str
    action: str
    start_us: int
    end_us: int
    binding_type: str = "CHARACTER_DRIVING"
    source_role: str | None = None

    def validate(self) -> None:
        if not self.actor_id.strip() or not self.action.strip() or self.start_us < 0 or self.end_us <= self.start_us:
            raise DomainRuleError("PERFORMANCE_BINDING_INVALID", "PerformanceBinding actor/action/time range 无效")
        if self.binding_type not in {
            "CHARACTER_DRIVING",
            "POSE",
            "ACTION",
            "LIP_SYNC",
            "FACE_DRIVING",
            "DRIVING_VIDEO",
            "AUDIO_GUIDE",
        }:
            raise DomainRuleError("PERFORMANCE_BINDING_TYPE_INVALID", "PerformanceBinding binding_type 无效")
        if self.source_role is not None and not self.source_role.strip():
            raise DomainRuleError("PERFORMANCE_BINDING_SOURCE_ROLE_INVALID", "PerformanceBinding source_role 不能为空")


def resolve_camera_plan(*, native_supported: bool, prompt_fallback_supported: bool, shot_type: str, movement: str,
                        prompt_text: str = "", direction: str = "UNSPECIFIED", intensity: float = 0.5,
                        curve: str = "LINEAR", profile_version_id: str | None = None) -> CameraPlan:
    if native_supported:
        plan = CameraPlan("NATIVE", shot_type, movement, prompt_text, direction, intensity, curve, profile_version_id)
    elif prompt_fallback_supported:
        plan = CameraPlan("PROMPT_FALLBACK", shot_type, movement, prompt_text or f"camera: {movement}", direction, intensity, curve, profile_version_id)
    else:
        plan = CameraPlan("UNSUPPORTED", shot_type, movement, "", direction, intensity, curve, profile_version_id)
    plan.validate()
    return plan
