"""Explainer factory domain rules: machine adoption, budget, fallback, licensing.

Pure policy evaluation only.  Nothing here writes a human approval, calls a
model or touches HTTP (《源码接入与开发任务清单》§3).

The separation this module enforces is the core of design §12.2 and §14:

* ``POLICY_ACCEPTED``  -> :class:`MachineAcceptance` (machine, rule version,
  thresholds, evidence, limitations)
* ``HUMAN_APPROVED``   -> produced by the service layer from a real operator
* publication          -> recorded separately, never implied by either

The policy evaluator returns ``CONTINUE`` or ``REQUEST_HUMAN`` so the existing
declarative automation workflow keeps its ``CONTINUE / PAUSE_HITL / STOP``
vocabulary.  There is deliberately no ``AUTO_APPROVE`` outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    HARD_BLOCKER_KINDS,
    SOFT_ISSUE_KINDS,
    Budget,
    DecisionKind,
    ExplainerContractError,
    ExplainerErrorCode,
    FallbackPolicy,
    RunStatus,
    Severity,
    VisualFallback,
    content_hash,
    utc_now_iso,
)

#: Version of the machine acceptance rule set.  Bumping this marks older reports
#: as produced under an older standard (design §18.3).
POLICY_RULE_VERSION = "explainer_standard_v1"
POLICY_PROCESSOR_NAME = "EXPLAINER_POLICY_EVALUATE"

#: Documented default audio acceptance band (design §11.4).
DEFAULT_LOUDNESS_LUFS = -16.0
DEFAULT_LOUDNESS_TOLERANCE_LU = 1.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
#: Documented default subtitle boundary tolerance (design §18.3).
DEFAULT_SUBTITLE_P95_MS = 150
#: 30-minute-long-form end-of-film clock drift budget (design §18.3).
DEFAULT_FILM_END_DRIFT_FRAMES = 1
#: Dialogue-under-narration ducking depth (design §11.4).
DEFAULT_BGM_DUCK_DB = (14.0, 20.0)

#: Severities that a machine policy may never accept on its own.
NON_ACCEPTABLE_SEVERITIES: frozenset[str] = frozenset({Severity.BLOCKER.value})


@dataclass(frozen=True)
class Thresholds:
    """Frozen numeric acceptance thresholds recorded with every policy decision."""

    loudness_lufs: float = DEFAULT_LOUDNESS_LUFS
    loudness_tolerance_lu: float = DEFAULT_LOUDNESS_TOLERANCE_LU
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP
    subtitle_boundary_p95_ms: int = DEFAULT_SUBTITLE_P95_MS
    film_end_drift_frames: int = DEFAULT_FILM_END_DRIFT_FRAMES
    bgm_duck_db_min: float = DEFAULT_BGM_DUCK_DB[0]
    bgm_duck_db_max: float = DEFAULT_BGM_DUCK_DB[1]
    natural_tolerance_percent: float = 5.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "loudness_lufs": self.loudness_lufs,
            "loudness_tolerance_lu": self.loudness_tolerance_lu,
            "true_peak_dbtp": self.true_peak_dbtp,
            "subtitle_boundary_p95_ms": self.subtitle_boundary_p95_ms,
            "film_end_drift_frames": self.film_end_drift_frames,
            "bgm_duck_db_min": self.bgm_duck_db_min,
            "bgm_duck_db_max": self.bgm_duck_db_max,
            "natural_tolerance_percent": self.natural_tolerance_percent,
            "source": "PRODUCT_DEFAULT_NOT_PLATFORM_STANDARD",
        }


@dataclass(frozen=True)
class IssueFact:
    """Minimal issue projection the policy evaluator needs."""

    issue_kind: str
    severity: str
    status: str = "OPEN"
    detector: str = ""
    confidence: float | None = None
    unknown_reason: str | None = None
    scope: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in {"OPEN", "FIXING"}

    @property
    def is_hard(self) -> bool:
        return self.severity == Severity.BLOCKER.value or self.issue_kind in HARD_BLOCKER_KINDS

    @property
    def is_soft(self) -> bool:
        return self.issue_kind in SOFT_ISSUE_KINDS


@dataclass(frozen=True)
class CoverageFact:
    """Coverage projection: layers are never collapsed into one number."""

    total_frames: int
    decoded_frames: int
    technical_checked_frames: int
    semantic_checked_frames: int
    human_reviewed_frames: int = 0
    semantic_detector_available: bool = True

    @property
    def decode_complete(self) -> bool:
        return self.total_frames > 0 and self.decoded_frames >= self.total_frames

    @property
    def technical_complete(self) -> bool:
        return self.total_frames > 0 and self.technical_checked_frames >= self.total_frames


@dataclass(frozen=True)
class MachineAcceptance:
    """Record of a machine policy acceptance.  Never a human approval."""

    accepted: bool
    rule_version: str
    thresholds: Mapping[str, Any]
    evidence: Mapping[str, Any]
    limitations: str
    blockers: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[Mapping[str, Any], ...] = ()
    workflow_effect: str = "CONTINUE"
    processor: str = POLICY_PROCESSOR_NAME
    decided_at: str = field(default_factory=utc_now_iso)

    @property
    def decision_kind(self) -> str:
        return DecisionKind.POLICY_ACCEPTED.value

    def decision_hash(self) -> str:
        return content_hash(
            {
                "accepted": self.accepted,
                "rule_version": self.rule_version,
                "thresholds": dict(self.thresholds),
                "evidence": dict(self.evidence),
                "limitations": self.limitations,
                "blockers": [dict(item) for item in self.blockers],
                "warnings": [dict(item) for item in self.warnings],
                "workflow_effect": self.workflow_effect,
                "processor": self.processor,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_kind": self.decision_kind,
            "actor_type": "MACHINE",
            "accepted": self.accepted,
            "rule_version": self.rule_version,
            "thresholds": dict(self.thresholds),
            "evidence": dict(self.evidence),
            "limitations": self.limitations,
            "blockers": [dict(item) for item in self.blockers],
            "warnings": [dict(item) for item in self.warnings],
            "workflow_effect": self.workflow_effect,
            "processor": self.processor,
            "decided_at": self.decided_at,
            "human_approval_written": False,
            "publication_authorized": False,
        }


def evaluate_machine_acceptance(
    *,
    subject_kind: str,
    subject_revision_id: str,
    subject_hash: str,
    issues: Sequence[IssueFact],
    coverage: CoverageFact,
    thresholds: Thresholds | None = None,
    automation_mode: str = "AUTO_WITH_EXCEPTIONS",
    policy_rule_version: str = POLICY_RULE_VERSION,
) -> MachineAcceptance:
    """Decide whether a machine policy may accept a subject for auto-export.

    Returns ``accepted=False`` with ``workflow_effect='REQUEST_HUMAN'`` whenever a
    hard blocker, an unacceptable severity, or insufficient technical coverage is
    present.  Soft aesthetic hints never block and never trigger redraws.
    """

    resolved = thresholds or Thresholds()
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for issue in issues:
        if not issue.is_open:
            continue
        if issue.is_soft:
            warnings.append(
                {
                    "issue_kind": issue.issue_kind,
                    "severity": issue.severity,
                    "detector": issue.detector,
                    "treatment": "HINT_ONLY_NO_AUTOMATIC_REDRAW",
                }
            )
            continue
        if issue.is_hard or issue.severity in NON_ACCEPTABLE_SEVERITIES:
            blockers.append(
                {
                    "issue_kind": issue.issue_kind,
                    "severity": issue.severity,
                    "detector": issue.detector,
                    "scope": issue.scope,
                    "reason": "HARD_BLOCKER_REQUIRES_HUMAN_OR_REPAIR",
                }
            )
        elif issue.severity == Severity.MAJOR.value:
            warnings.append(
                {
                    "issue_kind": issue.issue_kind,
                    "severity": issue.severity,
                    "detector": issue.detector,
                    "treatment": "REPORTED_NOT_BLOCKING",
                }
            )

    if not coverage.semantic_detector_available and coverage.semantic_checked_frames == 0:
        blockers.append(
            {
                "issue_kind": "SEMANTIC_QC_UNCHECKED",
                "severity": Severity.UNKNOWN.value,
                "reason": "NO_VISUAL_QC_PROVIDER",
                "treatment": "UNCHECKED_NOT_PASSED",
            }
        )

    if not coverage.decode_complete:
        blockers.append(
            {
                "issue_kind": "DECODE_COVERAGE_INCOMPLETE",
                "severity": Severity.BLOCKER.value,
                "reason": "TECHNICAL_FULL_DECODE_REQUIRED",
                "coverage": {
                    "decoded_frames": coverage.decoded_frames,
                    "total_frames": coverage.total_frames,
                },
            }
        )

    if not coverage.technical_complete:
        blockers.append(
            {
                "issue_kind": "TECHNICAL_COVERAGE_INCOMPLETE",
                "severity": Severity.BLOCKER.value,
                "reason": "TECHNICAL_FULL_CHECK_REQUIRED",
                "coverage": {
                    "technical_checked_frames": coverage.technical_checked_frames,
                    "total_frames": coverage.total_frames,
                },
            }
        )

    accepted = not blockers
    review_first = automation_mode in {"REVIEW_BEFORE_RENDER", "MANUAL_REVIEW"}
    effect = "CONTINUE" if accepted and not review_first else "REQUEST_HUMAN"
    limitations = (
        "机器政策只声明规则、阈值与检测证据；未检测区域不因通过而视为已理解，"
        "也不构成人工审阅或发布授权。"
    )
    if coverage.human_reviewed_frames == 0:
        limitations += " 本次没有人工实际审阅记录。"

    return MachineAcceptance(
        accepted=accepted,
        rule_version=policy_rule_version,
        thresholds=resolved.as_dict(),
        evidence={
            "subject_kind": subject_kind,
            "subject_revision_id": subject_revision_id,
            "subject_hash": subject_hash,
            "issue_count": len(issues),
            "open_issue_count": sum(1 for issue in issues if issue.is_open),
            "hard_blocker_count": len(blockers),
            "warning_count": len(warnings),
            "coverage": {
                "total_frames": coverage.total_frames,
                "decoded_frames": coverage.decoded_frames,
                "technical_checked_frames": coverage.technical_checked_frames,
                "semantic_checked_frames": coverage.semantic_checked_frames,
                "human_reviewed_frames": coverage.human_reviewed_frames,
            },
            "automation_mode": automation_mode,
            "allows_per_shot_approval": False,
        },
        limitations=limitations,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        workflow_effect=effect,
    )


@dataclass(frozen=True)
class BudgetLedger:
    """Budget consumption tracker.  ``BUDGET_EXCEEDED`` stops work, it never upsizes."""

    budget: Budget
    gpu_seconds_used: float = 0.0
    wall_seconds_used: float = 0.0
    creative_repairs_by_beat: Mapping[str, int] = field(default_factory=dict)
    technical_retries_by_step: Mapping[str, int] = field(default_factory=dict)
    script_revisions_used: int = 0

    def check_gpu(self, *, additional_seconds: float) -> None:
        if additional_seconds < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "追加 GPU 预算不能为负")
        if self.gpu_seconds_used + additional_seconds > self.budget.max_gpu_seconds:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "GPU 时间预算不足，已停止而不是无限重试",
                {
                    "limit_seconds": self.budget.max_gpu_seconds,
                    "used_seconds": self.gpu_seconds_used,
                    "requested_seconds": additional_seconds,
                },
            )

    def check_wall(self, *, additional_seconds: float) -> None:
        if additional_seconds < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "追加墙钟预算不能为负")
        if self.wall_seconds_used + additional_seconds > self.budget.max_wall_seconds:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "墙钟预算不足，已停止而不是无限重试",
                {
                    "limit_seconds": self.budget.max_wall_seconds,
                    "used_seconds": self.wall_seconds_used,
                    "requested_seconds": additional_seconds,
                },
            )

    def check_creative_repair(self, beat_id: str) -> None:
        """Raise when one more creative repair would exceed the authorized budget.

        The ledger is a *check*, not an automatic counter: the caller records the
        consumption after the attempt so a crash between the check and the record
        cannot silently grant extra repairs.
        """

        used = int(self.creative_repairs_by_beat.get(beat_id, 0))
        if used + 1 > self.budget.max_creative_repairs_per_beat:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "该画面段的创作修复次数已用尽；请改用允许的替代路径或交由人工处理",
                {"beat_id": beat_id, "used": used, "limit": self.budget.max_creative_repairs_per_beat},
            )

    def check_technical_retry(self, step_code: str) -> None:
        used = int(self.technical_retries_by_step.get(step_code, 0))
        if used + 1 > self.budget.max_technical_retries_per_step:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "该步骤的技术重试次数已用尽",
                {"step_code": step_code, "used": used, "limit": self.budget.max_technical_retries_per_step},
            )

    def check_script_revision(self) -> None:
        if self.script_revisions_used + 1 > self.budget.max_script_revisions:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "讲稿修订预算已用尽；锁定文字不应被自动改写",
                {
                    "used": self.script_revisions_used,
                    "limit": self.budget.max_script_revisions,
                },
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "limits": self.budget.as_dict(),
            "gpu_seconds_used": self.gpu_seconds_used,
            "wall_seconds_used": self.wall_seconds_used,
            "gpu_seconds_remaining": max(0.0, self.budget.max_gpu_seconds - self.gpu_seconds_used),
            "wall_seconds_remaining": max(0.0, self.budget.max_wall_seconds - self.wall_seconds_used),
            "creative_repairs_by_beat": dict(self.creative_repairs_by_beat),
            "technical_retries_by_step": dict(self.technical_retries_by_step),
            "script_revisions_used": self.script_revisions_used,
        }


def plan_initial_candidates(*, is_key_identity: bool, budget: Budget) -> int:
    """Initial candidate count: 1 for ordinary beats, 2 for key identities."""

    return (
        budget.initial_candidates_per_key_identity if is_key_identity else budget.initial_candidates_per_ordinary_beat
    )


@dataclass(frozen=True)
class FallbackDecision:
    allowed: bool
    fallback: str | None
    reason: str
    preserves_must_be_motion: bool
    preserves_human_lock: bool


def resolve_visual_fallback(
    *,
    beat_must_be_motion: bool,
    beat_locked_by_human: bool,
    planned_render_type: str,
    allowed_fallbacks: Sequence[str] | FallbackPolicy,
    candidate_allowed_fallbacks: Sequence[str] = (),
    repair_budget_exhausted: bool = True,
) -> FallbackDecision:
    """Pick a pre-authorized degradation for a failing beat.

    Ordering follows design §9.1: retry -> simplify prompt/composition ->
    switch to a pre-authorized lower-complexity type -> pause and report.  A beat
    whose ``must_be_motion`` is true never degrades to a still image, and a
    human-locked beat is never touched by an automated fallback.
    """

    policy_allowed = (
        allowed_fallbacks.allowed_visual_fallbacks
        if isinstance(allowed_fallbacks, FallbackPolicy)
        else tuple(allowed_fallbacks)
    )
    allowed_set = set(policy_allowed) & set(candidate_allowed_fallbacks or policy_allowed)

    if beat_locked_by_human:
        return FallbackDecision(
            allowed=False,
            fallback=None,
            reason="HUMAN_LOCKED_BEAT_NOT_DEGRADED_AUTOMATICALLY",
            preserves_must_be_motion=True,
            preserves_human_lock=True,
        )

    if not repair_budget_exhausted:
        return FallbackDecision(
            allowed=False,
            fallback=None,
            reason="REPAIR_BUDGET_REMAINS_PREFER_TARGETED_REPAIR",
            preserves_must_be_motion=True,
            preserves_human_lock=True,
        )

    if beat_must_be_motion:
        if VisualFallback.SIMPLER_MOTION.value in allowed_set:
            return FallbackDecision(
                allowed=True,
                fallback=VisualFallback.SIMPLER_MOTION.value,
                reason="MUST_BE_MOTION_KEEPS_MOTION_VIA_SIMPLER_COMPOSITION",
                preserves_must_be_motion=True,
                preserves_human_lock=True,
            )
        return FallbackDecision(
            allowed=False,
            fallback=None,
            reason="MUST_BE_MOTION_CANNOT_DEGRADE_TO_STILL_IMAGE",
            preserves_must_be_motion=True,
            preserves_human_lock=True,
        )

    if planned_render_type in {"I2V", "PARALLAX"}:
        for candidate in (
            VisualFallback.SIMPLER_MOTION.value,
            VisualFallback.I2V_TO_MOTION_STILL.value,
            VisualFallback.I2V_TO_INFORMATION_GRAPHIC.value,
            VisualFallback.APPROVED_LOWER_RESOURCE_PROFILE.value,
        ):
            if candidate in allowed_set:
                return FallbackDecision(
                    allowed=True,
                    fallback=candidate,
                    reason=f"PRE_AUTHORIZED_FALLBACK_{candidate}",
                    preserves_must_be_motion=True,
                    preserves_human_lock=True,
                )

    return FallbackDecision(
        allowed=False,
        fallback=None,
        reason="NO_PRE_AUTHORIZED_FALLBACK_REMAINS_PAUSE_AND_REPORT",
        preserves_must_be_motion=True,
        preserves_human_lock=True,
    )


class LicenseScope(StrEnum):
    GLOBAL = "GLOBAL"
    REGION_RESTRICTED = "REGION_RESTRICTED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class AssetLicense:
    """Usage license attached to one asset used by a publication package."""

    asset_kind: str
    asset_id: str
    asset_label: str
    scope: str
    territories: tuple[str, ...] = ()
    evidence_ref: str | None = None
    derived_from_asset_id: str | None = None
    note: str = ""


@dataclass(frozen=True)
class LicenseEvaluation:
    publishable: bool
    blockers: tuple[Mapping[str, Any], ...]
    per_asset: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "publishable": self.publishable,
            "blockers": [dict(item) for item in self.blockers],
            "per_asset": [dict(item) for item in self.per_asset],
            "scope_is_legal_conclusion": False,
        }


def evaluate_license_scope(
    *, assets: Sequence[AssetLicense], intended_territories: Sequence[str], require_verified_scope: bool = True
) -> LicenseEvaluation:
    """Per-asset, per-use license gate for a requested distribution scope.

    Renaming a file, switching an export template or relabelling a model does not
    clear a restriction: scope is evaluated on the recorded license of every
    asset in the chain, and derived assets inherit their source restriction.
    """

    wanted = {territory.strip().upper() for territory in intended_territories if territory and territory.strip()}
    global_requested = "GLOBAL" in wanted or not wanted
    blockers: list[dict[str, Any]] = []
    per_asset: list[dict[str, Any]] = []

    for asset in assets:
        scope = (asset.scope or "UNKNOWN").upper()
        territories = {territory.strip().upper() for territory in asset.territories if territory.strip()}
        record: dict[str, Any] = {
            "asset_kind": asset.asset_kind,
            "asset_id": asset.asset_id,
            "asset_label": asset.asset_label,
            "scope": scope,
            "territories": sorted(territories),
            "evidence_ref": asset.evidence_ref,
            "derived_from_asset_id": asset.derived_from_asset_id,
            "derived_scope_inherited": bool(asset.derived_from_asset_id),
        }
        if scope == "GLOBAL":
            record["verdict"] = "ALLOWED"
        elif scope == "REGION_RESTRICTED":
            covered = bool(wanted) and wanted <= territories and not global_requested
            record["verdict"] = "ALLOWED" if covered else "BLOCKED_TERRITORY_OUT_OF_SCOPE"
            if not covered:
                blockers.append(
                    {
                        "code": ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                        "asset_kind": asset.asset_kind,
                        "asset_id": asset.asset_id,
                        "reason": "DISTRIBUTION_TERRITORY_OUTSIDE_VERIFIED_SCOPE",
                        "licensed_territories": sorted(territories),
                        "requested_territories": sorted(wanted) or ["GLOBAL"],
                    }
                )
        elif scope == "NOT_APPLICABLE":
            record["verdict"] = "BLOCKED_NOT_APPLICABLE"
            blockers.append(
                {
                    "code": ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                    "asset_kind": asset.asset_kind,
                    "asset_id": asset.asset_id,
                    "reason": "LICENSE_EXPLICITLY_NOT_APPLICABLE",
                }
            )
        else:
            record["verdict"] = "BLOCKED_SCOPE_UNKNOWN"
            if require_verified_scope:
                blockers.append(
                    {
                        "code": ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                        "asset_kind": asset.asset_kind,
                        "asset_id": asset.asset_id,
                        "reason": "LICENSE_SCOPE_NOT_VERIFIED",
                        "note": asset.note or "需要该资产在拟定用途下的有效许可证据",
                    }
                )
        per_asset.append(record)

    return LicenseEvaluation(publishable=not blockers, blockers=tuple(blockers), per_asset=tuple(per_asset))


# --------------------------------------------------------------------------- #
# staleness propagation (design §13.2)
# --------------------------------------------------------------------------- #
#: Which downstream artifacts a change invalidates.  The table is explicit
#: because "one sentence changed" shifts every later absolute timecode.
STALENESS_RULES: dict[str, tuple[str, ...]] = {
    "SEGMENT_TEXT": ("NARRATION_TAKE", "ALIGNMENT", "SUBTITLE_REVISION", "VISUAL_BEAT", "COMPOSITION_REVISION"),
    "PRONUNCIATION_LEXICON": ("NARRATION_TAKE", "ALIGNMENT", "SUBTITLE_REVISION"),
    "SEGMENT_TIMING": ("ALIGNMENT", "SUBTITLE_REVISION", "COMPOSITION_REVISION"),
    "IDENTITY_REFERENCE": ("BEAT_SELECTION", "COMPOSITION_REVISION"),
    "ENTITY_STATE": ("BEAT_SELECTION", "COMPOSITION_REVISION"),
    "BEAT_PLAN": ("BEAT_SELECTION", "COMPOSITION_REVISION"),
    "SUBTITLE_STYLE": ("SUBTITLE_REVISION", "COMPOSITION_REVISION"),
    "SUBTITLE_FONT": ("SUBTITLE_REVISION", "COMPOSITION_REVISION"),
    "BGM_SELECTION": ("COMPOSITION_REVISION",),
    "MIX_SETTINGS": ("COMPOSITION_REVISION",),
    "ASPECT_RATIO": ("COMPOSITION_REVISION",),
    "FRAME_RATE": ("COMPOSITION_REVISION",),
    "CHANNEL_PROFILE_VERSION": ("VISUAL_BEAT", "BEAT_SELECTION", "COMPOSITION_REVISION"),
    "MODEL_WORKFLOW": ("BEAT_SELECTION", "COMPOSITION_REVISION"),
    "CLIP_RENDER": ("COMPOSITION_REVISION",),
}

#: Artifacts that survive a given change because they have no dependency on it.
STALENESS_PRESERVED: dict[str, tuple[str, ...]] = {
    "SEGMENT_TEXT": ("INDEPENDENT_VISUAL_ASSET", "OTHER_CHAPTER_ASSET", "FACT_LEDGER"),
    "PRONUNCIATION_LEXICON": ("SCRIPT_FACTS", "VISUAL_ASSET"),
    "IDENTITY_REFERENCE": ("OFFSCREEN_SEGMENT", "INDEPENDENT_NARRATION"),
    "SUBTITLE_STYLE": ("NARRATION_AUDIO", "SOURCE_IMAGE_VIDEO"),
    "SUBTITLE_FONT": ("NARRATION_AUDIO", "SOURCE_IMAGE_VIDEO"),
    "BGM_SELECTION": ("IMAGE_VIDEO_SOURCE", "NARRATION_AUDIO"),
    "ASPECT_RATIO": ("CONTENT_SOURCE", "USABLE_AUDIO", "WIDE_ASSET"),
}


def staleness_plan(change_kind: str) -> dict[str, tuple[str, ...]]:
    """Return the invalidation/preservation sets for a change kind."""

    if change_kind not in STALENESS_RULES:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"未知的变更类型：{change_kind}", {"change_kind": change_kind}
        )
    return {
        "invalidates": STALENESS_RULES[change_kind],
        "preserves": STALENESS_PRESERVED.get(change_kind, ()),
    }


# --------------------------------------------------------------------------- #
# run status projection
# --------------------------------------------------------------------------- #
#: Run statuses that indicate the run is genuinely waiting on a human being.
WAITING_STATUSES: frozenset[str] = frozenset({RunStatus.WAITING_INPUT.value, RunStatus.PAUSED.value})


def project_run_status(
    *,
    step_statuses: Mapping[str, str],
    has_blockers: bool,
    cancel_requested: bool,
    ready_to_export: bool,
    exporting: bool,
    completed: bool,
) -> str:
    """Derive the business run status from existing step/job authority.

    ``explainer_runs.status`` is a projection of the existing workflow/job
    truth, so this helper is the single place that maps step facts to it.
    """

    if completed:
        return RunStatus.COMPLETED.value
    if cancel_requested:
        return RunStatus.CANCELLING.value
    if has_blockers and any(status == "BLOCKED" for status in step_statuses.values()):
        return RunStatus.WAITING_INPUT.value
    if any(status in {"RETRYABLE_FAILED", "TERMINAL_FAILED"} for status in step_statuses.values()):
        return RunStatus.FAILED.value
    if exporting:
        return RunStatus.EXPORTING.value
    if ready_to_export:
        return RunStatus.READY_TO_EXPORT.value
    if any(status == "RUNNING" for status in step_statuses.values()):
        return RunStatus.RUNNING.value
    return RunStatus.QUEUED.value


def is_terminal_run_status(status: str) -> bool:
    return status in {item.value for item in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED)}
