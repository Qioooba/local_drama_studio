from __future__ import annotations

from local_drama.domain.errors import DomainRuleError
from local_drama.errors import ApiError


def api_error_from_domain(error: DomainRuleError) -> ApiError:
    if error.code in {"AUTOMATION_TOKEN_REQUIRED", "AUTOMATION_TOKEN_INVALID", "AUTOMATION_SCOPE_FORBIDDEN", "AUTOMATION_PROJECT_FORBIDDEN"}:
        status = 403
    elif error.code.endswith("_NOT_FOUND") or error.code in {"PROJECT_NOT_FOUND", "SHOT_NOT_FOUND"}:
        status = 404
    elif error.code in {
        "REVISION_CONFLICT",
        "INVALID_STATE_TRANSITION",
        "PROJECT_CODE_EXISTS",
        "REVIEW_STALE",
        "REVIEW_BATCH_STALE",
        "REVIEW_BATCH_TOKEN_INVALID",
        "CONTINUITY_IMPACT_CONFIRMATION_REQUIRED",
        "CONTINUITY_IMPACT_STALE",
        "MACHINE_QC_REQUIRED",
        "PROFILE_NOT_PUBLISHED",
        "IDEMPOTENCY_PAYLOAD_MISMATCH",
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
        "SHOT_REVISION_CONFLICT",
        "ASSET_PROPOSAL_REVISION_CONFLICT",
        "ASSET_PROPOSAL_ALREADY_DECIDED",
    }:
        status = 409
    else:
        status = 422
    return ApiError(error.code, error.message, status_code=status, details=error.details, suggested_action=error.suggested_action)
