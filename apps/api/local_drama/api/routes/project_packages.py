from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from local_drama.api.uploading import receive_bounded_upload
from local_drama.application.errors import api_error_from_domain
from local_drama.application.project_packages import ProjectPackageService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/project-packages", tags=["project-packages"])


class StageProjectPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inbox_name: str = Field(min_length=1, max_length=255)


class CommitProjectPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_mode: Literal["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"]
    code: str | None = Field(default=None, min_length=2, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=200)


def service(request: Request) -> ProjectPackageService:
    settings = request.app.state.settings
    return ProjectPackageService(request.app.state.database, settings.projects_root, settings.data_root, settings=settings)


@router.get(":inbox", operation_id="listProjectPackageInbox")
async def list_project_package_inbox(request: Request) -> dict[str, object]:
    return {
        "items": service(request).list_inbox_packages(),
        "read_only": True,
        "runtime_contacted": False,
        "network_contacted": False,
        "mutated": False,
    }


@router.post(":upload", status_code=201, operation_id="uploadProjectPackage")
async def upload_project_package(request: Request) -> dict[str, object]:
    try:
        async with receive_bounded_upload(
            request, work_group="project-package-uploads", allowed_suffixes=frozenset({".ldspkg"}),
            maximum_bytes=request.app.state.settings.uploads.project_package_mb * 1024 * 1024,
            default_filename="project.ldspkg", error_prefix="PROJECT_PACKAGE_UPLOAD",
        ) as (temporary, safe_filename, _received_bytes):
            return {"package": service(request).import_browser_upload(temporary, safe_filename)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(":stage", operation_id="stageProjectPackage")
async def stage_project_package(payload: StageProjectPackageRequest, request: Request) -> dict[str, object]:
    try:
        return {"staging": service(request).stage_from_inbox(payload.inbox_name)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{stage_token}:dry-run", operation_id="dryRunStagedProjectPackage")
async def dry_run_staged_project_package(stage_token: str, request: Request) -> dict[str, object]:
    try:
        return {"dry_run": service(request).dry_run_staged(stage_token)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{stage_token}:commit", operation_id="commitStagedProjectPackage", status_code=201)
async def commit_staged_project_package(
    stage_token: str, payload: CommitProjectPackageRequest, request: Request
) -> dict[str, object]:
    try:
        package_service = service(request)
        if payload.identity_mode == "REBIND_EXISTING":
            if payload.code is not None or payload.title is not None:
                raise DomainRuleError("PROJECT_PACKAGE_REBIND_FIELDS_FORBIDDEN", "rebind 不接受新 code 或 title")
            return {"commit": package_service.rebind_existing(stage_token)}
        if payload.code is None or payload.title is None:
            raise DomainRuleError("PROJECT_PACKAGE_COPY_IDENTITY_REQUIRED", "导入副本必须提供新 code 与 title")
        return {"commit": package_service.import_as_copy(stage_token, code=payload.code, title=payload.title)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
