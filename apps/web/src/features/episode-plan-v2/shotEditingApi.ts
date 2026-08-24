export type ShotEditContext = {
  episode_id: string;
  ordering_token: string;
  items: Array<{ id: string; code: string; order_key: string; target_duration_ms: number; revision: number }>;
};

export type ShotReorderCommand = {
  shot_id: string; before_shot_id?: string; after_shot_id?: string; expected_revision: number;
};
export type ShotSplitCommand = {
  shot_id: string; expected_revision: number; first_code: string; second_code: string; first_duration_ms: number;
};
export type ShotEditPayload = {
  ordering_token: string; reorder: ShotReorderCommand | null; splits: ShotSplitCommand[];
};
export type ShotEditPlan = {
  plan_hash: string; valid: boolean; issues: Array<{ code: string; message: string }>;
  summary: { reordered: boolean; split: number };
  effects: { timeline: string; selected_results: string; asset_bindings: string };
  ordered_shot_ids: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

export async function getShotEditContext(episodeId: string) {
  return (await request<{ context: ShotEditContext }>(`/episodes/${episodeId}/shot-edit`)).context;
}

export async function planShotEdit(episodeId: string, payload: ShotEditPayload) {
  return (await request<{ plan: ShotEditPlan }>(`/episodes/${episodeId}/shot-edit:plan`, {
    method: "POST", body: JSON.stringify(payload),
  })).plan;
}

export async function commitShotEdit(episodeId: string, payload: ShotEditPayload, planHash: string) {
  return request(`/episodes/${episodeId}/shot-edit:commit`, {
    method: "POST", body: JSON.stringify({ ...payload, expected_plan_hash: planHash }),
  });
}
import { requestJson as generatedRequestJson } from "../../generated/api";
