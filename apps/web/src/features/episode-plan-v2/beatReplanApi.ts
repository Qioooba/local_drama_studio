export type BeatReplanAction = "KEEP" | "ADD" | "MODIFY" | "DELETE" | "PROTECTED";
export type BeatReplanShot = {
  code: string; target_duration_ms: number; shot_type: string;
  fields: Record<string, unknown>;
};
export type BeatReplanDiff = {
  action: BeatReplanAction; shot_id: string | null; expected_revision: number | null;
  before: BeatReplanShot | null; after: BeatReplanShot | null; reason: string | null;
};
export type BeatReplanPlan = {
  episode_id: string; group_id: string; group_code: string; group_title: string;
  group_revision: number; draft_id: string; proposal_scene_no: number; plan_hash: string;
  valid: boolean; issues: Array<{ code: string; message: string }>;
  diff: BeatReplanDiff[]; summary: Record<BeatReplanAction, number>;
  scope: { selected_group_only: boolean; outside_group_shots_touched: number };
};
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json().catch(() => ({})) as { error?: { message?: string } };
  if (!response.ok) throw new Error(body.error?.message ?? `请求失败（${response.status}）`);
  return body as T;
}

type PlanInput = { draft_id: string; proposal_scene_no: number; expected_group_revision: number };

export async function planBeatReplan(episodeId: string, groupId: string, input: PlanInput) {
  return (await request<{ plan: BeatReplanPlan }>(`/episodes/${episodeId}/shot-groups/${groupId}/replan:plan`, {
    method: "POST", body: JSON.stringify(input),
  })).plan;
}

export async function applyBeatReplan(
  episodeId: string, groupId: string, input: PlanInput & { expected_plan_hash: string; idempotency_key: string },
) {
  return (await request<{ apply: {
    group_revision: number; created_shot_ids: string[]; modified_shot_ids: string[];
    archived_shot_ids: string[]; protected_shot_ids: string[]; historical_variants_deleted: number;
  } }>(`/episodes/${episodeId}/shot-groups/${groupId}/replan:apply`, {
    method: "POST", body: JSON.stringify(input),
  })).apply;
}
