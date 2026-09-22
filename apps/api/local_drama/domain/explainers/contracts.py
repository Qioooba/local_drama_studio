"""Explainer factory domain contracts.

Pure data contracts for the 解说工厂 (Explainer Factory) business domain.  This
module owns enums, invariants and cheap deterministic helpers only: no HTTP, no
SQLite, no downloads, no model calls (《源码接入与开发任务清单》§3).

The contracts encode the design decisions that must not drift:

* ``projects.product_kind`` is ``DRAMA`` or ``EXPLAINER``; an explainer project
  owns exactly one :class:`ExplainerVideo`.
* Run lifecycle is ``QUEUED -> PREFLIGHT -> RUNNING -> QC_RUNNING ->
  READY_TO_EXPORT -> EXPORTING -> COMPLETED`` with the documented side states.
* Only :data:`DecisionKind.POLICY_ACCEPTED` may be produced by a machine, and it
  always carries a ``policy_processor``; only ``HUMAN_APPROVED`` carries a real
  actor.  Publication authorization is recorded separately.
* The schedule de-duplication key is exactly ``(schedule_id, scheduled_for)``.
  ``config_revision`` is a snapshot and never part of the key.
* ``preserve_human_locks`` and ``preserve_must_be_motion`` are structural
  invariants, not tunable options.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "localdrama.explainer.v1"

#: Documented target duration presets (minutes).  ``None`` entries are custom.
SUPPORTED_TARGET_MINUTES: tuple[int, ...] = (3, 5, 10, 20, 30)

MIN_TARGET_SECONDS = 30
MAX_TARGET_SECONDS = 7200

#: Default natural-narration tolerance (《解说工厂完整设计与开发规范》§7).
DEFAULT_NATURAL_TOLERANCE_PERCENT = 5.0

MIN_SPEAKING_RATE_CHARS_PER_SECOND = 2.0
MAX_SPEAKING_RATE_CHARS_PER_SECOND = 12.0

_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_EDITION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExplainerContractError(ValueError):
    """A caller-supplied explainer payload violates a domain invariant."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


class ProductKind(StrEnum):
    DRAMA = "DRAMA"
    EXPLAINER = "EXPLAINER"


class ContentKind(StrEnum):
    FACTUAL_EXPLAINER = "FACTUAL_EXPLAINER"
    ORIGINAL_FICTION = "ORIGINAL_FICTION"


class InputKind(StrEnum):
    TOPIC = "TOPIC"
    PASTED_SCRIPT = "PASTED_SCRIPT"
    DOCUMENT_IMPORT = "DOCUMENT_IMPORT"
    REFERENCE_LINKS = "REFERENCE_LINKS"


class DurationMode(StrEnum):
    TARGET = "TARGET"
    FIXED = "FIXED"


class DurationPolicy(StrEnum):
    USE_SOURCE_TARGET = "USE_SOURCE_TARGET"
    NATURAL_NARRATION = "NATURAL_NARRATION"
    FIXED_FRAMES = "FIXED_FRAMES"


class AutomationMode(StrEnum):
    AUTO_WITH_EXCEPTIONS = "AUTO_WITH_EXCEPTIONS"
    REVIEW_BEFORE_RENDER = "REVIEW_BEFORE_RENDER"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class InferenceMode(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    ALLOW_CONFIGURED_CLOUD = "ALLOW_CONFIGURED_CLOUD"


class ResearchMode(StrEnum):
    OFFLINE_IMPORT = "OFFLINE_IMPORT"
    WEB_RESEARCH = "WEB_RESEARCH"


class AspectRatio(StrEnum):
    WIDE = "16:9"
    PORTRAIT = "9:16"
    THREE_FOUR = "3:4"
    SQUARE = "1:1"

    @property
    def pixels(self) -> tuple[int, int]:
        return _ASPECT_PIXELS[self]


_ASPECT_PIXELS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.WIDE: (1920, 1080),
    AspectRatio.PORTRAIT: (1080, 1920),
    AspectRatio.THREE_FOUR: (1080, 1440),
    AspectRatio.SQUARE: (1080, 1080),
}


class SubtitleMode(StrEnum):
    NONE = "NONE"
    BURNED = "BURNED"
    SOFT = "SOFT"
    #: Chinese voice with Chinese+English burned captions (design §11.2).
    BILINGUAL_BURNED = "BILINGUAL_BURNED"


class LocaleRole(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


class StatementType(StrEnum):
    FACT = "FACT"
    ORIGINAL_EXPLANATION = "ORIGINAL_EXPLANATION"
    TRANSITION = "TRANSITION"
    FICTION = "FICTION"
    QUESTION = "QUESTION"


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    DISPUTED = "DISPUTED"
    UNVERIFIED = "UNVERIFIED"
    EXCLUDED = "EXCLUDED"


class EvidenceStance(StrEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    CONTEXT = "CONTEXT"


class DatePrecision(StrEnum):
    UNKNOWN = "UNKNOWN"
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"
    MINUTE = "MINUTE"
    SECOND = "SECOND"


class CredibilityKind(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    AGGREGATOR = "AGGREGATOR"
    USER_GENERATED = "USER_GENERATED"
    AUTHORED_FICTION = "AUTHORED_FICTION"
    UNKNOWN = "UNKNOWN"


class RetrievalVia(StrEnum):
    OFFLINE_IMPORT = "OFFLINE_IMPORT"
    WEB_RESEARCH = "WEB_RESEARCH"
    USER_SUPPLIED = "USER_SUPPLIED"


class EntityType(StrEnum):
    REAL_PERSON = "REAL_PERSON"
    FICTIONAL_CHARACTER = "FICTIONAL_CHARACTER"
    GROUP = "GROUP"
    LOCATION = "LOCATION"
    PROP = "PROP"
    ORGANIZATION = "ORGANIZATION"
    CONCEPT = "CONCEPT"


class VisualFactuality(StrEnum):
    DOCUMENTED = "DOCUMENTED"
    RECONSTRUCTION = "RECONSTRUCTION"
    SYMBOLIC = "SYMBOLIC"
    FICTIONAL = "FICTIONAL"


class RenderType(StrEnum):
    STILL_MOTION = "STILL_MOTION"
    PARALLAX = "PARALLAX"
    I2V = "I2V"
    INFOGRAPHIC = "INFOGRAPHIC"
    LICENSED_MEDIA = "LICENSED_MEDIA"


class VisualFallback(StrEnum):
    I2V_TO_MOTION_STILL = "I2V_TO_MOTION_STILL"
    I2V_TO_INFORMATION_GRAPHIC = "I2V_TO_INFORMATION_GRAPHIC"
    APPROVED_LOWER_RESOURCE_PROFILE = "APPROVED_LOWER_RESOURCE_PROFILE"
    SIMPLER_MOTION = "SIMPLER_MOTION"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    QC_RUNNING = "QC_RUNNING"
    READY_TO_EXPORT = "READY_TO_EXPORT"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    WAITING_INPUT = "WAITING_INPUT"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


#: Forward progression of the run state machine.  Side states may be entered
#: from any active state and resolve back into the main line.
RUN_FORWARD: tuple[RunStatus, ...] = (
    RunStatus.QUEUED,
    RunStatus.PREFLIGHT,
    RunStatus.RUNNING,
    RunStatus.QC_RUNNING,
    RunStatus.READY_TO_EXPORT,
    RunStatus.EXPORTING,
    RunStatus.COMPLETED,
)
RUN_SIDE_STATES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.PAUSING,
        RunStatus.PAUSED,
        RunStatus.WAITING_INPUT,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
    }
)
RUN_TERMINAL_STATES: frozenset[RunStatus] = frozenset({RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED})


class StepStatus(StrEnum):
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class EditionStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    READY_TO_RENDER = "READY_TO_RENDER"
    RENDERING = "RENDERING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DecisionKind(StrEnum):
    POLICY_ACCEPTED = "POLICY_ACCEPTED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    PUBLICATION_AUTHORIZED = "PUBLICATION_AUTHORIZED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ActorType(StrEnum):
    MACHINE = "MACHINE"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class QcReportStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASS = "PASS"
    PASS_WITH_ISSUES = "PASS_WITH_ISSUES"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    STALE = "STALE"


class QcSubjectKind(StrEnum):
    COMPOSITION_RENDER = "COMPOSITION_RENDER"
    COMPOSITION_REVISION = "COMPOSITION_REVISION"
    NARRATION_ALIGNMENT = "NARRATION_ALIGNMENT"
    SUBTITLE_REVISION = "SUBTITLE_REVISION"
    VISUAL_BEAT = "VISUAL_BEAT"
    EDITION = "EDITION"
    SCRIPT_REVISION = "SCRIPT_REVISION"


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"
    UNKNOWN = "UNKNOWN"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    FIXING = "FIXING"
    CLOSED = "CLOSED"
    ACCEPTED_AS_IS = "ACCEPTED_AS_IS"
    SUPERSEDED = "SUPERSEDED"


#: Hard blockers from design §18.2: these cannot be waved through by a policy
#: decision, and they cannot be closed without evidence.
HARD_BLOCKER_KINDS: frozenset[str] = frozenset(
    {
        "MISSING_NARRATION",
        "NARRATION_SEGMENT_MISSING",
        "NARRATION_MISREAD_KEY_TERM",
        "NARRATION_MISREAD_NUMBER",
        "FACT_KEY_CONFLICT",
        "FACT_UNSUPPORTED_KEY_CLAIM",
        "SUBTITLE_TEXT_MISMATCH",
        "SUBTITLE_OUT_OF_SAFE_AREA",
        "SUBTITLE_STALE_TEXT",
        "MEDIA_CORRUPT",
        "MEDIA_MISSING",
        "LICENSE_SCOPE_UNVERIFIED",
        "IDENTITY_WRONG_CHARACTER",
        "SOURCE_EVIDENCE_MISSING",
    }
)

#: Soft hints from design §18.2: surface them, never trigger endless redraws.
SOFT_ISSUE_KINDS: frozenset[str] = frozenset(
    {
        "COMPOSITION_AESTHETIC",
        "PACING_SUGGESTION",
        "STYLE_MINOR_DRIFT",
        "MUSIC_MATCH_HINT",
        "FRAMING_HINT",
    }
)


class ScheduleOccurrenceKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class ScheduleOccurrenceStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"
    FAILED = "FAILED"
    MISSED = "MISSED"


class PublicationStatus(StrEnum):
    DRAFT = "DRAFT"
    BUILDING = "BUILDING"
    READY = "READY"
    NEEDS_MANUAL_PUBLISH = "NEEDS_MANUAL_PUBLISH"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLICATION_RESULT_UNKNOWN = "PUBLICATION_RESULT_UNKNOWN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    WITHDRAWN = "WITHDRAWN"


class PublicationReceiptStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    NEEDS_MANUAL_PUBLISH = "NEEDS_MANUAL_PUBLISH"
    PUBLICATION_RESULT_UNKNOWN = "PUBLICATION_RESULT_UNKNOWN"
    QUERIED_EXISTING = "QUERIED_EXISTING"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"


class ExplainerErrorCode(StrEnum):
    SOURCE_EVIDENCE_MISSING = "SOURCE_EVIDENCE_MISSING"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    INFERENCE_EGRESS_DENIED = "INFERENCE_EGRESS_DENIED"
    GPU_CAPACITY_UNAVAILABLE = "GPU_CAPACITY_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STALE_PLAN = "STALE_PLAN"
    STALE_REVISION = "STALE_REVISION"
    QC_BLOCKED = "QC_BLOCKED"
    LICENSE_SCOPE_UNVERIFIED = "LICENSE_SCOPE_UNVERIFIED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"


#: Retryability contract surfaced to the API layer.
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value,
        ExplainerErrorCode.BUDGET_EXCEEDED.value,
        ExplainerErrorCode.STALE_PLAN.value,
        ExplainerErrorCode.STALE_REVISION.value,
    }
)

#: Next-step guidance surfaced with every structured error.
ERROR_NEXT_STEP: dict[str, str] = {
    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value: "补充可定位的来源，或在稿件中删改该断言后重新预检。",
    ExplainerErrorCode.CLAIM_CONFLICT.value: "在资料页解决冲突：补充证据、保留有依据的表述或移除该断言。",
    ExplainerErrorCode.SCHEMA_INVALID.value: "按错误指出的字段修正请求；不要通过重试相同载荷绕过校验。",
    ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value: "在模型中心补齐能力并完成 smoke，或调整计划降低对该能力的要求。",
    ExplainerErrorCode.INFERENCE_EGRESS_DENIED.value: "推理保持 LOCAL_ONLY；如需云端能力，须显式配置并重新预检。",
    ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value: "等待当前 GPU 作业释放，或降低并发与质量档后重试。",
    ExplainerErrorCode.BUDGET_EXCEEDED.value: "提高预算上限或减少输出/候选后重新预检。",
    ExplainerErrorCode.STALE_PLAN.value: "输入、政策或能力已变化；重新预检得到新的 plan_hash 再提交。",
    ExplainerErrorCode.STALE_REVISION.value: "对象已被其他操作更新；刷新后基于最新 revision 重新提交。",
    ExplainerErrorCode.QC_BLOCKED.value: "先处理硬阻塞问题，再重新检查受影响的节点。",
    ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value: "补充该资产的用途许可证据，或改用许可范围内的替代资产。",
    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value: "检查产物完整性与探测结果；不要用文件名或 HTTP 状态代替有效输出。",
    ExplainerErrorCode.IDEMPOTENCY_CONFLICT.value: "同一 Idempotency-Key 已用于不同 payload；改用新的 key。",
    ExplainerErrorCode.NOT_FOUND.value: "确认对象属于当前项目且未被归档。",
    ExplainerErrorCode.INVALID_REQUEST.value: "按字段错误修正请求。",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Stable JSON encoding used for every content hash in this domain."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.match(value or ""))


def normalize_locale(value: str) -> str:
    if not value or not _LOCALE_RE.match(value):
        raise ExplainerContractError("SCHEMA_INVALID", f"语言标签不合法：{value!r}", {"locale": value})
    parts = value.split("-")
    if len(parts) == 1:
        return parts[0].lower()
    return parts[0].lower() + "-" + "-".join(part.upper() if len(part) == 2 and part.isalpha() else part for part in parts[1:])


def validate_edition_key(value: str) -> str:
    if not value or not _EDITION_KEY_RE.match(value):
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "edition_key 必须为 3–64 位小写字母、数字、下划线或连字符，且以字母或数字开头",
            {"edition_key": value},
        )
    return value


def is_hard_blocker(issue_kind: str) -> bool:
    return issue_kind in HARD_BLOCKER_KINDS


def is_soft_issue(issue_kind: str) -> bool:
    return issue_kind in SOFT_ISSUE_KINDS or (not is_hard_blocker(issue_kind) and issue_kind.endswith("_HINT"))


# --------------------------------------------------------------------------- #
# value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ratio:
    """Exact rational frame rate or time base."""

    num: int
    den: int = 1

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "有理数分子分母必须为正", {"num": self.num, "den": self.den}
            )

    @property
    def value(self) -> float:
        return self.num / self.den

    def frames_for_seconds(self, seconds: float) -> int:
        """Half-open frame count for a duration, using integer math only."""

        if seconds < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "时长不能为负", {"seconds": seconds})
        # seconds * num / den rounded to nearest, computed in integers.
        numerator = round(seconds * 1_000_000)
        return (numerator * self.num + (self.den * 1_000_000) // 2) // (self.den * 1_000_000)

    def seconds_for_frames(self, frames: int) -> float:
        if frames < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "帧数不能为负", {"frames": frames})
        return frames * self.den / self.num

    def samples_for_frames(self, frames: int, sample_rate: int) -> int:
        if sample_rate <= 0:
            raise ExplainerContractError("SCHEMA_INVALID", "采样率必须为正", {"sample_rate": sample_rate})
        return (frames * self.den * sample_rate + self.num // 2) // self.num

    def as_dict(self) -> dict[str, int]:
        return {"num": self.num, "den": self.den}


@dataclass(frozen=True)
class Budget:
    """Authorized stop limits.  Never a performance prediction (design §5.2)."""

    max_gpu_seconds: int
    max_wall_seconds: int
    initial_candidates_per_ordinary_beat: int = 1
    initial_candidates_per_key_identity: int = 2
    max_creative_repairs_per_beat: int = 2
    max_technical_retries_per_step: int = 2
    max_script_revisions: int = 0

    def __post_init__(self) -> None:
        if self.max_gpu_seconds <= 0 or self.max_wall_seconds <= 0:
            raise ExplainerContractError("SCHEMA_INVALID", "预算上限必须为正整数")
        if not 1 <= self.initial_candidates_per_ordinary_beat <= 4:
            raise ExplainerContractError("SCHEMA_INVALID", "普通镜头初始候选数必须在 1–4 之间")
        if not 1 <= self.initial_candidates_per_key_identity <= 4:
            raise ExplainerContractError("SCHEMA_INVALID", "关键人物设定初始候选数必须在 1–4 之间")
        if not 0 <= self.max_creative_repairs_per_beat <= 5:
            raise ExplainerContractError("SCHEMA_INVALID", "创作修复次数必须在 0–5 之间")
        if not 0 <= self.max_technical_retries_per_step <= 5:
            raise ExplainerContractError("SCHEMA_INVALID", "技术重试次数必须在 0–5 之间")
        if not 0 <= self.max_script_revisions <= 10:
            raise ExplainerContractError("SCHEMA_INVALID", "讲稿修订次数必须在 0–10 之间")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_gpu_seconds": self.max_gpu_seconds,
            "max_wall_seconds": self.max_wall_seconds,
            "initial_candidates_per_ordinary_beat": self.initial_candidates_per_ordinary_beat,
            "initial_candidates_per_key_identity": self.initial_candidates_per_key_identity,
            "max_creative_repairs_per_beat": self.max_creative_repairs_per_beat,
            "max_technical_retries_per_step": self.max_technical_retries_per_step,
            "max_script_revisions": self.max_script_revisions,
        }


@dataclass(frozen=True)
class FallbackPolicy:
    """Which degradations are pre-authorized.  Human locks are never optional."""

    allowed_visual_fallbacks: tuple[str, ...] = ()
    script_rewrite_policy: str = "NO_AUTOMATIC_REWRITE"
    max_script_revisions: int = 0
    preserve_must_be_motion: bool = True
    preserve_human_locks: bool = True

    def __post_init__(self) -> None:
        if not self.preserve_must_be_motion or not self.preserve_human_locks:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "preserve_must_be_motion 与 preserve_human_locks 是结构不变量，不能关闭",
            )
        if self.script_rewrite_policy not in {"ALLOW_NEW_REVISION_WITHIN_BUDGET", "NO_AUTOMATIC_REWRITE"}:
            raise ExplainerContractError("SCHEMA_INVALID", "script_rewrite_policy 取值不合法")

    def allows(self, fallback: str | VisualFallback) -> bool:
        return str(fallback) in set(self.allowed_visual_fallbacks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_visual_fallbacks": list(self.allowed_visual_fallbacks),
            "preserve_must_be_motion": self.preserve_must_be_motion,
            "preserve_human_locks": self.preserve_human_locks,
            "script_rewrite_policy": self.script_rewrite_policy,
            "max_script_revisions": self.max_script_revisions,
        }


@dataclass(frozen=True)
class DurationSpec:
    mode: DurationMode
    target_seconds: int
    tolerance_percent: float = DEFAULT_NATURAL_TOLERANCE_PERCENT

    def __post_init__(self) -> None:
        if not MIN_TARGET_SECONDS <= self.target_seconds <= MAX_TARGET_SECONDS:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"目标时长必须在 {MIN_TARGET_SECONDS}–{MAX_TARGET_SECONDS} 秒之间",
                {"target_seconds": self.target_seconds},
            )
        if not 0 <= self.tolerance_percent <= 25:
            raise ExplainerContractError("SCHEMA_INVALID", "容差必须在 0–25% 之间")
        if self.mode is DurationMode.FIXED and self.tolerance_percent != 0:
            raise ExplainerContractError("SCHEMA_INVALID", "FIXED 模式的容差必须为 0")

    @property
    def target_frames_fixed(self) -> int | None:
        return None

    def fixed_total_frames(self, fps: Ratio) -> int:
        if self.mode is not DurationMode.FIXED:
            raise ExplainerContractError("SCHEMA_INVALID", "只有 FIXED 模式才有精确总帧数目标")
        return fps.frames_for_seconds(self.target_seconds)

    def natural_bounds_seconds(self) -> tuple[float, float]:
        delta = self.target_seconds * self.tolerance_percent / 100.0
        return (self.target_seconds - delta, self.target_seconds + delta)

    def accepts_seconds(self, measured_seconds: float) -> bool:
        low, high = self.natural_bounds_seconds()
        return low <= measured_seconds <= high

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "target_seconds": self.target_seconds,
            "tolerance_percent": self.tolerance_percent,
        }


@dataclass(frozen=True)
class OutputRequest:
    edition_key: str
    voice_locale: str
    subtitle_locales: tuple[str, ...] = ()
    subtitle_mode: SubtitleMode = SubtitleMode.NONE
    aspect_ratio: AspectRatio = AspectRatio.WIDE
    fps: Ratio = Ratio(25, 1)
    duration_policy: DurationPolicy = DurationPolicy.NATURAL_NARRATION
    allow_soft_subtitle_fallback: bool = False

    def __post_init__(self) -> None:
        validate_edition_key(self.edition_key)
        normalize_locale(self.voice_locale)
        for locale in self.subtitle_locales:
            normalize_locale(locale)
        if len(set(self.subtitle_locales)) != len(self.subtitle_locales):
            raise ExplainerContractError("SCHEMA_INVALID", "subtitle_locales 不能重复", {"edition_key": self.edition_key})
        if self.subtitle_mode is SubtitleMode.NONE:
            if self.subtitle_locales:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "subtitle_mode=NONE 时不能声明字幕语言", {"edition_key": self.edition_key}
                )
            if self.allow_soft_subtitle_fallback:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "subtitle_mode=NONE 时不能允许软字幕回退", {"edition_key": self.edition_key}
                )
        elif not self.subtitle_locales:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "需要字幕时必须声明至少一种字幕语言", {"edition_key": self.edition_key}
            )
        if self.subtitle_mode is SubtitleMode.BILINGUAL_BURNED and len(self.subtitle_locales) < 2:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "双语烧录需要两种字幕语言",
                {"edition_key": self.edition_key, "subtitle_locales": list(self.subtitle_locales)},
            )

    @property
    def is_bilingual(self) -> bool:
        return len(self.subtitle_locales) >= 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "edition_key": self.edition_key,
            "voice_locale": self.voice_locale,
            "subtitle_locales": list(self.subtitle_locales),
            "subtitle_mode": self.subtitle_mode.value,
            "aspect_ratio": self.aspect_ratio.value,
            "fps": self.fps.as_dict(),
            "duration_policy": self.duration_policy.value,
            "allow_soft_subtitle_fallback": self.allow_soft_subtitle_fallback,
        }


@dataclass(frozen=True)
class CreateExplainerRequest:
    """Input to ``POST /explainers`` (design §5.2, §14)."""

    title: str
    topic: str
    content_kind: ContentKind
    source_locale: str
    duration: DurationSpec
    outputs: tuple[OutputRequest, ...]
    automation_mode: AutomationMode = AutomationMode.AUTO_WITH_EXCEPTIONS
    inference_mode: InferenceMode = InferenceMode.LOCAL_ONLY
    research_mode: ResearchMode = ResearchMode.OFFLINE_IMPORT
    allowed_domains: tuple[str, ...] = ()
    max_external_requests: int = 0
    channel_profile_id: str | None = None
    channel_profile_version_id: str | None = None
    input_kind: InputKind = InputKind.TOPIC
    input_payload: Mapping[str, Any] = field(default_factory=dict)
    budget: Budget | None = None
    fallback_policy: FallbackPolicy | None = None

    def __post_init__(self) -> None:
        if not self.title or len(self.title) > 200:
            raise ExplainerContractError("SCHEMA_INVALID", "标题必须是 1–200 个字符")
        if not self.outputs:
            raise ExplainerContractError("SCHEMA_INVALID", "至少需要一个输出 edition")
        normalize_locale(self.source_locale)
        keys = [output.edition_key for output in self.outputs]
        if len(set(keys)) != len(keys):
            raise ExplainerContractError("SCHEMA_INVALID", "同一作品的 edition_key 必须唯一", {"edition_keys": keys})
        if self.research_mode is ResearchMode.OFFLINE_IMPORT:
            if self.max_external_requests != 0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "OFFLINE_IMPORT 模式的外发请求上限必须为 0"
                )
        else:
            if self.max_external_requests <= 0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "WEB_RESEARCH 模式必须声明正的外发请求上限"
                )
        if self.input_kind is InputKind.DOCUMENT_IMPORT and not (self.input_payload.get("source_refs") or []):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "文档导入至少需要一个来源引用"
            )
        if self.input_kind is InputKind.TOPIC and not self.topic:
            raise ExplainerContractError("SCHEMA_INVALID", "题目模式必须提供 topic")

    @property
    def target_minutes(self) -> int:
        return round(self.duration.target_seconds / 60)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "title": self.title,
            "topic": self.topic,
            "content_kind": self.content_kind.value,
            "source_locale": self.source_locale,
            "duration": self.duration.as_dict(),
            "outputs": [output.as_dict() for output in self.outputs],
            "automation_mode": self.automation_mode.value,
            "inference_mode": self.inference_mode.value,
            "research_mode": self.research_mode.value,
            "allowed_domains": list(self.allowed_domains),
            "max_external_requests": self.max_external_requests,
            "channel_profile_id": self.channel_profile_id,
            "channel_profile_version_id": self.channel_profile_version_id,
            "input_kind": self.input_kind.value,
            "input_payload": dict(self.input_payload),
            "budget": (self.budget or default_budget()).as_dict(),
            "fallback_policy": (self.fallback_policy or default_fallback_policy()).as_dict(),
        }

    def request_hash(self) -> str:
        return content_hash(self.as_dict())


def default_budget() -> Budget:
    return Budget(max_gpu_seconds=36_000, max_wall_seconds=43_200)


def default_fallback_policy() -> FallbackPolicy:
    return FallbackPolicy(
        allowed_visual_fallbacks=(
            VisualFallback.I2V_TO_MOTION_STILL.value,
            VisualFallback.I2V_TO_INFORMATION_GRAPHIC.value,
        ),
        script_rewrite_policy="NO_AUTOMATIC_REWRITE",
        max_script_revisions=0,
    )


@dataclass(frozen=True)
class PreflightBlocker:
    code: str
    message: str
    scope: str = "RUN"
    details: Mapping[str, Any] = field(default_factory=dict)
    next_step: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "scope": self.scope,
            "details": dict(self.details),
            "next_step": self.next_step or ERROR_NEXT_STEP.get(self.code, ""),
            "retryable": self.code in RETRYABLE_ERROR_CODES,
        }


#: Preflight report categories from design §5.2.
PREFLIGHT_CATEGORIES: tuple[str, ...] = (
    "EXECUTABLE",
    "NEEDS_SOURCE_MATERIAL",
    "MISSING_LOCAL_CAPABILITY",
    "INSUFFICIENT_ESTIMATED_RESOURCES",
    "LICENSE_SCOPE_UNCONFIRMED",
)


def classify_blocker(code: str) -> str:
    mapping = {
        ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value: "NEEDS_SOURCE_MATERIAL",
        ExplainerErrorCode.CLAIM_CONFLICT.value: "NEEDS_SOURCE_MATERIAL",
        ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value: "MISSING_LOCAL_CAPABILITY",
        ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value: "INSUFFICIENT_ESTIMATED_RESOURCES",
        ExplainerErrorCode.BUDGET_EXCEEDED.value: "INSUFFICIENT_ESTIMATED_RESOURCES",
        ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value: "LICENSE_SCOPE_UNCONFIRMED",
    }
    return mapping.get(code, "EXECUTABLE")


@dataclass(frozen=True)
class PreflightReport:
    """Frozen preflight outcome.  ``plan_hash`` commits inputs/policy/budget/skeleton."""

    project_id: str
    video_id: str
    status: str
    categories: Mapping[str, tuple[PreflightBlocker, ...]]
    plan_hash: str
    frozen_inputs: Mapping[str, Any]
    policy_snapshot: Mapping[str, Any]
    capability_snapshot: Mapping[str, Any]
    budget: Mapping[str, Any]
    task_skeleton: tuple[Mapping[str, Any], ...]
    estimate: Mapping[str, Any]
    generated_at: str
    parent_plan_id: str | None = None

    @property
    def executable(self) -> bool:
        return not any(self.categories.get(category) for category in PREFLIGHT_CATEGORIES if category != "EXECUTABLE")

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "video_id": self.video_id,
            "status": self.status,
            "executable": self.executable,
            "categories": {
                category: [blocker.as_dict() for blocker in self.categories.get(category, ())]
                for category in PREFLIGHT_CATEGORIES
            },
            "plan_hash": self.plan_hash,
            "parent_plan_id": self.parent_plan_id,
            "frozen_inputs": dict(self.frozen_inputs),
            "policy_snapshot": dict(self.policy_snapshot),
            "capability_snapshot": dict(self.capability_snapshot),
            "budget": dict(self.budget),
            "task_skeleton": [dict(item) for item in self.task_skeleton],
            "estimate": dict(self.estimate),
            "generated_at": self.generated_at,
            "would_create_jobs": False,
        }


# --------------------------------------------------------------------------- #
# invariants
# --------------------------------------------------------------------------- #
def ensure_monotonic_frames(intervals: Iterable[tuple[int, int]]) -> None:
    """Every half-open ``[start, end)`` interval must be positive and ordered."""

    cursor: int | None = None
    for start, end in intervals:
        if end <= start:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "帧区间必须满足 end > start", {"start": start, "end": end}
            )
        if cursor is not None and start != cursor:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "帧区间必须连续且无重叠",
                {"expected_start": cursor, "actual_start": start},
            )
        cursor = end


def ensure_policy_decision_allowed(
    *, decision_kind: str, actor_type: str, actor: str | None, policy_processor: str | None
) -> None:
    """HTTP clients may never mint a machine acceptance (design §14)."""

    if decision_kind == DecisionKind.POLICY_ACCEPTED.value:
        if actor_type != ActorType.MACHINE.value:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "POLICY_ACCEPTED 只能由内部处理器以 MACHINE 身份创建",
                {"actor_type": actor_type},
            )
        if not (policy_processor or "").strip():
            raise ExplainerContractError(
                "SCHEMA_INVALID", "POLICY_ACCEPTED 必须记录 policy_processor", {}
            )
    if decision_kind == DecisionKind.HUMAN_APPROVED.value:
        if actor_type != ActorType.HUMAN.value:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "HUMAN_APPROVED 必须由真实操作者创建", {"actor_type": actor_type}
            )
        if not (actor or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "HUMAN_APPROVED 必须记录 actor", {})


def schedule_trigger_key(schedule_id: str, scheduled_for: str) -> tuple[str, str]:
    """The only legal de-duplication key for a scheduled occurrence."""

    if not schedule_id:
        raise ExplainerContractError("SCHEMA_INVALID", "schedule_id 不能为空")
    if not scheduled_for.endswith("Z"):
        raise ExplainerContractError(
            "SCHEMA_INVALID", "scheduled_for 必须以 UTC 形式存储（...Z）", {"scheduled_for": scheduled_for}
        )
    return (schedule_id, scheduled_for)


def estimate_shot_budget(
    *, target_seconds: int, average_shot_seconds: float = 7.5, motion_ratio: float = 0.3
) -> dict[str, Any]:
    """Arithmetic shot-count planning helper.

    This is explicitly *not* a throughput promise: it only converts a target
    duration into a shot-count order of magnitude (design §10.1).
    """

    if average_shot_seconds <= 0:
        raise ExplainerContractError("SCHEMA_INVALID", "平均镜头时长必须为正")
    if not 0 <= motion_ratio <= 1:
        raise ExplainerContractError("SCHEMA_INVALID", "视频比例必须在 0–1 之间")
    total = max(1, round(target_seconds / average_shot_seconds))
    return {
        "estimated_shot_count": total,
        "estimated_motion_shot_count": round(total * motion_ratio),
        "average_shot_seconds": average_shot_seconds,
        "motion_ratio": motion_ratio,
        "timing_status": "ARITHMETIC_ESTIMATE_NOT_BENCHMARK",
        "measured_samples": None,
    }


def script_length_estimate(text: str, *, chars_per_second: float) -> dict[str, Any]:
    if not MIN_SPEAKING_RATE_CHARS_PER_SECOND <= chars_per_second <= MAX_SPEAKING_RATE_CHARS_PER_SECOND:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"语速必须在 {MIN_SPEAKING_RATE_CHARS_PER_SECOND}–{MAX_SPEAKING_RATE_CHARS_PER_SECOND} 字/秒之间",
            {"chars_per_second": chars_per_second},
        )
    length = len(text.strip())
    return {
        "character_count": length,
        "chars_per_second": chars_per_second,
        "estimated_seconds": round(length / chars_per_second, 2),
        "timing_status": "TEXT_ESTIMATE_NOT_MEASURED_TTS",
    }


def summarise_coverage(
    *,
    total_frames: int,
    decoded_frames: int,
    technical_checked_frames: int,
    semantic_checked_frames: int,
    human_reviewed_intervals: Sequence[Sequence[int]] = (),
    sampled_frame_ids: Sequence[int] = (),
) -> dict[str, Any]:
    """Coverage report where each layer is reported separately (design §18.1)."""

    if total_frames < 0:
        raise ExplainerContractError("SCHEMA_INVALID", "总帧数不能为负")
    for value, label in (
        (decoded_frames, "decoded_frames"),
        (technical_checked_frames, "technical_checked_frames"),
        (semantic_checked_frames, "semantic_checked_frames"),
    ):
        if value < 0 or value > total_frames:
            raise ExplainerContractError(
                "SCHEMA_INVALID", f"{label} 必须在 0–总帧数之间", {label: value, "total_frames": total_frames}
            )
    human_frames = 0
    for interval in human_reviewed_intervals:
        start, end = int(interval[0]), int(interval[1])
        if end <= start or start < 0 or end > total_frames:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "人工审阅区间必须合法", {"interval": [start, end], "total_frames": total_frames}
            )
        human_frames += end - start
    def ratio(value: int) -> float:
        return round(value / total_frames, 6) if total_frames else 0.0
    return {
        "total_frames": total_frames,
        "decoded_frames": decoded_frames,
        "decoded_ratio": ratio(decoded_frames),
        "technical_checked_frames": technical_checked_frames,
        "technical_ratio": ratio(technical_checked_frames),
        "semantic_checked_frames": semantic_checked_frames,
        "semantic_ratio": ratio(semantic_checked_frames),
        "sampled_frame_ids": sorted({int(frame) for frame in sampled_frame_ids}),
        "human_reviewed_intervals": [[int(i[0]), int(i[1])] for i in human_reviewed_intervals],
        "human_reviewed_frames": human_frames,
        "human_ratio": ratio(human_frames),
        "semantic_equals_full_understanding": False,
    }


def subtitle_reading_rates(
    *,
    locale: str,
    text: str,
    duration_ms: int,
    max_chars_per_second_cjk: float = 8.0,
    max_chars_per_second_latin: float = 20.0,
) -> dict[str, Any]:
    """Reading-rate check used by the subtitle layout detector."""

    if duration_ms <= 0:
        raise ExplainerContractError("SCHEMA_INVALID", "字幕时长必须为正")
    is_cjk = locale.lower().startswith(("zh", "ja", "ko"))
    limit = max_chars_per_second_cjk if is_cjk else max_chars_per_second_latin
    length = len(text.strip())
    rate = length / (duration_ms / 1000.0)
    return {
        "locale": locale,
        "character_count": length,
        "duration_ms": duration_ms,
        "characters_per_second": round(rate, 3),
        "limit_characters_per_second": limit,
        "exceeds_limit": rate > limit,
    }


def assert_human_locks_preserved(*, locked: bool, new_value: Any, current_value: Any) -> None:
    """Automated repair must never rewrite a human-locked script or shot."""

    if locked and new_value != current_value:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "人工锁定的讲稿或镜头不能被自动修复覆盖",
            {"locked": True},
        )
