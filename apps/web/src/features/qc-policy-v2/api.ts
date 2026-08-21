import type { QcPolicy, QcPolicyPut, QcPolicyResolution, QcScopeOption, QcStage } from "./types";

export class QcPolicyApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) { super(message); this.name = "QcPolicyApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string; details?: Record<string, unknown> } } | null;
  if (!response.ok) throw new QcPolicyApiError(response.status, body?.error?.code ?? `HTTP_${response.status}`, body?.error?.message ?? "QC Policy 请求失败", body?.error?.details);
  return body as T;
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

