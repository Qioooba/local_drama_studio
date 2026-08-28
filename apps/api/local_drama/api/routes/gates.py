from __future__ import annotations

import ipaddress
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

from local_drama.api.schemas.g3 import (
    I2VEvidenceKeyframePrepareRequest,
    I2VEvidenceProbeFinalizeRequest,
    I2VEvidenceProbeSubmitRequest,
)
from local_drama.api.schemas.g7 import (
    BrandKitRequest,
    CompliancePolicyRequest,
    ProjectAssetGrantRequest,
    ProjectAssetGrantRevokeRequest,
    WatermarkProfileRequest,
    WorkspaceAssetAuthorizationRequest,
)
from local_drama.api.schemas.g7_model import (
    GlobalModelArtifactResponse,
    GlobalModelCompatibilityReportResponse,
    GlobalModelRegistryResponse,
    LocalModelReferenceRequest,
    ModelCompatibilityRequest,
    ModelLicenseEvidenceRequest,
    ModelRegistryScanRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.g6_readiness import G6ReadinessService
from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.g8_readiness import G8ReadinessService
from local_drama.application.g9_readiness import G9ReadinessService
from local_drama.application.i2v_probe import I2VProbePlanService
from local_drama.application.model_compatibility import ModelCompatibilityService
from local_drama.application.network_e2e import NetworkE2EService
from local_drama.application.t2i_probe import T2IProbePlanService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import FilePickerRequest

router = APIRouter(tags=["phase-gates"])


def _require_server_dialog(request: Request) -> None:
    if not request.app.state.settings.is_lan_service:
        return
    host = request.client.host if request.client else ""
    try:
        allowed = ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = host.casefold() in {"localhost", "testclient"}
    if not allowed:
        raise DomainRuleError("SERVER_DIALOG_REMOTE_CLIENT", "远程浏览器不能打开服务器桌面的文件选择器",
                              suggested_action="请使用浏览器上传，或从管理员配置的服务端资源库选择")


@router.post("/system/dialogs:model-file", operation_id="pickLocalModelFile")
def pick_model_file(request: Request) -> dict[str, object]:
    try:
        _require_server_dialog(request)
        selection = request.app.state.platform.file_picker.choose(
            FilePickerRequest("MODEL", "选择电脑中的模型文件", (".safetensors", ".ckpt", ".bin", ".pt", ".pth"))
        )
        return {"selection": selection.public()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/system/dialogs:document-file", operation_id="pickLocalDocumentFile")
def pick_document_file(request: Request) -> dict[str, object]:
    try:
        _require_server_dialog(request)
        selection = request.app.state.platform.file_picker.choose(
            FilePickerRequest("DOCUMENT", "选择电脑中的剧本文档", (".txt", ".md", ".markdown", ".docx"))
        )
        return {"selection": selection.public()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/model-registry/roots", operation_id="listModelLibraryRoots")
async def list_model_library_roots(request: Request) -> dict[str, object]:
    items = [{"id": f"root-{index + 1}", "label": root.resolve().name or f"模型库 {index + 1}", "path": str(root.resolve())}
             for index, root in enumerate(request.app.state.settings.model_library_roots)]
    return {"items": items, "configured": bool(items), "read_only": True}


@router.post("/model-registry:scan", operation_id="scanLocalModelRegistry")
async def scan_local_model_registry(payload: ModelRegistryScanRequest, request: Request) -> dict[str, object]:
    try:
        requested = Path(payload.root_path).resolve()
        configured = tuple(root.resolve() for root in request.app.state.settings.model_library_roots)
        if configured and requested not in configured:
            raise DomainRuleError("MODEL_LIBRARY_ROOT_NOT_ALLOWED", "只能扫描管理员配置的服务端模型库")
        if request.app.state.settings.is_lan_service and not configured:
            raise DomainRuleError("MODEL_LIBRARY_ROOTS_NOT_CONFIGURED", "服务器尚未配置可供远程选择的模型库",
                                  suggested_action="设置 LOCAL_DRAMA_MODEL_LIBRARY_ROOTS 后重启服务")
        return {"scan": ModelCompatibilityService.scan_local_directory(str(requested), payload.max_files)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6", operation_id="getG6Readiness")
async def get_g6_readiness(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"readiness": G6ReadinessService(request.app.state.database).inspect(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6/i2v-probe-plan", operation_id="planG6I2VProbe")
async def plan_g6_i2v_probe(
    project_id: str,
    request: Request,
    profile_version_id: str | None = None,
    workflow_version_id: str | None = None,
) -> dict[str, object]:
    try:
        return {
            "plan": I2VProbePlanService(request.app.state.database).plan(
                project_id, profile_version_id, workflow_version_id
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/projects/{project_id}/gates/g6/i2v-probe-keyframe:prepare",
    status_code=201,
    operation_id="prepareG6I2VEvidenceKeyframe",
)
async def prepare_g6_i2v_evidence_keyframe(
    project_id: str,
    payload: I2VEvidenceKeyframePrepareRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return I2VProbePlanService(
            request.app.state.database,
            request.app.state.settings,
        ).prepare_keyframe(
            project_id,
            payload.source_media_version_id,
            payload.confirm_review_checks,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/gates/g6/i2v-probe:submit", status_code=201, operation_id="submitG6I2VEvidenceProbe")
async def submit_g6_i2v_probe(
    project_id: str, payload: I2VEvidenceProbeSubmitRequest, request: Request,
) -> dict[str, object]:
    try:
        return I2VProbePlanService(request.app.state.database, request.app.state.settings).submit(
            project_id,
            payload.profile_version_id,
            payload.workflow_version_id,
            payload.plan_hash,
            payload.idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/gates/g6/i2v-probe:finalize", operation_id="finalizeG6I2VEvidenceProbe")
async def finalize_g6_i2v_probe(
    project_id: str, payload: I2VEvidenceProbeFinalizeRequest, request: Request,
) -> dict[str, object]:
    try:
        return I2VProbePlanService(request.app.state.database, request.app.state.settings).finalize(project_id, payload.job_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/gates/g6/t2i-probe-plan", operation_id="planG6T2IProbe")
async def plan_g6_t2i_probe(
    project_id: str,
    request: Request,
    profile_version_id: str | None = None,
    workflow_version_id: str | None = None,
) -> dict[str, object]:
    try:
        return {
            "plan": T2IProbePlanService(request.app.state.database).plan(
                project_id, profile_version_id, workflow_version_id
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/gates/g6/t2i-probe:submit", status_code=201, operation_id="submitG6T2IEvidenceProbe")
async def submit_g6_t2i_probe(
    project_id: str, payload: I2VEvidenceProbeSubmitRequest, request: Request,
) -> dict[str, object]:
    try:
        return T2IProbePlanService(request.app.state.database, request.app.state.settings).submit(
            project_id,
            payload.profile_version_id,
            payload.workflow_version_id,
            payload.plan_hash,
            payload.idempotency_key,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/gates/g6/t2i-probe:finalize", operation_id="finalizeG6T2IEvidenceProbe")
async def finalize_g6_t2i_probe(
    project_id: str, payload: I2VEvidenceProbeFinalizeRequest, request: Request,
) -> dict[str, object]:
    try:
        return T2IProbePlanService(request.app.state.database, request.app.state.settings).finalize(project_id, payload.job_id)
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


@router.get("/model-registry", operation_id="getGlobalModelRegistry", response_model=GlobalModelRegistryResponse)
async def get_global_model_registry(request: Request) -> dict[str, object]:
    return {"compatibility": ModelCompatibilityService(request.app.state.database).system_snapshot()}


@router.post(
    "/model-registry/artifacts",
    status_code=201,
    operation_id="registerGlobalModelReference",
    response_model=GlobalModelArtifactResponse,
)
async def register_global_model_reference(payload: LocalModelReferenceRequest, request: Request) -> dict[str, object]:
    try:
        artifact = ModelCompatibilityService(request.app.state.database).register_local_reference(
            None,
            payload.code,
            payload.kind,
            payload.machine_path_ref,
            payload.license_note,
        )
        return {"artifact": artifact}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/model-registry/compatibility-report",
    status_code=201,
    operation_id="createGlobalModelCompatibilityReport",
    response_model=GlobalModelCompatibilityReportResponse,
)
async def create_global_model_compatibility_report(payload: ModelCompatibilityRequest, request: Request) -> dict[str, object]:
    try:
        return {"report": ModelCompatibilityService(request.app.state.database).report(
            payload.model_artifact_id,
            required_capability=payload.required_capability,
        )}
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


@router.get("/projects/{project_id}/asset-grant-candidates", operation_id="listProjectAssetGrantCandidates")
async def list_project_asset_grant_candidates(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": WorkspaceAssetService(request.app.state.database, request.app.state.settings).list_grant_candidates(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/workspace-assets/{media_version_id}:revoke", operation_id="revokeWorkspaceAssetAuthorization")
async def revoke_workspace_asset_authorization(project_id: str, media_version_id: str, payload: ProjectAssetGrantRevokeRequest, request: Request) -> dict[str, object]:
    try:
        return {"authorization": WorkspaceAssetService(request.app.state.database, request.app.state.settings).revoke_authorization(project_id, media_version_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/workspace-assets/authorizations", operation_id="listWorkspaceAssetAuthorizations")
async def list_workspace_asset_authorizations(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": WorkspaceAssetService(request.app.state.database, request.app.state.settings).list_authorizations(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/asset-grants", operation_id="listProjectAssetGrants")
async def list_project_asset_grants(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": WorkspaceAssetService(request.app.state.database, request.app.state.settings).list_grants(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/asset-grants", status_code=201, operation_id="createProjectAssetGrant")
async def create_project_asset_grant(project_id: str, payload: ProjectAssetGrantRequest, request: Request) -> dict[str, object]:
    try:
        return {"grant": WorkspaceAssetService(request.app.state.database, request.app.state.settings).create_grant(project_id, payload.authorization_id, payload.access_mode)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/asset-grants/{grant_id}:revoke", operation_id="revokeProjectAssetGrant")
async def revoke_project_asset_grant(grant_id: str, payload: ProjectAssetGrantRevokeRequest, request: Request) -> dict[str, object]:
    try:
        return {"grant": WorkspaceAssetService(request.app.state.database, request.app.state.settings).revoke_grant(grant_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/brand-kits", status_code=201, operation_id="createBrandKit")
async def create_brand_kit(project_id: str, payload: BrandKitRequest, request: Request) -> dict[str, object]:
    try:
        return {"brand_kit": WorkspaceAssetService(request.app.state.database, request.app.state.settings).create_brand_kit(project_id, payload.code, payload.title, payload.tokens)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/brand-controls", operation_id="listBrandControls")
async def list_brand_controls(project_id: str, request: Request) -> dict[str, object]:
    try:
        controls = WorkspaceAssetService(request.app.state.database, request.app.state.settings).list_brand_controls(project_id)
        return {"brand_kits": controls["brand_kits"], "watermark_profiles": controls["watermark_profiles"], "compliance_policies": controls["compliance_policies"]}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/watermark-profiles", status_code=201, operation_id="createWatermarkProfile")
async def create_watermark_profile(project_id: str, payload: WatermarkProfileRequest, request: Request) -> dict[str, object]:
    try:
        return {"watermark_profile": WorkspaceAssetService(request.app.state.database, request.app.state.settings).create_watermark_profile(project_id, payload.code, payload.title, payload.config)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/compliance-policies", status_code=201, operation_id="createCompliancePolicy")
async def create_compliance_policy(project_id: str, payload: CompliancePolicyRequest, request: Request) -> dict[str, object]:
    try:
        return {"compliance_policy": WorkspaceAssetService(request.app.state.database, request.app.state.settings).create_compliance_policy(project_id, payload.code, payload.title, payload.rules)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/model-compatibility-report", status_code=201, operation_id="createModelCompatibilityReport")
async def create_model_compatibility_report(project_id: str, payload: ModelCompatibilityRequest, request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return {"report": ModelCompatibilityService(request.app.state.database).report(payload.model_artifact_id, project_id, required_capability=payload.required_capability)}
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


# Engineering phase-gate endpoints (G6/G7/G8/G9 readiness + I2V/T2I probes +
# network-e2e) are consumed by professional/internal verification flows, not by
# ordinary creator-facing product UI. Keep them fully functional while removing
# them from the public OpenAPI document (design §13.2 / §11.1).
for _route in router.routes:
    _path = getattr(_route, "path", "")
    if isinstance(_route, APIRoute) and "/gates/g" in _path:
        _route.include_in_schema = False
