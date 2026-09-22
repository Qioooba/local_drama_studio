from __future__ import annotations

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import (
    ERROR_NEXT_STEP,
    RETRYABLE_ERROR_CODES,
    ExplainerContractError,
    ExplainerErrorCode,
)
from local_drama.errors import ApiError

#: Explainer error code -> HTTP status.  The explainer domain uses its own
#: structured codes (design §14), so they are mapped here instead of being added
#: to the long legacy ``DomainRuleError`` code lists.
_EXPLAINER_STATUS: dict[str, int] = {
    ExplainerErrorCode.NOT_FOUND.value: 404,
    ExplainerErrorCode.SCHEMA_INVALID.value: 422,
    ExplainerErrorCode.INVALID_REQUEST.value: 400,
    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value: 422,
    ExplainerErrorCode.CLAIM_CONFLICT.value: 409,
    ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value: 409,
    ExplainerErrorCode.INFERENCE_EGRESS_DENIED.value: 403,
    ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value: 503,
    ExplainerErrorCode.BUDGET_EXCEEDED.value: 409,
    ExplainerErrorCode.STALE_PLAN.value: 409,
    ExplainerErrorCode.STALE_REVISION.value: 409,
    ExplainerErrorCode.QC_BLOCKED.value: 409,
    ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value: 409,
    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value: 422,
    ExplainerErrorCode.IDEMPOTENCY_CONFLICT.value: 409,
    "IDEMPOTENCY_KEY_REQUIRED": 400,
    "IDEMPOTENCY_PAYLOAD_MISMATCH": 409,
}


def api_error_from_explainer(error: ExplainerContractError) -> ApiError:
    """Translate an explainer domain error into the shared API error envelope."""

    status = _EXPLAINER_STATUS.get(error.code, 422)
    return ApiError(
        error.code,
        error.message,
        status_code=status,
        details=dict(error.details),
        retryable=error.code in RETRYABLE_ERROR_CODES,
        suggested_action=ERROR_NEXT_STEP.get(error.code),
    )


def api_error_from_domain(error: DomainRuleError) -> ApiError:
    if error.code == "PROJECT_CREATE_CONTENDED":
        # Concurrent creation held the project write lock past the busy timeout.
        # This is transient and safe to retry, so it must not become a raw 500.
        return ApiError(
            error.code,
            error.message,
            status_code=503,
            details=error.details,
            retryable=True,
            suggested_action=error.suggested_action,
        )
    if error.code in {"AUTOMATION_TOKEN_REQUIRED", "AUTOMATION_TOKEN_INVALID", "AUTOMATION_SCOPE_FORBIDDEN", "AUTOMATION_PROJECT_FORBIDDEN"}:
        status = 403
    elif error.code.endswith("_NOT_FOUND") or error.code in {"PROJECT_NOT_FOUND", "SHOT_NOT_FOUND"}:
        status = 404
    elif error.code in {
        "MP_PROFILE_CROSSWALK_FIELDS_REQUIRED",
        "MP_PROFILE_CROSSWALK_LEGACY_NOT_PUBLISHED",
        "MP_PROFILE_CROSSWALK_LEGACY_CAPABILITY_UNKNOWN",
        "MP_PROFILE_CROSSWALK_V2_NOT_PUBLISHED",
        "MP_PROFILE_CROSSWALK_CAPABILITY_MISMATCH",
        "MP_PROFILE_CROSSWALK_PARAMETER_CONTRACT_MISMATCH",
    }:
        status = 400
    elif error.code in {
        "PIPELINE_ALREADY_RUNNING",
        "PIPELINE_STATE_INVALID",
        "PIPELINE_CURSOR_STALE",
        "PIPELINE_COVERAGE_COMPLETE",
        "IMPORT_COMMIT_SCOPE_CONFLICT",
        "PROJECT_ROOT_EXISTS",
        "SHOT_PROJECT_MISMATCH",
        "SHOT_GROUP_PROJECT_MISMATCH",
        "PROJECT_PACKAGE_OUTPUT_CONFLICT",
        "PROJECT_PACKAGE_IDENTITY_CONFLICT",
        "MP_PROFILE_CROSSWALK_ACTIVE_MAPPING_EXISTS",
        "MP_PROFILE_CROSSWALK_NOT_APPROVED",
        "REVISION_CONFLICT",
        "INVALID_STATE_TRANSITION",
        "PROJECT_CODE_EXISTS",
        "REVIEW_STALE",
        "REVIEW_BATCH_STALE",
        "REVIEW_BATCH_TOKEN_INVALID",
        "EPISODE_RENDER_REVIEW_BATCH_STALE",
        "EPISODE_RENDER_REVIEW_BATCH_TOKEN_INVALID",
        "CONTINUITY_IMPACT_CONFIRMATION_REQUIRED",
        "CONTINUITY_IMPACT_STALE",
        "MACHINE_QC_REQUIRED",
        "PROFILE_NOT_PUBLISHED",
        "IDEMPOTENCY_PAYLOAD_MISMATCH",
        "FRAME_BRIDGE_IDEMPOTENCY_MISMATCH",
        "DIALOGUE_IDEMPOTENCY_MISMATCH",
        "AUDIO_ADOPTION_IDEMPOTENCY_MISMATCH",
        "DIALOGUE_TEXT_REVISION_CONFLICT",
        "LEASE_TOKEN_INVALID",
        "LEASE_EXPIRED",
        "ATTEMPT_NOT_ACTIVE",
        "JOB_NOT_RETRYABLE",
        "INVALID_JOB_DEPENDENCY",
        "IMAGE_CONTENT_REQUIRES_THUMBNAIL",
        "STORY_ASSET_CODE_CONFLICT",
        "STORY_ASSET_REVISION_CONFLICT",
        "STORY_ASSET_ALREADY_BOUND",
        "STORY_ASSET_ALREADY_ARCHIVED",
        "GENERATION_PREFERENCE_REVISION_CONFLICT",
        "QC_POLICY_REVISION_CONFLICT",
        "QC_AUTO_REROLL_ALREADY_ATTACHED",
        "SHOT_GROUP_REVISION_CONFLICT",
        "SHOT_GROUP_CODE_EXISTS",
        "SHOT_EDIT_PLAN_STALE",
        "BEAT_REPLAN_PLAN_STALE",
        "TIMELINE_REFRESH_PLAN_STALE",
        "SHOT_REVISION_CONFLICT",
        "ASSET_PROPOSAL_REVISION_CONFLICT",
        "ASSET_PROPOSAL_ALREADY_DECIDED",
        "MEDIA_DERIVATIVE_NOT_READY",
        "CONFIGURATION_CHANGED",
        "EPISODE_PROJECT_MISMATCH",
        "EPISODE_PRODUCTION_IDEMPOTENCY_MISMATCH",
        "EPISODE_PRODUCTION_RUN_REVISION_CONFLICT",
        "REVIEW_IDEMPOTENCY_PAYLOAD_MISMATCH",
        "REVIEW_REVISION_CONFLICT",
        "REVIEW_DECISION_REVISION_CONFLICT",
        "REVIEW_DECISION_ALREADY_REVOKED",
        "AUDIO_IDEMPOTENCY_PAYLOAD_MISMATCH",
        "AUDIO_MIX_REVISION_CONFLICT",
        "AUDIO_TRACK_REVISION_CONFLICT",
        "TIMELINE_IDEMPOTENCY_PAYLOAD_MISMATCH",
        "TIMELINE_REVISION_CONFLICT",
        "TIMELINE_UPSTREAM_CONFLICT",
        "TIMELINE_VIDEO_SELECTION_CONFLICT",
        "ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS",
        "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH",
        "PRODUCTION_SESSION_ACTIVE_CONFLICT",
        "PRODUCTION_SESSION_PLAN_STALE",
        "PRODUCTION_SESSION_REVISION_CONFLICT",
        "PRODUCTION_SESSION_STATE_INVALID",
        "PRODUCTION_SESSION_TERMINAL",
        "PRODUCTION_SESSION_REVIEW_NOT_READY",
        "PRODUCTION_SESSION_APPROVALS_INCOMPLETE",
        "PRODUCTION_SESSION_TIMELINE_CHOICE_MISMATCH",
        "PRODUCTION_SESSION_HUMAN_APPROVAL_INVALID",
        "PRODUCTION_CHOICE_REVISION_CONFLICT",
        "PRODUCTION_CHOICE_ALREADY_CONFIRMED",
        "PRODUCTION_SESSION_ITEM_REVISION_CONFLICT",
        "PRODUCTION_SESSION_ITEM_NOT_RETRYABLE",
        "IDEMPOTENCY_KEY_CONFLICT",
        "UPSCALE_SELECTION_STALE",
        "UPSCALE_PLAN_STALE",
        "UPSCALE_PLAN_EXPIRED",
        "UPSCALE_CLEANUP_PLAN_STALE",
        "UPSCALE_CLEANUP_ACTIVE_LEASE",
        "DELIVERY_SELECTION_PLAN_STALE",
        "UPSCALE_PRESET_CODE_CONFLICT",
        "UPSCALE_PRESET_VERSION_DUPLICATE",
    }:
        status = 409
    else:
        status = 422
    return ApiError(error.code, error.message, status_code=status, details=error.details, suggested_action=error.suggested_action)
