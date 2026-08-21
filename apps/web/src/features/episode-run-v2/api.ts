export type PreflightCheckStatus = "PASS" | "BLOCKED";
export type EpisodeProductionMode = "DRAFT" | "BALANCED" | "QUALITY";
export type EpisodeCheckpointPolicy = "AUTO_CONTINUE" | "AFTER_ASSETS" | "AFTER_SHOT_PLAN" | "BEFORE_VIDEO" | "ON_EXCEPTION";

export type EpisodePreflightCheck = {
  code: string;
  label: string;
  status: PreflightCheckStatus;
  blocking: boolean;
  detail: string;
  evidence: Record<string, unknown>;
};

export type EpisodeProductionPreflight = {
  episode: {
    id: string;
    code: string;
    title: string;
    project_id: string;
    project_title: string;
  };
  status: "PASS" | "BLOCKED";
  checks: EpisodePreflightCheck[];
  blockers: EpisodePreflightCheck[];
  input_fingerprint: string;
  tts_enabled: boolean;
  production_mode: EpisodeProductionMode;
  mode_policy: { target_take_count: number; label: string; intent: string };
  checkpoint_policy: EpisodeCheckpointPolicy;
  would_create_jobs: false;
  runtime_contacted: false;
  network_contacted: false;
  mutated: false;
};

export type EpisodeRunStage = {
  ordinal: number;
  code: "STORY_ANALYSIS" | "ASSET_EXTRACTION" | "ASSET_COMPLETION" | "SHOT_PLANNING" | "SHOT_IMAGE" | "VIDEO" | "AUDIO_SUBTITLE" | "COMPOSE_QC";
  label: string;
  background_stages: Array<"STORY_READY" | "ASSET_READY" | "SHOT_PLAN_READY" | "KEYFRAME_GENERATION" | "VIDEO_GENERATION" | "QC" | "AUDIO" | "TIMELINE" | "EPISODE_COMPOSE" | "HUMAN_REVIEW" | "DELIVERY_READY">;
  status: "PENDING" | "RUNNING" | "PAUSED" | "BLOCKED" | "COMPLETED";
  completed: number;
  total: number;
  remaining_count: number;
  running_jobs: number;
  failed: number;
  failed_jobs: number;
  needs_human_decision: number;
  hitl_jobs: number;
  estimated_remaining_seconds: number | null;
  estimate_status: "NOT_AVAILABLE" | "AVAILABLE";
  jobs?: Array<{ task_id: string; job_id: string | null; job_state: string | null; item_key: string; status: string }>;
};

export type EpisodeProductionRun = {
  id: string;
  episode_id: string;
  project_id: string;
  status: string;
  input_fingerprint: string;
  production_mode: EpisodeProductionMode;
  mode_policy: { target_take_count: number; label: string; intent: string };
  checkpoint_policy: EpisodeCheckpointPolicy;
  stages: EpisodeRunStage[];
  pending_gate: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
  revision: number;
  local_only: true;
  queue_reused: true;
};

type ErrorEnvelope = { detail?: string | { message?: string }; error?: { message?: string } };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    const detail = typeof payload?.detail === "string" ? payload.detail : payload?.detail?.message;
    throw new Error(detail ?? payload?.error?.message ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export async function preflightEpisodeRun(episodeId: string, ttsEnabled: boolean, productionMode: EpisodeProductionMode, checkpointPolicy: EpisodeCheckpointPolicy = "ON_EXCEPTION") {
  const query = new URLSearchParams({ tts_enabled: String(ttsEnabled), production_mode: productionMode, checkpoint_policy: checkpointPolicy });
  const payload = await request<{ preflight: EpisodeProductionPreflight }>(
    `/episodes/${encodeURIComponent(episodeId)}/production-runs/preflight?${query}`,
  );
  return payload.preflight;
}

export async function startEpisodeRun(episodeId: string, ttsEnabled: boolean, productionMode: EpisodeProductionMode, checkpointPolicy: EpisodeCheckpointPolicy = "ON_EXCEPTION") {
  const payload = await request<{ run: EpisodeProductionRun }>(
    `/episodes/${encodeURIComponent(episodeId)}/production-runs`,
    {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ tts_enabled: ttsEnabled, production_mode: productionMode, checkpoint_policy: checkpointPolicy }),
    },
  );
  return payload.run;
}

export async function getEpisodeRun(runId: string) {
  const payload = await request<{ run: EpisodeProductionRun }>(
    `/episode-production-runs/${encodeURIComponent(runId)}?include_jobs=false`,
  );
  return payload.run;
}

async function command(runId: string, action: "pause" | "resume" | "cancel", body?: object) {
  const payload = await request<{ run: EpisodeProductionRun }>(
    `/episode-production-runs/${encodeURIComponent(runId)}/${action}`,
    { method: "POST", body: body ? JSON.stringify(body) : undefined },
  );
  return payload.run;
}

export const pauseEpisodeRun = (runId: string) => command(runId, "pause", { reason: "CREATOR_PAUSE" });
export const resumeEpisodeRun = (runId: string) => command(runId, "resume", { note: "创作者确认后继续整集自动生产" });
export const cancelEpisodeRun = (runId: string) => command(runId, "cancel");
