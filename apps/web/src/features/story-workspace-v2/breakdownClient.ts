import { requestJson as generatedRequestJson } from "../../generated/api";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(path, init);
}

export interface LocalLLMProbeLevel {
  passed: boolean;
  name?: string;
  message?: string;
  code?: string;
  duration_ms?: number;
}

export interface LocalLLMStatus {
  status: "PASS" | "FAIL" | "BLOCKED" | "NOT_CONFIGURED" | "CONFIGURED";
  provider?: string;
  base_url?: string;
  model?: string | null;
  models?: string[];
  has_api_key?: boolean;
  masked_api_key?: string | null;
  probe_level_passed?: number;
  probe_levels?: {
    level_1_network?: LocalLLMProbeLevel;
    level_2_auth?: LocalLLMProbeLevel;
    level_3_model?: LocalLLMProbeLevel;
    level_4_inference?: LocalLLMProbeLevel;
  };
  model_present?: boolean;
  sample_output?: Record<string, unknown>;
  error_code?: string;
  message?: string;
}

export interface BreakdownSubmission {
  job: Job;
  automatic_apply: false;
  requires_human_action: true;
}

export interface ProbeLocalLLMParams {
  provider_connection_id?: string;
  provider?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  remember_api_key?: boolean;
  load_test?: boolean;
  allow_remote_outbound?: boolean;
}

export interface SyncLocalLLMProfileParams {
  provider_connection_id?: string;
  model?: string;
  capability?: string;
  provider?: string;
  base_url?: string;
  api_key?: string;
  allow_remote_outbound?: boolean;
  probe_job_id?: string;
}

export interface PublishLocalLLMProfileParams {
  profileVersionId: string;
  apiKey?: string;
  allowRemoteOutbound?: boolean;
  probeJobId?: string;
}

export async function getLocalLLMStatus(params?: {
  provider?: string;
  base_url?: string;
  model?: string;
  live_probe?: boolean;
}): Promise<{ status: LocalLLMStatus }> {
  const query = new URLSearchParams();
  if (params?.provider) query.set("provider", params.provider);
  if (params?.base_url) query.set("base_url", params.base_url);
  if (params?.model) query.set("model", params.model);
  if (params?.live_probe !== undefined) query.set("live_probe", String(params.live_probe));
  const qs = query.toString();
  return requestJson(qs ? `/api/v1/local-llm/status?${qs}` : "/api/v1/local-llm/status");
}

export async function probeLocalLLM(
  params: ProbeLocalLLMParams
): Promise<{ probe: LocalLLMStatus }> {
  return requestJson("/api/v1/local-llm/probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function submitLocalLLMProbe(projectId: string, params: ProbeLocalLLMParams): Promise<{ job: Job }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/local-llm/probe:submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function getLocalLLMProbeResult(jobId: string): Promise<{ job: Job; probe: LocalLLMStatus | null }> {
  return requestJson(`/api/v1/local-llm/probes/${encodeURIComponent(jobId)}`);
}

export async function syncLocalLLMProfile(
  params?: SyncLocalLLMProfileParams | string
): Promise<{ profile: { profile_version_id: string; profile_code: string; status: string; probe?: LocalLLMStatus } }> {
  const body = typeof params === "string" ? { model: params } : params ?? {};
  return requestJson("/api/v1/local-llm/profile:sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function publishLocalLLMProfile(
  params: PublishLocalLLMProfileParams | string
): Promise<{ profile: { profile_version_id: string; status: string; probe?: LocalLLMStatus } }> {
  const body =
    typeof params === "string"
      ? { profile_version_id: params }
      : {
          profile_version_id: params.profileVersionId,
          api_key: params.apiKey,
          allow_remote_outbound: params.allowRemoteOutbound,
          probe_job_id: params.probeJobId,
        };
  return requestJson("/api/v1/local-llm/profile:publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function requestScriptBreakdown(
  sessionId: string,
  profileVersionId: string,
  episodeId: string,
  idempotencyKey: string,
  sourceRange?: { sourceParagraphStart?: number; sourceParagraphEnd?: number }
): Promise<BreakdownSubmission> {
  return requestJson(`/api/v1/import-sessions/${encodeURIComponent(sessionId)}:request-breakdown`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({
      profile_version_id: profileVersionId,
      episode_id: episodeId,
      source_paragraph_start: sourceRange?.sourceParagraphStart,
      source_paragraph_end: sourceRange?.sourceParagraphEnd,
    }),
  });
}
import type { Job } from "../../generated/api";
