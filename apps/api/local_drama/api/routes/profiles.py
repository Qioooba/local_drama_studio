from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.g3 import ProfileBindingRequest, ProfileContractDraftRequest, ProfileEvidencePublishRequest
from local_drama.application.configuration import ConfigurationService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.profiles import ProfileService
from local_drama.domain.errors import DomainRuleError
from local_drama.errors import ApiError
from local_drama.infrastructure.manifest import ManifestValidationError

router = APIRouter(tags=["profiles"])


def service(request: Request) -> ProfileService:
    return ProfileService(request.app.state.database, request.app.state.settings.manifest_path)


@router.get("/profiles/manifest", operation_id="getModelManifest")
async def manifest(request: Request) -> dict[str, object]:
    try:
        return {"manifest": service(request).get_manifest()}
    except ManifestValidationError as error:
        raise ApiError("MANIFEST_INVALID", str(error), status_code=503, retryable=False) from error


@router.get("/profiles", operation_id="listProfiles")
async def list_profiles(request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_profiles(), "manifest": service(request).get_manifest()}
    except ManifestValidationError as error:
        raise ApiError("MANIFEST_INVALID", str(error), status_code=503) from error


@router.get("/profile-versions/{profile_version_id}", operation_id="getProfileVersion")
async def get_profile_version(profile_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"profile_version": service(request).get_version(profile_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-versions/{profile_version_id}:derive-contract", status_code=201, operation_id="deriveProfileContractVersion")
async def derive_profile_contract(
    profile_version_id: str, payload: ProfileContractDraftRequest, request: Request
) -> dict[str, object]:
    try:
        return {
            "profile_version": service(request).derive_contract_version(
                profile_version_id,
                payload.expected_source_revision,
                payload.input_contract,
                payload.parameter_schema,
                payload.output_contract,
                payload.resource_policy,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-versions/{profile_version_id}:validate-contract", operation_id="validateProfileContractVersion")
async def validate_profile_contract(profile_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"validation": service(request).validate_contract_version(profile_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-versions/{profile_version_id}:publish-contract", operation_id="publishProfileContractVersion")
async def publish_profile_contract(profile_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"profile_version": service(request).publish_validated_contract(profile_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profiles:sync", operation_id="syncProfiles")
async def sync_profiles(request: Request) -> dict[str, object]:
    try:
        return service(request).sync_manifest()
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    except ManifestValidationError as error:
        raise ApiError("MANIFEST_INVALID", str(error), status_code=503) from error


@router.post("/profile-versions/{profile_version_id}:publish-evidence", operation_id="publishProfileVersionFromEvidence")
async def publish_profile_evidence(
    profile_version_id: str, payload: ProfileEvidencePublishRequest, request: Request
) -> dict[str, object]:
    try:
        return {
            "profile_version": service(request).publish_from_evidence(
                profile_version_id,
                payload.media_version_id,
                payload.workflow_version_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/profile-bindings", operation_id="bindProjectProfile")
async def bind_profile(project_id: str, payload: ProfileBindingRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "binding": ConfigurationService(request.app.state.database).bind_profile(
                project_id, payload.capability, payload.profile_version_id, payload.confirm_candidate
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
