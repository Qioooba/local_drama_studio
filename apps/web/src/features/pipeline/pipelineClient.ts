import { requestJson } from "../../generated/api";

export type PipelineAssetCandidate = {
  id?: string;
  kind: string;
  code?: string;
  name: string;
  description?: string;
  introduction?: string;
  biography?: string;
  role?: string;
  aliases?: string[];
  appearance?: string;
  costume?: string;
  personality?: string;
  motivation?: string;
  background?: string;
  skills?: string[];
  weakness?: string;
  arc?: string;
  relationships?: string[];
  voice?: string;
  geography?: string;
  architecture?: string;
  layout?: string;
  atmosphere?: string;
  lighting?: string;
  time?: string;
  color_palette?: string[];
  key_elements?: string[];
  story_function?: string;
  material?: string;
  function?: string;
  story_significance?: string;
  owner?: string;
  rules?: string;
  visual_prompt?: string;
  status?: string;
};

export type PipelineDraft = {
  schema_version?: string;
  source?: { document_version_id: string; sha256: string; character_count: number };
  settings?: { visual_style: string; target_episode_duration_seconds: number; voice_preset: string };
  story_plan?: {
    episodes: Array<{
      number: number;
      code: string;
      title: string;
      summary: string;
      logline?: string;
      opening_hook?: string;
      core_conflict?: string;
      climax?: string;
      ending_hook?: string;
      theme?: string;
      source_evidence?: string[];
      source_start_paragraph?: number;
      source_end_paragraph?: number;
    }>;
  };
  story_bible?: {
    title: string;
    logline: string;
    synopsis: string;
    genre?: string[];
    audience?: string;
    themes?: string[];
    tone?: string;
    worldview?: string;
    world_rules?: string[];
    timeline?: string;
    central_conflict?: string;
    narrative_structure?: string;
    color_language?: string;
    taboos?: string[];
    ending_direction?: string;
    visual_style: string;
    target_episode_duration_seconds: number;
  };
  assets?: {
    characters: PipelineAssetCandidate[];
    scenes: PipelineAssetCandidate[];
    props: PipelineAssetCandidate[];
  };
  breakdowns?: Array<{
    episode_number: number;
    episode_code: string;
    episode_title: string;
    draft: { scenes: Array<{ scene_no: number; title: string; introduction: string; location: string; time: string; atmosphere: string; purpose: string; characters: string[]; shots: unknown[] }> };
  }>;
  generation?: {
    generation_mode: "LLM_COMPLETE" | "LLM_STAGED";
    profile_version_id?: string | null;
    provider: string;
    model: string;
    llm_call_count: number;
    generated_at: string;
    media_generation_started: false;
    coverage: string[];
  };
};

export type PipelineQualityReport = {
  status?: "READY" | "REVIEW_REQUIRED" | "BLOCKED";
  blockers?: string[];
  warnings?: string[];
  checks?: Array<{ code: string; label: string; passed: boolean }>;
};

export type PipelineRun = {
  run_id: string;
  project_id: string;
  job_id?: string | null;
  state: "RUNNING" | "PAUSED" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  stage: string;
  stage_label: string;
  progress_pct: number;
  revision: number;
  visual_style: string;
  target_episode_duration_seconds: number;
  voice_preset: string;
  auto_run_rendering: boolean;
  source_document_version_id?: string | null;
  source_label?: string;
  capability_profile_version_id?: string | null;
  episodes_count: number;
  characters_count: number;
  scenes_count: number;
  props_count: number;
  shots_count: number;
  episodes: Array<{ number?: number; code: string; title: string; summary?: string }>;
  assets: {
    characters: PipelineAssetCandidate[];
    scenes: PipelineAssetCandidate[];
    props: PipelineAssetCandidate[];
  };
  draft: PipelineDraft;
  quality_report: PipelineQualityReport;
  apply_state: "NOT_APPLIED" | "APPLIED";
  applied_sections: string[];
  applied_at?: string | null;
  supersedes_run_id?: string | null;
  extraction_mode?: "LLM" | "UNKNOWN";
  llm_model?: string | null;
  llm_provider?: string | null;
  llm_error?: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
};

export type PipelineRunSummary = Pick<PipelineRun,
  | "run_id" | "project_id" | "state" | "stage" | "stage_label" | "progress_pct" | "revision"
  | "source_label" | "episodes_count" | "characters_count" | "scenes_count" | "props_count"
  | "shots_count" | "apply_state" | "created_at" | "updated_at" | "error_message"
>;

export type LLMConfigPayload = {
  provider: "OLLAMA_LOOPBACK" | "OPENAI_COMPAT" | "LLAMA_CPP_MANAGED";
  base_url: string;
  model: string;
  api_key?: string;
};

export type StartPipelinePayload = {
  source_document_version_id?: string;
  raw_text?: string;
  visual_style?: string;
  target_episode_duration_seconds?: number;
  voice_preset?: string;
  capability_profile_version_id?: string;
  llm_config?: LLMConfigPayload;
};

export type PipelinePreflight = {
  safe_mode: boolean;
  ai: {
    required: true;
    ready: boolean;
    profile_version_id?: string | null;
    provider?: string | null;
    model?: string | null;
    error_code?: string;
    message?: string;
  };
  source: {
    label: string;
    character_count: number;
    paragraph_count: number;
    chapter_count: number;
    sha256: string;
  };
  existing: { episodes: number; bibles: number; assets: number; shots: number };
  estimated_episode_count: number;
  warnings: string[];
  effects: { generation: string; apply: string };
};

export async function preflightStoryPipeline(
  projectId: string,
  payload: Pick<StartPipelinePayload, "source_document_version_id" | "raw_text" | "target_episode_duration_seconds" | "capability_profile_version_id">,
): Promise<PipelinePreflight> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline:preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function startOneClickPipeline(projectId: string, payload: StartPipelinePayload): Promise<{ run: PipelineRun }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline:start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getPipelineRun(projectId: string, runId: string): Promise<{ run: PipelineRun }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/${encodeURIComponent(runId)}`);
}

export async function getLatestPipeline(projectId: string): Promise<{ run: PipelineRun | null }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/latest`);
}

export async function cancelPipelineRun(projectId: string, runId: string): Promise<{ run: PipelineRun }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/${encodeURIComponent(runId)}:cancel`, {
    method: "POST",
  });
}

export async function retryPipelineRun(projectId: string, runId: string, expectedRevision: number): Promise<{ run: PipelineRun }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/${encodeURIComponent(runId)}:retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function applyPipelineRun(
  projectId: string,
  runId: string,
  expectedRevision: number,
  sections: string[],
): Promise<{
  run: PipelineRun;
  created: { episodes: number; bible_revisions: number; asset_proposals: number; creative_dossiers: number; breakdown_drafts: number };
  sections: string[];
}> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/${encodeURIComponent(runId)}:apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: expectedRevision, sections }),
  });
}

export async function listPipelineRuns(projectId: string): Promise<{ runs: PipelineRunSummary[] }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/pipeline/runs`);
}

export type WholeDramaEpisodeStatus = {
  episode_id: string;
  code: string;
  title: string;
  production_status: string;
  total_shots: number;
  ready_shots: number;
  draft_shots: number;
  keyframes_count: number;
  videos_count: number;
  dialogue_lines: number;
  voiced_lines: number;
  timeline_status: string;
  workflow_run_id?: string | null;
  workflow_run_status: string;
};

export type WholeDramaStatus = {
  project_id: string;
  project_code: string;
  project_title: string;
  overall_status: string;
  total_episodes: number;
  episodes: WholeDramaEpisodeStatus[];
};

export async function getWholeDramaStatus(projectId: string): Promise<WholeDramaStatus> {
  return requestJson(`/api/v2/projects/${encodeURIComponent(projectId)}/whole-drama:status`);
}

export async function runWholeDrama(
  projectId: string,
  payload?: {
    tts_enabled?: boolean;
    production_mode?: string;
    checkpoint_policy?: string;
    min_free_disk_bytes?: number;
    actor?: string;
  },
): Promise<{
  project_id: string;
  preparation: unknown;
  dispatched_runs: Array<{ episode_id: string; code: string; status: string; run_id?: string }>;
  total_episodes: number;
  dispatched_count: number;
}> {
  return requestJson(`/api/v2/projects/${encodeURIComponent(projectId)}/whole-drama:run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

