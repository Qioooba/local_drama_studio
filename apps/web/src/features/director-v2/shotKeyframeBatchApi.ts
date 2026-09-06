import { requestJson } from "../../generated/api";
import type { StoryboardGenerationBatchTarget } from "./storyboardGenerationBatchApi";

export type ShotFrameStrategy = "FIRST_ONLY" | "FIRST_AND_LAST";
export type ShotPromptBundleRequest = {
  base_prompt?: string | null;
  positive_override?: string;
  negative_prompt?: string;
  provenance?: "AI_GENERATED" | "PAGE_USER_EDIT";
  frame_reframe_mode?: "NONE" | "SINGLE_MOMENT";
};
export type ShotPromptBundle = ShotPromptBundleRequest & {
  schema_version: string;
  base_prompt: string;
  effective_base_prompt: string;
  frame_role: "FIRST_FRAME" | "END_FRAME" | null;
  frame_reframe_mode: "NONE" | "SINGLE_MOMENT";
  positive_override: string;
  negative_prompt: string;
  provenance: "AI_GENERATED" | "PAGE_USER_EDIT";
  compiler_mode: "WORKFLOW_NEGATIVE_BINDING" | "PROMPT_AVOID_FALLBACK" | string;
  final_prompt: string;
};
export type ShotKeyframeBatchPlan = {
  episode_id: string;
  project_id: string;
  targets: StoryboardGenerationBatchTarget[];
  frame_strategy: ShotFrameStrategy;
  candidate_count: number;
  prompt_bundle?: ShotPromptBundle | null;
  execution_contract?: {
    source?: string | null;
    contract_id?: string | null;
    runtime_environment_version_id?: string | null;
    published_contract_bound?: boolean;
    compiler_mode?: string | null;
    semantic_roles?: string[];
    workflow_bindings?: Record<string, { node_id?: string; input?: string; [key: string]: unknown }>;
    seed_policy?: string | null;
    [key: string]: unknown;
  };
  plan_hash: string;
  valid: boolean;
  issues: Array<{ code: string; shot_id?: string; frame_role?: string; message: string; [key: string]: unknown }>;
  items: Array<{
    shot_id: string; shot_code: string; shot_revision: number; frame_role: "FIRST_FRAME" | "END_FRAME";
    candidate_index: number; profile_version_id: string | null; shot_keyframe_route?: Record<string, unknown>; identity_inputs?: { snapshot_hash?: string | null; references?: Array<{ role: string; character_name: string; media_version_id: string; pack_version_id: string }> }; workflow_bindings?: Record<string, unknown>; semantic_inputs?: Record<string, unknown>; prompt: string; prompt_bundle: ShotPromptBundle;
    status: "READY" | "BLOCKED"; blockers: Array<{ code: string; message: string }>;
  }>;
  summary: { shots: number; jobs: number; blocked: number };
};

export type ShotKeyframeBatch = {
  id: string; episode_id: string; project_id: string; frame_strategy: ShotFrameStrategy;
  candidate_count: number; status: string; plan_hash: string; created_at: string; prompt_bundle?: ShotPromptBundle | null;
  summary: { total: number; succeeded: number; failed: number; active: number };
};

export function planShotKeyframeBatch(
  episodeId: string,
  targets: StoryboardGenerationBatchTarget[],
  frameStrategy: ShotFrameStrategy,
  candidateCount: number,
  promptBundle?: ShotPromptBundleRequest,
): Promise<{ plan: ShotKeyframeBatchPlan }> {
  return requestJson(`/api/v2/episodes/${encodeURIComponent(episodeId)}/shot-keyframe-batches:plan`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets, frame_strategy: frameStrategy, candidate_count: candidateCount, ...(promptBundle ? { prompt_bundle: promptBundle } : {}) }),
  });
}

export function submitShotKeyframeBatch(
  episodeId: string,
  targets: StoryboardGenerationBatchTarget[],
  frameStrategy: ShotFrameStrategy,
  candidateCount: number,
  expectedPlanHash: string,
  idempotencyKey: string,
  promptBundle?: ShotPromptBundleRequest,
): Promise<{ batch: ShotKeyframeBatch }> {
  return requestJson(`/api/v2/episodes/${encodeURIComponent(episodeId)}/shot-keyframe-batches:submit`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets, frame_strategy: frameStrategy, candidate_count: candidateCount, ...(promptBundle ? { prompt_bundle: promptBundle } : {}), expected_plan_hash: expectedPlanHash, idempotency_key: idempotencyKey }),
  });
}
