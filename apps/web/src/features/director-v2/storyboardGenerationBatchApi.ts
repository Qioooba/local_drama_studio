import { requestJson } from "../../generated/api";

export type StoryboardGenerationBatchTarget = { shot_id: string; expected_revision: number };

export type StoryboardGenerationBatchPlan = {
  episode_id: string;
  project_id: string;
  targets: StoryboardGenerationBatchTarget[];
  input_fingerprint: string;
  plan_hash: string;
  valid: boolean;
  issues: Array<{ code: string; shot_id?: string; message: string; [key: string]: unknown }>;
  items: Array<{
    shot_id: string;
    shot_code: string;
    shot_revision: number;
    prompt: string;
    prompt_modifiers: string[];
    status: "READY" | "BLOCKED";
    blockers: Array<{ code: string; [key: string]: unknown }>;
  }>;
  summary: { selected: number; ready: number; blocked: number };
};

export type StoryboardGenerationBatchSubmission = {
  plan_hash: string;
  workflow_id: string;
  run_id: string;
  status: string;
  task_count: number;
  selected_shot_ids: string[];
  idempotent_replay: boolean;
};

export async function planStoryboardGenerationBatch(
  episodeId: string,
  targets: StoryboardGenerationBatchTarget[],
): Promise<{ plan: StoryboardGenerationBatchPlan }> {
  return requestJson(`/api/v2/episodes/${encodeURIComponent(episodeId)}/storyboard-generation-batches:plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets }),
  });
}

export async function submitStoryboardGenerationBatch(
  episodeId: string,
  targets: StoryboardGenerationBatchTarget[],
  expectedPlanHash: string,
  idempotencyKey: string,
): Promise<{ batch: StoryboardGenerationBatchSubmission }> {
  return requestJson(`/api/v2/episodes/${encodeURIComponent(episodeId)}/storyboard-generation-batches:submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets, expected_plan_hash: expectedPlanHash, idempotency_key: idempotencyKey }),
  });
}
