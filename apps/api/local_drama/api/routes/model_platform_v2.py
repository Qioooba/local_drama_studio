"""Read/assignment API for the V2 Model Platform control plane."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Header, Query, Request
from starlette.concurrency import run_in_threadpool

from local_drama.api.schemas.model_platform import (
    CapabilityAssignmentPutRequest,
    ModelPlatformBusinessSelectionRolloutPutRequest,
    ModelPlatformComfySmokeSubmissionRequest,
    ModelPlatformComfyWorkflowBindingRequest,
    ModelPlatformExecutionPreviewRequest,
    ModelPlatformOfflineImportPlanRequest,
    ModelPlatformProfileCrosswalkApprovalRequest,
    ModelPlatformProfileCrosswalkRevocationRequest,
    ModelPlatformProfileProvisionRequest,
    ModelPlatformProfilePublishRequest,
    ModelPlatformProjectKnowledgeIndexPrepareRequest,
    ModelPlatformProjectKnowledgeSearchRequest,
    ModelPlatformQuickCreateV2DirectImagePreviewRequest,
    ModelPlatformQuickCreateV2DirectImageSubmitRequest,
    ModelPlatformQuickCreateV2ImageCandidatePreviewRequest,
    ModelPlatformQuickCreateV2ImageCandidateSubmitRequest,
    ModelPlatformQuickCreateV2ImageToVideoSubmitRequest,
    ModelPlatformTrustedDownloadPlanRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.model_platform.application.assignment_catalog import CapabilityAssignmentCatalogService
from local_drama.model_platform.application.business_selection_rollouts import (
    BusinessSelectionRolloutRequest,
    BusinessSelectionRolloutService,
)
from local_drama.model_platform.application.business_selection_shadow import BusinessSelectionShadowService
from local_drama.model_platform.application.candidate_readiness import CandidateReadinessService, RegisteredCandidateReadiness
from local_drama.model_platform.application.capability_resolution import (
    CapabilityAssignmentRequest,
    CapabilityAssignmentService,
    CapabilityScopeContext,
)
from local_drama.model_platform.application.capability_smoke import CapabilitySmokeService
from local_drama.model_platform.application.comfy_capability_smoke_jobs import ComfyCapabilitySmokeSubmissionService
from local_drama.model_platform.application.comfy_workflow_bindings import ComfyWorkflowBindingService
from local_drama.model_platform.application.discovery_registration import DiscoveryRegistrationService
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.generation_capability_configuration_facade import (
    GenerationCapabilityConfigurationFacade,
)
from local_drama.model_platform.application.installation_integrity import InstallationIntegrityService
from local_drama.model_platform.application.installation_plans import (
    ExpectedInstallArtifact,
    InstallationPlan,
    InstallationPlanService,
    OfflineImportPlanRequest,
    TrustedDownloadArtifact,
    TrustedDownloadPlanRequest,
)
from local_drama.model_platform.application.llama_cpp_discovery import LlamaCppDiscoveryOrchestrator
from local_drama.model_platform.application.model_lock_discovery import (
    ModelLockDiscoveryOrchestrator,
    configured_model_lock_runtime_ids,
)
from local_drama.model_platform.application.ollama_discovery import OllamaDiscoveryOrchestrator
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers
from local_drama.model_platform.application.profile_catalog import ProfileCatalogService
from local_drama.model_platform.application.profile_templates import ProfileTemplateService
from local_drama.model_platform.application.profile_version_crosswalks import (
    ProfileVersionCrosswalkRequest,
    ProfileVersionCrosswalkService,
)
from local_drama.model_platform.application.project_knowledge_indexing import (
    ProjectKnowledgeIndexPreparationService,
    ProjectKnowledgeIndexQueueService,
)
from local_drama.model_platform.application.project_knowledge_retrieval import ProjectKnowledgeRetrievalService
from local_drama.model_platform.application.quick_create_direct_execution import QuickCreateV2DirectImageService
from local_drama.model_platform.application.quick_create_readiness import QuickCreateV2ReadinessService
from local_drama.model_platform.application.quick_create_v2_image_candidates import (
    QuickCreateV2ImageCandidatePlan,
    QuickCreateV2ImageCandidatePreview,
    QuickCreateV2ImageCandidateService,
)
from local_drama.model_platform.application.quick_create_v2_image_to_video import QuickCreateV2ImageToVideoService
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2RunService
from local_drama.model_platform.application.validation_history import ValidationHistoryService

router = APIRouter(prefix="/model-platform", tags=["model-platform-v2"])


@router.get("/overview", operation_id="getModelPlatformOverview")
async def get_overview(request: Request) -> dict[str, object]:
    """Return bounded, non-sensitive control-plane counts for the model center.

    Discovery is deliberately reported separately from registered models.  A
    scanner observation is evidence only; it never means that a model is
    complete, verified, published, or executable.
    """
    active_model_lock_runtimes = configured_model_lock_runtime_ids(request.app.state.settings)
    active_runtime_placeholders = ",".join("?" for _ in active_model_lock_runtimes) or "NULL"
    with request.app.state.database.connect() as connection:
        row = connection.execute(
            f"""SELECT
              (SELECT COUNT(*) FROM mp_capability_definitions) AS capability_count,
              (SELECT COUNT(*) FROM mp_model_releases) AS registered_model_release_count,
              (SELECT COUNT(*) FROM mp_runtime_installations) AS runtime_installation_count,
              (SELECT COUNT(*) FROM (
                 SELECT ROW_NUMBER() OVER (
                   PARTITION BY COALESCE(runtime_version.runtime_installation_id, run.runtime_installation_version_id, run.source || ':' || observation.kind),
                                observation.native_id
                   ORDER BY observation.created_at DESC, observation.id DESC
                 ) AS current_rank
                 FROM mp_discovery_observations observation
                 JOIN mp_discovery_runs run ON run.id=observation.discovery_run_id
                 LEFT JOIN mp_runtime_installation_versions runtime_version
                   ON runtime_version.id=run.runtime_installation_version_id
                 WHERE run.status='SUCCEEDED'
                   AND (run.source<>'MODEL_LOCK' OR runtime_version.runtime_installation_id IN ({active_runtime_placeholders}))
              ) current_observation WHERE current_rank=1) AS discovery_observation_count,
              (SELECT COUNT(*) FROM mp_profile_publications WHERE status='PUBLISHED') AS published_profile_count"""
            , active_model_lock_runtimes,
        ).fetchone()
    return {
        "overview": {
            "capability_count": int(row["capability_count"]),
            "registered_model_release_count": int(row["registered_model_release_count"]),
            "runtime_installation_count": int(row["runtime_installation_count"]),
            "discovery_observation_count": int(row["discovery_observation_count"]),
            "published_profile_count": int(row["published_profile_count"]),
        },
        "read_only": True,
    }


@router.get("/quick-create-v2-readiness", operation_id="listModelPlatformQuickCreateV2Readiness")
async def list_quick_create_v2_readiness(request: Request) -> dict[str, object]:
    """Show V2 SYSTEM eligibility only; Quick Create still submits through V1."""
    items = QuickCreateV2ReadinessService(request.app.state.database).list()
    return {"items": [
        {"mode": item.mode, "capability_code": item.capability_code,
         "execution_profile_version_id": item.execution_profile_version_id, "ready": item.ready, "blocker": item.blocker}
        for item in items
    ], "read_only": True, "execution_switched": False}


@router.post("/quick-create-v2/direct-image:preview", operation_id="previewModelPlatformQuickCreateV2DirectImage")
async def preview_quick_create_v2_direct_image(
    payload: ModelPlatformQuickCreateV2DirectImagePreviewRequest,
    request: Request,
) -> dict[str, object]:
    """Preview the first V2-only Quick Create command without creating a Job."""
    try:
        preview = QuickCreateV2DirectImageService(request.app.state.database).preview(
            prompt=payload.prompt,
            run_overrides=payload.run_overrides,
        )
        return {
            "preview": {
                "capability_code": preview.capability_code,
                "execution_profile_version_id": preview.execution_profile_version_id,
                "resolution_hash": preview.resolution_hash,
                "executable": preview.executable,
                "blockers": list(preview.blockers),
            },
            "read_only": True,
            "execution_switched": True,
            "legacy_quick_generation_touched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/direct-image:submit", status_code=201, operation_id="submitModelPlatformQuickCreateV2DirectImage")
async def submit_quick_create_v2_direct_image(
    payload: ModelPlatformQuickCreateV2DirectImageSubmitRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    """Queue a V2-only direct image command; it never calls the V1 quick aggregate."""
    try:
        submitted = QuickCreateV2DirectImageService(request.app.state.database).submit(
            prompt=payload.prompt,
            run_overrides=payload.run_overrides,
            expected_resolution_hash=payload.expected_resolution_hash,
            idempotency_key=idempotency_key,
        )
        return {
            "execution": {
                "capability_code": "IMAGE_CONCEPT",
                "job_id": submitted.job_id,
                "execution_snapshot_id": submitted.execution_snapshot_id,
                "execution_snapshot_hash": submitted.execution_snapshot_hash,
                "handler_code": submitted.handler_code,
                "handler_version": submitted.handler_version,
                "idempotent_replay": submitted.idempotent_replay,
            },
            "execution_switched": True,
            "legacy_quick_generation_touched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/quick-create-v2/direct-image/jobs/{job_id}", operation_id="getModelPlatformQuickCreateV2DirectImageStatus")
async def get_quick_create_v2_direct_image_status(job_id: str, request: Request) -> dict[str, object]:
    """Read a direct-image V2 Job without opening a generic Job or V1 record."""
    try:
        status = QuickCreateV2DirectImageService(request.app.state.database).status(job_id)
        return {
            "execution": {
                "capability_code": "IMAGE_CONCEPT",
                "job_id": status.job_id,
                "state": status.state,
                "progress": dict(status.progress),
                "error_code": status.error_code,
                "error_detail_redacted": status.error_detail_redacted,
                "execution_snapshot_id": status.execution_snapshot_id,
                "execution_snapshot_hash": status.execution_snapshot_hash,
                "artifacts": [dict(item) for item in status.artifacts],
            },
            "read_only": True,
            "execution_switched": True,
            "legacy_quick_generation_touched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/image-candidates:preview", operation_id="previewModelPlatformQuickCreateV2ImageCandidates")
async def preview_quick_create_v2_image_candidates(
    payload: ModelPlatformQuickCreateV2ImageCandidatePreviewRequest,
    request: Request,
) -> dict[str, object]:
    """Plan a bounded V2 candidate set; no run or V1 record is created."""
    try:
        plan = QuickCreateV2ImageCandidateService(request.app.state.database).preview(
            prompt=payload.prompt, candidate_count=payload.candidate_count
        )
        return {
            "plan": _public_candidate_plan(plan), "read_only": True,
            "execution_switched": True, "legacy_quick_generation_touched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/image-candidates:submit", status_code=201, operation_id="submitModelPlatformQuickCreateV2ImageCandidates")
async def submit_quick_create_v2_image_candidates(
    payload: ModelPlatformQuickCreateV2ImageCandidateSubmitRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    """Create the V2 aggregate and one immutable V2 Job per candidate."""
    try:
        submitted = QuickCreateV2ImageCandidateService(request.app.state.database).submit(
            prompt=payload.prompt,
            expected_candidates=tuple(QuickCreateV2ImageCandidatePreview(**item.model_dump()) for item in payload.candidates),
            idempotency_key=idempotency_key,
        )
        return {
            "run": {"id": submitted.run.id, "mode": submitted.run.mode, "state": submitted.run.state,
                    "idempotent_replay": submitted.run.idempotent_replay},
            "executions": [{"capability_code": "IMAGE_CONCEPT", "job_id": job_id, "execution_snapshot_id": snapshot_id}
                           for job_id, snapshot_id in zip(submitted.job_ids, submitted.execution_snapshot_ids, strict=True)],
            "execution_switched": True, "legacy_quick_generation_touched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/runs/{run_id}/candidates/{step_id}:select", operation_id="selectModelPlatformQuickCreateV2ImageCandidate")
async def select_quick_create_v2_image_candidate(run_id: str, step_id: str, request: Request) -> dict[str, object]:
    """Freeze exactly one verified V2 candidate; arbitrary artifact IDs are impossible here."""
    try:
        service = QuickCreateV2RunService(request.app.state.database)
        service.select_image_candidate(run_id, step_id)
        return {"run": service.public_run(run_id), "execution_switched": True, "legacy_quick_generation_touched": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/runs/{run_id}/image-to-video:preview", operation_id="previewModelPlatformQuickCreateV2ImageToVideo")
async def preview_quick_create_v2_image_to_video(run_id: str, request: Request) -> dict[str, object]:
    try:
        preview = QuickCreateV2ImageToVideoService(request.app.state.database).preview(run_id=run_id)
        return {"preview": {"run_id": preview.run_id, "selected_image_artifact_id": preview.selected_image_artifact_id,
                            "execution_profile_version_id": preview.execution_profile_version_id,
                            "resolution_hash": preview.resolution_hash, "executable": preview.executable,
                            "blockers": list(preview.blockers)},
                "read_only": True, "execution_switched": True, "legacy_quick_generation_touched": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/quick-create-v2/runs/{run_id}/image-to-video:submit", status_code=201, operation_id="submitModelPlatformQuickCreateV2ImageToVideo")
async def submit_quick_create_v2_image_to_video(
    run_id: str,
    payload: ModelPlatformQuickCreateV2ImageToVideoSubmitRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        submitted = QuickCreateV2ImageToVideoService(request.app.state.database).submit(
            run_id=run_id, expected_resolution_hash=payload.expected_resolution_hash, idempotency_key=idempotency_key
        )
        return {"execution": {"capability_code": "VIDEO_I2V", "run_id": submitted.run_id, "job_id": submitted.job_id,
                              "execution_snapshot_id": submitted.execution_snapshot_id,
                              "execution_snapshot_hash": submitted.execution_snapshot_hash,
                              "handler_code": submitted.handler_code, "handler_version": submitted.handler_version,
                              "idempotent_replay": submitted.idempotent_replay},
                "execution_switched": True, "legacy_quick_generation_touched": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/quick-create-v2/runs/{run_id}", operation_id="getModelPlatformQuickCreateV2Run")
async def get_quick_create_v2_run(run_id: str, request: Request) -> dict[str, object]:
    """Read the V2 aggregate projection, exposing artifact downloads but never paths."""
    try:
        return {"run": QuickCreateV2RunService(request.app.state.database).public_run(run_id), "read_only": True,
                "execution_switched": True, "legacy_quick_generation_touched": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/storage-policy", operation_id="getModelPlatformStoragePolicy")
async def get_storage_policy(request: Request) -> dict[str, object]:
    """Expose configuration posture without disclosing server file locations.

    Browser clients can understand which operational zones and discovery
    libraries exist, but mutation stays in the authenticated machine/Host
    boundary. This is especially important for an unauthenticated LAN service.
    """
    settings = request.app.state.settings
    root = settings.model_root
    default_root = (settings.instance_root / "models").resolve()
    labels: list[str] = []
    for index, library in enumerate(settings.model_library_roots, start=1):
        name = library.name.casefold()
        labels.append(
            {
                "comfyui": "ComfyUI 模型库",
                "pytorch": "PyTorch 模型库",
                "ollama": "Ollama 模型库",
                "audio": "音频模型库",
            }.get(name, f"受管模型库 {index}")
        )
    return {
        "storage_policy": {
            "model_root_configured": root is not None,
            "root_kind": "INSTANCE_DEFAULT" if root == default_root else "DEDICATED_LOCAL_VOLUME" if root else "NOT_CONFIGURED",
            "discovery_library_count": len(settings.model_library_roots),
            "discovery_libraries": labels,
            "operational_areas": ["下载队列", "暂存区", "隔离区"],
            "configuration_authority": "HOST_CLI",
            "absolute_paths_exposed": False,
            "trusted_download_source_count": len(settings.model_download_source_hosts),
            "online_download_default_enabled": bool(settings.model_download_source_hosts),
        },
        "read_only": True,
    }


@router.get("/installation-plans", operation_id="listModelPlatformInstallationPlans")
async def list_installation_plans(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    """Return safe plan summaries; source bundle names and filesystem paths stay server-side."""
    plans = InstallationPlanService(request.app.state.database, request.app.state.settings).list(limit=limit)
    return {
        "items": [_public_offline_import_plan(item) for item in plans],
        "count": len(plans),
        "read_only": True,
        "host_import_available": False,
    }


@router.get("/installation-targets", operation_id="listModelPlatformInstallationTargets")
async def list_installation_targets(request: Request) -> dict[str, object]:
    """List safe V2 Library destinations without exposing their filesystem roots."""
    targets = InstallationPlanService(request.app.state.database, request.app.state.settings).list_targets()
    return {
        "items": [{"id": item.id, "label": item.label} for item in targets],
        "count": len(targets),
        "read_only": True,
        "absolute_paths_exposed": False,
    }


@router.post("/installation-plans/offline", status_code=201, operation_id="createModelPlatformOfflineInstallationPlan")
async def create_offline_import_plan(
    payload: ModelPlatformOfflineImportPlanRequest,
    request: Request,
) -> dict[str, object]:
    """Persist an immutable import contract without downloading or copying any model."""
    try:
        plan = InstallationPlanService(request.app.state.database, request.app.state.settings).create_offline_import(
            OfflineImportPlanRequest(
                target_library_id=payload.target_library_id,
                release_code=payload.release_code,
                bundle_reference=payload.bundle_reference,
                license_id=payload.license_id,
                expected_artifacts=tuple(
                    ExpectedInstallArtifact(
                        relative_path=item.relative_path,
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                    )
                    for item in payload.expected_artifacts
                ),
            )
        )
        return {"plan": _public_offline_import_plan(plan), "file_operations_started": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/installation-plans/trusted-download", status_code=201, operation_id="createModelPlatformTrustedDownloadPlan")
async def create_trusted_download_plan(
    payload: ModelPlatformTrustedDownloadPlanRequest,
    request: Request,
) -> dict[str, object]:
    """Persist an allowlisted HTTPS contract; only the Windows Host may download it."""
    try:
        plan = InstallationPlanService(request.app.state.database, request.app.state.settings).create_trusted_download(
            TrustedDownloadPlanRequest(
                target_library_id=payload.target_library_id,
                release_code=payload.release_code,
                bundle_reference=payload.bundle_reference,
                license_id=payload.license_id,
                artifacts=tuple(
                    TrustedDownloadArtifact(
                        relative_path=item.relative_path,
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                        source_url=item.source_url,
                    )
                    for item in payload.artifacts
                ),
            )
        )
        return {
            "plan": _public_offline_import_plan(plan),
            "network_operations_started": False,
            "host_download_available": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/capabilities", operation_id="listModelPlatformCapabilities")
async def list_capabilities(
    request: Request,
    business_surface: str | None = Query(default=None, max_length=80),
) -> dict[str, object]:
    with request.app.state.database.connect() as connection:
        rows = connection.execute(
            """SELECT code,title,family,input_modalities_json,output_modalities_json,business_surfaces_json,background_only
            FROM mp_capability_definitions ORDER BY family,code"""
        ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        surfaces = _json_list(row["business_surfaces_json"])
        if business_surface and business_surface not in surfaces:
            continue
        items.append(
            {
                "code": str(row["code"]),
                "title": str(row["title"]),
                "family": str(row["family"]),
                "input_modalities": _json_list(row["input_modalities_json"]),
                "output_modalities": _json_list(row["output_modalities_json"]),
                "business_surfaces": surfaces,
                "background_only": bool(row["background_only"]),
            }
        )
    return {"items": items, "count": len(items), "read_only": True}


@router.get("/discovery-observations", operation_id="listModelPlatformDiscoveryObservations")
async def list_discovery_observations(
    request: Request,
    limit: int = Query(default=36, ge=1, le=100),
) -> dict[str, object]:
    """List recent V2 scanner evidence without exposing runtime wiring.

    This is intentionally an evidence view.  It contains no model-library
    path, endpoint, credential, raw adapter payload, or execution readiness.
    """
    active_model_lock_runtimes = configured_model_lock_runtime_ids(request.app.state.settings)
    active_runtime_placeholders = ",".join("?" for _ in active_model_lock_runtimes) or "NULL"
    with request.app.state.database.connect() as connection:
        rows = connection.execute(
            f"""WITH ranked_observations AS (
                 SELECT observation.id,observation.native_id,observation.kind,observation.observed_json,observation.status,
                        observation.created_at,run.id AS discovery_run_id,run.status AS discovery_run_status,
                        run.finished_at,run.summary_json,
                        ROW_NUMBER() OVER (
                          PARTITION BY COALESCE(runtime_version.runtime_installation_id, run.runtime_installation_version_id, run.source || ':' || observation.kind),
                                       observation.native_id
                          ORDER BY observation.created_at DESC,observation.id DESC
                        ) AS current_rank
                 FROM mp_discovery_observations observation
                 JOIN mp_discovery_runs run ON run.id=observation.discovery_run_id
                 LEFT JOIN mp_runtime_installation_versions runtime_version
                   ON runtime_version.id=run.runtime_installation_version_id
                 WHERE run.status='SUCCEEDED'
                   AND (run.source<>'MODEL_LOCK' OR runtime_version.runtime_installation_id IN ({active_runtime_placeholders}))
               )
               SELECT id,native_id,kind,observed_json,status,created_at,discovery_run_id,
                      discovery_run_status,finished_at,summary_json
               FROM ranked_observations
               WHERE current_rank=1
               ORDER BY created_at DESC,id DESC LIMIT ?""",
            (*active_model_lock_runtimes, limit),
        ).fetchall()
    items = [_public_discovery_observation(row) for row in rows]
    return {"items": items, "count": len(items), "read_only": True}


@router.get("/registered-candidates", operation_id="listModelPlatformRegisteredCandidates")
async def list_registered_candidates(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    """List registered candidates with per-capability, fail-closed readiness."""
    candidates = CandidateReadinessService(request.app.state.database).list(limit=limit)
    return {
        "items": [_public_candidate_readiness(candidate) for candidate in candidates],
        "count": len(candidates),
        "read_only": True,
    }


@router.get(
    "/registered-candidates/{runtime_model_installation_id}/validation-history",
    operation_id="listModelPlatformValidationHistory",
)
async def list_registered_candidate_validation_history(
    runtime_model_installation_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """List safe, immutable validation facts without returning evidence payloads."""
    try:
        items = ValidationHistoryService(request.app.state.database).list_for_installation(
            runtime_model_installation_id,
            limit=limit,
        )
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
    return {
        "items": [
            {
                "validation_run_id": item.validation_run_id,
                "target": item.target,
                "capability_code": item.capability_code,
                "validation_kind": item.validation_kind,
                "status": item.status,
                "occurred_at": item.occurred_at,
            }
            for item in items
        ],
        "count": len(items),
        "read_only": True,
        "evidence_payload_exposed": False,
    }


@router.get("/profile-versions", operation_id="listModelPlatformProfileVersions")
async def list_profile_versions(
    request: Request,
    runtime_model_installation_id: str | None = Query(default=None, min_length=1, max_length=64),
    capability_code: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, object]:
    """Recover safe V2 Profile lifecycle state after navigation or refresh."""
    items = ProfileCatalogService(request.app.state.database).list(
        runtime_model_installation_id=runtime_model_installation_id,
        capability_code=capability_code,
        limit=limit,
    )
    return {
        "items": [
            {
                "profile_version_id": item.profile_version_id,
                "profile_code": item.profile_code,
                "profile_title": item.profile_title,
                "version_no": item.version_no,
                "capability_code": item.capability_code,
                "runtime_model_installation_ids": list(item.runtime_model_installation_ids),
                "lifecycle_status": item.lifecycle_status,
                "latest_validation_run_id": item.latest_validation_run_id,
                "latest_validation_status": item.latest_validation_status,
            }
            for item in items
        ],
        "count": len(items),
        "read_only": True,
    }


@router.post(
    "/registered-candidates/{runtime_model_installation_id}/capability-offerings/{capability_code}:smoke",
    operation_id="smokeModelPlatformCapabilityOffering",
)
async def smoke_registered_candidate_capability(
    runtime_model_installation_id: str,
    capability_code: str,
    request: Request,
) -> dict[str, object]:
    """Run a real, implementation-bound capability smoke under service identity."""
    try:
        result = CapabilitySmokeService(request.app.state.database, request.app.state.settings).smoke(
            runtime_model_installation_id,
            capability_code,
        )
        return {
            "validation": {
                "validation_run_id": result.validation_run_id,
                "runtime_model_installation_id": result.runtime_model_installation_id,
                "capability_code": result.capability_code,
                "status": result.status,
                "installation_ready": result.installation_ready,
                "runtime_active": result.runtime_active,
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/registered-candidates/{runtime_model_installation_id}:verify-integrity",
    operation_id="verifyModelPlatformInstallationIntegrity",
)
async def verify_registered_candidate_integrity(runtime_model_installation_id: str, request: Request) -> dict[str, object]:
    """Re-check a controlled ComfyUI/PyTorch model bundle under service identity."""
    try:
        result = InstallationIntegrityService(request.app.state.database, request.app.state.settings).verify(
            runtime_model_installation_id
        )
        return {
            "validation": {
                "validation_run_id": result.validation_run_id,
                "runtime_model_installation_id": result.runtime_model_installation_id,
                "status": result.status,
                "install_state": result.install_state,
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/registered-candidates/{runtime_model_installation_id}/capability-offerings/{capability_code}:bind-workflow",
    operation_id="bindModelPlatformComfyWorkflow",
)
async def bind_comfy_workflow(
    runtime_model_installation_id: str,
    capability_code: str,
    payload: ModelPlatformComfyWorkflowBindingRequest,
    request: Request,
) -> dict[str, object]:
    """Persist an explicit Comfy Offering→published Workflow binding after local schema validation."""
    try:
        binding = ComfyWorkflowBindingService(request.app.state.database, request.app.state.settings).bind(
            runtime_model_installation_id,
            capability_code,
            payload.workflow_version_id,
        )
        return {"workflow_binding": {
            "id": binding.id,
            "runtime_model_installation_id": binding.runtime_model_installation_id,
            "capability_code": binding.capability_code,
            "workflow_version_id": binding.workflow_version_id,
            "status": binding.binding_status,
            "created": binding.created,
        }}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get(
    "/registered-candidates/{runtime_model_installation_id}/capability-offerings/{capability_code}/workflow-bindings",
    operation_id="listModelPlatformComfyWorkflowBindings",
)
async def list_comfy_workflow_bindings(
    runtime_model_installation_id: str,
    capability_code: str,
    request: Request,
) -> dict[str, object]:
    """Recover safe binding IDs so a page refresh can resume Comfy smoke."""
    items = ComfyWorkflowBindingService(request.app.state.database, request.app.state.settings).list(
        runtime_model_installation_id,
        capability_code,
    )
    return {
        "items": [
                {"id": item.id, "workflow_version_id": item.workflow_version_id, "status": item.binding_status, "capability_smoke_passed": item.capability_smoke_passed}
            for item in items
        ],
        "count": len(items),
        "read_only": True,
    }


@router.post(
    "/registered-candidates/{runtime_model_installation_id}/capability-offerings/{capability_code}:queue-comfy-smoke",
    operation_id="submitModelPlatformComfyCapabilitySmoke",
)
async def submit_comfy_capability_smoke(
    runtime_model_installation_id: str,
    capability_code: str,
    payload: ModelPlatformComfySmokeSubmissionRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, object]:
    """Queue an immutable, bounded Comfy smoke; completion happens in Worker."""
    try:
        submitted = ComfyCapabilitySmokeSubmissionService(request.app.state.database, request.app.state.settings).submit(
            payload.workflow_binding_id,
            idempotency_key,
            runtime_model_installation_id=runtime_model_installation_id,
            capability_code=capability_code,
        )
        return {"smoke_job": {
            "job_id": submitted.job_id,
            "workflow_binding_id": submitted.workflow_binding_id,
            "runtime_model_installation_id": submitted.runtime_model_installation_id,
            "capability_code": submitted.capability_code,
            "workflow_version_id": submitted.workflow_version_id,
            "smoke_contract_hash": submitted.smoke_contract_hash,
            "idempotent_replay": submitted.idempotent_replay,
        }}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/registered-candidates/{runtime_model_installation_id}/capability-offerings/{capability_code}:provision-profile",
    operation_id="provisionModelPlatformProfileTemplate",
)
async def provision_profile_template(
    runtime_model_installation_id: str,
    capability_code: str,
    request: Request,
    payload: ModelPlatformProfileProvisionRequest | None = None,
) -> dict[str, object]:
    """Create the installed V2 Profile template for a verified Offering."""
    try:
        service = ProfileTemplateService(request.app.state.database, request.app.state.settings)
        profile = (
            service.provision(runtime_model_installation_id, capability_code, workflow_binding_id=payload.workflow_binding_id)
            if payload is not None
            else service.provision(runtime_model_installation_id, capability_code)
        )
        return {"profile": {"profile_version_id": profile.profile_version_id, "profile_code": profile.profile_code, "created": profile.created}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-versions/{profile_version_id}:smoke", operation_id="smokeModelPlatformProfileTemplate")
async def smoke_profile_template(profile_version_id: str, request: Request) -> dict[str, object]:
    """Run the persisted template's profile-level smoke; it cannot publish by itself."""
    try:
        result = ProfileTemplateService(request.app.state.database, request.app.state.settings).smoke(profile_version_id)
        return {"validation": {
            "validation_run_id": result.validation_run_id,
            "profile_version_id": result.profile_version_id,
            "status": result.status,
            "failure_code": getattr(result, "failure_code", None),
        }}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-versions/{profile_version_id}:publish", operation_id="publishModelPlatformProfile")
async def publish_profile(
    profile_version_id: str,
    payload: ModelPlatformProfilePublishRequest,
    request: Request,
) -> dict[str, object]:
    """Publish only the immutable profile that owns the supplied passed smoke evidence."""
    try:
        ProfileTemplateService(request.app.state.database, request.app.state.settings).publish(
            profile_version_id,
            payload.validation_run_id,
            payload.reason,
        )
        return {"profile": {"profile_version_id": profile_version_id, "status": "PUBLISHED"}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/discovery-runs:ollama", operation_id="runModelPlatformOllamaDiscovery")
async def run_ollama_discovery(request: Request) -> dict[str, object]:
    """Scan configured local Ollama tags under the API service identity."""
    try:
        settings = request.app.state.settings
        client = LocalLLMClient(
            settings.llm_base_url,
            settings.llm_model or "__model_platform_discovery__",
            provider=settings.llm_provider,
            allow_private_network=settings.network_mode.value == "LAN_SERVICE",
        )
        run = OllamaDiscoveryOrchestrator(request.app.state.database, settings).scan(client)
        return {"discovery_run": {"id": run.id, "status": run.status, "observation_count": run.observation_count}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/discovery-runs:llama-cpp", operation_id="runModelPlatformLlamaCppDiscovery")
async def run_llama_cpp_discovery(request: Request) -> dict[str, object]:
    """Scan the configured GGUF directory under the managed llama.cpp identity."""
    try:
        run = LlamaCppDiscoveryOrchestrator(request.app.state.database, request.app.state.settings).scan()
        return {"discovery_run": {"id": run.id, "status": run.status, "observation_count": run.observation_count}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/discovery-runs:model-lock", operation_id="runModelPlatformModelLockDiscovery")
async def run_model_lock_discovery(request: Request) -> dict[str, object]:
    """Scan configured libraries for ComfyUI and PyTorch lock entries."""
    try:
        result = ModelLockDiscoveryOrchestrator(request.app.state.database, request.app.state.settings).scan()
        return {
            "discovery_runs": [
                {"id": item.id, "status": item.status, "observation_count": item.observation_count}
                for item in result.runs
            ]
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/discovery-observations/{observation_id}:register", operation_id="registerModelPlatformDiscoveryCandidate")
async def register_discovery_candidate(observation_id: str, request: Request) -> dict[str, object]:
    """Explicitly register one present observation as an unvalidated candidate."""
    try:
        candidate = DiscoveryRegistrationService(request.app.state.database).register(observation_id)
        return {
            "candidate": {
                "model_release_id": candidate.model_release_id,
                "model_release_code": candidate.model_release_code,
                "runtime_model_installation_id": candidate.runtime_model_installation_id,
                "created": candidate.created,
                "validation_status": "NOT_RUN",
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/capability-resolution", operation_id="resolveModelPlatformCapability")
async def resolve_capability(
    request: Request,
    capability_code: str = Query(min_length=1, max_length=80),
    project_id: str | None = Query(default=None, max_length=36),
    episode_id: str | None = Query(default=None, max_length=36),
    shot_id: str | None = Query(default=None, max_length=36),
    character_id: str | None = Query(default=None, max_length=36),
) -> dict[str, object]:
    try:
        resolution = CapabilityAssignmentService(request.app.state.database).resolve(
            capability_code,
            CapabilityScopeContext(project_id=project_id, episode_id=episode_id, shot_id=shot_id, character_id=character_id),
        )
        return {
            "resolution": {
                "capability_code": resolution.capability_code,
                "execution_profile_version_id": resolution.execution_profile_version_id,
                "resolution_reason": resolution.resolution_reason,
                "assignment_chain": list(resolution.assignment_chain),
                "blocked_reason": resolution.blocked_reason,
            },
            "read_only": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/system-capability-assignments", operation_id="listModelPlatformSystemCapabilityAssignments")
async def list_system_capability_assignments(request: Request) -> dict[str, object]:
    """List safe V2 SYSTEM assignment/Profile options for the model center.

    Values are returned only for explicitly permitted, non-sensitive SYSTEM
    override fields.  No runtime path, native locator, endpoint, secret or
    hidden Profile setting reaches the browser.
    """
    items = CapabilityAssignmentCatalogService(request.app.state.database).list_system()
    return {"items": items, "count": len(items), "read_only": True, "scope_type": "SYSTEM"}


@router.get("/capability-assignment-catalog", operation_id="listModelPlatformCapabilityAssignmentCatalog")
async def list_capability_assignment_catalog(
    request: Request,
    scope_type: str = Query(min_length=1, max_length=16),
    scope_id: str = Query(default="", max_length=36),
) -> dict[str, object]:
    """Return safe V2 assignment options for one verified business scope.

    This is deliberately a control-plane read model.  It does not project V1
    generation preferences and cannot make a creator surface use V2.
    """
    try:
        normalized_scope = scope_type.strip().upper()
        normalized_id = scope_id.strip()
        items = CapabilityAssignmentCatalogService(request.app.state.database).list_scope(
            normalized_scope, normalized_id
        )
        return {
            "items": items,
            "count": len(items),
            "read_only": True,
            "scope_type": normalized_scope,
            "scope_id": normalized_id,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/business-selection-shadow", operation_id="compareModelPlatformBusinessSelection")
async def compare_business_selection(
    request: Request,
    capability_code: str = Query(min_length=1, max_length=80),
    project_id: str = Query(min_length=1, max_length=36),
    episode_id: str | None = Query(default=None, max_length=36),
    shot_id: str | None = Query(default=None, max_length=36),
    character_id: str | None = Query(default=None, max_length=36),
) -> dict[str, object]:
    """Compare V1 creator selection and V2 assignment without submitting work."""
    try:
        comparison = BusinessSelectionShadowService(request.app.state.database).compare(
            capability_code,
            CapabilityScopeContext(
                project_id=project_id,
                episode_id=episode_id,
                shot_id=shot_id,
                character_id=character_id,
            ),
        )
        return {"comparison": comparison.as_dict(), "read_only": True, "mutated": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/business-selection-facade-evaluation", operation_id="evaluateModelPlatformBusinessSelectionFacade")
async def evaluate_business_selection_facade(
    request: Request,
    business_surface: str = Query(min_length=1, max_length=80),
    capability_code: str = Query(min_length=1, max_length=80),
    project_id: str = Query(min_length=1, max_length=36),
    episode_id: str | None = Query(default=None, max_length=36),
    shot_id: str | None = Query(default=None, max_length=36),
    character_id: str | None = Query(default=None, max_length=36),
) -> dict[str, object]:
    """Evaluate all V2 cutover preconditions without changing creator execution."""
    try:
        evaluation = GenerationCapabilityConfigurationFacade(request.app.state.database).evaluate(
            business_surface=business_surface,
            capability_code=capability_code,
            scope=CapabilityScopeContext(
                project_id=project_id,
                episode_id=episode_id,
                shot_id=shot_id,
                character_id=character_id,
            ),
        )
        return {"evaluation": evaluation.as_dict(), "read_only": True, "execution_switched": False}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/business-selection-rollouts", operation_id="listModelPlatformBusinessSelectionRollouts")
async def list_business_selection_rollouts(
    request: Request,
    business_surface: str | None = Query(default=None, max_length=80),
) -> dict[str, object]:
    """Read migration eligibility gates; a gate never switches execution itself."""
    try:
        items = BusinessSelectionRolloutService(request.app.state.database).list(business_surface=business_surface)
        return {
            "items": [item.as_dict() for item in items],
            "count": len(items),
            "read_only": True,
            "execution_switched": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/business-selection-rollouts", operation_id="putModelPlatformBusinessSelectionRollout")
async def put_business_selection_rollout(
    payload: ModelPlatformBusinessSelectionRolloutPutRequest, request: Request
) -> dict[str, object]:
    """Persist an auditable cutover eligibility decision, without changing jobs."""
    try:
        rollout = BusinessSelectionRolloutService(request.app.state.database).put(
            BusinessSelectionRolloutRequest(**payload.model_dump())
        )
        return {"rollout": rollout.as_dict()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-version-crosswalks", status_code=201, operation_id="approveModelPlatformProfileCrosswalk")
async def approve_profile_crosswalk(
    payload: ModelPlatformProfileCrosswalkApprovalRequest, request: Request
) -> dict[str, object]:
    try:
        crosswalk = ProfileVersionCrosswalkService(request.app.state.database).approve(
            ProfileVersionCrosswalkRequest(**payload.model_dump())
        )
        return {"crosswalk": crosswalk.as_dict()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/profile-version-crosswalks/{crosswalk_id}:revoke", operation_id="revokeModelPlatformProfileCrosswalk")
async def revoke_profile_crosswalk(
    crosswalk_id: str, payload: ModelPlatformProfileCrosswalkRevocationRequest, request: Request
) -> dict[str, object]:
    try:
        ProfileVersionCrosswalkService(request.app.state.database).revoke(
            crosswalk_id,
            revocation_reason=payload.revocation_reason,
            revoked_by=payload.revoked_by,
        )
        return {"crosswalk": {"id": crosswalk_id, "status": "REVOKED"}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/capability-assignments", operation_id="putModelPlatformCapabilityAssignment")
async def put_capability_assignment(payload: CapabilityAssignmentPutRequest, request: Request) -> dict[str, object]:
    try:
        CapabilityAssignmentService(request.app.state.database).put(CapabilityAssignmentRequest(**payload.model_dump()))
        return {"status": "SAVED"}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/execution-resolution:preview", operation_id="previewModelPlatformExecution")
async def preview_execution(payload: ModelPlatformExecutionPreviewRequest, request: Request) -> dict[str, object]:
    """Resolve a capability to immutable V2 inputs without queuing work."""
    try:
        preview = ExecutionPlanningService(request.app.state.database).preview(
            ExecutionPreviewRequest(
                capability_code=payload.capability_code,
                scope=CapabilityScopeContext(
                    project_id=payload.project_id,
                    episode_id=payload.episode_id,
                    shot_id=payload.shot_id,
                    character_id=payload.character_id,
                ),
                semantic_inputs=payload.semantic_inputs,
                run_overrides=payload.run_overrides,
                expected_resolution_hash=payload.expected_resolution_hash,
            )
        )
        return {
            "preview": {
                "capability_code": preview.capability_code,
                "execution_profile_version_id": preview.execution_profile_version_id,
                "resolution_reason": preview.resolution_reason,
                "assignment_chain": list(preview.assignment_chain),
                "resolved_parameters": {
                    name: {"value": item.value, "source": item.source.value, "locked": item.locked}
                    for name, item in preview.resolved_parameters.items()
                },
                "network_policy": dict(preview.network_policy),
                "runtime_ready": preview.runtime_ready,
                "blockers": list(preview.blockers),
                "resolution_hash": preview.resolution_hash,
                "executable": preview.executable,
            },
            "read_only": True,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/executions", status_code=201, operation_id="submitModelPlatformExecution")
async def submit_execution(
    payload: ModelPlatformExecutionPreviewRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, object]:
    """Queue a fresh, immutable V2 execution.

    The production registry contains only handlers with an exact Worker
    implementation and a completed runtime/Profile smoke gate. Everything
    else remains fail-closed at submission time.
    """
    try:
        submitted = ExecutionSubmissionService(request.app.state.database, production_execution_handlers()).submit(
            ExecutionPreviewRequest(
                capability_code=payload.capability_code,
                scope=CapabilityScopeContext(
                    project_id=payload.project_id,
                    episode_id=payload.episode_id,
                    shot_id=payload.shot_id,
                    character_id=payload.character_id,
                ),
                semantic_inputs=payload.semantic_inputs,
                run_overrides=payload.run_overrides,
                expected_resolution_hash=payload.expected_resolution_hash,
            ),
            idempotency_key,
        )
        return {
            "execution": {
                "job": submitted.job,
                "execution_snapshot_id": submitted.execution_snapshot_id,
                "execution_snapshot_hash": submitted.execution_snapshot_hash,
                "handler_code": submitted.handler_code,
                "handler_version": submitted.handler_version,
                "idempotent_replay": submitted.idempotent_replay,
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/project-knowledge-indexes", operation_id="listModelPlatformProjectKnowledgeIndexes")
async def list_project_knowledge_indexes(
    request: Request,
    project_id: str = Query(min_length=1, max_length=36),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """List V2-only project knowledge indexing state without vectors or source text."""
    with request.app.state.database.connect() as connection:
        rows = connection.execute(
            """SELECT run.id,run.source_document_version_id,run.execution_profile_version_id,run.chunk_count,run.attempt_no,
                      run.retry_of_index_run_id,
                      run.completed_batch_count,run.status,run.failure_code,run.created_at,run.updated_at,
                      COUNT(batch.id) AS batch_count
               FROM mp_project_knowledge_index_runs run
               LEFT JOIN mp_project_knowledge_index_batches batch ON batch.index_run_id=run.id
               WHERE run.project_id=?
               GROUP BY run.id
               ORDER BY run.updated_at DESC,run.id DESC LIMIT ?""",
            (project_id, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "count": len(rows), "read_only": True}


@router.post("/project-knowledge-indexes", status_code=201, operation_id="prepareModelPlatformProjectKnowledgeIndex")
async def prepare_project_knowledge_index(
    payload: ModelPlatformProjectKnowledgeIndexPrepareRequest, request: Request
) -> dict[str, object]:
    """Create immutable text/chunk manifests; this endpoint never accepts paths or vectors."""
    try:
        prepared = ProjectKnowledgeIndexPreparationService(request.app.state.database, request.app.state.settings).prepare(
            project_id=payload.project_id,
            source_document_version_id=payload.source_document_version_id,
            actor=payload.actor,
        )
        return {
            "index": {
                "index_run_id": prepared.index_run_id,
                "execution_profile_version_id": prepared.execution_profile_version_id,
                "chunk_count": prepared.chunk_count,
                "reused": prepared.reused,
                "status": prepared.status,
                "attempt_no": prepared.attempt_no,
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/project-knowledge-indexes/{index_run_id}:queue", operation_id="queueModelPlatformProjectKnowledgeIndex")
async def queue_project_knowledge_index(index_run_id: str, request: Request) -> dict[str, object]:
    """Queue every prepared V2 knowledge batch with an immutable snapshot/Job binding."""
    try:
        queued = ProjectKnowledgeIndexQueueService(request.app.state.database).queue(index_run_id)
        return {
            "index": {
                "index_run_id": queued.index_run_id,
                "queued_batch_count": queued.queued_batch_count,
                "job_ids": list(queued.job_ids),
                "status": "QUEUED",
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/project-knowledge-search", operation_id="searchModelPlatformProjectKnowledge")
async def search_project_knowledge(
    payload: ModelPlatformProjectKnowledgeSearchRequest, request: Request
) -> dict[str, object]:
    """Embed server-received query text locally and search only verified V2 vectors."""
    try:
        result = await run_in_threadpool(
            ProjectKnowledgeRetrievalService(request.app.state.database, request.app.state.settings).search,
            project_id=payload.project_id,
            query=payload.query,
            limit=payload.limit,
        )
        profile_version_id, hits = result
        return {
            "search": {
                "execution_profile_version_id": profile_version_id,
                "items": [
                    {
                        "index_run_id": item.index_run_id,
                        "source_document_version_id": item.source_document_version_id,
                        "ordinal": item.ordinal,
                        "source_start": item.source_start,
                        "source_end": item.source_end,
                        "excerpt": item.excerpt,
                        "score": item.score,
                    }
                    for item in hits
                ],
            }
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _json_list(value: object) -> list[str]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded if item] if isinstance(decoded, list) else []


def _public_discovery_observation(row: sqlite3.Row) -> dict[str, object]:
    """Build the read-safe discovery projection consumed by the model center."""
    observed = _json_object(getattr(row, "__getitem__", lambda _: "{}")("observed_json"))
    raw_metadata = observed.get("metadata")
    metadata: dict[str, object] = raw_metadata if isinstance(raw_metadata, dict) else {}
    candidates = observed.get("candidate_capabilities")
    capability_candidates = [
        str(item.get("capability"))
        for item in candidates
        if isinstance(item, dict) and str(item.get("capability") or "").strip()
    ] if isinstance(candidates, list) else []
    safe_metadata = {
        key: metadata[key]
        for key in ("format", "family", "families", "parameter_size", "quantization_level", "context_length", "release_code", "declared_runtime")
        if key in metadata
    }
    native_id = str(row["native_id"])
    return {
        "id": str(row["id"]),
        "native_id": native_id if _safe_native_locator(native_id) else "受控模型资源",
        "runtime_kind": str(row["kind"]),
        "presence": str(observed.get("presence") or row["status"]),
        "size_bytes": observed.get("size_bytes") if isinstance(observed.get("size_bytes"), int) else None,
        "candidate_capabilities": capability_candidates,
        "metadata": safe_metadata,
        "observed_at": str(row["created_at"]),
        "discovery_run_status": str(row["discovery_run_status"]),
    }


def _json_object(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _safe_native_locator(value: str) -> bool:
    """Native locators may be tags/codes, never a browser-visible file path."""
    cleaned = value.strip()
    windows_drive_path = len(cleaned) >= 3 and cleaned[1] == ":" and cleaned[2] in {"/", "\\"}
    return bool(cleaned) and "/" not in cleaned and "\\" not in cleaned and not windows_drive_path


def _public_candidate_readiness(candidate: RegisteredCandidateReadiness) -> dict[str, object]:
    return {
        "runtime_model_installation_id": candidate.runtime_model_installation_id,
        "model_release_id": candidate.model_release_id,
        "model_release_code": candidate.model_release_code,
        "model_title": candidate.model_title,
        "runtime_kind": candidate.runtime_kind,
        "install_state": candidate.install_state,
        "integrity_status": candidate.integrity_status,
        "readiness_status": candidate.readiness_status,
        "assignable_capability_count": candidate.assignable_capability_count,
        "blockers": list(candidate.blockers),
        "capabilities": [
            {
                "code": item.code,
                "title": item.title,
                "offering_validation_status": item.offering_validation_status,
                "profile_version_count": item.profile_version_count,
                "published_profile_count": item.published_profile_count,
                "workflow_binding_count": item.workflow_binding_count,
                "workflow_schema_validated_count": item.workflow_schema_validated_count,
                "readiness_status": item.readiness_status,
                "blockers": list(item.blockers),
            }
            for item in candidate.capabilities
        ],
    }


def _public_candidate_plan(plan: QuickCreateV2ImageCandidatePlan) -> dict[str, object]:
    """Project the V2 candidate preflight without workflow wiring or seeds beyond the frozen command."""
    return {
        "execution_profile_version_id": plan.execution_profile_version_id,
        "executable": plan.executable,
        "blockers": list(plan.blockers),
        "candidates": [
            {"ordinal": item.ordinal, "seed": item.seed, "resolution_hash": item.resolution_hash}
            for item in plan.candidates
        ],
    }


def _public_offline_import_plan(plan: InstallationPlan) -> dict[str, object]:
    """Expose plan state without bundle references, paths or source wiring."""
    return {
        "id": plan.id,
        "target_library_id": plan.target_library_id,
        "target_library_label": plan.target_library_label,
        "release_code": plan.release_code,
        "source_kind": plan.source_kind,
        "expected_artifact_count": plan.expected_artifact_count,
        "expected_total_bytes": plan.expected_total_bytes,
        "license_id": plan.license_id,
        "status": plan.status,
        "created_at": plan.created_at,
    }
