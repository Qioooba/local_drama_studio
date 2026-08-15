from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g7 import BrandKitRequest, WorkspaceAssetAuthorizationRequest
from local_drama.api.schemas.g7_model import LocalModelReferenceRequest, ModelCompatibilityRequest, ModelLicenseEvidenceRequest
from local_drama.application.errors import api_error_from_domain
from local_drama.application.g6_readiness import G6ReadinessService
from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.g8_readiness import G8ReadinessService
from local_drama.application.g9_readiness import G9ReadinessService
from local_drama.application.i2v_probe import I2VProbePlanService
from local_drama.application.local_picker import pick_local_document_file, pick_local_model_file
from local_drama.application.model_compatibility import ModelCompatibilityService
from local_drama.application.network_e2e import NetworkE2EService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["phase-gates"])


@router.post("/system/dialogs:model-file", operation_id="pickLocalModelFile")
async def pick_model_file() -> dict[str, object]:
    try:
        return {"selection": pick_local_model_file()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/system/dialogs:document-file", operation_id="pickLocalDocumentFile")
async def pick_document_file() -> dict[str, object]:
    try:
        return {"selection": pick_local_document_file()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6", operation_id="getG6Readiness")
async def get_g6_readiness(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"readiness": G6ReadinessService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6/i2v-probe-plan", operation_id="planG6I2VProbe")
async def plan_g6_i2v_probe(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"plan": I2VProbePlanService(request.app.state.database).plan(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g7", operation_id="getG7Readiness")
async def get_g7_readiness(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"readiness": G7ReadinessService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g8", operation_id="getG8Readiness")
async def get_g8_readiness(project_id: str, request: Request, episode_id: str | None = None) -> dict[str, object]:
    try:
        return {"readiness": G8ReadinessService(request.app.state.database).inspect(project_id, episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g9", operation_id="getG9Readiness")
async def get_g9_readiness(project_id: str, request: Request, episode_id: str | None = None) -> dict[str, object]:
    try:
        return {"readiness": G9ReadinessService(request.app.state.database).inspect(project_id, episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/model-compatibility", operation_id="getModelCompatibility")
async def get_model_compatibility(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"compatibility": ModelCompatibilityService(request.app.state.database).project_snapshot(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/model-artifacts", status_code=201, operation_id="registerLocalModelReference")
async def register_local_model_reference(project_id: str, payload: LocalModelReferenceRequest, request: Request) -> dict[str, object]:
    try:
        artifact = ModelCompatibilityService(request.app.state.database).register_local_reference(
            project_id,
            payload.code,
            payload.kind,
            payload.machine_path_ref,
            payload.license_note,
        )
        return {"artifact": artifact}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/gates/g7/network-e2e", status_code=201, operation_id="runG7NetworkE2E")
async def run_g7_network_e2e(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"attestation": NetworkE2EService(request.app.state.database).run(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/workspace-assets/authorize", status_code=201, operation_id="authorizeWorkspaceAsset")
async def authorize_workspace_asset(project_id: str, payload: WorkspaceAssetAuthorizationRequest, request: Request) -> dict[str, object]:
    try:
        return {"authorization": WorkspaceAssetService(request.app.state.database, request.app.state.settings).authorize_media_version(project_id, payload.media_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/brand-kits", status_code=201, operation_id="createBrandKit")
async def create_brand_kit(project_id: str, payload: BrandKitRequest, request: Request) -> dict[str, object]:
    try:
        return {"brand_kit": WorkspaceAssetService(request.app.state.database, request.app.state.settings).create_brand_kit(project_id, payload.code, payload.title, payload.tokens)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/model-compatibility-report", status_code=201, operation_id="createModelCompatibilityReport")
async def create_model_compatibility_report(project_id: str, payload: ModelCompatibilityRequest, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return {"report": ModelCompatibilityService(request.app.state.database).report(payload.model_artifact_id, project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/model-license-evidence", status_code=201, operation_id="importModelLicenseEvidence")
async def import_model_license_evidence(project_id: str, payload: ModelLicenseEvidenceRequest, request: Request) -> dict[str, object]:
    try:
        service = ModelCompatibilityService(request.app.state.database)
        evidence = service.import_license_evidence(
            project_id,
            payload.model_artifact_id,
            payload.evidence_path,
            payload.license_name,
            payload.license_status,
            request.app.state.settings,
        )
        report = service.report(payload.model_artifact_id, project_id)
        return {"evidence": evidence, "report": report}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
