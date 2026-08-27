from __future__ import annotations

from fastapi import APIRouter, Query, Request

from local_drama.api.schemas.post_v2 import (
    EpisodePostOverviewResponse,
    ReviewAnnotationCommandResponse,
    ReviewAnnotationCreateCommand,
    ReviewAnnotationPage,
    ReviewDecisionCommandResponse,
    ReviewDecisionCreateCommand,
    ReviewDecisionRevokeCommand,
    ReviewTargetKind,
    ReviewTargetPage,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.post import PostQueryService
from local_drama.application.review_annotations import ReviewAnnotationService
from local_drama.application.review_decisions import ReviewDecisionCommandService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.post_repository import SqlitePostReadRepository
from local_drama.infrastructure.database.review_annotation_repository import SqliteReviewAnnotationRepository
from local_drama.infrastructure.database.review_decision_repository import SqliteReviewDecisionCommandRepository

router = APIRouter(tags=["post-v2"])


def _queries(request: Request) -> PostQueryService:
    return PostQueryService(SqlitePostReadRepository(request.app.state.database))


def _commands(request: Request) -> ReviewDecisionCommandService:
    return ReviewDecisionCommandService(SqliteReviewDecisionCommandRepository(request.app.state.database))


def _annotations(request: Request) -> ReviewAnnotationService:
    return ReviewAnnotationService(SqliteReviewAnnotationRepository(request.app.state.database))


@router.get(
    "/episodes/{episode_id}/post/overview",
    response_model=EpisodePostOverviewResponse,
    operation_id="getEpisodePostOverviewV2",
)
async def overview(episode_id: str, request: Request) -> EpisodePostOverviewResponse:
    try:
        return EpisodePostOverviewResponse.model_validate(_queries(request).overview(episode_id))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/episodes/{episode_id}/review-targets",
    response_model=ReviewTargetPage,
    operation_id="listEpisodeReviewTargetsV2",
)
async def review_targets(
    episode_id: str,
    request: Request,
    target_kind: list[ReviewTargetKind] = Query(default=[]),  # noqa: B008
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    include_resolved: bool = False,
) -> ReviewTargetPage:
    try:
        return ReviewTargetPage.model_validate(_queries(request).review_targets(
            episode_id,
            cursor=cursor,
            limit=limit,
            target_kinds=set(target_kind),
            include_resolved=include_resolved,
        ))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/review-decisions",
    response_model=ReviewDecisionCommandResponse,
    status_code=201,
    operation_id="createReviewDecisionV2",
)
async def create_review_decision(
    payload: ReviewDecisionCreateCommand, request: Request
) -> ReviewDecisionCommandResponse:
    try:
        return ReviewDecisionCommandResponse.model_validate(_commands(request).create(payload.model_dump()))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/review-decisions/{decision_id}:revoke",
    response_model=ReviewDecisionCommandResponse,
    operation_id="revokeReviewDecisionV2",
)
async def revoke_review_decision(
    decision_id: str, payload: ReviewDecisionRevokeCommand, request: Request
) -> ReviewDecisionCommandResponse:
    try:
        return ReviewDecisionCommandResponse.model_validate(
            _commands(request).revoke(decision_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/review-targets/{target_kind}/{target_id}/annotations",
    response_model=ReviewAnnotationPage,
    operation_id="listReviewAnnotationsV2",
)
async def list_review_annotations(
    target_kind: ReviewTargetKind,
    target_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=100),  # noqa: B008
) -> ReviewAnnotationPage:
    try:
        return ReviewAnnotationPage.model_validate(
            _annotations(request).list_page(target_kind, target_id, cursor=cursor, limit=limit)
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/review-targets/{target_kind}/{target_id}/annotations",
    response_model=ReviewAnnotationCommandResponse,
    status_code=201,
    operation_id="createReviewAnnotationV2",
)
async def create_review_annotation(
    target_kind: ReviewTargetKind,
    target_id: str,
    payload: ReviewAnnotationCreateCommand,
    request: Request,
) -> ReviewAnnotationCommandResponse:
    try:
        return ReviewAnnotationCommandResponse.model_validate(
            _annotations(request).create(target_kind, target_id, payload.model_dump())
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
