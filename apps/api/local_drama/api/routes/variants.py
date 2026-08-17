from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.variants import (
    VariantCreateRequest,
    VariantDeriveRequest,
    VariantPlanRequest,
    VariantSeedBatchRequest,
    VariantSubmitRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.generation import GenerationService
from local_drama.application.prompt_anchors import PromptAnchorService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["generation-variants"])


def service(request: Request) -> GenerationService:
    return GenerationService(request.app.state.database, request.app.state.settings)


@router.post("/generation-variants/{variant_id}:derive-plan", operation_id="deriveGenerationVariantPlan")
async def derive_variant_plan(variant_id: str, payload: VariantDeriveRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "plan": service(request).derive_variant_plan(
                variant_id,
                payload.operation,
                explicit_seed=payload.explicit_seed,
                prompt_revision_id=payload.prompt_revision_id,
                first_frame_media_version_id=payload.first_frame_media_version_id,
                profile_version_id=payload.profile_version_id,
                branch_reason=payload.branch_reason,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-variants/{variant_id}:derive-seed-batch", operation_id="deriveGenerationVariantSeedBatch")
async def derive_seed_batch(variant_id: str, payload: VariantSeedBatchRequest, request: Request) -> dict[str, object]:
    try:
        return {"batch": service(request).derive_seed_batch(variant_id, payload.seeds, payload.branch_reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-variants:plan", operation_id="planGenerationVariant")
async def plan_variant(payload: VariantPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).preflight_variant(payload.intent_id, payload.to_domain())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-variants", status_code=201, operation_id="createGenerationVariant")
async def create_variant(payload: VariantCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "variant": service(request).create_confirmed_variant(payload.intent_id, payload.to_domain(), payload.plan_hash)
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-variants:submit", status_code=201, operation_id="submitGenerationVariant")
async def submit_variant(payload: VariantSubmitRequest, request: Request) -> dict[str, object]:
    try:
        return service(request).submit_confirmed_variant(
            payload.intent_id,
            payload.to_domain(),
            payload.plan_hash,
            payload.idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-intents/{intent_id}/variants", operation_id="listGenerationVariants")
async def list_variants(intent_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_variants(intent_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-variants/{variant_id}", operation_id="getGenerationVariant")
async def get_variant(variant_id: str, request: Request) -> dict[str, object]:
    try:
        return {"variant": service(request).get_variant(variant_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-variants/{variant_id}/lineage", operation_id="getGenerationVariantLineage")
async def get_lineage(variant_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).lineage(variant_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}/prompt-anchor", operation_id="getShotPromptAnchor")
async def get_shot_prompt_anchor(shot_id: str, request: Request) -> dict[str, object]:
    """Preview the exact character appearance anchors injected at submit time."""
    try:
        return PromptAnchorService(request.app.state.database).shot_prompt_anchor(shot_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
