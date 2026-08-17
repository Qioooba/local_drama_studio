from __future__ import annotations

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.automation_workflows import (
    AutomationWorkflowPauseRequest,
    AutomationWorkflowRequest,
    AutomationWorkflowResumeRequest,
    AutomationWorkflowRunRequest,
    AutomationWorkflowStepRequest,
    AutomationWorkflowTemplateRequest,
)
from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["automation-workflows"])


def service(request: Request) -> AutomationWorkflowService:
    return AutomationWorkflowService(request.app.state.database)


@router.post("/projects/{project_id}/automation-workflows", status_code=201, operation_id="createAutomationWorkflow")
async def create_workflow(project_id: str, payload: AutomationWorkflowRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow": service(request).create_workflow(project_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/automation-workflows:from-template", status_code=201, operation_id="createAutomationWorkflowFromTemplate")
async def create_workflow_from_template(project_id: str, payload: AutomationWorkflowTemplateRequest, request: Request) -> dict[str, object]:
    try:
        return {"workflow": service(request).create_from_template(project_id, template_code=payload.template_code, title=payload.title)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/automation-templates", operation_id="listAutomationWorkflowTemplates")
async def list_templates(request: Request) -> dict[str, object]:
    return {"items": service(request).list_templates()}


@router.get("/projects/{project_id}/automation-workflows", operation_id="listAutomationWorkflows")
async def list_workflows(project_id: str, request: Request, include_archived: bool = False) -> dict[str, object]:
    try:
        return service(request).list_workflows(project_id, include_archived=include_archived)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/automation-workflows/{workflow_id}", operation_id="getAutomationWorkflow")
async def get_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    try:
        return {"workflow": service(request).get_workflow(workflow_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-workflows/{workflow_id}:plan", operation_id="planAutomationWorkflow")
async def plan_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).plan_workflow(workflow_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-workflows/{workflow_id}/runs", status_code=201, operation_id="startAutomationWorkflowRun")
async def start_run(
    workflow_id: str,
    payload: AutomationWorkflowRunRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return {"run": service(request).start_run(workflow_id, plan_hash=payload.plan_hash, idempotency_key=idempotency_key)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/automation-runs", operation_id="listAutomationWorkflowRuns")
async def list_runs(project_id: str, request: Request, status: str | None = None, limit: int = 100) -> dict[str, object]:
    try:
        return service(request).list_runs(project_id, status=status, limit=limit)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/automation-runs/{run_id}", operation_id="getAutomationWorkflowRun")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).get_run(run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-runs/{run_id}:step", operation_id="stepAutomationWorkflowRun")
async def step_run(run_id: str, payload: AutomationWorkflowStepRequest, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).step_run(run_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-runs/{run_id}:resume", operation_id="resumeAutomationWorkflowRun")
async def resume_run(run_id: str, payload: AutomationWorkflowResumeRequest, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).resume_run(run_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-runs/{run_id}:pause", operation_id="pauseAutomationWorkflowRun")
async def pause_run(run_id: str, payload: AutomationWorkflowPauseRequest, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).pause_run(run_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/automation-runs/{run_id}:cancel", operation_id="cancelAutomationWorkflowRun")
async def cancel_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        return {"run": service(request).cancel_run(run_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
