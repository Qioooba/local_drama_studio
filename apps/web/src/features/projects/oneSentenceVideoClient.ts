import { requestJson, type Job } from "../../generated/api";

export type OneSentenceOutputSpec = {
  width: number;
  height: number;
  frame_count: number;
  fps: number;
  duration_seconds: number;
  target_duration_ms: number;
  aspect_ratio: string;
  source: "PUBLISHED_WORKFLOW";
  editable: false;
};

export type OneSentenceImageSpec = {
  width: number;
  height: number;
  aspect_ratio: string;
  source: "PUBLISHED_WORKFLOW";
  editable: false;
};

export type OneSentenceMode = "DIRECT_T2V" | "KEYFRAME_I2V";

export type OneSentenceVideoPlan = {
  schema_version: string;
  mode: OneSentenceMode;
  story: string;
  language: "zh-CN" | "en-US";
  video_plan: {
    title: string;
    video_prompt: string;
    keyframe_prompt: string;
    director_intent: Record<string, unknown>;
    camera_movement: string;
    provider: string;
    model: string;
    remote: boolean;
  };
  output_spec: OneSentenceOutputSpec;
  image_spec: OneSentenceImageSpec | null;
  image_candidate_count: number;
  llm: { title: string; provider: string; model: string; remote: boolean; profile_version_id: string };
  image: { title: string; profile_version_id: string; workflow_version_id: string; workflow_title: string } | null;
  video: { title: string; capability: "VIDEO_T2V" | "VIDEO_I2V"; profile_version_id: string; workflow_version_id: string; workflow_title: string };
  runtime: { status: string; endpoint?: string | null };
  production_mutations: string[];
  confirmation_required: true;
};

export type OneSentenceVideoRun = {
  id: string;
  mode: OneSentenceMode;
  state: "PLANNING" | "PLANNED" | "COMMITTING" | "GENERATING" | "AWAITING_SELECTION" | "CANCELLING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  stage: string;
  story: { text: string };
  story_sha256: string;
  language: "zh-CN" | "en-US";
  llm_profile_version_id: string;
  image_profile_version_id?: string | null;
  video_profile_version_id: string;
  image_candidate_count: number;
  remote_outbound_confirmed: boolean;
  plan: OneSentenceVideoPlan | Record<string, never>;
  plan_hash?: string | null;
  project_id?: string | null;
  episode_id?: string | null;
  shot_id?: string | null;
  variant_id?: string | null;
  job_id?: string | null;
  media_version_id?: string | null;
  selected_candidate_id?: string | null;
  selected_image_media_version_id?: string | null;
  seed?: number | null;
  retry_count: number;
  error: { code?: string; message?: string; details?: Record<string, unknown>; retryable?: boolean };
  links: { project?: string; generation?: string; review?: string };
  job?: Job | null;
  candidates: OneSentenceImageCandidate[];
  updated_at: string;
};

export type OneSentenceImageCandidate = {
  id: string;
  batch_no: number;
  ordinal: number;
  state: string;
  seed: number;
  variant_id?: string | null;
  job_id?: string | null;
  media_version_id?: string | null;
  parent_candidate_id?: string | null;
  selected: boolean;
  error: { code?: string; message?: string };
  job?: Job | null;
};

export type OneSentencePlanInput = {
  story: string;
  mode: OneSentenceMode;
  language: "zh-CN" | "en-US";
  llm_profile_version_id: string;
  image_profile_version_id: string | null;
  video_profile_version_id: string;
  image_candidate_count: number;
  allow_remote_outbound: boolean;
};

export function planOneSentenceVideo(input: OneSentencePlanInput, idempotencyKey: string, signal?: AbortSignal) {
  return requestJson<{ run: OneSentenceVideoRun }>("/api/v1/one-sentence-video-runs:plan", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
    signal,
  });
}

export function getOneSentenceVideoRun(runId: string, signal?: AbortSignal) {
  return requestJson<{ run: OneSentenceVideoRun }>(`/api/v1/one-sentence-video-runs/${encodeURIComponent(runId)}`, { signal });
}

export function listOneSentenceVideoRuns(limit = 8, signal?: AbortSignal) {
  return requestJson<{ items: OneSentenceVideoRun[] }>(`/api/v1/one-sentence-video-runs?limit=${limit}`, { signal });
}

function command(runId: string, action: "commit" | "resume" | "cancel", signal?: AbortSignal) {
  return requestJson<{ run: OneSentenceVideoRun }>(
    `/api/v1/one-sentence-video-runs/${encodeURIComponent(runId)}:${action}`,
    { method: "POST", signal },
  );
}

export const commitOneSentenceVideoRun = (runId: string, signal?: AbortSignal) => command(runId, "commit", signal);
export const resumeOneSentenceVideoRun = (runId: string, signal?: AbortSignal) => command(runId, "resume", signal);
export const cancelOneSentenceVideoRun = (runId: string, signal?: AbortSignal) => command(runId, "cancel", signal);

export function retryOneSentenceVideoRun(runId: string, mode: "SAME_INPUT" | "NEW_SEED", signal?: AbortSignal) {
  return requestJson<{ run: OneSentenceVideoRun }>(`/api/v1/one-sentence-video-runs/${encodeURIComponent(runId)}:retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
    signal,
  });
}

export function rerollOneSentenceImages(
  runId: string,
  input: { count: number; parent_candidate_id?: string | null },
  signal?: AbortSignal,
) {
  return requestJson<{ run: OneSentenceVideoRun }>(
    `/api/v1/one-sentence-video-runs/${encodeURIComponent(runId)}:reroll-images`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    },
  );
}

export function selectOneSentenceImageCandidate(runId: string, candidateId: string, signal?: AbortSignal) {
  return requestJson<{ run: OneSentenceVideoRun }>(
    `/api/v1/one-sentence-video-runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}:select`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_review_checks: true }),
      signal,
    },
  );
}
