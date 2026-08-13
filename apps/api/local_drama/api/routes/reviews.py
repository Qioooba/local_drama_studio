from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.reviews import BatchCommitRequest, BatchPreflightRequest, MachineCheckRequest, ReviewRequest, SelectionRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["reviews"])


def service(request: Request) -> ReviewService:
    return ReviewService(request.app.state.database, request.app.state.settings)


@router.get("/review-templates", operation_id="listReviewTemplates")
async def list_templates(request: Request) -> dict[str, object]:
    return {"items": service(request).templates()}


@router.get("/reviews/inbox", operation_id="getReviewInbox")
async def review_inbox(request: Request, project_id: str | None = None, media_kind: str | None = None, limit: int = 100) -> dict[str, object]:
    return {"items": service(request).inbox(project_id, media_kind, limit)}


@router.get("/subjects/{subject_type}/{subject_id}/review-context", operation_id="getReviewContext")
async def review_context(subject_type: str, subject_id: str, request: Request) -> dict[str, object]:
    try:
        if subject_type != "MEDIA_VERSION":
            raise DomainRuleError("UNSUPPORTED_REVIEW_SUBJECT", "G4 当前审核 subject_type 只支持 MEDIA_VERSION")
        return service(request).review_context(subject_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/subjects/{subject_type}/{subject_id}/reviews", status_code=201, operation_id="submitReview")
async def submit_review(subject_type: str, subject_id: str, payload: ReviewRequest, request: Request) -> dict[str, object]:
    try:
        if subject_type != "MEDIA_VERSION":
            raise DomainRuleError("UNSUPPORTED_REVIEW_SUBJECT", "G4 当前审核 subject_type 只支持 MEDIA_VERSION")
        checks = [item.model_dump() for item in payload.checks]
        return {
            "review": service(request).submit_review(
                subject_id,
                payload.template_version_id,
                payload.decision,
                payload.expected_subject_revision,
                checks,
                payload.comment,
                continuity_plan_hash=payload.continuity_plan_hash,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/approval-impact", operation_id="previewMediaApprovalImpact")
async def preview_media_approval_impact(media_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"impact": service(request).preview_approval_impact(media_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/subjects/{subject_type}/{subject_id}/reviews", operation_id="listReviews")
async def list_reviews(subject_type: str, subject_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list_reviews(subject_type, subject_id)}


@router.post("/reviews/{review_id}:void", operation_id="voidReview")
async def void_review(review_id: str, request: Request) -> dict[str, object]:
    try:
        return {"review": service(request).void_review(review_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/subjects/{subject_type}/{subject_id}/machine-checks", status_code=201, operation_id="runMachineCheck")
async def machine_check(subject_type: str, subject_id: str, payload: MachineCheckRequest, request: Request) -> dict[str, object]:
    try:
        if subject_type != "MEDIA_VERSION":
            raise DomainRuleError("UNSUPPORTED_REVIEW_SUBJECT", "机器 QC 当前只支持 MEDIA_VERSION")
        return {"machine_check": service(request).machine_check(subject_id, payload.policy_version)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/reviews/batch:preflight", operation_id="preflightReviewBatch")
async def batch_preflight(payload: BatchPreflightRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).batch_preflight(payload.project_id, [item.model_dump() for item in payload.items])}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/reviews/batch:commit", operation_id="commitReviewBatch")
async def batch_commit(payload: BatchCommitRequest, request: Request) -> dict[str, object]:
    try:
        return {"result": service(request).batch_commit(payload.plan_token, payload.decision, [item.model_dump() for item in payload.checks], payload.comment)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-versions/{media_version_id}:select", operation_id="selectMediaVersion")
async def select_media_version(media_version_id: str, payload: SelectionRequest, request: Request) -> dict[str, object]:
    try:
        return {"selection": service(request).select_version(media_version_id, payload.selection_type)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
