import type { QcPolicy, QcPolicyPut, QcPolicyResolution, QcScopeOption, QcStage } from "./types";
import { ApiRequestError, requestJson as generatedRequestJson } from "../../generated/api";

export class QcPolicyApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) { super(message); this.name = "QcPolicyApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try { return await generatedRequestJson<T>(`/api/v1${path}`, init); }
  catch (error) {
    if (error instanceof ApiRequestError) throw new QcPolicyApiError(error.status, error.code, error.message);
    throw error;
  }
}

export async function listQcPolicies(projectId: string) {
  return (await request<{ items: QcPolicy[] }>(`/projects/${encodeURIComponent(projectId)}/qc-policies`)).items;
}
export async function resolveQcPolicy(projectId: string, stage: QcStage, episodeId?: string, shotId?: string) {
  const query = new URLSearchParams({ stage });
  if (episodeId) query.set("episode_id", episodeId);
  if (shotId) query.set("shot_id", shotId);
  return (await request<{ resolution: QcPolicyResolution | null }>(`/projects/${encodeURIComponent(projectId)}/qc-policies?${query}`)).resolution;
}
export async function putQcPolicy(projectId: string, payload: QcPolicyPut) {
  return (await request<{ policy: QcPolicy }>(`/projects/${encodeURIComponent(projectId)}/qc-policies`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).policy;
}
export async function listQcSeasons(projectId: string) { return (await request<{ items: QcScopeOption[] }>(`/projects/${encodeURIComponent(projectId)}/seasons`)).items; }
export async function listQcEpisodes(seasonId: string) { return (await request<{ items: QcScopeOption[] }>(`/projects/seasons/${encodeURIComponent(seasonId)}/episodes`)).items; }
export async function listQcShots(episodeId: string) { return (await request<{ items: QcScopeOption[] }>(`/projects/episodes/${encodeURIComponent(episodeId)}/shots`)).items; }
