"""Application services for the Model Platform V2 bounded context."""

from .candidate_readiness import CandidateCapabilityReadiness, CandidateReadinessService, RegisteredCandidateReadiness
from .capability_resolution import CapabilityAssignmentRequest, CapabilityAssignmentService, CapabilityResolution, CapabilityScopeContext
from .capability_smoke import CapabilitySmokeResult, CapabilitySmokeService
from .comfy_workflow_bindings import ComfyWorkflowBinding, ComfyWorkflowBindingService
from .discovery import DiscoveryService, PersistedDiscoveryRun
from .discovery_registration import DiscoveryRegistrationService, RegisteredDiscoveryCandidate
from .execution_handlers import ExecutionHandlerDescriptor, ExecutionHandlerRegistry
from .execution_job_links import ExecutionJobLink, ExecutionJobLinkService, WorkerExecutionSnapshot
from .execution_planning import ExecutionPlanningService, ExecutionPreview, ExecutionPreviewRequest
from .execution_snapshots import ExecutionSnapshot, ExecutionSnapshotDraft, ExecutionSnapshotService
from .execution_submission import ExecutionSubmission, ExecutionSubmissionService
from .generation_capability_configuration_facade import (
    GenerationCapabilityConfigurationEvaluation,
    GenerationCapabilityConfigurationFacade,
)
from .installation_integrity import InstallationIntegrityResult, InstallationIntegrityService
from .legacy_inventory import LegacyModelPlatformInventory, LegacyModelPlatformInventoryReader
from .model_lock_discovery import ModelLockDiscoveryOrchestrator, ModelLockDiscoveryResult
from .ollama_discovery import OllamaDiscoveryOrchestrator
from .ollama_text_profiles import OllamaProfileSmokeResult, OllamaTextProfileService, ProvisionedOllamaProfile
from .parameters import ParameterContract, ParameterOverride, ParameterResolutionService, ProfileParameterPolicy
from .production_execution_registry import production_execution_handlers, production_worker_execution_handlers
from .profile_catalog import ProfileCatalogService, ProfileLifecycleItem
from .profile_publication import ProfilePublicationService, ProfileVersionDraft
from .profile_templates import ProfileTemplateService, ProfileTemplateSmokeResult, ProvisionedProfileTemplate
from .project_knowledge_indexing import (
    ProjectKnowledgeIndexCompletionService,
    ProjectKnowledgeIndexPreparation,
    ProjectKnowledgeIndexPreparationService,
    ProjectKnowledgeIndexQueueResult,
    ProjectKnowledgeIndexQueueService,
)
from .project_knowledge_retrieval import ProjectKnowledgeRetrievalService, ProjectKnowledgeSearchHit
from .pytorch_embedding_profiles import PyTorchEmbeddingProfileService
from .quick_create_v2_image_to_video import QuickCreateV2ImageToVideoService
from .quick_create_v2_runs import QuickCreateV2Run, QuickCreateV2RunService, QuickCreateV2SelectedImage
from .runtime_adapters import DiscoveryReport, ModelLockRuntimeAdapter, OllamaRuntimeAdapter, RuntimeAdapter
from .validation_history import ValidationHistoryItem, ValidationHistoryService
from .worker_execution_handlers import WorkerExecutionHandlerDescriptor, WorkerExecutionHandlerRegistry

__all__ = [
    "CapabilityAssignmentRequest",
    "CapabilityAssignmentService",
    "CapabilityResolution",
    "CapabilityScopeContext",
    "CandidateCapabilityReadiness",
    "CandidateReadinessService",
    "CapabilitySmokeResult",
    "CapabilitySmokeService",
    "ComfyWorkflowBinding",
    "ComfyWorkflowBindingService",
    "DiscoveryReport",
    "DiscoveryService",
    "DiscoveryRegistrationService",
    "ExecutionSnapshot",
    "ExecutionSnapshotDraft",
    "ExecutionSnapshotService",
    "ExecutionSubmission",
    "ExecutionSubmissionService",
    "QuickCreateV2Run",
    "QuickCreateV2RunService",
    "QuickCreateV2ImageToVideoService",
    "QuickCreateV2SelectedImage",
    "GenerationCapabilityConfigurationEvaluation",
    "GenerationCapabilityConfigurationFacade",
    "InstallationIntegrityResult",
    "InstallationIntegrityService",
    "ExecutionPlanningService",
    "ExecutionPreview",
    "ExecutionPreviewRequest",
    "ExecutionJobLink",
    "ExecutionJobLinkService",
    "ExecutionHandlerDescriptor",
    "ExecutionHandlerRegistry",
    "LegacyModelPlatformInventory",
    "LegacyModelPlatformInventoryReader",
    "ModelLockRuntimeAdapter",
    "ModelLockDiscoveryOrchestrator",
    "ModelLockDiscoveryResult",
    "OllamaRuntimeAdapter",
    "OllamaDiscoveryOrchestrator",
    "OllamaProfileSmokeResult",
    "OllamaTextProfileService",
    "ParameterContract",
    "ParameterOverride",
    "ParameterResolutionService",
    "PersistedDiscoveryRun",
    "ProfileParameterPolicy",
    "ProfileCatalogService",
    "ProfileLifecycleItem",
    "ProfilePublicationService",
    "ProfileTemplateService",
    "ProfileTemplateSmokeResult",
    "ProfileVersionDraft",
    "ProjectKnowledgeIndexPreparation",
    "ProjectKnowledgeIndexPreparationService",
    "ProjectKnowledgeIndexQueueResult",
    "ProjectKnowledgeIndexQueueService",
    "ProjectKnowledgeIndexCompletionService",
    "ProjectKnowledgeRetrievalService",
    "ProjectKnowledgeSearchHit",
    "production_execution_handlers",
    "production_worker_execution_handlers",
    "ProvisionedProfileTemplate",
    "PyTorchEmbeddingProfileService",
    "ProvisionedOllamaProfile",
    "RuntimeAdapter",
    "RegisteredDiscoveryCandidate",
    "RegisteredCandidateReadiness",
    "WorkerExecutionSnapshot",
    "WorkerExecutionHandlerDescriptor",
    "WorkerExecutionHandlerRegistry",
    "ValidationHistoryItem",
    "ValidationHistoryService",
]
