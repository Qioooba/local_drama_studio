import { requestJson } from "../../generated/api";

export type AdaptationMode =
  | "COMPLETE_WORK"
  | "SERIAL_INCREMENTAL"
  | "PRESEGMENTED_SCRIPT"
  | "SINGLE_EPISODE";

export type SourceVersionSummary = {
  source_document_id: string;
  source_document_version_id: string;
  title: string;
  source_name: string;
  version_no: number;
  parse_status: string;
  source_sha256: string;
  text_sha256: string;
  character_count: number;
  paragraph_count: number;
  chapters: Array<{ title: string; start_paragraph: number; end_paragraph: number }>;
  preview_truncated: boolean;
  created_at: string;
};

export type AdaptationPreflight = {
  source: {
    source_document_id: string;
    source_document_version_id: string;
    title: string;
    source_name: string;
    source_sha256: string;
    text_sha256: string;
  };
  scope: {
    source_document_version_id: string;
    source_paragraph_start: number;
    source_paragraph_end: number;
    unicode_start: number;
    unicode_end: number;
    selection_mode: "EXPLICIT" | "FULL_DOCUMENT";
  };
  diagnosis: {
    type: "NOVEL_LONG_FORM" | "NOVEL_EXCERPT" | "SINGLE_EPISODE_SCRIPT";
    character_count: number;
    paragraph_count: number;
    body_paragraph_count: number;
    chapter_count: number;
    chapter_titles: string[];
    recommended_mode: AdaptationMode;
    estimated_episode_range: { minimum: number; maximum: number };
    target_duration_ms: number;
  };
  execution: {
    stage: "PREFLIGHT";
    requires_episode: false;
    creates_media: false;
    remote_outbound_consent_required: boolean;
    profile_status: "NOT_SELECTED";
    estimated_input_tokens: number;
  };
  warnings: Array<{ code: string; message: string }>;
};

export type AdaptationPlanCreateInput = {
  source_document_version_id: string;
  mode: AdaptationMode;
  target_duration_ms: number;
  episode_strategy?: "AI_ESTIMATE" | "FIXED_COUNT";
  requested_episode_count?: number;
  season_strategy?: "NONE" | "AI_SUGGESTED" | "FIXED_COUNT" | "INHERIT_EXISTING";
  requested_season_count?: number;
};

export type AdaptationPlanCreated = {
  plan_id: string;
  artifact_status: string;
  revision_id: string;
  revision_no: number;
  run_id: string;
  run_status: string;
  created_at: string;
  idempotent: boolean;
};

export type AdaptationPlanSummary = {
  id: string;
  mode: AdaptationMode;
  artifact_status: string;
  source_document_version_id: string;
  source_title: string;
  revision_id: string;
  revision_no: number;
  diagnosis: AdaptationPreflight["diagnosis"];
  latest_run_status: string;
  episode_count: number;
  updated_at: string;
};

export type AdaptationPlanWorkspace = {
  plan: {
    id: string;
    project_id: string;
    source_document_version_id: string;
    source_title: string;
    source_name: string;
    mode: AdaptationMode;
    artifact_status: string;
    updated_at: string;
  };
  revision: {
    id: string;
    revision_no: number;
    source_scope: AdaptationPreflight["scope"];
    constraints: Record<string, unknown>;
    diagnosis: AdaptationPreflight["diagnosis"];
    validation_summary: Record<string, unknown>;
    content_sha256: string;
  };
  run: {
    id: string | null;
    status: string;
    total_nodes: number;
    completed_nodes: number;
    failed_nodes: number;
    estimated_input_tokens: number | null;
    estimated_cost_microunits: number | null;
  };
  episodes: Array<{
    id: string;
    logical_episode_id: string;
    display_ordinal: number;
    title: string;
    logline: string;
    target_duration_ms: number;
    estimated_duration_ms: number | null;
    review_state: string;
    lock_state: string;
    evidence_count: number;
  }>;
  analysis_nodes: Array<{
    node_key: string;
    stage: "CHUNK_MAP" | "ARC_REDUCE" | "SEASON_PLAN" | "EPISODE_BOUNDARY" | "VALIDATE";
    core_source_start: number | null;
    core_source_end: number | null;
    context_source_start: number | null;
    context_source_end: number | null;
    job_id: string | null;
    state: string;
    quality_flags: string[];
  }>;
  next_action: string;
};

export type AdaptationAnalysisManifest = {
  run_id: string;
  run_status: string;
  total_nodes: number;
  idempotent: boolean;
};

export type AdaptationAnalysisReadiness = {
  plan_id: string;
  revision_id: string;
  run_id: string | null;
  profile: {
    id: string;
    code: string;
    title: string;
    version_no: number;
    status: string;
    capability: string;
    provider: string;
    model: string;
  } | null;
  execution: {
    state: "READY" | "BLOCKED";
    planned_node_count: number;
    map_node_count: number;
    estimated_input_characters: number;
    estimated_input_tokens: number;
    provider_remote: boolean;
    requires_remote_outbound_confirmation: boolean;
    creates_media: false;
    materializes_project_structure: false;
  };
  blockers: Array<{ code: string; message: string }>;
  read_only: true;
};

export type AdaptationAnalysisSubmitResponse = {
  run_id: string;
  run_status: "QUEUED";
  job_count: number;
  created_job_ids: string[];
  provider_remote: boolean;
  idempotent: boolean;
};

export type AdaptationPlanApproval = {
  plan_id: string;
  artifact_status: "APPROVED";
  revision_id: string;
  approved_at: string;
};

export type AdaptationMaterializationPreflight = {
  plan_id: string;
  revision_id: string;
  artifact_status: "APPROVED" | "MATERIALIZED" | string;
  strategy: "APPEND_NEW";
  ready: boolean;
  blockers: Array<{ code: string; message: string }>;
  existing_structure: { season_count: number; episode_count: number; max_global_episode_number?: number };
  would_create: { season_count: number; episode_count: number; strategy: "APPEND_NEW" };
  manifest_sha256: string;
  already_materialized: boolean;
  mutated: false;
};

export type AdaptationMaterialization = {
  materialization_id: string;
  plan_id: string;
  revision_id: string;
  project_id: string;
  strategy: "APPEND_NEW";
  created_at: string;
  items: Array<{ plan_episode_id: string; season_id: string; season_code: string; episode_id: string; episode_code: string }>;
  idempotent: boolean;
};

const adaptationPath = (projectId: string, suffix: string) =>
  "/api/v2/projects/" + encodeURIComponent(projectId) + suffix;

export function listAdaptationSources(projectId: string): Promise<{ items: SourceVersionSummary[] }> {
  return requestJson(adaptationPath(projectId, "/source-versions"));
}

export function preflightAdaptationPlan(
  projectId: string,
  input: Pick<AdaptationPlanCreateInput, "source_document_version_id" | "target_duration_ms">,
): Promise<AdaptationPreflight> {
  return requestJson(adaptationPath(projectId, "/adaptation-plans:preflight"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function createAdaptationPlan(
  projectId: string,
  input: AdaptationPlanCreateInput,
  idempotencyKey: string,
): Promise<AdaptationPlanCreated> {
  return requestJson(adaptationPath(projectId, "/adaptation-plans"), {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function listAdaptationPlans(projectId: string): Promise<{ items: AdaptationPlanSummary[] }> {
  return requestJson(adaptationPath(projectId, "/adaptation-plans"));
}

export function getAdaptationPlanWorkspace(planId: string): Promise<AdaptationPlanWorkspace> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/workspace");
}

export function prepareAdaptationAnalysisManifest(planId: string): Promise<AdaptationAnalysisManifest> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/analysis-manifest", {
    method: "POST",
  });
}

export function getAdaptationAnalysisReadiness(
  planId: string,
  profileVersionId: string,
): Promise<AdaptationAnalysisReadiness> {
  const query = new URLSearchParams({ profile_version_id: profileVersionId });
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/analysis-readiness?" + query.toString());
}

export function submitAdaptationAnalysisRun(
  planId: string,
  input: { profile_version_id: string; allow_remote_outbound: boolean },
  idempotencyKey: string,
): Promise<AdaptationAnalysisSubmitResponse> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/analysis-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function approveAdaptationPlan(planId: string, expectedContentSha256: string): Promise<AdaptationPlanApproval> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + ":approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_content_sha256: expectedContentSha256 }),
  });
}

export function getAdaptationMaterializationPreflight(planId: string): Promise<AdaptationMaterializationPreflight> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/materialization-preflight");
}

export function materializeAdaptationPlan(
  planId: string,
  expectedContentSha256: string,
  idempotencyKey: string,
): Promise<AdaptationMaterialization> {
  return requestJson("/api/v2/adaptation-plans/" + encodeURIComponent(planId) + "/materializations", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ expected_content_sha256: expectedContentSha256, confirm_append: true }),
  });
}
