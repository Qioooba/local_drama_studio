import { requestJson } from "../../generated/api";

export type ModelPlatformCapability = {
  code: string;
  title: string;
  family: string;
  input_modalities: string[];
  output_modalities: string[];
  business_surfaces: string[];
  background_only: boolean;
};

export type ModelPlatformOverview = {
  capability_count: number;
  registered_model_release_count: number;
  runtime_installation_count: number;
  discovery_observation_count: number;
  published_profile_count: number;
};

export type ModelPlatformStoragePolicy = {
  model_root_configured: boolean;
  root_kind: "INSTANCE_DEFAULT" | "DEDICATED_LOCAL_VOLUME" | "NOT_CONFIGURED";
  discovery_library_count: number;
  discovery_libraries: string[];
  operational_areas: string[];
  configuration_authority: "HOST_CLI";
  absolute_paths_exposed: false;
  trusted_download_source_count: number;
  online_download_default_enabled: boolean;
};

export type ModelPlatformDiscoveryObservation = {
  id: string;
  native_id: string;
  runtime_kind: string;
  presence: string;
  size_bytes: number | null;
  candidate_capabilities: string[];
  metadata: Record<string, unknown>;
  observed_at: string;
  discovery_run_status: string;
};

export type ModelPlatformCandidateCapabilityReadiness = {
  code: string;
  title: string;
  offering_validation_status: string;
  profile_version_count: number;
  published_profile_count: number;
  workflow_binding_count: number;
  workflow_schema_validated_count: number;
  readiness_status: string;
  blockers: string[];
};

export type ModelPlatformRegisteredCandidate = {
  runtime_model_installation_id: string;
  model_release_id: string;
  model_release_code: string;
  model_title: string;
  runtime_kind: string;
  install_state: string;
  integrity_status: string;
  readiness_status: string;
  assignable_capability_count: number;
  blockers: string[];
  capabilities: ModelPlatformCandidateCapabilityReadiness[];
};

/** Immutable validation facts only; payloads, paths and runtime wiring stay server-side. */
export type ModelPlatformValidationHistoryItem = {
  validation_run_id: string;
  target: "INSTALLATION" | "CAPABILITY";
  capability_code: string | null;
  validation_kind: string;
  status: string;
  occurred_at: string;
};

export type ModelPlatformInstallationTarget = {
  id: string;
  label: string;
};

export type ModelPlatformInstallationPlan = {
  id: string;
  target_library_id: string;
  target_library_label: string;
  release_code: string;
  source_kind: "OFFLINE_BUNDLE" | "TRUSTED_HTTPS";
  expected_artifact_count: number;
  expected_total_bytes: number;
  license_id: string;
  status: "AWAITING_TRUSTED_DOWNLOAD" | "DOWNLOADING" | "DOWNLOAD_FAILED" | "AWAITING_OFFLINE_IMPORT" | "IMPORTING" | "IMPORTED" | "QUARANTINED" | "FAILED";
  created_at: string;
};

export type ModelPlatformOfflineInstallationPlanInput = {
  target_library_id: string;
  release_code: string;
  bundle_reference: string;
  license_id: string;
  expected_artifacts: Array<{ relative_path: string; sha256: string; size_bytes: number }>;
};

export type ModelPlatformTrustedDownloadPlanInput = {
  target_library_id: string;
  release_code: string;
  bundle_reference: string;
  license_id: string;
  artifacts: Array<{ relative_path: string; sha256: string; size_bytes: number; source_url: string }>;
};

export type ModelPlatformQuickCreateV2Readiness = { mode: string; capability_code: string; execution_profile_version_id: string | null; ready: boolean; blocker: string | null };

export function listModelPlatformQuickCreateV2Readiness() {
  return requestJson<{ items: ModelPlatformQuickCreateV2Readiness[]; read_only: true; execution_switched: false }>("/api/v2/model-platform/quick-create-v2-readiness");
}

export type ModelPlatformQuickCreateV2DirectImagePreview = {
  capability_code: "IMAGE_CONCEPT";
  execution_profile_version_id: string | null;
  resolution_hash: string;
  executable: boolean;
  blockers: string[];
};

export function previewModelPlatformQuickCreateV2DirectImage(input: { prompt: string; run_overrides?: Record<string, unknown> }) {
  return requestJson<{ preview: ModelPlatformQuickCreateV2DirectImagePreview; read_only: true; execution_switched: true; legacy_quick_generation_touched: false }>(
    "/api/v2/model-platform/quick-create-v2/direct-image:preview",
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function submitModelPlatformQuickCreateV2DirectImage(input: { prompt: string; expected_resolution_hash: string; run_overrides?: Record<string, unknown> }, idempotencyKey: string) {
  return requestJson<{ execution: { capability_code: "IMAGE_CONCEPT"; job_id: string; execution_snapshot_id: string; execution_snapshot_hash: string; handler_code: string; handler_version: string; idempotent_replay: boolean }; execution_switched: true; legacy_quick_generation_touched: false }>(
    "/api/v2/model-platform/quick-create-v2/direct-image:submit",
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(input) },
  );
}

export type ModelPlatformQuickCreateV2DirectImageStatus = {
  capability_code: "IMAGE_CONCEPT";
  job_id: string;
  state: string;
  progress: Record<string, unknown>;
  error_code: string | null;
  error_detail_redacted: string | null;
  execution_snapshot_id: string;
  execution_snapshot_hash: string;
  artifacts: Array<{ artifact_id: string; kind: "COMFY_OUTPUT"; download_url: string }>;
};

export function getModelPlatformQuickCreateV2DirectImageStatus(jobId: string) {
  return requestJson<{ execution: ModelPlatformQuickCreateV2DirectImageStatus; read_only: true; execution_switched: true; legacy_quick_generation_touched: false }>(
    `/api/v2/model-platform/quick-create-v2/direct-image/jobs/${encodeURIComponent(jobId)}`,
  );
}

export type ModelPlatformQuickCreateV2CandidatePlan = {
  execution_profile_version_id: string | null;
  executable: boolean;
  blockers: string[];
  candidates: Array<{ ordinal: number; seed: number; resolution_hash: string }>;
};

export type ModelPlatformQuickCreateV2Run = {
  id: string;
  mode: string;
  state: string;
  selected_step_id: string | null;
  final_step_id: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
  steps: Array<{
    id: string;
    step_no: number;
    kind: "IMAGE_CANDIDATE" | "VIDEO_I2V" | string;
    state: string;
    capability_code: string;
    job_id: string;
    job_state: string;
    progress: Record<string, unknown>;
    error_code: string | null;
    error_detail_redacted: string | null;
    selection_rank: number | null;
    input_artifact_id: string | null;
    output_artifact: { artifact_id: string; kind: "COMFY_OUTPUT"; download_url: string } | null;
  }>;
};

export function previewModelPlatformQuickCreateV2ImageCandidates(input: { prompt: string; candidate_count: number }) {
  return requestJson<{ plan: ModelPlatformQuickCreateV2CandidatePlan; read_only: true; execution_switched: true; legacy_quick_generation_touched: false }>(
    "/api/v2/model-platform/quick-create-v2/image-candidates:preview",
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function submitModelPlatformQuickCreateV2ImageCandidates(
  input: { prompt: string; candidates: ModelPlatformQuickCreateV2CandidatePlan["candidates"] }, idempotencyKey: string,
) {
  return requestJson<{ run: { id: string; mode: string; state: string; idempotent_replay: boolean }; executions: Array<{ capability_code: "IMAGE_CONCEPT"; job_id: string; execution_snapshot_id: string }>; execution_switched: true; legacy_quick_generation_touched: false }>(
    "/api/v2/model-platform/quick-create-v2/image-candidates:submit",
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(input) },
  );
}

export function getModelPlatformQuickCreateV2Run(runId: string) {
  return requestJson<{ run: ModelPlatformQuickCreateV2Run; read_only: true; execution_switched: true; legacy_quick_generation_touched: false }>(
    `/api/v2/model-platform/quick-create-v2/runs/${encodeURIComponent(runId)}`,
  );
}

export function selectModelPlatformQuickCreateV2ImageCandidate(runId: string, stepId: string) {
  return requestJson<{ run: ModelPlatformQuickCreateV2Run; execution_switched: true; legacy_quick_generation_touched: false }>(
    `/api/v2/model-platform/quick-create-v2/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(stepId)}:select`,
    { method: "POST" },
  );
}

export type ModelPlatformQuickCreateV2ImageToVideoPreview = {
  run_id: string;
  selected_image_artifact_id: string;
  execution_profile_version_id: string | null;
  resolution_hash: string;
  executable: boolean;
  blockers: string[];
};

export function previewModelPlatformQuickCreateV2ImageToVideo(runId: string) {
  return requestJson<{ preview: ModelPlatformQuickCreateV2ImageToVideoPreview; read_only: true; execution_switched: true; legacy_quick_generation_touched: false }>(
    `/api/v2/model-platform/quick-create-v2/runs/${encodeURIComponent(runId)}/image-to-video:preview`,
    { method: "POST" },
  );
}

export function submitModelPlatformQuickCreateV2ImageToVideo(runId: string, expectedResolutionHash: string, idempotencyKey: string) {
  return requestJson<{ execution: { capability_code: "VIDEO_I2V"; run_id: string; job_id: string; execution_snapshot_id: string; execution_snapshot_hash: string; handler_code: string; handler_version: string; idempotent_replay: boolean }; execution_switched: true; legacy_quick_generation_touched: false }>(
    `/api/v2/model-platform/quick-create-v2/runs/${encodeURIComponent(runId)}/image-to-video:submit`,
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ expected_resolution_hash: expectedResolutionHash }) },
  );
}

export type ModelPlatformProfileLifecycle = {
  profile_version_id: string;
  profile_code: string;
  profile_title: string;
  version_no: number;
  capability_code: string;
  runtime_model_installation_ids: string[];
  lifecycle_status: "DRAFT" | "PROFILE_SMOKE_PASSED" | "PROFILE_SMOKE_FAILED" | "PUBLISHED";
  latest_validation_run_id: string | null;
  latest_validation_status: string | null;
};

export type ModelPlatformComfyWorkflowBinding = {
  id: string;
  workflow_version_id: string;
  status: "SCHEMA_VALIDATED";
  capability_smoke_passed: boolean;
};

export type ModelPlatformBusinessSelectionShadow = {
  capability_code: string;
  scope: { project_id: string; episode_id: string | null; shot_id: string | null; character_id: string | null };
  legacy_resolution: {
    execution_profile_version_id: string | null;
    source: string;
    resolution_mode: string;
    blocked_reason: string | null;
    ready: boolean;
  };
  legacy_project_binding: { execution_profile_version_id: string; binding_status: string; profile_status: string | null } | null;
  v2_resolution: {
    execution_profile_version_id: string | null;
    resolution_reason: string;
    assignment_chain: Array<{ scope_type: string; scope_id: string; resolution_mode: string; revision: number }>;
    blocked_reason: string | null;
    ready: boolean;
  };
  profile_version_crosswalk: {
    id: string;
    legacy_execution_profile_version_id: string;
    v2_execution_profile_version_id: string;
    capability_code: string;
    status: "APPROVED";
    approval_reason: string;
    approved_by: string;
    approved_at: string;
  } | null;
  parameter_contract_comparison: {
    status: string;
    matches: boolean;
    legacy_field_count: number;
    v2_field_count: number;
    common_fields: string[];
    legacy_only_fields: string[];
    v2_only_fields: string[];
    differences: { type: string[]; required: string[]; scope: string[]; constraint: string[]; default: string[] };
    unsafe_field_names: string[];
    values_exposed: false;
  };
  comparison: {
    status: "MAPPED_EQUIVALENT" | "BOTH_PRESENT_UNMAPPED" | "LEGACY_ONLY" | "V2_ONLY" | "BOTH_BLOCKED" | "LEGACY_SCOPE_UNSUPPORTED";
    comparable: boolean;
    reason: string;
    profile_version_crosswalk_available: boolean;
  };
};

/** A durable approval that only makes a V2 cutover eligible for Facade checks. */
export type ModelPlatformBusinessSelectionRollout = {
  id: string | null;
  business_surface: string;
  capability_code: string;
  scope_type: "PROJECT" | "EPISODE" | "SHOT";
  state: "SHADOW" | "CUTOVER_APPROVED";
  approval_reason: string | null;
  approved_by: string | null;
  approved_at: string | null;
  persisted: boolean;
  execution_switched: false;
};

/** Facade eligibility is informational until a separately shipped command uses it. */
export type ModelPlatformBusinessSelectionFacadeEvaluation = {
  business_surface: string;
  capability_code: string;
  scope_type: "PROJECT" | "EPISODE" | "SHOT" | "CHARACTER";
  legacy_execution_profile_version_id: string | null;
  v2_execution_profile_version_id: string | null;
  rollout_state: "SHADOW" | "CUTOVER_APPROVED";
  decision: "LEGACY_ONLY" | "CUTOVER_CANDIDATE";
  blockers: string[];
  execution_owner: "LEGACY_V1";
  execution_switched: false;
};

/** Project-scoped V2 Embedding state; source text, paths and vectors never cross this boundary. */
export type ModelPlatformProjectKnowledgeIndex = {
  id: string;
  source_document_version_id: string;
  execution_profile_version_id: string;
  chunk_count: number;
  attempt_no: number;
  retry_of_index_run_id: string | null;
  completed_batch_count: number;
  batch_count: number;
  status: "PREPARED" | "QUEUING" | "QUEUED" | "QUEUE_FAILED" | "SUCCEEDED" | string;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
};

export type ModelPlatformProjectKnowledgeSearchHit = {
  index_run_id: string;
  source_document_version_id: string;
  ordinal: number;
  source_start: number;
  source_end: number;
  excerpt: string;
  score: number;
};

export type ModelPlatformSystemOverrideField = {
  name: string;
  label: string;
  help: string;
  schema: {
    type?: "integer" | "number" | "string" | "boolean";
    minimum?: number;
    maximum?: number;
    multipleOf?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
    enum?: Array<string | number | boolean>;
    default?: string | number | boolean;
  };
};

export type ModelPlatformSystemAssignmentProfile = {
  profile_version_id: string;
  profile_code: string;
  profile_title: string;
  version_no: number;
  system_override_fields: ModelPlatformSystemOverrideField[];
};

export type ModelPlatformSystemCapabilityAssignment = {
  capability_code: string;
  title: string;
  family: string;
  background_only: boolean;
  assignment: {
    resolution_mode: "AUTO" | "EXPLICIT";
    execution_profile_version_id: string | null;
    revision: number | null;
    overrides: Record<string, string | number | boolean>;
    has_unrenderable_override: boolean;
  };
  profiles: ModelPlatformSystemAssignmentProfile[];
};

export type ModelPlatformCapabilityAssignmentPut = {
  scope_type: "SYSTEM";
  scope_id: "";
  capability_code: string;
  resolution_mode: "AUTO" | "EXPLICIT";
  execution_profile_version_id?: string | null;
  overrides: Record<string, string | number | boolean>;
  reason: string;
  actor: string;
};

/**
 * V2 control-plane reads intentionally use the generated transport.  The
 * OpenAPI generator has no hand-maintained endpoint list, so this thin,
 * typed boundary keeps API headers and contract checks centralized without
 * pretending discovery data is a generated client model.
 */
export function getModelPlatformOverview() {
  return requestJson<{ overview: ModelPlatformOverview; read_only: true }>("/api/v2/model-platform/overview");
}

export function getModelPlatformStoragePolicy() {
  return requestJson<{ storage_policy: ModelPlatformStoragePolicy; read_only: true }>("/api/v2/model-platform/storage-policy");
}

export function listModelPlatformInstallationTargets() {
  return requestJson<{
    items: ModelPlatformInstallationTarget[];
    count: number;
    read_only: true;
    absolute_paths_exposed: false;
  }>("/api/v2/model-platform/installation-targets");
}

export function listModelPlatformInstallationPlans() {
  return requestJson<{
    items: ModelPlatformInstallationPlan[];
    count: number;
    read_only: true;
    host_import_available: false;
  }>("/api/v2/model-platform/installation-plans");
}

export function createModelPlatformOfflineInstallationPlan(input: ModelPlatformOfflineInstallationPlanInput) {
  return requestJson<{ plan: ModelPlatformInstallationPlan; file_operations_started: false }>(
    "/api/v2/model-platform/installation-plans/offline",
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function createModelPlatformTrustedDownloadPlan(input: ModelPlatformTrustedDownloadPlanInput) {
  return requestJson<{ plan: ModelPlatformInstallationPlan; network_operations_started: false; host_download_available: false }>(
    "/api/v2/model-platform/installation-plans/trusted-download",
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function listModelPlatformCapabilities() {
  return requestJson<{ items: ModelPlatformCapability[]; count: number; read_only: true }>("/api/v2/model-platform/capabilities");
}

export function listModelPlatformDiscoveryObservations() {
  return requestJson<{ items: ModelPlatformDiscoveryObservation[]; count: number; read_only: true }>("/api/v2/model-platform/discovery-observations");
}

export function listModelPlatformRegisteredCandidates() {
  return requestJson<{ items: ModelPlatformRegisteredCandidate[]; count: number; read_only: true }>("/api/v2/model-platform/registered-candidates");
}

export function listModelPlatformValidationHistory(runtimeModelInstallationId: string) {
  return requestJson<{
    items: ModelPlatformValidationHistoryItem[];
    count: number;
    read_only: true;
    evidence_payload_exposed: false;
  }>(`/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/validation-history`);
}

export function listModelPlatformProfileVersions() {
  return requestJson<{ items: ModelPlatformProfileLifecycle[]; count: number; read_only: true }>("/api/v2/model-platform/profile-versions");
}

export function getModelPlatformBusinessSelectionShadow(
  capabilityCode: string,
  scope: { projectId: string; episodeId?: string | null; shotId?: string | null; characterId?: string | null },
) {
  const params = new URLSearchParams({ capability_code: capabilityCode, project_id: scope.projectId });
  if (scope.episodeId) params.set("episode_id", scope.episodeId);
  if (scope.shotId) params.set("shot_id", scope.shotId);
  if (scope.characterId) params.set("character_id", scope.characterId);
  return requestJson<{ comparison: ModelPlatformBusinessSelectionShadow; read_only: true; mutated: false }>(
    `/api/v2/model-platform/business-selection-shadow?${params}`,
  );
}

export function getModelPlatformBusinessSelectionFacadeEvaluation(
  businessSurface: string,
  capabilityCode: string,
  scope: { projectId: string; episodeId?: string | null; shotId?: string | null; characterId?: string | null },
) {
  const params = new URLSearchParams({
    business_surface: businessSurface,
    capability_code: capabilityCode,
    project_id: scope.projectId,
  });
  if (scope.episodeId) params.set("episode_id", scope.episodeId);
  if (scope.shotId) params.set("shot_id", scope.shotId);
  if (scope.characterId) params.set("character_id", scope.characterId);
  return requestJson<{ evaluation: ModelPlatformBusinessSelectionFacadeEvaluation; read_only: true; execution_switched: false }>(
    `/api/v2/model-platform/business-selection-facade-evaluation?${params}`,
  );
}

export function listModelPlatformBusinessSelectionRollouts() {
  return requestJson<{
    items: ModelPlatformBusinessSelectionRollout[];
    count: number;
    read_only: true;
    execution_switched: false;
  }>("/api/v2/model-platform/business-selection-rollouts");
}

export function listModelPlatformProjectKnowledgeIndexes(projectId: string) {
  const query = new URLSearchParams({ project_id: projectId });
  return requestJson<{ items: ModelPlatformProjectKnowledgeIndex[]; count: number; read_only: true }>(
    `/api/v2/model-platform/project-knowledge-indexes?${query}`,
  );
}

export function prepareModelPlatformProjectKnowledgeIndex(projectId: string, sourceDocumentVersionId: string) {
  return requestJson<{ index: { index_run_id: string; execution_profile_version_id: string; chunk_count: number; reused: boolean; status: ModelPlatformProjectKnowledgeIndex["status"]; attempt_no: number } }>(
    "/api/v2/model-platform/project-knowledge-indexes",
    { method: "POST", body: JSON.stringify({ project_id: projectId, source_document_version_id: sourceDocumentVersionId }) },
  );
}

export function queueModelPlatformProjectKnowledgeIndex(indexRunId: string) {
  return requestJson<{ index: { index_run_id: string; queued_batch_count: number; job_ids: string[]; status: "QUEUED" } }>(
    `/api/v2/model-platform/project-knowledge-indexes/${encodeURIComponent(indexRunId)}:queue`,
    { method: "POST" },
  );
}

export function searchModelPlatformProjectKnowledge(projectId: string, query: string, limit = 5) {
  return requestJson<{ search: { execution_profile_version_id: string; items: ModelPlatformProjectKnowledgeSearchHit[] } }>(
    "/api/v2/model-platform/project-knowledge-search",
    { method: "POST", body: JSON.stringify({ project_id: projectId, query, limit }) },
  );
}

export function listModelPlatformSystemCapabilityAssignments() {
  return requestJson<{
    items: ModelPlatformSystemCapabilityAssignment[];
    count: number;
    read_only: true;
    scope_type: "SYSTEM";
  }>("/api/v2/model-platform/system-capability-assignments");
}

export function putModelPlatformCapabilityAssignment(payload: ModelPlatformCapabilityAssignmentPut) {
  return requestJson<{ status: "SAVED" }>("/api/v2/model-platform/capability-assignments", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function smokeModelPlatformCapabilityOffering(runtimeModelInstallationId: string, capabilityCode: string) {
  return requestJson<{ validation: { validation_run_id: string; runtime_model_installation_id: string; capability_code: string; status: string; installation_ready: boolean; runtime_active: boolean } }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/capability-offerings/${encodeURIComponent(capabilityCode)}:smoke`,
    { method: "POST" },
  );
}

export function provisionModelPlatformProfile(runtimeModelInstallationId: string, capabilityCode: string, workflowBindingId?: string) {
  return requestJson<{ profile: { profile_version_id: string; profile_code: string; created: boolean } }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/capability-offerings/${encodeURIComponent(capabilityCode)}:provision-profile`,
    { method: "POST", ...(workflowBindingId ? { body: JSON.stringify({ workflow_binding_id: workflowBindingId }) } : {}) },
  );
}

export function smokeModelPlatformProfile(profileVersionId: string) {
  return requestJson<{ validation: { validation_run_id: string; profile_version_id: string; status: string; failure_code: string | null } }>(
    `/api/v2/model-platform/profile-versions/${encodeURIComponent(profileVersionId)}:smoke`,
    { method: "POST" },
  );
}

export function publishModelPlatformProfile(profileVersionId: string, validationRunId: string, reason: string) {
  return requestJson<{ profile: { profile_version_id: string; status: "PUBLISHED" } }>(
    `/api/v2/model-platform/profile-versions/${encodeURIComponent(profileVersionId)}:publish`,
    { method: "POST", body: JSON.stringify({ validation_run_id: validationRunId, reason }) },
  );
}

export function verifyModelPlatformInstallationIntegrity(runtimeModelInstallationId: string) {
  return requestJson<{ validation: { validation_run_id: string; runtime_model_installation_id: string; status: string; install_state: string } }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}:verify-integrity`,
    { method: "POST" },
  );
}

/** Bind a model Offering to an existing immutable, published Comfy workflow. */
export function bindModelPlatformComfyWorkflow(runtimeModelInstallationId: string, capabilityCode: string, workflowVersionId: string) {
  return requestJson<{ workflow_binding: { id: string; runtime_model_installation_id: string; capability_code: string; workflow_version_id: string; status: "SCHEMA_VALIDATED"; created: boolean } }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/capability-offerings/${encodeURIComponent(capabilityCode)}:bind-workflow`,
    { method: "POST", body: JSON.stringify({ workflow_version_id: workflowVersionId }) },
  );
}

export function listModelPlatformComfyWorkflowBindings(runtimeModelInstallationId: string, capabilityCode: string) {
  return requestJson<{ items: ModelPlatformComfyWorkflowBinding[]; count: number; read_only: true }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/capability-offerings/${encodeURIComponent(capabilityCode)}/workflow-bindings`,
  );
}

export function submitModelPlatformComfyCapabilitySmoke(runtimeModelInstallationId: string, capabilityCode: string, workflowBindingId: string, idempotencyKey: string) {
  return requestJson<{ smoke_job: { job_id: string; workflow_binding_id: string; runtime_model_installation_id: string; capability_code: string; workflow_version_id: string; smoke_contract_hash: string; idempotent_replay: boolean } }>(
    `/api/v2/model-platform/registered-candidates/${encodeURIComponent(runtimeModelInstallationId)}/capability-offerings/${encodeURIComponent(capabilityCode)}:queue-comfy-smoke`,
    { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ workflow_binding_id: workflowBindingId }) },
  );
}

export function runModelPlatformOllamaDiscovery() {
  return requestJson<{ discovery_run: { id: string; status: string; observation_count: number } }>("/api/v2/model-platform/discovery-runs:ollama", { method: "POST" });
}

export function runModelPlatformLlamaCppDiscovery() {
  return requestJson<{ discovery_run: { id: string; status: string; observation_count: number } }>("/api/v2/model-platform/discovery-runs:llama-cpp", { method: "POST" });
}

export function runModelPlatformModelLockDiscovery() {
  return requestJson<{ discovery_runs: Array<{ id: string; status: string; observation_count: number }> }>("/api/v2/model-platform/discovery-runs:model-lock", { method: "POST" });
}

export function registerModelPlatformDiscoveryObservation(observationId: string) {
  return requestJson<{ candidate: { model_release_id: string; model_release_code: string; runtime_model_installation_id: string; created: boolean; validation_status: string } }>(`/api/v2/model-platform/discovery-observations/${encodeURIComponent(observationId)}:register`, { method: "POST" });
}
