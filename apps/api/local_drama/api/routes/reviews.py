from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.reviews import (
    MachineCheckRequest,
    ReviewTemplateVersionRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["reviews"])


def service(request: Request) -> ReviewService:
    return ReviewService(request.app.state.database, request.app.state.settings)


@router.get("/review-templates", operation_id="listReviewTemplates")
async def list_templates(request: Request) -> dict[str, object]:
    return {"items": service(request).templates()}


@router.post("/review-templates", status_code=201, operation_id="createReviewTemplateVersion")
async def create_template_version(payload: ReviewTemplateVersionRequest, request: Request) -> dict[str, object]:
    """Append a versioned template; existing versions remain immutable."""
    try:
        template = service(request).create_template_version(
            payload.code,
            payload.subject_type,
            [item.model_dump() for item in payload.items],
        )
        return {"template": template}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/reviews/inbox", operation_id="getReviewInbox")
async def review_inbox(
    request: Request,
    project_id: str | None = None,
    media_kind: str | None = None,
    episode_id: str | None = None,
    age: str | None = None,
    priority: str | None = None,
    blocking: str | None = None,
    min_age_days: float | None = None,
    max_age_days: float | None = None,
    include_resolved: bool = False,
    cursor: int = 0,
    limit: int = 100,
) -> dict[str, object]:
    try:
        return service(request).inbox_page(
            project_id,
            media_kind,
            cursor,
            limit,
            episode_id=episode_id,
            age=age,
            priority=priority,
            blocking=blocking,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            include_resolved=include_resolved,
        )
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


@router.post("/subjects/{subject_type}/{subject_id}/machine-checks", status_code=201, operation_id="runMachineCheck")
async def machine_check(subject_type: str, subject_id: str, payload: MachineCheckRequest, request: Request) -> dict[str, object]:
    try:
        if subject_type != "MEDIA_VERSION":
            raise DomainRuleError("UNSUPPORTED_REVIEW_SUBJECT", "机器 QC 当前只支持 MEDIA_VERSION")
        return {"machine_check": service(request).machine_check(subject_id, payload.policy_version)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error

