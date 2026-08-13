from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.experiments import (
    ExperimentConfirmRequest,
    ExperimentExpandRequest,
    ExperimentPlanRequest,
    GenerationIntentRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.experiments import ExperimentService
from local_drama.application.generation import GenerationService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["generation-plans"])


@router.post("/generation-intents", status_code=201, operation_id="createGenerationIntent")
async def create_intent(payload: GenerationIntentRequest, request: Request) -> dict[str, object]:
    try:
        return {"intent": GenerationService(request.app.state.database, request.app.state.settings).create_intent(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-intents", operation_id="listGenerationIntents")
async def list_intents(request: Request, project_id: str | None = None) -> dict[str, object]:
    return {"items": GenerationService(request.app.state.database, request.app.state.settings).list_intents(project_id)}


@router.get("/generation-intents/{intent_id}", operation_id="getGenerationIntent")
async def get_intent(intent_id: str, request: Request) -> dict[str, object]:
    try:
        return {"intent": GenerationService(request.app.state.database, request.app.state.settings).get_intent(intent_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-experiments", status_code=201, operation_id="createGenerationExperiment")
async def create_experiment(payload: ExperimentPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"experiment": ExperimentService(request.app.state.database).create_plan(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-experiments/{experiment_id}", operation_id="getGenerationExperiment")
async def get_experiment(experiment_id: str, request: Request) -> dict[str, object]:
    try:
        return {"experiment": ExperimentService(request.app.state.database).get_plan(experiment_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/generation-experiments/{experiment_id}/estimate", operation_id="estimateGenerationExperiment")
async def estimate(experiment_id: str, request: Request) -> dict[str, object]:
    try:
        return {"estimate": ExperimentService(request.app.state.database).estimate(experiment_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-experiments/{experiment_id}:confirm", operation_id="confirmGenerationExperiment")
async def confirm(experiment_id: str, payload: ExperimentConfirmRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "result": ExperimentService(request.app.state.database).confirm(
                experiment_id,
                payload.plan_hash,
                limit=payload.limit,
                confirm_large_matrix=payload.confirm_large_matrix,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-experiments/{experiment_id}:expand", operation_id="expandGenerationExperiment")
async def expand(experiment_id: str, payload: ExperimentExpandRequest, request: Request) -> dict[str, object]:
    try:
        return {"result": ExperimentService(request.app.state.database).expand(experiment_id, payload.limit)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/experiment-cells/{cell_id}:cancel", operation_id="cancelExperimentCell")
async def cancel_cell(cell_id: str, request: Request) -> dict[str, object]:
    try:
        return {"cell": ExperimentService(request.app.state.database).cancel_cell(cell_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/generation-experiments/{experiment_id}:cancel-remaining", operation_id="cancelRemainingGenerationExperiment")
async def cancel_remaining(experiment_id: str, request: Request) -> dict[str, object]:
    try:
        return {"result": ExperimentService(request.app.state.database).cancel_remaining(experiment_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
